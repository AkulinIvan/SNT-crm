from datetime import date
from decimal import Decimal
from django.shortcuts import render
from django.views import View
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter, OrderingFilter
from django.db import transaction as db_transaction
from django.db.models import Sum
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from land.models import LandPlot
from users.models import Owner
from .qr_generator import QRCodeGenerator, SNTDetailsGenerator

from .models import (
    ConsolidatedAssessment, ConsolidatedAssessmentLine, PaymentCategory, PaymentPeriod, Assessment,
    Payment, BankStatement, BankTransaction, ReceiptTemplate
)
from .serializers import (
    AssessmentCreateSerializer, ConsolidatedAssessmentSerializer, PaymentCategorySerializer, PaymentPeriodSerializer,
    AssessmentListSerializer, AssessmentDetailSerializer,
    PaymentSerializer, BankStatementSerializer, BankTransactionSerializer, ReceiptTemplateSerializer
)
from .bank_parser import BankStatementParser, PaymentMatcher


class PaymentCategoryViewSet(viewsets.ModelViewSet):
    queryset = PaymentCategory.objects.all()
    serializer_class = PaymentCategorySerializer


class PaymentPeriodViewSet(viewsets.ModelViewSet):
    queryset = PaymentPeriod.objects.all()
    serializer_class = PaymentPeriodSerializer
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = ['year', 'is_active']
    ordering = ['-year', '-quarter']



    
def generate_receipts_pdf(receipts_data, owner_name):
    """Генерация PDF с несколькими квитанциями"""
    from weasyprint import HTML
    from django.http import HttpResponse
    from datetime import date
    
    # Собираем все HTML квитанции
    combined_html = """
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>Квитанции для {owner_name}</title>
        <style>
            @page {{
                size: A4;
                margin: 1cm;
            }}
            body {{
                font-family: Arial, sans-serif;
            }}
            .receipt {{
                page-break-after: always;
                margin-bottom: 20px;
            }}
            .receipt:last-child {{
                page-break-after: auto;
            }}
        </style>
    </head>
    <body>
    {receipts}
    </body>
    </html>
    """.format(
        owner_name=owner_name,
        receipts='\n<div class="receipt">\n'.join([r['html'] for r in receipts_data]) + '\n</div>'
    )
    
    # Генерируем PDF
    pdf_file = HTML(string=combined_html).write_pdf()
    
    response = HttpResponse(pdf_file, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="квитанции_{owner_name}_{date.today()}.pdf"'
    return response

def generate_mass_receipts_pdf(receipts_html, period, category):
    """Генерация PDF для массовых квитанций"""
    from weasyprint import HTML
    from django.http import HttpResponse
    from datetime import date
    
    combined_html = """
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>Массовые квитанции - {period} - {category}</title>
        <style>
            @page {{
                size: A4;
                margin: 1cm;
            }}
            body {{
                font-family: Arial, sans-serif;
            }}
            .receipt-page {{
                page-break-after: always;
                margin-bottom: 20px;
            }}
            .receipt-page:last-child {{
                page-break-after: auto;
            }}
        </style>
    </head>
    <body>
    {receipts}
    </body>
    </html>
    """.format(
        period=str(period),
        category=category.name,
        receipts='\n<div class="receipt-page">\n'.join(receipts_html) + '\n</div>'
    )
    
    pdf_file = HTML(string=combined_html).write_pdf()
    
    response = HttpResponse(pdf_file, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="массовые_квитанции_{period.year}_{date.today()}.pdf"'
    return response

class AssessmentViewSet(viewsets.ModelViewSet):
    queryset = Assessment.objects.select_related('owner', 'land_plot', 'category', 'period')
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ['owner', 'land_plot', 'category', 'period', 'status']
    search_fields = ['owner__full_name', 'land_plot__plot_number', 'notes']
    ordering_fields = ['amount', 'created_at', 'period__year']
    ordering = ['-created_at']

    def get_serializer_class(self):
        if self.action == 'list':
            return AssessmentListSerializer
        elif self.action == 'create':
            return AssessmentCreateSerializer  
        return AssessmentDetailSerializer

    @action(detail=False, methods=['post'], url_path='mass-generate')
    def mass_generate_assessments(self, request):
        """
        POST /api/assessments/mass-generate/
        Массовое создание начислений для выбранных владельцев.

        Тело: {
            "owner_ids": [1, 2, 3],  // массив ID владельцев
            "period_id": 1,
            "category_id": 1,
            "generate_receipts": true,  // опционально
            "format": "pdf"  // опционально
        }
        """
        from django.db import transaction as db_transaction
        from users.models import Owner
        from land.models import LandPlot
        from django.template.loader import render_to_string
        from django.http import HttpResponse
        from datetime import date

        owner_ids = request.data.get('owner_ids', [])
        period_id = request.data.get('period_id')
        category_id = request.data.get('category_id')
        generate_receipts = request.data.get('generate_receipts', False)
        output_format = request.data.get('format', 'json')

        if not owner_ids or not period_id or not category_id:
            return Response(
                {'detail': 'Укажите owner_ids, period_id и category_id'},
                status=400
            )

        try:
            period = PaymentPeriod.objects.get(id=period_id)
            category = PaymentCategory.objects.get(id=category_id)
            owners = Owner.objects.filter(id__in=owner_ids)
        except PaymentPeriod.DoesNotExist:
            return Response({'detail': 'Период не найден'}, status=404)
        except PaymentCategory.DoesNotExist:
            return Response({'detail': 'Категория не найдена'}, status=404)

        if not owners.exists():
            return Response({'detail': 'Владельцы не найдены'}, status=404)

        results = []
        total_created = 0
        all_receipts_html = []

        qr_gen = QRCodeGenerator()
        snt_gen = SNTDetailsGenerator()
        snt_details = snt_gen.get_details()

        with db_transaction.atomic():
            for owner in owners:
                owner_result = {
                    'owner_id': owner.id,
                    'owner_name': owner.full_name,
                    'created': 0,
                    'skipped': 0,
                    'assessments': [],
                    'receipts': []
                }

                plots = owner.land_plots.all()

                if not plots.exists():
                    owner_result['error'] = 'Нет привязанных участков'
                    results.append(owner_result)
                    continue
                
                for plot in plots:
                    # Расчёт суммы
                    if category.unit == 'сотка' and category.rate_per_unit:
                        area_sotka = plot.area_sqm / 100
                        amount = Decimal(str(area_sotka * float(category.rate_per_unit))).quantize(Decimal('0.01'))
                        notes = f"Площадь: {plot.area_sqm} м² ({area_sotka:.2f} соток) × {category.rate_per_unit} ₽/сотка"
                    elif category.default_amount:
                        amount = category.default_amount
                        notes = f"Фиксированная сумма: {category.default_amount} ₽"
                    else:
                        continue
                    
                    # Проверяем существование
                    exists = Assessment.objects.filter(
                        owner=owner,
                        land_plot=plot,
                        category=category,
                        period=period,
                    ).exists()

                    if not exists and amount > 0:
                        assessment = Assessment.objects.create(
                            owner=owner,
                            land_plot=plot,
                            category=category,
                            period=period,
                            amount=amount,
                            payment_uid=Assessment.generate_uid(),
                            notes=notes
                        )

                        assessment_data = {
                            'id': assessment.id,
                            'payment_uid': assessment.payment_uid,
                            'amount': str(assessment.amount),
                            'plot_number': plot.plot_number,
                            'area_sqm': str(plot.area_sqm)
                        }
                        owner_result['assessments'].append(assessment_data)
                        owner_result['created'] += 1
                        total_created += 1

                        # Генерируем квитанцию
                        if generate_receipts:
                            qr_data = qr_gen.generate_qr_data(
                                owner_name=owner.full_name,
                                plot_number=plot.plot_number,
                                amount=assessment.debt,
                                assessment_id=assessment.id,
                                period=str(period),
                                category_name=category.name,
                            )
                            qr_image = qr_gen.get_qr_data_uri(qr_data)

                            receipt_html = render_to_string('payments/receipt.html', {
                                'assessment': assessment,
                                'assessment_id': assessment.id,
                                'qr_code': qr_image,
                                'snt_details': snt_details,
                                'owner_name': owner.full_name,
                                'plot_number': plot.plot_number,
                                'amount': str(assessment.debt),
                                'uid': assessment.payment_uid,
                                'due_date': str(period.due_date) if period.due_date else '',
                            })

                            owner_result['receipts'].append({
                                'assessment_id': assessment.id,
                                'payment_uid': assessment.payment_uid,
                                'plot_number': plot.plot_number,
                                'html': receipt_html
                            })
                            all_receipts_html.append(receipt_html)
                    else:
                        owner_result['skipped'] += 1

                results.append(owner_result)

        response_data = {
            'detail': f'Создано {total_created} начислений',
            'total_created': total_created,
            'owners_processed': len(owners),
            'results': results
        }

        # Если запрошена генерация PDF с квитанциями
        if generate_receipts and output_format == 'pdf' and all_receipts_html:
            return generate_mass_receipts_pdf(all_receipts_html, period, category)

        if generate_receipts:
            response_data['receipts_count'] = len(all_receipts_html)
            response_data['receipts_html'] = all_receipts_html if output_format == 'html' else None

        return Response(response_data)

    @action(detail=False, methods=['post'], url_path='generate')
    def generate_assessments(self, request):
        """
        POST /api/assessments/generate/
        Генерирует начисления для всех участков за указанный период.

        Тело: {
            "period_id": 1, 
            "category_id": 1,
            "custom_amount": null  // опционально, переопределяет сумму
        }
        """
        period_id = request.data.get('period_id')
        category_id = request.data.get('category_id')
        custom_amount = request.data.get('custom_amount')

        if not period_id or not category_id:
            return Response(
                {'detail': 'Укажите period_id и category_id'},
                status=400
            )

        try:
            period = PaymentPeriod.objects.get(id=period_id)
            category = PaymentCategory.objects.get(id=category_id)
        except (PaymentPeriod.DoesNotExist, PaymentCategory.DoesNotExist):
            return Response({'detail': 'Период или категория не найдены'}, status=404)

        from land.models import LandPlot
        from django.db import transaction

        created_count = 0
        assessments_to_create = []

        # Получаем все активные участки с владельцами
        plots = LandPlot.objects.filter(status='active').prefetch_related('owners')

        with transaction.atomic():
            for plot in plots:
                owners = plot.owners.all()
                for owner in owners:
                    # ======== РАСЧЁТ СУММЫ В ЗАВИСИМОСТИ ОТ КАТЕГОРИИ ========

                    if custom_amount:
                        # Если переопределена сумма — используем её
                        amount = Decimal(str(custom_amount))

                    elif category.code == 'membership' and category.unit == 'сотка':
                        # Членские взносы: тариф × площадь в сотках
                        area_sotka = plot.area_sqm / 100  # Переводим м² в сотки
                        rate = category.default_amount or category.rate_per_unit
                        amount = Decimal(str(area_sotka * float(rate))).quantize(Decimal('0.01'))

                    elif category.unit == 'сотка' and category.rate_per_unit:
                        # Любая другая категория с тарифом за сотку
                        area_sotka = plot.area_sqm / 100
                        amount = Decimal(str(area_sotka * float(category.rate_per_unit))).quantize(Decimal('0.01'))

                    elif category.unit == 'кВт·ч' and category.rate_per_unit:
                        # Электроэнергия — нужно передавать показания отдельно
                        # Пока пропускаем, т.к. нет данных о потреблении
                        continue

                    else:
                        # Фиксированная сумма
                        amount = category.default_amount

                    # Проверяем, нет ли уже начисления
                    exists = Assessment.objects.filter(
                        owner=owner,
                        land_plot=plot,
                        category=category,
                        period=period,
                    ).exists()

                    if not exists and amount > 0:
                        # Используем generate_uid для массового создания
                        assessments_to_create.append(
                            Assessment(
                                owner=owner,
                                land_plot=plot,
                                category=category,
                                period=period,
                                amount=amount,
                                payment_uid=Assessment.generate_uid(),  # Явно генерируем UID
                                notes=f"Площадь: {plot.area_sqm} м² ({plot.area_sqm/100:.2f} соток) × {category.default_amount or category.rate_per_unit} ₽/сотка",
                            )
                        )
                        created_count += 1

            # Массовое создание (один запрос к БД вместо многих)
            if assessments_to_create:
                Assessment.objects.bulk_create(assessments_to_create)

        return Response({
            'detail': f'Создано {created_count} начислений',
            'count': created_count,
        })

    @action(detail=False, methods=['get'], url_path='stats')
    def stats(self, request):
        """Статистика по начислениям"""
        data = {
            'total_amount': Assessment.objects.aggregate(s=Sum('amount'))['s'] or 0,
            'total_paid': Assessment.objects.aggregate(s=Sum('paid_amount'))['s'] or 0,
            'total_debt': 0,  # Будет рассчитано ниже
            'by_status': {},
            'by_category': {},
        }
        
        debt_sum = 0
        for a in Assessment.objects.filter(status__in=['pending', 'partial', 'overdue']):
            debt_sum += a.debt
        data['total_debt'] = debt_sum
        
        from django.db.models import Count
        data['by_status'] = dict(
            Assessment.objects.values_list('status').annotate(c=Count('id'))
        )
        data['by_category'] = dict(
            Assessment.objects.values_list('category__name').annotate(c=Count('id'))
        )
        
        return Response(data)

    @action(detail=True, methods=['post'], url_path='add-payment')
    def add_payment(self, request, pk=None):
        """
        POST /api/assessments/{id}/add-payment/
        Добавить платёж к начислению.
        Тело: {"amount": 5000, "payment_method": "cash"}
        """
        assessment = self.get_object()
        amount = request.data.get('amount')
        
        if not amount:
            return Response({'detail': 'Укажите сумму'}, status=400)
        
        payment = Payment.objects.create(
            assessment=assessment,
            amount=amount,
            payment_method=request.data.get('payment_method', 'cash'),
            payment_date=request.data.get('payment_date', date.today()),
            notes=request.data.get('notes', ''),
        )
        
        return Response(PaymentSerializer(payment).data, status=201)

    @action(detail=True, methods=['get'], url_path='receipt')
    def get_receipt(self, request, pk=None):
        """
        GET /api/assessments/{id}/receipt/
        Получить данные для квитанции с QR-кодом.
        
        Query params:
        - format: json (по умолчанию) или html
        """
        assessment = self.get_object()
        
        # Генерируем данные для QR-кода
        qr_gen = QRCodeGenerator()
        qr_data = qr_gen.generate_qr_data(
            owner_name=assessment.owner.full_name,
            plot_number=assessment.land_plot.plot_number,
            amount=assessment.debt,
            assessment_id=assessment.id,
            period=str(assessment.period),
            category_name=assessment.category.name,
        )
        
        # Генерируем QR-код в base64
        qr_image_data = qr_gen.get_qr_data_uri(qr_data)
        
        # Получаем реквизиты СНТ
        snt_gen = SNTDetailsGenerator()
        snt_details = snt_gen.get_details()
        
        data = {
            'assessment_id': assessment.id,
            'assessment': AssessmentDetailSerializer(assessment).data,
            'qr_code': qr_image_data,
            'qr_data': qr_data,
            'snt_details': snt_details,
            'owner_name': assessment.owner.full_name,
            'plot_number': assessment.land_plot.plot_number,
            'amount': str(assessment.debt),
            'purpose': qr_data.split('Purpose=')[-1].split('|')[0] if 'Purpose=' in qr_data else '',
            'due_date': str(assessment.period.due_date) if assessment.period.due_date else '',
        }
        
        return Response(data)

    @action(detail=True, methods=['get'], url_path='receipt-html')
    def get_receipt_html(self, request, pk=None):
        """Получить HTML-квитанцию"""
        assessment = self.get_object()

        qr_gen = QRCodeGenerator()
        # Правильно генерируем данные для QR
        qr_data = qr_gen.generate_qr_data(
            owner_name=assessment.owner.full_name,
            plot_number=assessment.land_plot.plot_number,
            amount=assessment.debt,
            assessment_id=assessment.id,
            period=str(assessment.period),
            category_name=assessment.category.name,
        )
        qr_image = qr_gen.get_qr_data_uri(qr_data)

        snt_gen = SNTDetailsGenerator()
        snt_details = snt_gen.get_details()

        data = {
            'assessment': assessment,
            'assessment_id': assessment.id,
            'qr_code': qr_image,
            'snt_details': snt_details,
            'owner_name': assessment.owner.full_name,
            'plot_number': assessment.land_plot.plot_number,
            'amount': str(assessment.debt),
            'uid': assessment.payment_uid,
            'due_date': str(assessment.period.due_date) if assessment.period.due_date else '',
        }

        return render(request, 'payments/receipt.html', data)
    
    @action(detail=True, methods=['get'], url_path='receipt-pdf')
    def get_receipt_pdf(self, request, pk=None):
        """
        GET /api/assessments/{id}/receipt-pdf/
        Скачать квитанцию в PDF (для печати).
        """
        assessment = self.get_object()
        
        # Генерируем данные
        qr_gen = QRCodeGenerator()
        qr_data = qr_gen.generate_qr_data(
            owner_name=assessment.owner.full_name,
            plot_number=assessment.land_plot.plot_number,
            amount=assessment.debt,
            assessment_id=assessment.id,
            period=str(assessment.period),
            category_name=assessment.category.name,
        )
        qr_image_data = qr_gen.get_qr_data_uri(qr_data)
        
        snt_gen = SNTDetailsGenerator()
        snt_details = snt_gen.get_details()
        
        # Рендерим HTML шаблон
        from django.shortcuts import render
        from django.http import HttpResponse
        
        html = render(request, 'payments/receipt_pdf.html', {
            'assessment': assessment,
            'qr_code': qr_image_data,
            'snt_details': snt_details,
            'owner_name': assessment.owner.full_name,
            'plot_number': assessment.land_plot.plot_number,
            'amount': str(assessment.debt),
            'due_date': str(assessment.period.due_date) if assessment.period.due_date else '',
        }).content.decode('utf-8')
        
        try:
            # Пробуем использовать WeasyPrint для PDF
            from weasyprint import HTML
            pdf_file = HTML(string=html).write_pdf()
            
            response = HttpResponse(pdf_file, content_type='application/pdf')
            response['Content-Disposition'] = f'attachment; filename="квитанция_{assessment.id}.pdf"'
            return response
            
        except ImportError:
            # Если WeasyPrint не установлен, возвращаем HTML
            response = HttpResponse(html)
            response['Content-Type'] = 'text/html'
            response['Content-Disposition'] = f'attachment; filename="квитанция_{assessment.id}.html"'
            return response

    @action(detail=False, methods=['get'], url_path='owner-receipts')
    def get_owner_receipts(self, request):
        """
        GET /api/assessments/owner-receipts/?owner_id=1&period_id=1
        Получить все квитанции владельца за период (для личного кабинета).
        """
        owner_id = request.query_params.get('owner_id')
        period_id = request.query_params.get('period_id')
        
        if not owner_id:
            return Response({'detail': 'Укажите owner_id'}, status=400)
        
        assessments = Assessment.objects.filter(owner_id=owner_id)
        if period_id:
            assessments = assessments.filter(period_id=period_id)
        
        qr_gen = QRCodeGenerator()
        snt_gen = SNTDetailsGenerator()
        snt_details = snt_gen.get_details()
        
        receipts = []
        for assessment in assessments:
            qr_data = qr_gen.generate_qr_data(
                owner_name=assessment.owner.full_name,
                plot_number=assessment.land_plot.plot_number,
                amount=assessment.debt,
                assessment_id=assessment.id,
                period=str(assessment.period),
                category_name=assessment.category.name,
            )
            qr_image_data = qr_gen.get_qr_data_uri(qr_data, size=200)
            
            receipts.append({
                'assessment_id': assessment.id,
                'category': assessment.category.name,
                'period': str(assessment.period),
                'amount': str(assessment.amount),
                'paid': str(assessment.paid_amount),
                'debt': str(assessment.debt),
                'status': assessment.get_status_display(),
                'qr_code': qr_image_data,
                'qr_data': qr_data,
                'snt_details': snt_details,
                'due_date': str(assessment.period.due_date) if assessment.period.due_date else '',
            })
        
        return Response(receipts)

    @action(detail=False, methods=['post'], url_path='generate-for-owner')
    def generate_for_owner(self, request):
        """
        POST /api/assessments/generate-for-owner/
        Генерирует начисления для конкретного владельца и возвращает квитанции.

        Тело: {
            "owner_id": 1,
            "period_id": 1,
            "category_id": 1,
            "generate_receipts": true  // опционально, сразу сгенерировать квитанции
        }
        """
        from django.db import transaction as db_transaction
        from users.models import Owner
        from django.template.loader import render_to_string
        
        owner_id = request.data.get('owner_id')
        period_id = request.data.get('period_id')
        category_id = request.data.get('category_id')
        generate_receipts = request.data.get('generate_receipts', False)

        if not all([owner_id, period_id, category_id]):
            return Response(
                {'detail': 'Укажите owner_id, period_id и category_id'}, 
                status=400
            )

        try:
            owner = Owner.objects.get(id=owner_id)
            period = PaymentPeriod.objects.get(id=period_id)
            category = PaymentCategory.objects.get(id=category_id)
        except Owner.DoesNotExist:
            return Response({'detail': 'Владелец не найден'}, status=404)
        except PaymentPeriod.DoesNotExist:
            return Response({'detail': 'Период не найден'}, status=404)
        except PaymentCategory.DoesNotExist:
            return Response({'detail': 'Категория не найдена'}, status=404)

        assessments_created = []
        plots = owner.land_plots.all()

        if not plots.exists():
            return Response({
                'detail': f'У владельца {owner.full_name} нет привязанных участков',
                'assessments': []
            }, status=200)

        qr_gen = QRCodeGenerator()
        snt_gen = SNTDetailsGenerator()
        snt_details = snt_gen.get_details()

        receipts_data = []

        with db_transaction.atomic():
            for plot in plots:
                # Расчёт суммы в зависимости от категории
                if category.unit == 'сотка' and category.rate_per_unit:
                    # Тариф за сотку
                    area_sotka = plot.area_sqm / 100
                    amount = Decimal(str(area_sotka * float(category.rate_per_unit))).quantize(Decimal('0.01'))
                    notes = f"Площадь: {plot.area_sqm} м² ({area_sotka:.2f} соток) × {category.rate_per_unit} ₽/сотка"
                elif category.default_amount:
                    # Фиксированная сумма
                    amount = category.default_amount
                    notes = f"Фиксированная сумма: {category.default_amount} ₽"
                else:
                    # Пропускаем, если сумма не определена
                    continue
                
                # Проверяем, нет ли уже начисления
                exists = Assessment.objects.filter(
                    owner=owner,
                    land_plot=plot,
                    category=category,
                    period=period,
                ).exists()

                if not exists and amount > 0:
                    assessment = Assessment.objects.create(
                        owner=owner,
                        land_plot=plot,
                        category=category,
                        period=period,
                        amount=amount,
                        payment_uid=Assessment.generate_uid(),
                        notes=notes
                    )

                    assessment_data = {
                        'id': assessment.id,
                        'payment_uid': assessment.payment_uid,
                        'amount': str(assessment.amount),
                        'plot_number': plot.plot_number,
                        'area_sqm': str(plot.area_sqm)
                    }
                    assessments_created.append(assessment_data)

                    # Генерируем квитанцию, если нужно
                    if generate_receipts:
                        # Генерируем QR-код
                        qr_data = qr_gen.generate_qr_data(
                            owner_name=owner.full_name,
                            plot_number=plot.plot_number,
                            amount=assessment.debt,
                            assessment_id=assessment.id,
                            period=str(period),
                            category_name=category.name,
                        )
                        qr_image = qr_gen.get_qr_data_uri(qr_data)

                        receipt_html = render_to_string('payments/receipt.html', {
                            'assessment': assessment,
                            'assessment_id': assessment.id,
                            'qr_code': qr_image,
                            'snt_details': snt_details,
                            'owner_name': owner.full_name,
                            'plot_number': plot.plot_number,
                            'amount': str(assessment.debt),
                            'uid': assessment.payment_uid,
                            'due_date': str(period.due_date) if period.due_date else '',
                        })

                        receipts_data.append({
                            'assessment_id': assessment.id,
                            'payment_uid': assessment.payment_uid,
                            'plot_number': plot.plot_number,
                            'html': receipt_html,
                            'qr_code': qr_image
                        })

        response_data = {
            'detail': f'Создано {len(assessments_created)} начислений для владельца {owner.full_name}',
            'assessments': assessments_created
        }

        if generate_receipts:
            response_data['receipts'] = receipts_data

            # Если запрошен PDF, генерируем его
            if request.data.get('format') == 'pdf':
                return generate_receipts_pdf(receipts_data, owner.full_name)

        return Response(response_data)

@csrf_exempt
@require_http_methods(["POST"])
def generate_combined_pdf(request):
    """Генерация объединённого PDF из HTML"""
    import json
    from weasyprint import HTML
    from django.http import HttpResponse
    from datetime import date
    
    try:
        data = json.loads(request.body)
        html_content = data.get('html', '')
        
        pdf_file = HTML(string=html_content).write_pdf()
        
        response = HttpResponse(pdf_file, content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="combined_receipts_{date.today()}.pdf"'
        return response
    except Exception as e:
        return HttpResponse(json.dumps({'error': str(e)}), status=500, content_type='application/json')
    
    
    
class PaymentViewSet(viewsets.ModelViewSet):
    queryset = Payment.objects.select_related(
        'assessment__owner',
        'assessment__land_plot',
        'assessment__category'
    )
    serializer_class = PaymentSerializer
    filter_backends = [DjangoFilterBackend, OrderingFilter, SearchFilter]
    filterset_fields = [
        'assessment', 
        'payment_method', 
        'status', 
        'payment_date',
        'assessment__owner',      
        'assessment__land_plot',  
    ]
    search_fields = ['assessment__owner__full_name', 'payment_purpose']
    ordering = ['-payment_date']


class BankStatementViewSet(viewsets.ModelViewSet):
    queryset = BankStatement.objects.all()
    serializer_class = BankStatementSerializer
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = ['bank_name', 'status']
    ordering = ['-statement_date']

    @action(detail=False, methods=['post'], url_path='import')
    def import_statement(self, request):
        """
        POST /api/bank-statements/import/
        Импорт банковской выписки из файла.
        """
        import logging
        logger = logging.getLogger('payments')
        
        file = request.FILES.get('file')
        if not file:
            return Response({'detail': 'Загрузите файл'}, status=400)
        
        logger.info(f'Получен файл: {file.name}, размер: {file.size} байт, тип: {file.content_type}')
        
        # Сохраняем файл
        bank_name = request.data.get('bank_name', '')
        statement = BankStatement.objects.create(
            bank_name=bank_name or 'Неизвестный банк',
            account_number=request.data.get('account_number', ''),
            statement_date=date.today(),
            file_original=file,
        )
        
        logger.info(f'Создана запись выписки #{statement.id}, путь: {statement.file_original.path}')
        
        # Парсим файл
        parser = BankStatementParser(bank_name if bank_name else None)
        
        try:
            transactions_data = parser.parse_file(statement.file_original.path)
            logger.info(f'Распарсено транзакций: {len(transactions_data)}')
            
            # Логируем первые 3 транзакции
            for i, t in enumerate(transactions_data[:3]):
                logger.info(f'Транзакция #{i}: дата={t.get("transaction_date")}, сумма={t.get("amount")}, плательщик={t.get("payer_name", "")[:50]}')
                
        except Exception as e:
            logger.error(f'Ошибка парсинга: {e}', exc_info=True)
            statement.status = BankStatement.STATUS_ERROR
            statement.notes = str(e)
            statement.save()
            
            # Пробуем извлечь текст для отладки
            debug_text = ""
            try:
                import pdfplumber
                with pdfplumber.open(statement.file_original.path) as pdf:
                    for page in pdf.pages:
                        t = page.extract_text()
                        if t:
                            debug_text += t + "\n"
            except:
                try:
                    from PyPDF2 import PdfReader
                    reader = PdfReader(statement.file_original.path)
                    for page in reader.pages:
                        t = page.extract_text()
                        if t:
                            debug_text += t + "\n"
                except:
                    debug_text = "Не удалось извлечь текст"
            
            return Response({
                'detail': f'Ошибка парсинга: {str(e)}',
                'statement_id': statement.id,
                'matched': 0,
                'debug_text': debug_text[:500] if debug_text else 'Текст не извлечён',
            }, status=400)
        
        if not transactions_data:
            # Пробуем извлечь текст для отладки
            debug_text = ""
            try:
                import pdfplumber
                with pdfplumber.open(statement.file_original.path) as pdf:
                    for page in pdf.pages:
                        t = page.extract_text()
                        if t:
                            debug_text += t + "\n"
            except:
                try:
                    from PyPDF2 import PdfReader
                    reader = PdfReader(statement.file_original.path)
                    for page in reader.pages:
                        t = page.extract_text()
                        if t:
                            debug_text += t + "\n"
                except:
                    debug_text = "Не удалось извлечь текст"
            
            statement.status = BankStatement.STATUS_ERROR
            statement.notes = 'Не удалось извлечь транзакции из файла'
            statement.save()
            
            return Response({
                'detail': 'Не найдено транзакций в файле. Проверьте формат выписки.',
                'statement_id': statement.id,
                'matched': 0,
                'debug_text': debug_text[:1000] if debug_text else 'Текст не извлечён',
            }, status=400)
        
        # Создаём транзакции
        matcher = PaymentMatcher()
        matched_count = 0
        
        with db_transaction.atomic():
            for trans_data in transactions_data:
                bank_trans = BankTransaction.objects.create(
                    statement=statement,
                    transaction_date=trans_data['transaction_date'],
                    amount=trans_data['amount'],
                    payer_name=trans_data.get('payer_name', ''),
                    payer_account=trans_data.get('payer_account', ''),
                    payer_inn=trans_data.get('payer_inn', ''),
                    payment_purpose=trans_data.get('payment_purpose', ''),
                )
                
                # Пытаемся сопоставить
                match = matcher.match_owner(trans_data)
                if match:
                    owner, confidence = match
                    bank_trans.matched_owner = owner
                    bank_trans.match_confidence = confidence
                    bank_trans.is_matched = confidence >= 50
                    
                    if confidence >= 50:
                        assessment = matcher.match_assessment(
                            owner, trans_data['amount'], trans_data.get('payment_purpose', '')
                        )
                        if assessment:
                            payment = Payment.objects.create(
                                assessment=assessment,
                                amount=trans_data['amount'],
                                payment_date=trans_data['transaction_date'],
                                payment_method='bank',
                                bank_name=statement.bank_name,
                                bank_account=trans_data.get('payer_account', ''),
                                transaction_id=str(bank_trans.id),
                                payment_purpose=trans_data.get('payment_purpose', ''),
                                status=Payment.STATUS_PROCESSED,
                            )
                            bank_trans.matched_payment = payment
                            bank_trans.is_matched = True
                            matched_count += 1
                    
                    bank_trans.save()
        
        statement.total_transactions = len(transactions_data)
        statement.matched_transactions = matched_count
        statement.status = BankStatement.STATUS_PROCESSED
        statement.save()
        
        logger.info(f'Импорт завершён: {len(transactions_data)} транзакций, {matched_count} сопоставлено')
        
        return Response({
            'detail': f'Импортировано {len(transactions_data)} транзакций',
            'statement_id': statement.id,
            'matched': matched_count,
        })


class BankTransactionViewSet(viewsets.ModelViewSet):
    queryset = BankTransaction.objects.select_related('statement', 'matched_owner')
    serializer_class = BankTransactionSerializer
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = ['statement', 'is_matched', 'matched_owner']
    ordering = ['-transaction_date']

class QuickPaymentViewSet(viewsets.ViewSet):
    """ViewSet для быстрой оплаты через QR"""
    
    permission_classes = []  # Доступно всем
    
    @action(detail=False, methods=['get'], url_path='verify/(?P<assessment_id>\\d+)')
    def verify_payment(self, request, assessment_id=None):
        """
        GET /api/quick-payment/verify/{assessment_id}/
        Проверить статус оплаты по ID начисления.
        Используется после оплаты через банк.
        """
        try:
            assessment = Assessment.objects.get(id=assessment_id)
        except Assessment.DoesNotExist:
            return Response({'status': 'not_found'}, status=404)
        
        return Response({
            'assessment_id': assessment.id,
            'status': assessment.status,
            'amount': str(assessment.amount),
            'paid': str(assessment.paid_amount),
            'debt': str(assessment.debt),
        })
    
    @action(detail=False, methods=['post'], url_path='match-payment')
    def match_payment(self, request):
        """
        POST /api/quick-payment/match-payment/
        Ручное сопоставление платежа (если автоматика не сработала).
        
        Тело: {
            "transaction_id": "12345",
            "assessment_id": 1,
            "amount": 5000
        }
        """
        from .models import BankTransaction, Payment
        
        transaction_id = request.data.get('transaction_id')
        assessment_id = request.data.get('assessment_id')
        amount = request.data.get('amount')
        
        if not all([transaction_id, assessment_id, amount]):
            return Response({'detail': 'Все поля обязательны'}, status=400)
        
        try:
            assessment = Assessment.objects.get(id=assessment_id)
            bank_trans = BankTransaction.objects.get(id=transaction_id)
        except (Assessment.DoesNotExist, BankTransaction.DoesNotExist):
            return Response({'detail': 'Начисление или транзакция не найдены'}, status=404)
        
        # Создаём платёж
        payment = Payment.objects.create(
            assessment=assessment,
            amount=Decimal(str(amount)),
            payment_date=bank_trans.transaction_date,
            payment_method='bank',
            bank_name=bank_trans.statement.bank_name,
            bank_account=bank_trans.payer_account,
            transaction_id=str(bank_trans.id),
            payment_purpose=bank_trans.payment_purpose,
            status=Payment.STATUS_PROCESSED,
        )
        
        # Обновляем банковскую транзакцию
        bank_trans.matched_payment = payment
        bank_trans.matched_owner = assessment.owner
        bank_trans.is_matched = True
        bank_trans.match_confidence = 100
        bank_trans.save()
        
        return Response({
            'detail': 'Платёж успешно сопоставлен',
            'payment_id': payment.id,
        })
        
# Веб-представления
class PaymentsDashboardView(View):
    def get(self, request):
        return render(request, 'payments/dashboard.html', {'active_page': 'payments'})


class AssessmentsListView(View):
    def get(self, request):
        return render(request, 'payments/assessments.html', {'active_page': 'payments'})


class BankImportView(View):
    def get(self, request):
        return render(request, 'payments/bank_import.html', {'active_page': 'payments'})
    

class ReceiptTemplateViewSet(viewsets.ModelViewSet):
    queryset = ReceiptTemplate.objects.prefetch_related('lines__category')
    serializer_class = ReceiptTemplateSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['is_active']


class ConsolidatedAssessmentViewSet(viewsets.ModelViewSet):
    queryset = ConsolidatedAssessment.objects.select_related(
        'owner', 'land_plot', 'period'
    ).prefetch_related('lines__category')
    serializer_class = ConsolidatedAssessmentSerializer
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = ['owner', 'land_plot', 'period', 'status']
    ordering = ['-created_at']

    @action(detail=False, methods=['post'], url_path='generate-from-template')
    def generate_from_template(self, request):
        """
        POST /api/consolidated/generate-from-template/
        Создаёт составную квитанцию по шаблону.
        
        Тело: {
            "template_id": 1,
            "owner_id": 1,
            "land_plot_id": 5,
            "period_id": 1,
            "electricity_quantity": 200  // опционально
        }
        """
        template_id = request.data.get('template_id')
        owner_id = request.data.get('owner_id')
        land_plot_id = request.data.get('land_plot_id')
        period_id = request.data.get('period_id')

        if not all([template_id, owner_id, land_plot_id, period_id]):
            return Response({'detail': 'Все поля обязательны'}, status=400)

        try:
            template = ReceiptTemplate.objects.prefetch_related('lines__category').get(id=template_id)
            owner = Owner.objects.get(id=owner_id)
            land_plot = LandPlot.objects.get(id=land_plot_id)
            period = PaymentPeriod.objects.get(id=period_id)
        except Exception as e:
            return Response({'detail': str(e)}, status=404)

        # Создаём составное начисление
        consolidated = ConsolidatedAssessment.objects.create(
            owner=owner,
            land_plot=land_plot,
            period=period,
            total_amount=0,
        )

        total = 0
        lines_data = request.data.get('manual_lines', {})  # {"category_id": quantity}

        for line_template in template.lines.all():
            category = line_template.category

            if line_template.calc_type == 'fixed':
                quantity = 1
                rate = line_template.amount
                description = f"{category.name}"
            else:  # per_unit
                if line_template.auto_quantity:
                    if category.unit == 'сотка':
                        quantity = land_plot.area_sqm / 100
                    else:
                        quantity = 1
                else:
                    # Ручной ввод количества
                    quantity = Decimal(str(lines_data.get(str(category.id), line_template.manual_quantity)))
                
                rate = line_template.amount
                description = f"{category.name} ({quantity} {category.unit})"

            line_amount = quantity * rate
            total += line_amount

            ConsolidatedAssessmentLine.objects.create(
                consolidated=consolidated,
                category=category,
                description=description,
                quantity=quantity,
                unit=category.unit,
                rate=rate,
                amount=line_amount,
                order=line_template.order,
            )

        consolidated.total_amount = total
        consolidated.save()

        return Response(ConsolidatedAssessmentSerializer(consolidated).data, status=201)

    @action(detail=True, methods=['get'], url_path='receipt')
    def get_consolidated_receipt(self, request, pk=None):
        """Квитанция для составного начисления"""
        consolidated = self.get_object()

        qr_gen = QRCodeGenerator()

        # Формируем назначение платежа со всеми строками
        lines_desc = []
        for line in consolidated.lines.all():
            lines_desc.append(f"{line.description}: {line.amount} ₽")
        
        purpose = (
            f"Оплата за {consolidated.period}. "
            + ". ".join(lines_desc)
            + f". Уч.№{consolidated.land_plot.plot_number}, "
            + f"Владелец: {consolidated.owner.full_name}, "
            + f"UID:{consolidated.payment_uid}. Без НДС."
        )[:210]

        # Генерируем QR вручную для составного
        snt_gen = SNTDetailsGenerator()
        snt_details = snt_gen.get_details()

        # Ручной QR
        fields = [
            "ST00012",
            f"Name={snt_details['name'][:160]}",
            f"PersonalAcc={snt_details['account'].replace(' ', '')}",
            f"BankName={snt_details['bank_name'][:45]}",
            f"BIC={snt_details['bank_bik']}",
            f"CorrespAcc={snt_details['bank_corr'].replace(' ', '')}",
            f"Sum={int(consolidated.debt * 100)}",
            f"Purpose={purpose}",
        ]
        qr_data = "|".join(fields)

        try:
            import qrcode, base64
            from io import BytesIO
            qr = qrcode.QRCode(version=2, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=10, border=4)
            qr.add_data(qr_data)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            buf = BytesIO()
            img.save(buf, format="PNG")
            qr_code = f"data:image/png;base64,{base64.b64encode(buf.getvalue()).decode()}"
        except:
            qr_code = ""

        data = {
            'assessment_id': consolidated.id,
            'uid': consolidated.payment_uid,
            'owner_name': consolidated.owner.full_name,
            'plot_number': consolidated.land_plot.plot_number,
            'period': str(consolidated.period),
            'total_amount': str(consolidated.total_amount),
            'paid_amount': str(consolidated.paid_amount),
            'debt': str(consolidated.debt),
            'lines': [
                {
                    'description': line.description,
                    'quantity': str(line.quantity),
                    'unit': line.unit,
                    'rate': str(line.rate),
                    'amount': str(line.amount),
                }
                for line in consolidated.lines.all()
            ],
            'qr_code': qr_code,
            'snt_details': snt_details,
        }

        if request.query_params.get('format') == 'html':
            return render(request, 'payments/consolidated_receipt.html', data)

        return Response(data)
    
    
