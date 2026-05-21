from datetime import date
from decimal import Decimal
from django.shortcuts import render
from django.views import View
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter, OrderingFilter
from django.db import transaction as db_transaction
from django.db.models import Sum
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.http import HttpResponse, JsonResponse
from django.template.loader import render_to_string
import json
import logging

from .email_service import EmailReceiptService
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

logger = logging.getLogger(__name__)


class PaymentCategoryViewSet(viewsets.ModelViewSet):
    queryset = PaymentCategory.objects.all()
    serializer_class = PaymentCategorySerializer
    
    @action(detail=False, methods=['get'], url_path='check-membership')
    def check_membership(self, request):
        """Проверка настроек членских взносов"""
        categories = PaymentCategory.objects.filter(code='membership')
        
        result = []
        for cat in categories:
            result.append({
                'id': cat.id,
                'name': cat.name,
                'code': cat.code,
                'unit': cat.unit,
                'rate_per_unit': float(cat.rate_per_unit) if cat.rate_per_unit else 0,
                'default_amount': float(cat.default_amount) if cat.default_amount else 0,
                'is_active': cat.is_active,
                'can_calculate_by_area': cat.unit == 'сотка' and cat.rate_per_unit and cat.rate_per_unit > 0,
            })
        
        return Response(result)


class PaymentPeriodViewSet(viewsets.ModelViewSet):
    queryset = PaymentPeriod.objects.all()
    serializer_class = PaymentPeriodSerializer
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = ['year', 'is_active']
    ordering = ['-year', '-quarter']


def generate_receipts_pdf(receipts_data, owner_name, period=None, category=None):
    """Генерация PDF с несколькими квитанциями"""
    from weasyprint import HTML
    from django.http import HttpResponse
    
    # Собираем все HTML квитанции
    receipts_html = []
    for receipt in receipts_data:
        if isinstance(receipt, dict) and 'html' in receipt:
            receipts_html.append(receipt['html'])
        elif isinstance(receipt, str):
            receipts_html.append(receipt)
    
    combined_html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>Квитанции для {owner_name}</title>
        <style>
            @page {{ size: A4; margin: 1cm; }}
            body {{ font-family: Arial, sans-serif; }}
            .receipt {{ page-break-after: always; margin-bottom: 20px; }}
            .receipt:last-child {{ page-break-after: auto; }}
        </style>
    </head>
    <body>
        {''.join(receipts_html)}
    </body>
    </html>
    """
    
    pdf_file = HTML(string=combined_html).write_pdf()
    response = HttpResponse(pdf_file, content_type='application/pdf')
    filename = f"квитанции_{owner_name}_{date.today()}.pdf"
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


def generate_mass_receipts_pdf(receipts_html, period, category):
    """Генерация PDF для массовых квитанций"""
    from weasyprint import HTML
    from django.http import HttpResponse
    
    combined_html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>Массовые квитанции - {period} - {category.name}</title>
        <style>
            @page {{ size: A4; margin: 1cm; }}
            body {{ font-family: Arial, sans-serif; }}
            .receipt-page {{ page-break-after: always; margin-bottom: 20px; }}
            .receipt-page:last-child {{ page-break-after: auto; }}
        </style>
    </head>
    <body>
        {''.join([f'<div class="receipt-page">{html}</div>' for html in receipts_html])}
    </body>
    </html>
    """
    
    pdf_file = HTML(string=combined_html).write_pdf()
    response = HttpResponse(pdf_file, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="массовые_квитанции_{period.year}_{date.today()}.pdf"'
    return response


class AssessmentViewSet(viewsets.ModelViewSet):
    queryset = Assessment.objects.select_related('owner', 'land_plot', 'category', 'period')
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ['owner', 'land_plot', 'category', 'period', 'status']
    search_fields = ['owner__full_name', 'land_plot__plot_number', 'notes', 'payment_uid']
    ordering_fields = ['amount', 'created_at', 'period__year']
    ordering = ['-created_at']

    def get_serializer_class(self):
        if self.action == 'list':
            return AssessmentListSerializer
        elif self.action == 'create':
            return AssessmentCreateSerializer  
        return AssessmentDetailSerializer

    def create(self, request, *args, **kwargs):
        """Создание начисления с автоматическим расчётом суммы если amount=0"""
        data = request.data.copy()
        
        # Если сумма не указана или 0 - рассчитываем автоматически
        if not data.get('amount') or Decimal(str(data.get('amount'))) == 0:
            category_id = data.get('category')
            land_plot_id = data.get('land_plot')
            
            if category_id and land_plot_id:
                try:
                    category = PaymentCategory.objects.get(id=category_id)
                    land_plot = LandPlot.objects.get(id=land_plot_id)
                    
                    if category.unit == 'сотка' and category.rate_per_unit:
                        area_sotka = land_plot.area_sqm / 100
                        amount = Decimal(str(area_sotka * float(category.rate_per_unit))).quantize(Decimal('0.01'))
                        data['amount'] = str(amount)
                        data['notes'] = f"Авторасчёт: {area_sotka:.2f} соток × {category.rate_per_unit} ₽/сотка"
                    elif category.default_amount:
                        data['amount'] = str(category.default_amount)
                except Exception as e:
                    pass
        
        serializer = self.get_serializer(data=data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)

    @action(detail=False, methods=['get'], url_path='owners-without-email')
    def owners_without_email(self, request):
        """
        GET /api/assessments/owners-without-email/
        Получить список владельцев без email
        """
        from users.models import ContactInfo

        # Получаем всех владельцев, у которых есть начисления
        assessments = Assessment.objects.filter(
            status__in=['pending', 'partial', 'overdue']
        ).select_related('owner')

        category_id = request.query_params.get('category_id')
        period_id = request.query_params.get('period_id')

        if category_id:
            assessments = assessments.filter(category_id=category_id)
        if period_id:
            assessments = assessments.filter(period_id=period_id)

        # Находим владельцев без активного email
        owners_without_email = []
        owner_ids_seen = set()

        for assessment in assessments:
            owner = assessment.owner
            if owner.id in owner_ids_seen:
                continue
            owner_ids_seen.add(owner.id)

            # Проверяем наличие активного email в ContactInfo
            has_email = owner.contacts.filter(type='em', is_active=True).exists()
            if not has_email:
                # Получаем общую задолженность владельца
                total_debt = sum(
                    a.debt for a in Assessment.objects.filter(
                        owner=owner,
                        status__in=['pending', 'partial', 'overdue']
                    )
                )
                owners_without_email.append({
                    'owner_id': owner.id,
                    'owner_name': owner.full_name,
                    'has_debt': total_debt > 0,
                    'total_debt': str(total_debt),
                })

        return Response({
            'total': len(owners_without_email),
            'owners': owners_without_email,
        })
    
    @action(detail=False, methods=['post'], url_path='mass-generate')
    def mass_generate_assessments(self, request):
        """Массовое создание начислений для выбранных владельцев"""
        
        logger.info("=" * 50)
        logger.info("MASS GENERATE ASSESSMENTS CALLED")
        logger.info(f"Request data: {request.data}")
        
        owner_ids = request.data.get('owner_ids', [])
        period_id = request.data.get('period_id')
        category_id = request.data.get('category_id')
        generate_receipts = request.data.get('generate_receipts', False)
        output_format = request.data.get('format', 'json')
        skip_existing = request.data.get('skip_existing', True) 

        logger.info(f"Params: owner_ids={owner_ids}, period_id={period_id}, category_id={category_id}")
        logger.info(f"generate_receipts={generate_receipts}, output_format={output_format}")    

        if not owner_ids or not period_id or not category_id:
            logger.error("Missing required parameters")
            return Response(
                {'detail': 'Укажите owner_ids, period_id и category_id'},
                status=status.HTTP_400_BAD_REQUEST
            )   

        try:
            period = PaymentPeriod.objects.get(id=period_id)
            category = PaymentCategory.objects.get(id=category_id)
            owners = Owner.objects.filter(id__in=owner_ids)
            
            logger.info(f"Period found: {period}")
            logger.info(f"Category found: id={category.id}, name={category.name}")
            logger.info(f"Category unit={category.unit}, rate_per_unit={category.rate_per_unit}, default_amount={category.default_amount}")
            logger.info(f"Owners count: {owners.count()}")
            
        except PaymentPeriod.DoesNotExist:
            logger.error(f"Period {period_id} not found")
            return Response({'detail': 'Период не найден'}, status=status.HTTP_404_NOT_FOUND)
        except PaymentCategory.DoesNotExist:
            logger.error(f"Category {category_id} not found")
            return Response({'detail': 'Категория не найдена'}, status=status.HTTP_404_NOT_FOUND)   

        if not owners.exists():
            logger.error("No owners found")
            return Response({'detail': 'Владельцы не найдены'}, status=status.HTTP_404_NOT_FOUND)   

        results = []
        total_created = 0
        all_receipts_html = []  

        qr_gen = QRCodeGenerator()
        snt_gen = SNTDetailsGenerator()
        snt_details = snt_gen.get_details() 

        with db_transaction.atomic():
            for owner in owners:
                logger.info(f"Processing owner: {owner.id} - {owner.full_name}")
                
                owner_result = {
                    'owner_id': owner.id,
                    'owner_name': owner.full_name,
                    'created': 0,
                    'skipped': 0,
                    'assessments': [],
                    'receipts': []
                }   

                plots = owner.land_plots.all()
                logger.info(f"Plots count: {plots.count()}")
                
                for plot in plots:
                    logger.info(f"Processing plot: {plot.id} - {plot.plot_number}, area: {plot.area_sqm} sqm")
                    
                    # РАСЧЁТ СУММЫ
                    amount = Decimal('0')
                    notes = ''
                    
                    # Проверяем, как считаем
                    if category.rate_per_unit and category.rate_per_unit > 0 and category.unit == 'сотка':
                        area_sotka = Decimal(str(plot.area_sqm)) / Decimal('100')
                        amount = (area_sotka * category.rate_per_unit).quantize(Decimal('0.01'))
                        notes = f"{category.name}: {area_sotka:.2f} соток × {category.rate_per_unit} ₽/сотка = {amount} ₽"
                        logger.info(f"Calculated by area: {area_sotka} * {category.rate_per_unit} = {amount}")
                        
                    elif category.default_amount and category.default_amount > 0:
                        amount = category.default_amount
                        notes = f"{category.name}: {amount} ₽ (фиксированная сумма)"
                        logger.info(f"Fixed amount: {amount}")
                        
                    else:
                        logger.warning(f"Cannot calculate: rate_per_unit={category.rate_per_unit}, unit={category.unit}, default={category.default_amount}")
                        owner_result['skipped'] += 1
                        continue
                    
                    if amount <= 0:
                        logger.warning(f"Amount <= 0, skipping")
                        owner_result['skipped'] += 1
                        continue
                    
                    # Проверяем существование
                    exists = Assessment.objects.filter(
                        owner=owner,
                        land_plot=plot,
                        category=category,
                        period=period,
                    ).exists()
                    
                    if skip_existing and exists:
                        logger.info(f"Assessment already exists, skipping")
                        owner_result['skipped'] += 1
                        continue    

                    # Создаём начисление
                    logger.info(f"Creating assessment with amount {amount}")
                    assessment = Assessment.objects.create(
                        owner=owner,
                        land_plot=plot,
                        category=category,
                        period=period,
                        amount=amount,
                        notes=notes
                    )
                    # Генерируем UID после создания (чтобы был ID)
                    assessment.payment_uid = f"SNT-{assessment.id:06d}"
                    assessment.save(update_fields=['payment_uid'])
                    
                    logger.info(f"Created assessment #{assessment.id} with UID {assessment.payment_uid}")   

                    assessment_data = {
                        'id': assessment.id,
                        'payment_uid': assessment.payment_uid,
                        'amount': str(assessment.amount),
                        'plot_number': plot.plot_number,
                        'area_sqm': str(plot.area_sqm),
                        'area_sotka': str(plot.area_sqm / 100),
                        'calculation': notes,
                    }
                    owner_result['assessments'].append(assessment_data)
                    owner_result['created'] += 1
                    total_created += 1  

                    # Генерируем квитанцию если нужно
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
                            'purpose': qr_data.split('Purpose=')[-1].split('|')[0] if 'Purpose=' in qr_data else '',
                            'calculation_details': notes,
                        })  

                        owner_result['receipts'].append({
                            'assessment_id': assessment.id,
                            'payment_uid': assessment.payment_uid,
                            'plot_number': plot.plot_number,
                            'html': receipt_html
                        })
                        all_receipts_html.append(receipt_html)  

                results.append(owner_result)
                logger.info(f"Owner result: created={owner_result['created']}, skipped={owner_result['skipped']}")  

        response_data = {
            'detail': f'Создано {total_created} начислений',
            'total_created': total_created,
            'owners_processed': len(owners),
            'results': results
        }
        
        logger.info(f"Final response: {response_data}") 

        if generate_receipts and output_format == 'pdf' and all_receipts_html:
            return generate_mass_receipts_pdf(all_receipts_html, period, category)  

        if generate_receipts:
            response_data['receipts_count'] = len(all_receipts_html)
            response_data['receipts_html'] = all_receipts_html if output_format == 'html' else None 

        return Response(response_data)

    @action(detail=False, methods=['post'], url_path='generate')
    def generate_assessments(self, request):
        """Генерирует начисления для всех участков за указанный период"""
        period_id = request.data.get('period_id')
        category_id = request.data.get('category_id')
        custom_amount = request.data.get('custom_amount')

        if not period_id or not category_id:
            return Response(
                {'detail': 'Укажите period_id и category_id'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            period = PaymentPeriod.objects.get(id=period_id)
            category = PaymentCategory.objects.get(id=category_id)
        except (PaymentPeriod.DoesNotExist, PaymentCategory.DoesNotExist):
            return Response({'detail': 'Период или категория не найдены'}, status=status.HTTP_404_NOT_FOUND)

        created_count = 0
        assessments_to_create = []

        # Получаем все активные участки с владельцами
        plots = LandPlot.objects.filter(status='active').prefetch_related('owners')

        with db_transaction.atomic():
            for plot in plots:
                owners_list = plot.owners.all()
                for owner in owners_list:
                    # ИСПРАВЛЕНО: используем calculate_amount
                    if custom_amount:
                        amount = Decimal(str(custom_amount))
                        notes = f"Ручная установка: {custom_amount} ₽"
                    else:
                        amount, notes = category.calculate_amount(land_plot=plot)
                    
                    if amount == 0:
                        continue

                    # Проверяем, нет ли уже начисления
                    exists = Assessment.objects.filter(
                        owner=owner,
                        land_plot=plot,
                        category=category,
                        period=period,
                    ).exists()

                    if not exists:
                        assessments_to_create.append(
                            Assessment(
                                owner=owner,
                                land_plot=plot,
                                category=category,
                                period=period,
                                amount=amount,
                                payment_uid=Assessment.generate_uid(),
                                notes=notes,
                            )
                        )
                        created_count += 1

            if assessments_to_create:
                Assessment.objects.bulk_create(assessments_to_create)

        return Response({
            'detail': f'Создано {created_count} начислений',
            'count': created_count,
        })

    @action(detail=True, methods=['post'], url_path='send-email')
    def send_receipt_email(self, request, pk=None):
        """
        POST /api/assessments/{id}/send-email/
        Отправить квитанцию на email владельца
        """
        assessment = self.get_object()
        from .email_service import EmailReceiptService
        email_service = EmailReceiptService()
        
        recipient_email = request.data.get('email')
        send_to_all = request.data.get('send_to_all', False)  # Отправить на все email
        
        if send_to_all:
            results = email_service.send_receipt_to_all_emails(
                assessment=assessment,
                send_pdf_attachment=request.data.get('attach_pdf', True)
            )
            sent_count = sum(1 for r in results if r['success'])
            return Response({
                'detail': f'Отправлено на {sent_count} из {len(results)} адресов',
                'results': results,
            })
        else:
            result = email_service.send_receipt_to_owner(
                assessment=assessment,
                recipient_email=recipient_email,
                send_pdf_attachment=request.data.get('attach_pdf', True)
            )
            
            if result['success']:
                return Response(result)
            else:
                return Response(result, status=status.HTTP_400_BAD_REQUEST)
    
    @action(detail=False, methods=['get'], url_path='owners-without-email')
    def owners_without_email(self, request):
        """
        GET /api/assessments/owners-without-email/
        Получить список должников без email
        """
        category_id = request.query_params.get('category_id')
        period_id = request.query_params.get('period_id')
        
        from .email_service import email_sender
        result = email_sender.send_to_owners_without_email(
            period_id=int(period_id) if period_id else None,
            category_id=int(category_id) if category_id else None
        )
        
        return Response({
            'total': len(result),
            'owners': result,
        })
        
    @action(detail=False, methods=['post'], url_path='bulk-send-email')
    def bulk_send_receipts_email(self, request):
        """
        POST /api/assessments/bulk-send-email/
        
        Тело запроса:
        {
            "assessment_ids": [1,2,3],  // ID начислений
            "category_id": 1,           // или фильтр по категории
            "period_id": 1,             // или фильтр по периоду
            "only_debtors": true,       // только должники
            "min_debt": 100,            // минимальная сумма долга
            "attach_pdf": true
        }
        """
        assessment_ids = request.data.get('assessment_ids', [])
        category_id = request.data.get('category_id')
        period_id = request.data.get('period_id')
        only_debtors = request.data.get('only_debtors', False)
        min_debt = Decimal(str(request.data.get('min_debt', 0)))
        attach_pdf = request.data.get('attach_pdf', True)
        
        # Формируем queryset
        if assessment_ids:
            assessments = Assessment.objects.filter(id__in=assessment_ids)
        else:
            assessments = Assessment.objects.filter(
                owner__email__isnull=False,
                owner__email__gt='',
            )
            if category_id:
                assessments = assessments.filter(category_id=category_id)
            if period_id:
                assessments = assessments.filter(period_id=period_id)
            if only_debtors:
                assessments = assessments.filter(
                    status__in=['pending', 'partial', 'overdue']
                )
            if min_debt > 0:
                assessments = assessments.filter(amount__gte=min_debt)
        
        if not assessments.exists():
            return Response({
                'detail': 'Нет начислений для отправки',
                'total': 0,
            }, status=status.HTTP_200_OK)
        
        email_service = EmailReceiptService()
        
        # Отправляем асинхронно в фоновом потоке
        from threading import Thread
        
        def send_in_background():
            email_service.send_bulk_receipts(
                assessments=list(assessments),
                send_pdf_attachment=attach_pdf
            )
        
        Thread(target=send_in_background, daemon=True).start()
        
        return Response({
            'detail': f'Запущена рассылка {assessments.count()} квитанций',
            'total': assessments.count(),
            'status': 'processing',
        })
    
    @action(detail=False, methods=['post'], url_path='send-to-debtors')
    def send_to_debtors(self, request):
        """
        POST /api/assessments/send-to-debtors/
        Отправка квитанций всем должникам
        """
        from .email_service import email_sender
        
        category_id = request.data.get('category_id')
        period_id = request.data.get('period_id')
        min_debt = Decimal(str(request.data.get('min_debt', 0)))
        attach_pdf = request.data.get('attach_pdf', False)  # ← по умолчанию False
        
        assessments = Assessment.objects.filter(
            status__in=['pending', 'partial', 'overdue']
        ).select_related('owner', 'category', 'period')
        
        if category_id:
            assessments = assessments.filter(category_id=category_id)
        if period_id:
            assessments = assessments.filter(period_id=period_id)
        if min_debt > 0:
            assessments = assessments.filter(amount__gte=min_debt)
        
        # Фильтруем только тех, у кого есть email
        assessments_with_email = []
        for assessment in assessments:
            has_email = assessment.owner.contacts.filter(
                type='em', is_active=True
            ).exists()
            if has_email:
                assessments_with_email.append(assessment)
        
        if not assessments_with_email:
            return Response({
                'detail': 'Нет должников с email для рассылки',
                'total': 0,
            })
        
        # Запускаем асинхронную отправку
        from threading import Thread
        
        def send_in_background():
            email_sender.email_service.send_bulk_receipts(
                assessments=assessments_with_email,
                send_pdf_attachment=attach_pdf
            )
        
        Thread(target=send_in_background, daemon=True).start()
        
        return Response({
            'detail': f'Запущена рассылка {len(assessments_with_email)} квитанций должникам',
            'total': len(assessments_with_email),
            'status': 'processing',
        })
    
    @action(detail=False, methods=['post'], url_path='bulk-send-email')
    def bulk_send_receipts_email(self, request):
        """
        POST /api/assessments/bulk-send-email/
        
        Массовая рассылка квитанций
        """
        from .email_service import email_sender
        
        assessment_ids = request.data.get('assessment_ids', [])
        category_id = request.data.get('category_id')
        period_id = request.data.get('period_id')
        only_debtors = request.data.get('only_debtors', False)
        min_debt = Decimal(str(request.data.get('min_debt', 0)))
        attach_pdf = request.data.get('attach_pdf', True)
        
        # Формируем queryset
        if assessment_ids:
            assessments = Assessment.objects.filter(id__in=assessment_ids)
        else:
            assessments = Assessment.objects.all()
            if category_id:
                assessments = assessments.filter(category_id=category_id)
            if period_id:
                assessments = assessments.filter(period_id=period_id)
            if only_debtors:
                assessments = assessments.filter(
                    status__in=['pending', 'partial', 'overdue']
                )
            if min_debt > 0:
                assessments = assessments.filter(amount__gte=min_debt)
        
        # Фильтруем только тех, у кого есть email
        assessments_with_email = []
        for assessment in assessments:
            has_email = assessment.owner.contacts.filter(
                type='em', is_active=True
            ).exists()
            if has_email:
                assessments_with_email.append(assessment)
        
        if not assessments_with_email:
            return Response({
                'detail': 'Нет начислений для отправки (у владельцев нет email)',
                'total': 0,
            }, status=status.HTTP_200_OK)
        
        # Запускаем асинхронно
        from threading import Thread
        
        def send_in_background():
            result = email_sender.email_service.send_bulk_receipts(
                assessments=assessments_with_email,
                send_pdf_attachment=attach_pdf
            )
            logger.info(f"Bulk email sent: {result}")
        
        Thread(target=send_in_background, daemon=True).start()
        
        return Response({
            'detail': f'Запущена рассылка {len(assessments_with_email)} квитанций',
            'total': len(assessments_with_email),
            'status': 'processing',
        })

    @action(detail=False, methods=['get'], url_path='stats')
    def stats(self, request):
        """Статистика по начислениям"""
        from django.db.models import Count
        
        total_amount = Assessment.objects.aggregate(s=Sum('amount'))['s'] or 0
        total_paid = Assessment.objects.aggregate(s=Sum('paid_amount'))['s'] or 0
        
        # Расчёт общей задолженности
        total_debt = 0
        for a in Assessment.objects.filter(status__in=['pending', 'partial', 'overdue']):
            total_debt += a.debt
        
        data = {
            'total_amount': float(total_amount),
            'total_paid': float(total_paid),
            'total_debt': float(total_debt),
            'by_status': dict(
                Assessment.objects.values_list('status').annotate(c=Count('id'))
            ),
            'by_category': dict(
                Assessment.objects.values_list('category__name').annotate(c=Count('id'))
            ),
        }
        
        return Response(data)

    @action(detail=True, methods=['post'], url_path='add-payment')
    def add_payment(self, request, pk=None):
        """Добавить платёж к начислению"""
        assessment = self.get_object()
        amount = request.data.get('amount')
        
        if not amount:
            return Response({'detail': 'Укажите сумму'}, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            amount = Decimal(str(amount))
        except:
            return Response({'detail': 'Неверный формат суммы'}, status=status.HTTP_400_BAD_REQUEST)
        
        if amount <= 0:
            return Response({'detail': 'Сумма должна быть больше 0'}, status=status.HTTP_400_BAD_REQUEST)
        
        payment = Payment.objects.create(
            assessment=assessment,
            amount=amount,
            payment_method=request.data.get('payment_method', 'cash'),
            payment_date=request.data.get('payment_date', date.today()),
            notes=request.data.get('notes', ''),
            status=Payment.STATUS_PROCESSED,
        )
        
        return Response(PaymentSerializer(payment).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['get'], url_path='receipt')
    def get_receipt(self, request, pk=None):
        """Получить данные для квитанции с QR-кодом"""
        assessment = self.get_object()
        
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
            'uid': assessment.payment_uid,
        }
        
        return Response(data)

    @action(detail=True, methods=['get'], url_path='receipt-html')
    def get_receipt_html(self, request, pk=None):
        """Получить HTML-квитанцию с QR-кодом"""
        assessment = self.get_object()

        # Инициализируем генераторы
        qr_gen = QRCodeGenerator()
        snt_gen = SNTDetailsGenerator()

        # Генерируем данные для QR-кода
        qr_data = qr_gen.generate_qr_data(
            owner_name=assessment.owner.full_name,
            plot_number=assessment.land_plot.plot_number,
            amount=assessment.debt,
            assessment_id=assessment.id,
            period=str(assessment.period),
            category_name=assessment.category.name,
        )

        # Генерируем QR-код в base64
        qr_image = qr_gen.get_qr_data_uri(qr_data)

        # Получаем реквизиты СНТ
        snt_details = snt_gen.get_details()

        # Формируем purpose для отображения
        purpose = (
            f"Оплата {assessment.category.name} за {assessment.period}. "
            f"Уч.№{assessment.land_plot.plot_number}, "
            f"Владелец: {assessment.owner.full_name}, "
            f"UID:{assessment.payment_uid}. Без НДС."
        )

        context = {
            'assessment': assessment,
            'assessment_id': assessment.id,
            'qr_code': qr_image,  # Убедитесь, что это не пустая строка
            'qr_data': qr_data,
            'snt_details': snt_details,
            'owner_name': assessment.owner.full_name,
            'plot_number': assessment.land_plot.plot_number,
            'amount': str(assessment.debt),
            'uid': assessment.payment_uid,
            'due_date': str(assessment.period.due_date) if assessment.period.due_date else '',
            'purpose': purpose,
        }

        # Добавим отладочную информацию в консоль
        import logging
        logger = logging.getLogger(__name__)
        logger.info(f"QR Code generated for assessment {assessment.id}")
        logger.info(f"QR data length: {len(qr_data)}")
        logger.info(f"QR image data URI length: {len(qr_image) if qr_image else 0}")

        return render(request, 'payments/receipt.html', context)

    @action(detail=True, methods=['get'], url_path='receipt-pdf')
    def get_receipt_pdf(self, request, pk=None):
        """Скачать квитанцию в PDF с использованием reportlab и поддержкой кириллицы"""
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import mm
        from reportlab.pdfgen import canvas
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.lib.utils import ImageReader
        from io import BytesIO
        import os

        assessment = self.get_object()

        # Регистрируем шрифт с поддержкой кириллицы
        # Ищем системные шрифты
        font_paths = [
            "C:/Windows/Fonts/arial.ttf",           # Windows
            "C:/Windows/Fonts/times.ttf",           # Windows
            "C:/Windows/Fonts/calibri.ttf",         # Windows
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",  # Linux
            "/System/Library/Fonts/Arial.ttf",      # macOS
            "/Library/Fonts/Arial.ttf",             # macOS
        ]

        font_registered = False
        for font_path in font_paths:
            if os.path.exists(font_path):
                try:
                    pdfmetrics.registerFont(TTFont('RussianFont', font_path))
                    font_registered = True
                    print(f"Font loaded from: {font_path}")
                    break
                except Exception as e:
                    print(f"Failed to load font from {font_path}: {e}")
                    continue
                
        if not font_registered:
            # Если не нашли шрифт, используем стандартный но с предупреждением
            print("WARNING: No Cyrillic font found, using default (may show squares)")

        # Генерируем QR-код
        qr_gen = QRCodeGenerator()
        qr_data = qr_gen.generate_qr_data(
            owner_name=assessment.owner.full_name,
            plot_number=assessment.land_plot.plot_number,
            amount=assessment.debt,
            assessment_id=assessment.id,
            period=str(assessment.period),
            category_name=assessment.category.name,
        )

        # Получаем изображение QR-кода
        qr_image_bytes = qr_gen.generate_qr_image(qr_data)

        # Создаём PDF
        buffer = BytesIO()
        c = canvas.Canvas(buffer, pagesize=A4)
        width, height = A4

        # Устанавливаем шрифт с поддержкой кириллицы
        if font_registered:
            c.setFont("RussianFont", 16)
        else:
            c.setFont("Helvetica", 16)

        # Заголовок
        c.drawString(50, height - 50, f"Квитанция № {assessment.payment_uid}")

        if font_registered:
            c.setFont("RussianFont", 12)
        else:
            c.setFont("Helvetica", 12)

        c.drawString(50, height - 80, f'СНТ "Строитель-43"')
        c.drawString(50, height - 100, f"Дата: {date.today().strftime('%d.%m.%Y')}")

        # Плательщик
        if font_registered:
            c.setFont("RussianFont", 12)
        else:
            c.setFont("Helvetica-Bold", 12)
        c.drawString(50, height - 140, "Плательщик:")

        if font_registered:
            c.setFont("RussianFont", 11)
        else:
            c.setFont("Helvetica", 11)
        c.drawString(50, height - 160, f"ФИО: {assessment.owner.full_name}")
        c.drawString(50, height - 175, f"Участок: №{assessment.land_plot.plot_number}")
        c.drawString(50, height - 190, f"Период: {assessment.period}")

        # Реквизиты получателя
        snt_gen = SNTDetailsGenerator()
        snt_details = snt_gen.get_details()

        if font_registered:
            c.setFont("RussianFont", 12)
        else:
            c.setFont("Helvetica-Bold", 12)
        c.drawString(50, height - 230, "Получатель платежа:")

        if font_registered:
            c.setFont("RussianFont", 10)
        else:
            c.setFont("Helvetica", 10)
        c.drawString(50, height - 250, f"Получатель: {snt_details['name']}")
        c.drawString(50, height - 265, f"ИНН: {snt_details['inn']}")
        c.drawString(50, height - 280, f"Счёт: {snt_details['account']}")
        c.drawString(50, height - 295, f"Банк: {snt_details['bank_name']}")
        c.drawString(50, height - 310, f"БИК: {snt_details['bank_bik']}")
        c.drawString(50, height - 325, f"Корр. счёт: {snt_details['bank_corr']}")

        # Сумма
        if font_registered:
            c.setFont("RussianFont", 14)
        else:
            c.setFont("Helvetica-Bold", 14)
        c.setFillColorRGB(0.2, 0.5, 0.2)
        c.drawString(50, height - 370, f"Сумма к оплате: {assessment.debt} ₽")
        c.setFillColorRGB(0, 0, 0)

        # QR-код
        if qr_image_bytes:
            qr_buffer = BytesIO(qr_image_bytes)
            qr_img = ImageReader(qr_buffer)
            c.drawImage(qr_img, width - 150, height - 200, width=100, height=100)
            if font_registered:
                c.setFont("RussianFont", 8)
            else:
                c.setFont("Helvetica", 8)
            c.drawString(width - 140, height - 215, "Отсканируйте для оплаты")

        # Подпись
        if font_registered:
            c.setFont("RussianFont", 10)
        else:
            c.setFont("Helvetica", 10)
        c.drawString(50, 80, f"{snt_details['chairman']} _________________")
        c.drawString(50, 50, "При оплате через Сбербанк Онлайн сканируйте QR-код")

        c.save()
        buffer.seek(0)

        response = HttpResponse(buffer, content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="квитанция_{assessment.payment_uid}.pdf"'
        return response

    @action(detail=False, methods=['get'], url_path='owner-receipts')
    def get_owner_receipts(self, request):
        """Получить все квитанции владельца за период"""
        owner_id = request.query_params.get('owner_id')
        period_id = request.query_params.get('period_id')
        
        if not owner_id:
            return Response({'detail': 'Укажите owner_id'}, status=status.HTTP_400_BAD_REQUEST)
        
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
                'payment_uid': assessment.payment_uid,
            })
        
        return Response(receipts)

    @action(detail=False, methods=['post'], url_path='generate-for-owner')
    def generate_for_owner(self, request):
        """Генерирует начисления для конкретного владельца"""
        owner_id = request.data.get('owner_id')
        period_id = request.data.get('period_id')
        category_id = request.data.get('category_id')
        generate_receipts = request.data.get('generate_receipts', False)

        if not all([owner_id, period_id, category_id]):
            return Response(
                {'detail': 'Укажите owner_id, period_id и category_id'}, 
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            owner = Owner.objects.get(id=owner_id)
            period = PaymentPeriod.objects.get(id=period_id)
            category = PaymentCategory.objects.get(id=category_id)
        except Owner.DoesNotExist:
            return Response({'detail': 'Владелец не найден'}, status=status.HTTP_404_NOT_FOUND)
        except PaymentPeriod.DoesNotExist:
            return Response({'detail': 'Период не найден'}, status=status.HTTP_404_NOT_FOUND)
        except PaymentCategory.DoesNotExist:
            return Response({'detail': 'Категория не найдена'}, status=status.HTTP_404_NOT_FOUND)

        assessments_created = []
        plots = owner.land_plots.all()

        if not plots.exists():
            return Response({
                'detail': f'У владельца {owner.full_name} нет привязанных участков',
                'assessments': []
            }, status=status.HTTP_200_OK)

        qr_gen = QRCodeGenerator()
        snt_gen = SNTDetailsGenerator()
        snt_details = snt_gen.get_details()
        receipts_data = []

        with db_transaction.atomic():
            for plot in plots:
                # ИСПРАВЛЕНО: используем calculate_amount
                amount, notes = category.calculate_amount(land_plot=plot)
                
                if amount == 0:
                    continue
                
                # Проверяем, нет ли уже начисления
                exists = Assessment.objects.filter(
                    owner=owner,
                    land_plot=plot,
                    category=category,
                    period=period,
                ).exists()

                if not exists:
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
                        'area_sqm': str(plot.area_sqm),
                        'area_sotka': str(plot.area_sqm / 100),
                        'calculation': notes,
                    }
                    assessments_created.append(assessment_data)

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
                            'purpose': qr_data.split('Purpose=')[-1].split('|')[0] if 'Purpose=' in qr_data else '',
                            'calculation_details': notes,
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
            if request.data.get('format') == 'pdf':
                return generate_receipts_pdf(receipts_data, owner.full_name)

        return Response(response_data)


@csrf_exempt
@require_http_methods(["POST"])
def generate_combined_pdf(request):
    """Генерация объединённого PDF из HTML"""
    try:
        data = json.loads(request.body)
        html_content = data.get('html', '')
        
        if not html_content:
            return JsonResponse({'error': 'HTML content is empty'}, status=400)
        
        from weasyprint import HTML
        pdf_file = HTML(string=html_content).write_pdf()
        
        response = HttpResponse(pdf_file, content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="combined_receipts_{date.today()}.pdf"'
        return response
    except ImportError:
        return JsonResponse({'error': 'WeasyPrint not installed'}, status=500)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)
    
    
class PaymentViewSet(viewsets.ModelViewSet):
    queryset = Payment.objects.select_related(
        'assessment__owner',
        'assessment__land_plot',
        'assessment__category'
    )
    serializer_class = PaymentSerializer
    filter_backends = [DjangoFilterBackend, OrderingFilter, SearchFilter]
    filterset_fields = [
        'assessment', 'payment_method', 'status', 'payment_date',
        'assessment__owner', 'assessment__land_plot',
    ]
    search_fields = ['assessment__owner__full_name', 'payment_purpose', 'matched_uid']
    ordering = ['-payment_date']


class BankStatementViewSet(viewsets.ModelViewSet):
    queryset = BankStatement.objects.all()
    serializer_class = BankStatementSerializer
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = ['bank_name', 'status']
    ordering = ['-statement_date']

    @action(detail=False, methods=['post'], url_path='import')
    def import_statement(self, request):
        """Импорт банковской выписки из файла с автоматическим обновлением статуса"""
        file = request.FILES.get('file')
        if not file:
            return Response({'detail': 'Загрузите файл'}, status=status.HTTP_400_BAD_REQUEST)

        logger.info(f'Получен файл: {file.name}, размер: {file.size} байт')

        # Сохраняем файл
        bank_name = request.data.get('bank_name', '')
        statement = BankStatement.objects.create(
            bank_name=bank_name or 'Неизвестный банк',
            account_number=request.data.get('account_number', ''),
            statement_date=date.today(),
            file_original=file,
            status=BankStatement.STATUS_IMPORTED,
        )

        # Парсим файл
        parser = BankStatementParser(bank_name if bank_name else None)

        try:
            transactions_data = parser.parse_file(statement.file_original.path)
            logger.info(f'Распарсено транзакций: {len(transactions_data)}')
        except Exception as e:
            logger.error(f'Ошибка парсинга: {e}', exc_info=True)
            statement.status = BankStatement.STATUS_ERROR
            statement.notes = str(e)
            statement.save()
            return Response({
                'detail': f'Ошибка парсинга: {str(e)}',
                'statement_id': statement.id,
                'matched': 0,
            }, status=status.HTTP_400_BAD_REQUEST)

        if not transactions_data:
            statement.status = BankStatement.STATUS_ERROR
            statement.notes = 'Не удалось извлечь транзакции из файла'
            statement.save()
            return Response({
                'detail': 'Не найдено транзакций в файле',
                'statement_id': statement.id,
                'matched': 0,
            }, status=status.HTTP_400_BAD_REQUEST)

        # Создаём транзакции и обрабатываем платежи
        matcher = PaymentMatcher()
        matched_count = 0
        payment_results = []

        with db_transaction.atomic():
            for trans_data in transactions_data:
                # Добавляем информацию о банке
                trans_data['bank_name'] = statement.bank_name

                # Создаём банковскую транзакцию
                bank_trans = BankTransaction.objects.create(
                    statement=statement,
                    transaction_date=trans_data['transaction_date'],
                    amount=trans_data['amount'],
                    payer_name=trans_data.get('payer_name', ''),
                    payer_account=trans_data.get('payer_account', ''),
                    payer_inn=trans_data.get('payer_inn', ''),
                    payment_purpose=trans_data.get('payment_purpose', ''),
                )

                # Обрабатываем платеж с автоматическим обновлением статуса
                result = matcher.process_and_update_payments(trans_data)

                if result['matched']:
                    bank_trans.matched_owner_id = result.get('matched_owner_id')
                    bank_trans.is_matched = True
                    bank_trans.match_confidence = result.get('confidence', 0)

                    if result.get('payment_created'):
                        from .models import Payment
                        payment = Payment.objects.get(id=result['payment_id'])
                        bank_trans.matched_payment = payment
                        bank_trans.matched_uid = payment.assessment.payment_uid
                        matched_count += 1

                        payment_results.append({
                            'transaction_id': bank_trans.id,
                            'payer_name': trans_data.get('payer_name', ''),
                            'amount': str(trans_data['amount']),
                            'assessment_id': result['matched_assessment_id'],
                            'new_status': result['assessment_status'],
                            'debt_remaining': result['new_debt'],
                        })

                    bank_trans.save()

        statement.total_transactions = len(transactions_data)
        statement.matched_transactions = matched_count
        statement.status = BankStatement.STATUS_PROCESSED
        statement.save()

        return Response({
            'detail': f'Импортировано {len(transactions_data)} транзакций',
            'statement_id': statement.id,
            'matched': matched_count,
            'payments': payment_results,
        })


class BankTransactionViewSet(viewsets.ModelViewSet):
    queryset = BankTransaction.objects.select_related('statement', 'matched_owner', 'matched_payment')
    serializer_class = BankTransactionSerializer
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = ['statement', 'is_matched', 'matched_owner']
    ordering = ['-transaction_date']


class QuickPaymentViewSet(viewsets.ViewSet):
    """ViewSet для быстрой оплаты через QR"""
    
    permission_classes = []
    
    @action(detail=False, methods=['get'], url_path='verify/(?P<assessment_id>\\d+)')
    def verify_payment(self, request, assessment_id=None):
        """Проверить статус оплаты по ID начисления"""
        try:
            assessment = Assessment.objects.get(id=assessment_id)
        except Assessment.DoesNotExist:
            return Response({'status': 'not_found'}, status=status.HTTP_404_NOT_FOUND)
        
        return Response({
            'assessment_id': assessment.id,
            'status': assessment.status,
            'status_display': assessment.get_status_display(),
            'amount': str(assessment.amount),
            'paid': str(assessment.paid_amount),
            'debt': str(assessment.debt),
            'payment_uid': assessment.payment_uid,
        })
    
    @action(detail=False, methods=['post'], url_path='match-payment')
    def match_payment(self, request):
        """Ручное сопоставление платежа"""
        transaction_id = request.data.get('transaction_id')
        assessment_id = request.data.get('assessment_id')
        amount = request.data.get('amount')
        
        if not all([transaction_id, assessment_id, amount]):
            return Response({'detail': 'Все поля обязательны'}, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            assessment = Assessment.objects.get(id=assessment_id)
            bank_trans = BankTransaction.objects.get(id=transaction_id)
        except Assessment.DoesNotExist:
            return Response({'detail': 'Начисление не найдено'}, status=status.HTTP_404_NOT_FOUND)
        except BankTransaction.DoesNotExist:
            return Response({'detail': 'Транзакция не найдена'}, status=status.HTTP_404_NOT_FOUND)
        
        # Проверяем, не создан ли уже платёж
        if bank_trans.matched_payment:
            return Response({'detail': 'К этой транзакции уже привязан платёж'}, status=status.HTTP_400_BAD_REQUEST)
        
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
            'payment': PaymentSerializer(payment).data,
        })
    
    @action(detail=False, methods=['get'], url_path='by-uid/(?P<uid>[^/.]+)')
    def get_by_uid(self, request, uid=None):
        """Поиск начисления по UID из квитанции"""
        try:
            assessment = Assessment.objects.get(payment_uid=uid)
        except Assessment.DoesNotExist:
            return Response({'detail': 'Начисление не найдено'}, status=status.HTTP_404_NOT_FOUND)
        
        return Response({
            'assessment_id': assessment.id,
            'payment_uid': assessment.payment_uid,
            'owner_name': assessment.owner.full_name,
            'plot_number': assessment.land_plot.plot_number,
            'amount': str(assessment.amount),
            'debt': str(assessment.debt),
            'status': assessment.status,
            'status_display': assessment.get_status_display(),
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
        """Создаёт составную квитанцию по шаблону"""
        template_id = request.data.get('template_id')
        owner_id = request.data.get('owner_id')
        land_plot_id = request.data.get('land_plot_id')
        period_id = request.data.get('period_id')

        if not all([template_id, owner_id, land_plot_id, period_id]):
            return Response({'detail': 'Все поля обязательны'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            template = ReceiptTemplate.objects.prefetch_related('lines__category').get(id=template_id)
            owner = Owner.objects.get(id=owner_id)
            land_plot = LandPlot.objects.get(id=land_plot_id)
            period = PaymentPeriod.objects.get(id=period_id)
        except Exception as e:
            return Response({'detail': str(e)}, status=status.HTTP_404_NOT_FOUND)

        # Создаём составное начисление
        consolidated = ConsolidatedAssessment.objects.create(
            owner=owner,
            land_plot=land_plot,
            period=period,
            total_amount=0,
        )

        total = 0
        lines_data = request.data.get('manual_lines', {})

        for line_template in template.lines.all():
            category = line_template.category
            quantity = Decimal('1')
            rate = line_template.amount

            if line_template.calc_type == 'fixed':
                quantity = 1
                description = f"{category.name}"
            else:  # per_unit
                if line_template.auto_quantity:
                    if category.unit == 'сотка':
                        quantity = Decimal(str(land_plot.area_sqm / 100))
                    else:
                        quantity = 1
                else:
                    quantity = Decimal(str(lines_data.get(str(category.id), line_template.manual_quantity)))
                
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

        return Response(ConsolidatedAssessmentSerializer(consolidated).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['get'], url_path='receipt')
    def get_consolidated_receipt(self, request, pk=None):
        """Квитанция для составного начисления"""
        consolidated = self.get_object()

        qr_gen = QRCodeGenerator()

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

        snt_gen = SNTDetailsGenerator()
        snt_details = snt_gen.get_details()

        # Генерируем QR
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
            'purpose': purpose,
        }

        if request.query_params.get('format') == 'html':
            return render(request, 'payments/consolidated_receipt.html', data)

        return Response(data)