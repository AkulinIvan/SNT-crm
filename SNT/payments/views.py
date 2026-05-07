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
from .qr_generator import QRCodeGenerator, SNTDetailsGenerator

from .models import (
    PaymentCategory, PaymentPeriod, Assessment,
    Payment, BankStatement, BankTransaction
)
from .serializers import (
    AssessmentCreateSerializer, PaymentCategorySerializer, PaymentPeriodSerializer,
    AssessmentListSerializer, AssessmentDetailSerializer,
    PaymentSerializer, BankStatementSerializer, BankTransactionSerializer
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

    @action(detail=False, methods=['post'], url_path='generate')
    def generate_assessments(self, request):
        """
        POST /api/assessments/generate/
        Генерирует начисления для всех участков за указанный период.
        Тело: {"period_id": 1, "category_id": 1}
        """
        period_id = request.data.get('period_id')
        category_id = request.data.get('category_id')
        
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
        from users.models import Owner
        
        created_count = 0
        plots = LandPlot.objects.filter(status='active')
        
        for plot in plots:
            owners = plot.owners.all()
            for owner in owners:
                # Рассчитываем сумму
                amount = category.default_amount
                if category.unit == 'сотка' and category.rate_per_unit:
                    amount = (plot.area_sqm / 100) * category.rate_per_unit
                
                # Проверяем, нет ли уже начисления
                exists = Assessment.objects.filter(
                    owner=owner,
                    land_plot=plot,
                    category=category,
                    period=period,
                ).exists()
                
                if not exists and amount > 0:
                    Assessment.objects.create(
                        owner=owner,
                        land_plot=plot,
                        category=category,
                        period=period,
                        amount=amount,
                    )
                    created_count += 1
        
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
        
        # Если запросили HTML — рендерим шаблон квитанции
        if request.query_params.get('format') == 'html':
            from django.shortcuts import render
            return render(request, 'payments/receipt.html', data)
        
        return Response(data)

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