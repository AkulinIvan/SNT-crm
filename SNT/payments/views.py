from datetime import date
from decimal import Decimal
import os
from django.shortcuts import render
from django.views import View
from rest_framework import viewsets, status
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter, OrderingFilter
from django.db import transaction as db_transaction
from django.db import models
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.http import HttpResponse, JsonResponse
from django.template.loader import render_to_string
from rest_framework.exceptions import PermissionDenied

import json
import logging
import traceback
from typing import Dict, Any, List, Optional
from django.utils.decorators import method_decorator
from django.contrib.auth.decorators import login_required

from common.mixins import OrganizationMixin
from .email_service import EmailReceiptService
from land.models import LandPlot
from users.models import Owner
from .qr_generator import QRCodeGenerator, SNTDetailsGenerator
from subscriptions.decorators import subscription_required
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
        try:
            logger.info(f"User {request.user.id if request.user.is_authenticated else 'Anonymous'} checking membership settings")
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
            
            logger.info(f"Found {len(result)} membership categories")
            return Response(result)
        except Exception as e:
            logger.error(f"Error in check_membership: {e}\n{traceback.format_exc()}")
            return Response(
                {'detail': f'Ошибка при проверке настроек: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class PaymentPeriodViewSet(viewsets.ModelViewSet):
    queryset = PaymentPeriod.objects.all()
    serializer_class = PaymentPeriodSerializer
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = ['year', 'is_active']
    ordering = ['-year', '-quarter']


def generate_receipts_pdf(receipts_data, owner_name, period=None, category=None):
    """Генерация PDF с несколькими квитанциями"""
    try:
        logger.info(f"Generating PDF for owner: {owner_name}, receipts count: {len(receipts_data)}")
        from weasyprint import HTML
        from django.http import HttpResponse
        
        # Собираем все HTML квитанции
        receipts_html = []
        for idx, receipt in enumerate(receipts_data):
            if isinstance(receipt, dict) and 'html' in receipt:
                receipts_html.append(receipt['html'])
                logger.debug(f"Added receipt {idx + 1} from dict")
            elif isinstance(receipt, str):
                receipts_html.append(receipt)
                logger.debug(f"Added receipt {idx + 1} from string")
        
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
        logger.info(f"PDF generated successfully: {filename}, size: {len(pdf_file)} bytes")
        return response
    except ImportError as e:
        logger.error(f"WeasyPrint not installed: {e}")
        return HttpResponse("PDF generation requires weasyprint", status=500)
    except Exception as e:
        logger.error(f"Error generating PDF: {e}\n{traceback.format_exc()}")
        return HttpResponse(f"Error generating PDF: {str(e)}", status=500)


def generate_mass_receipts_pdf(receipts_html, period, category):
    """Генерация PDF для массовых квитанций"""
    try:
        logger.info(f"Generating mass PDF for period: {period}, category: {category.name}, receipts: {len(receipts_html)}")
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
        filename = f"массовые_квитанции_{period.year}_{date.today()}.pdf"
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        logger.info(f"Mass PDF generated successfully: {filename}, size: {len(pdf_file)} bytes")
        return response
    except ImportError as e:
        logger.error(f"WeasyPrint not installed: {e}")
        return HttpResponse("PDF generation requires weasyprint", status=500)
    except Exception as e:
        logger.error(f"Error generating mass PDF: {e}\n{traceback.format_exc()}")
        return HttpResponse(f"Error generating PDF: {str(e)}", status=500)


class AssessmentViewSet(OrganizationMixin, viewsets.ModelViewSet):
    queryset = Assessment.objects.select_related('owner', 'land_plot', 'category', 'period')
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ['owner', 'land_plot', 'category', 'period', 'status']
    search_fields = ['owner__full_name', 'land_plot__plot_number', 'notes', 'payment_uid']
    ordering_fields = ['amount', 'created_at', 'period__year']
    ordering = ['-created_at']

    def get_queryset(self):
        try:
            logger.debug(f"User {self.request.user.id} requesting assessments queryset")
            queryset = super().get_queryset()
            
            # Фильтрация по организации через параметр запроса
            organization = self.request.query_params.get('organization')
            if organization:
                queryset = queryset.filter(owner__memberships__organization_id=organization, owner__memberships__status='active')
                logger.debug(f"Filtered by organization: {organization}")
            
            count = queryset.count()
            logger.debug(f"Assessment queryset returned {count} records")
            return queryset
        except Exception as e:
            logger.error(f"Error in get_queryset: {e}\n{traceback.format_exc()}")
            return Assessment.objects.none()

    def perform_create(self, serializer):
        """При создании проверяем, что owner принадлежит организации пользователя"""
        try:
            logger.debug(f"Performing create for assessment")
            owner = serializer.validated_data.get('owner')
            
            # Проверяем, что владелец принадлежит организации
            if self.request.current_organization:
                if owner.organization != self.request.current_organization:
                    logger.warning(f"Owner {owner.id} does not belong to organization {self.request.current_organization}")
                    raise PermissionDenied("Этот владелец не принадлежит вашему СНТ")
            
            land_plot = serializer.validated_data.get('land_plot')
            
            if self.request.current_organization and land_plot.organization != self.request.current_organization:
                logger.warning(f"Land plot {land_plot.id} does not belong to organization {self.request.current_organization}")
                raise PermissionDenied("Этот участок не принадлежит вашему СНТ")
            
            logger.info(f"Creating assessment for owner {owner.id}, plot {land_plot.id}")
            super().perform_create(serializer)
            logger.info(f"Assessment created successfully with ID {serializer.instance.id}")
        except PermissionDenied:
            raise
        except Exception as e:
            logger.error(f"Error in perform_create: {e}\n{traceback.format_exc()}")
            raise
        
    def get_serializer_class(self):
        try:
            if self.action == 'list':
                return AssessmentListSerializer
            elif self.action == 'create':
                return AssessmentCreateSerializer  
            return AssessmentDetailSerializer
        except Exception as e:
            logger.error(f"Error getting serializer class: {e}")
            return AssessmentDetailSerializer

    def create(self, request, *args, **kwargs):
        """Создание начисления с автоматическим расчётом суммы если amount=0"""
        try:
            logger.info(f"User {request.user.id} creating assessment with data: {request.data}")
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
                            logger.info(f"Auto-calculated amount: {amount} from area {area_sotka} соток")
                        elif category.default_amount:
                            data['amount'] = str(category.default_amount)
                            logger.info(f"Using default amount: {category.default_amount}")
                    except PaymentCategory.DoesNotExist:
                        logger.warning(f"Category {category_id} not found for auto-calculation")
                    except LandPlot.DoesNotExist:
                        logger.warning(f"Land plot {land_plot_id} not found for auto-calculation")
                    except Exception as e:
                        logger.warning(f"Error auto-calculating amount: {e}")
            
            serializer = self.get_serializer(data=data)
            serializer.is_valid(raise_exception=True)
            self.perform_create(serializer)
            headers = self.get_success_headers(serializer.data)
            logger.info(f"Assessment created successfully with ID {serializer.instance.id}")
            return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)
        except Exception as e:
            logger.error(f"Error creating assessment: {e}\n{traceback.format_exc()}")
            return Response(
                {'detail': f'Ошибка при создании начисления: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @action(detail=False, methods=['get'], url_path='owners-without-email')
    def owners_without_email(self, request):
        """
        GET /api/assessments/owners-without-email/
        Получить список владельцев без email
        """
        try:
            logger.info(f"User {request.user.id} fetching owners without email")
            from users.models import ContactInfo

            # Получаем всех владельцев, у которых есть начисления
            assessments = Assessment.objects.filter(
                status__in=['pending', 'partial', 'overdue']
            ).select_related('owner')

            category_id = request.query_params.get('category_id')
            period_id = request.query_params.get('period_id')

            if category_id:
                assessments = assessments.filter(category_id=category_id)
                logger.debug(f"Filtered by category_id: {category_id}")
            if period_id:
                assessments = assessments.filter(period_id=period_id)
                logger.debug(f"Filtered by period_id: {period_id}")

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

            logger.info(f"Found {len(owners_without_email)} owners without email")
            return Response({
                'total': len(owners_without_email),
                'owners': owners_without_email,
            })
        except Exception as e:
            logger.error(f"Error in owners_without_email: {e}\n{traceback.format_exc()}")
            return Response(
                {'detail': f'Ошибка при получении списка владельцев: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=False, methods=['post'], url_path='mass-generate')
    def mass_generate_assessments(self, request):
        """Массовое создание начислений для выбранных владельцев"""
        logger.info("=" * 50)
        logger.info("MASS GENERATE ASSESSMENTS CALLED")
        logger.info(f"User: {request.user.id}")
        logger.info(f"Request data: {request.data}")

        try:
            owner_ids = request.data.get('owner_ids', [])
            period_id = request.data.get('period_id')
            category_id = request.data.get('category_id')
            generate_receipts = request.data.get('generate_receipts', False)
            output_format = request.data.get('format', 'json')
            skip_existing = request.data.get('skip_existing', True)     

            logger.info(f"Params: owner_ids={len(owner_ids)}, period_id={period_id}, category_id={category_id}")
            logger.info(f"generate_receipts={generate_receipts}, output_format={output_format}")        

            # Валидация входных параметров
            if not owner_ids:
                logger.error("owner_ids is empty")
                return Response(
                    {'detail': 'Укажите список owner_ids'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            if not period_id:
                logger.error("period_id is missing")
                return Response(
                    {'detail': 'Укажите period_id'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            if not category_id:
                logger.error("category_id is missing")
                return Response(
                    {'detail': 'Укажите category_id'},
                    status=status.HTTP_400_BAD_REQUEST
                )       

            # Получаем период и категорию
            try:
                period = PaymentPeriod.objects.get(id=period_id)
                logger.info(f"Period found: {period}")
            except PaymentPeriod.DoesNotExist:
                logger.error(f"Period {period_id} not found")
                return Response({'detail': 'Период не найден'}, status=status.HTTP_404_NOT_FOUND)
            
            try:
                category = PaymentCategory.objects.get(id=category_id)
                logger.info(f"Category found: id={category.id}, name={category.name}")
                logger.info(f"Category unit={category.unit}, rate_per_unit={category.rate_per_unit}, default_amount={category.default_amount}")
            except PaymentCategory.DoesNotExist:
                logger.error(f"Category {category_id} not found")
                return Response({'detail': 'Категория не найдена'}, status=status.HTTP_404_NOT_FOUND)

            # Получаем владельцев
            owners = Owner.objects.filter(id__in=owner_ids)
            logger.info(f"Owners count: {owners.count()}")

            if not owners.exists():
                logger.error("No owners found with provided IDs")
                return Response({'detail': 'Владельцы не найдены'}, status=status.HTTP_404_NOT_FOUND)       

            results = []
            total_created = 0
            all_receipts_html = []      

            # Инициализируем генераторы
            try:
                qr_gen = QRCodeGenerator(request=self.request)
                snt_gen = SNTDetailsGenerator(request=self.request)
                snt_details = snt_gen.get_details()
                logger.debug("QR and SNT generators initialized successfully")
            except Exception as e:
                logger.error(f"Error initializing generators: {e}")
                snt_details = {'name': 'СНТ', 'inn': '', 'account': '', 'bank_name': '', 'bank_bik': '', 'bank_corr': ''}

            # Получаем максимальный ID для генерации UID
            max_id = Assessment.objects.aggregate(models.Max('id'))['id__max'] or 0
            next_uid_num = max_id + 1
            existing_uids = set(Assessment.objects.values_list('payment_uid', flat=True))
            logger.debug(f"Next UID number: {next_uid_num}, existing UIDs: {len(existing_uids)}")

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

                    try:
                        plots = owner.land_plots.all()
                        logger.info(f"Owner {owner.id} has {plots.count()} plots")
                    except Exception as e:
                        logger.error(f"Error fetching plots for owner {owner.id}: {e}")
                        owner_result['skipped'] += 1
                        results.append(owner_result)
                        continue

                    for plot in plots:
                        logger.debug(f"Processing plot: {plot.id} - {plot.plot_number}, area: {plot.area_sqm} sqm")

                        # РАСЧЁТ СУММЫ
                        amount = Decimal('0')
                        notes = ''

                        try:
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
                        except Exception as e:
                            logger.error(f"Error calculating amount for plot {plot.id}: {e}")
                            owner_result['skipped'] += 1
                            continue
                        
                        # Проверяем существование
                        try:
                            exists = Assessment.objects.filter(
                                owner=owner,
                                land_plot=plot,
                                category=category,
                                period=period,
                            ).exists()

                            if skip_existing and exists:
                                logger.info(f"Assessment already exists for owner {owner.id}, plot {plot.id}, skipping")
                                owner_result['skipped'] += 1
                                continue
                        except Exception as e:
                            logger.error(f"Error checking existing assessment: {e}")
                            owner_result['skipped'] += 1
                            continue

                        # Генерируем уникальный UID
                        try:
                            uid = f"SNT-{next_uid_num:06d}"
                            while uid in existing_uids:
                                next_uid_num += 1
                                uid = f"SNT-{next_uid_num:06d}"
                            existing_uids.add(uid)
                            logger.debug(f"Generated UID: {uid}")
                        except Exception as e:
                            logger.error(f"Error generating UID: {e}")
                            owner_result['skipped'] += 1
                            continue

                        # Создаём начисление
                        try:
                            logger.info(f"Creating assessment with amount {amount} and UID {uid}")
                            assessment = Assessment.objects.create(
                                owner=owner,
                                land_plot=plot,
                                category=category,
                                period=period,
                                amount=amount,
                                payment_uid=uid,
                                notes=notes
                            )

                            logger.info(f"Created assessment #{assessment.id} with UID {assessment.payment_uid}")   
                            next_uid_num += 1   

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
                        except Exception as e:
                            logger.error(f"Error creating assessment: {e}")
                            owner_result['skipped'] += 1
                            continue

                        # Генерируем квитанцию если нужно
                        if generate_receipts:
                            try:
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
                                logger.debug(f"Receipt generated for assessment {assessment.id}")
                            except Exception as e:
                                logger.error(f"Error generating receipt for assessment {assessment.id}: {e}")

                    results.append(owner_result)
                    logger.info(f"Owner result: created={owner_result['created']}, skipped={owner_result['skipped']}")      

            response_data = {
                'detail': f'Создано {total_created} начислений',
                'total_created': total_created,
                'owners_processed': len(owners),
                'results': results
            }

            logger.info(f"Mass generation completed: {total_created} created, response prepared")     

            if generate_receipts and output_format == 'pdf' and all_receipts_html:
                logger.info("Returning PDF response")
                return generate_mass_receipts_pdf(all_receipts_html, period, category)      

            if generate_receipts:
                response_data['receipts_count'] = len(all_receipts_html)
                response_data['receipts_html'] = all_receipts_html if output_format == 'html' else None     

            return Response(response_data)
            
        except Exception as e:
            logger.error(f"Unexpected error in mass_generate_assessments: {e}\n{traceback.format_exc()}")
            return Response(
                {'detail': f'Внутренняя ошибка сервера: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @action(detail=False, methods=['post'], url_path='generate')
    def generate_assessments(self, request):
        """Генерирует начисления для всех участков за указанный период"""
        logger.info(f"User {request.user.id} generating assessments for all plots")
        
        try:
            period_id = request.data.get('period_id')
            category_id = request.data.get('category_id')
            custom_amount = request.data.get('custom_amount')

            if not period_id or not category_id:
                logger.error("Missing period_id or category_id")
                return Response(
                    {'detail': 'Укажите period_id и category_id'},
                    status=status.HTTP_400_BAD_REQUEST
                )

            try:
                period = PaymentPeriod.objects.get(id=period_id)
                category = PaymentCategory.objects.get(id=category_id)
                logger.info(f"Period: {period}, Category: {category.name}")
            except PaymentPeriod.DoesNotExist:
                logger.error(f"Period {period_id} not found")
                return Response({'detail': 'Период не найден'}, status=status.HTTP_404_NOT_FOUND)
            except PaymentCategory.DoesNotExist:
                logger.error(f"Category {category_id} not found")
                return Response({'detail': 'Категория не найдена'}, status=status.HTTP_404_NOT_FOUND)

            created_count = 0
            skipped_count = 0
            assessments_to_create = []

            # Получаем все активные участки с владельцами
            try:
                plots = LandPlot.objects.filter(status='active').prefetch_related('owners')
                logger.info(f"Found {plots.count()} active plots")
            except Exception as e:
                logger.error(f"Error fetching plots: {e}")
                return Response({'detail': f'Ошибка при получении участков: {str(e)}'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

            # Получаем максимальный существующий ID для генерации UID
            max_id = Assessment.objects.aggregate(models.Max('id'))['id__max'] or 0
            next_uid_num = max_id + 1

            # Собираем существующие UID для проверки
            existing_uids = set(Assessment.objects.values_list('payment_uid', flat=True))
            logger.debug(f"Next UID number: {next_uid_num}, existing UIDs: {len(existing_uids)}")

            with db_transaction.atomic():
                for plot in plots:
                    owners_list = plot.owners.all()
                    for owner in owners_list:
                        # Рассчитываем сумму
                        try:
                            if custom_amount:
                                amount = Decimal(str(custom_amount))
                                notes = f"Ручная установка: {custom_amount} ₽"
                            else:
                                amount, notes = category.calculate_amount(land_plot=plot)

                            if amount == 0:
                                skipped_count += 1
                                continue
                        except Exception as e:
                            logger.error(f"Error calculating amount for plot {plot.id}, owner {owner.id}: {e}")
                            skipped_count += 1
                            continue

                        # Проверяем, нет ли уже начисления
                        try:
                            exists = Assessment.objects.filter(
                                owner=owner,
                                land_plot=plot,
                                category=category,
                                period=period,
                            ).exists()

                            if not exists:
                                # Генерируем уникальный UID
                                uid = f"SNT-{next_uid_num:06d}"
                                while uid in existing_uids:
                                    next_uid_num += 1
                                    uid = f"SNT-{next_uid_num:06d}"

                                existing_uids.add(uid)

                                assessments_to_create.append(
                                    Assessment(
                                        owner=owner,
                                        land_plot=plot,
                                        category=category,
                                        period=period,
                                        amount=amount,
                                        payment_uid=uid,
                                        notes=notes,
                                    )
                                )
                                next_uid_num += 1
                                created_count += 1
                            else:
                                skipped_count += 1
                        except Exception as e:
                            logger.error(f"Error checking/creating assessment: {e}")
                            skipped_count += 1

                if assessments_to_create:
                    try:
                        Assessment.objects.bulk_create(assessments_to_create)
                        logger.info(f"Bulk created {len(assessments_to_create)} assessments")
                    except Exception as e:
                        logger.error(f"Error in bulk_create: {e}")
                        raise

            logger.info(f"Generation completed: created {created_count}, skipped {skipped_count}")
            return Response({
                'detail': f'Создано {created_count} начислений, пропущено {skipped_count}',
                'count': created_count,
                'skipped': skipped_count,
            })
            
        except Exception as e:
            logger.error(f"Error in generate_assessments: {e}\n{traceback.format_exc()}")
            return Response(
                {'detail': f'Ошибка при генерации начислений: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @action(detail=True, methods=['post'], url_path='send-email')
    def send_receipt_email(self, request, pk=None):
        """
        POST /api/assessments/{id}/send-email/
        Отправить квитанцию на email владельца
        """
        try:
            logger.info(f"User {request.user.id} sending receipt email for assessment {pk}")
            
            assessment = self.get_object()
            if not assessment:
                logger.error(f"Assessment {pk} not found")
                return Response({'detail': 'Начисление не найдено'}, status=status.HTTP_404_NOT_FOUND)
            
            from .email_service import EmailReceiptService
            email_service = EmailReceiptService()
            
            recipient_email = request.data.get('email')
            send_to_all = request.data.get('send_to_all', False)
            attach_pdf = request.data.get('attach_pdf', True)
            
            logger.debug(f"Recipient email: {recipient_email}, send_to_all: {send_to_all}, attach_pdf: {attach_pdf}")
            
            if send_to_all:
                results = email_service.send_receipt_to_all_emails(
                    assessment=assessment,
                    send_pdf_attachment=attach_pdf
                )
                sent_count = sum(1 for r in results if r['success'])
                logger.info(f"Email sent to {sent_count} of {len(results)} addresses")
                return Response({
                    'detail': f'Отправлено на {sent_count} из {len(results)} адресов',
                    'results': results,
                })
            else:
                result = email_service.send_receipt_to_owner(
                    assessment=assessment,
                    recipient_email=recipient_email,
                    send_pdf_attachment=attach_pdf
                )
                
                if result['success']:
                    logger.info(f"Email sent successfully to {result.get('recipient')}")
                    return Response(result)
                else:
                    logger.warning(f"Failed to send email: {result.get('message')}")
                    return Response(result, status=status.HTTP_400_BAD_REQUEST)
                    
        except Exception as e:
            logger.error(f"Error sending receipt email: {e}\n{traceback.format_exc()}")
            return Response(
                {'detail': f'Ошибка при отправке email: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

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
        try:
            logger.info(f"User {request.user.id} starting bulk email send")
            
            assessment_ids = request.data.get('assessment_ids', [])
            category_id = request.data.get('category_id')
            period_id = request.data.get('period_id')
            only_debtors = request.data.get('only_debtors', False)
            min_debt = Decimal(str(request.data.get('min_debt', 0)))
            attach_pdf = request.data.get('attach_pdf', True)
            
            logger.debug(f"Params: assessment_ids={len(assessment_ids)}, category_id={category_id}, period_id={period_id}, only_debtors={only_debtors}, min_debt={min_debt}")
            
            # Формируем queryset
            if assessment_ids:
                assessments = Assessment.objects.filter(id__in=assessment_ids)
                logger.info(f"Filtering by specific assessment IDs: {len(assessment_ids)}")
            else:
                assessments = Assessment.objects.filter(
                    owner__email__isnull=False,
                    owner__email__gt='',
                )
                if category_id:
                    assessments = assessments.filter(category_id=category_id)
                    logger.debug(f"Filtered by category_id: {category_id}")
                if period_id:
                    assessments = assessments.filter(period_id=period_id)
                    logger.debug(f"Filtered by period_id: {period_id}")
                if only_debtors:
                    assessments = assessments.filter(
                        status__in=['pending', 'partial', 'overdue']
                    )
                    logger.debug("Filtered by debtors only")
                if min_debt > 0:
                    assessments = assessments.filter(amount__gte=min_debt)
                    logger.debug(f"Filtered by min debt: {min_debt}")
            
            if not assessments.exists():
                logger.warning("No assessments found for bulk send")
                return Response({
                    'detail': 'Нет начислений для отправки',
                    'total': 0,
                }, status=status.HTTP_200_OK)
            
            logger.info(f"Found {assessments.count()} assessments for bulk send")
            
            email_service = EmailReceiptService()
            
            # Отправляем асинхронно в фоновом потоке
            from threading import Thread
            
            def send_in_background():
                try:
                    logger.info(f"Starting background bulk send for {assessments.count()} assessments")
                    result = email_service.send_bulk_receipts(
                        assessments=list(assessments),
                        send_pdf_attachment=attach_pdf
                    )
                    logger.info(f"Background bulk send completed: {result}")
                except Exception as e:
                    logger.error(f"Error in background bulk send: {e}")
            
            thread = Thread(target=send_in_background, daemon=True)
            thread.start()
            
            return Response({
                'detail': f'Запущена рассылка {assessments.count()} квитанций',
                'total': assessments.count(),
                'status': 'processing',
            })
            
        except Exception as e:
            logger.error(f"Error in bulk_send_receipts_email: {e}\n{traceback.format_exc()}")
            return Response(
                {'detail': f'Ошибка при массовой рассылке: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=False, methods=['post'], url_path='send-to-debtors')
    def send_to_debtors(self, request):
        """
        POST /api/assessments/send-to-debtors/
        Отправка квитанций всем должникам
        """
        try:
            logger.info(f"User {request.user.id} sending to debtors")
            from .email_service import email_sender

            category_id = request.data.get('category_id')
            period_id = request.data.get('period_id')
            min_debt = Decimal(str(request.data.get('min_debt', 0)))
            attach_pdf = request.data.get('attach_pdf', False)

            assessments = Assessment.objects.filter(
                status__in=['pending', 'partial', 'overdue']
            ).select_related('owner', 'category', 'period')

            if category_id:
                assessments = assessments.filter(category_id=category_id)
                logger.debug(f"Filtered by category: {category_id}")
            if period_id:
                assessments = assessments.filter(period_id=period_id)
                logger.debug(f"Filtered by period: {period_id}")
            if min_debt > 0:
                assessments = assessments.filter(amount__gte=min_debt)
                logger.debug(f"Filtered by min debt: {min_debt}")

            # Фильтруем только тех, у кого есть email
            assessments_with_email = []
            for assessment in assessments:
                has_email = assessment.owner.contacts.filter(
                    type='em', is_active=True
                ).exists()
                if has_email:
                    assessments_with_email.append(assessment)

            logger.info(f"Found {len(assessments_with_email)} debtors with email out of {assessments.count()} total")

            if not assessments_with_email:
                return Response({
                    'detail': 'Нет должников с email для рассылки',
                    'total': 0,
                })

            # Запускаем асинхронную отправку
            from threading import Thread

            def send_in_background():
                try:
                    logger.info(f"Starting background send to {len(assessments_with_email)} debtors")
                    result = email_sender.email_service.send_bulk_receipts(
                        assessments=assessments_with_email,
                        send_pdf_attachment=attach_pdf
                    )
                    logger.info(f"Background send to debtors completed: {result}")
                except Exception as e:
                    logger.error(f"Error in background send to debtors: {e}")

            Thread(target=send_in_background, daemon=True).start()

            return Response({
                'detail': f'Запущена рассылка {len(assessments_with_email)} квитанций должникам',
                'total': len(assessments_with_email),
                'status': 'processing',
            })
            
        except Exception as e:
            logger.error(f"Error in send_to_debtors: {e}\n{traceback.format_exc()}")
            return Response(
                {'detail': f'Ошибка при отправке должникам: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @action(detail=False, methods=['get'], url_path='stats')
    def stats(self, request):
        """Статистика по начислениям с фильтрацией по организации"""
        try:
            logger.info(f"User {request.user.id} requesting assessment stats")
            from django.db.models import Count, Sum

            # Базовый queryset
            assessments = Assessment.objects.all()

            # Фильтруем по организации текущего пользователя (если не админ)
            if not request.user.is_superuser and not request.user.is_admin:
                if hasattr(request, 'current_organization') and request.current_organization:
                    org = request.current_organization
                    assessments = assessments.filter(
                        owner__memberships__organization=org,
                        owner__memberships__status='active'
                    )
                    logger.debug(f"Filtered by organization: {org}")
                else:
                    # Если у пользователя нет организации, возвращаем пустую статистику
                    logger.warning(f"User {request.user.id} has no organization")
                    return Response({
                        'total_amount': 0,
                        'total_paid': 0,
                        'total_debt': 0,
                        'by_status': {},
                        'by_category': {},
                    })

            total_amount = assessments.aggregate(s=Sum('amount'))['s'] or 0
            total_paid = assessments.aggregate(s=Sum('paid_amount'))['s'] or 0

            # Расчёт общей задолженности
            total_debt = 0
            for a in assessments.filter(status__in=['pending', 'partial', 'overdue']):
                total_debt += a.debt

            data = {
                'total_amount': float(total_amount),
                'total_paid': float(total_paid),
                'total_debt': float(total_debt),
                'by_status': dict(
                    assessments.values_list('status').annotate(c=Count('id'))
                ),
                'by_category': dict(
                    assessments.values_list('category__name').annotate(c=Count('id'))
                ),
            }
            
            logger.info(f"Stats retrieved: total_amount={total_amount}, total_paid={total_paid}, total_debt={total_debt}")
            return Response(data)
            
        except Exception as e:
            logger.error(f"Error getting stats: {e}\n{traceback.format_exc()}")
            return Response(
                {'detail': f'Ошибка при получении статистики: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @action(detail=True, methods=['post'], url_path='add-payment')
    def add_payment(self, request, pk=None):
        """Добавить платёж к начислению"""
        try:
            logger.info(f"User {request.user.id} adding payment to assessment {pk}")
            
            assessment = self.get_object()
            if not assessment:
                logger.error(f"Assessment {pk} not found")
                return Response({'detail': 'Начисление не найдено'}, status=status.HTTP_404_NOT_FOUND)
            
            amount = request.data.get('amount')
            
            if not amount:
                logger.warning(f"Amount not provided for assessment {pk}")
                return Response({'detail': 'Укажите сумму'}, status=status.HTTP_400_BAD_REQUEST)
            
            try:
                amount = Decimal(str(amount))
                logger.debug(f"Amount parsed: {amount}")
            except Exception as e:
                logger.warning(f"Invalid amount format: {amount}, error: {e}")
                return Response({'detail': 'Неверный формат суммы'}, status=status.HTTP_400_BAD_REQUEST)
            
            if amount <= 0:
                logger.warning(f"Amount <= 0: {amount}")
                return Response({'detail': 'Сумма должна быть больше 0'}, status=status.HTTP_400_BAD_REQUEST)
            
            payment = Payment.objects.create(
                assessment=assessment,
                amount=amount,
                payment_method=request.data.get('payment_method', 'cash'),
                payment_date=request.data.get('payment_date', date.today()),
                notes=request.data.get('notes', ''),
                status=Payment.STATUS_PROCESSED,
            )
            
            logger.info(f"Payment {payment.id} created for assessment {pk} with amount {amount}")
            return Response(PaymentSerializer(payment).data, status=status.HTTP_201_CREATED)
            
        except Exception as e:
            logger.error(f"Error adding payment: {e}\n{traceback.format_exc()}")
            return Response(
                {'detail': f'Ошибка при добавлении платежа: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @action(detail=True, methods=['get'], url_path='receipt')
    def get_receipt(self, request, pk=None):
        """Получить данные для квитанции с QR-кодом"""
        try:
            logger.info(f"User {request.user.id} getting receipt data for assessment {pk}")
            
            assessment = self.get_object()
            if not assessment:
                logger.error(f"Assessment {pk} not found")
                return Response({'detail': 'Начисление не найдено'}, status=status.HTTP_404_NOT_FOUND)
            
            qr_gen = QRCodeGenerator(request=self.request)
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
            
            logger.info(f"Receipt data generated for assessment {pk}")
            return Response(data)
            
        except Exception as e:
            logger.error(f"Error getting receipt: {e}\n{traceback.format_exc()}")
            return Response(
                {'detail': f'Ошибка при получении квитанции: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @action(detail=True, methods=['get'], url_path='receipt-html')
    def get_receipt_html(self, request, pk=None):
        """Получить HTML-квитанцию с QR-кодом"""
        try:
            logger.info(f"User {request.user.id} getting receipt HTML for assessment {pk}")
            
            assessment = self.get_object()
            if not assessment:
                logger.error(f"Assessment {pk} not found")
                return Response({'detail': 'Начисление не найдено'}, status=status.HTTP_404_NOT_FOUND)

            # Инициализируем генераторы
            qr_gen = QRCodeGenerator(request=self.request)
            snt_gen = SNTDetailsGenerator(request=self.request)

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
                'qr_code': qr_image,
                'qr_data': qr_data,
                'snt_details': snt_details,
                'owner_name': assessment.owner.full_name,
                'plot_number': assessment.land_plot.plot_number,
                'amount': str(assessment.debt),
                'uid': assessment.payment_uid,
                'due_date': str(assessment.period.due_date) if assessment.period.due_date else '',
                'purpose': purpose,
            }

            logger.info(f"QR Code generated for assessment {assessment.id}")
            logger.debug(f"QR data length: {len(qr_data)}")
            logger.debug(f"QR image data URI length: {len(qr_image) if qr_image else 0}")

            return render(request, 'payments/receipt.html', context)
            
        except Exception as e:
            logger.error(f"Error getting receipt HTML: {e}\n{traceback.format_exc()}")
            return Response(
                {'detail': f'Ошибка при получении HTML квитанции: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

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

        try:
            logger.info(f"User {request.user.id} generating receipt PDF for assessment {pk}")
            
            assessment = self.get_object()
            if not assessment:
                logger.error(f"Assessment {pk} not found")
                return Response({'detail': 'Начисление не найдено'}, status=status.HTTP_404_NOT_FOUND)

            # Регистрируем шрифт с поддержкой кириллицы
            font_paths = [
                "C:/Windows/Fonts/arial.ttf",
                "C:/Windows/Fonts/times.ttf",
                "C:/Windows/Fonts/calibri.ttf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                "/System/Library/Fonts/Arial.ttf",
                "/Library/Fonts/Arial.ttf",
            ]

            font_registered = False
            for font_path in font_paths:
                if os.path.exists(font_path):
                    try:
                        pdfmetrics.registerFont(TTFont('RussianFont', font_path))
                        font_registered = True
                        logger.info(f"Font loaded from: {font_path}")
                        break
                    except Exception as e:
                        logger.warning(f"Failed to load font from {font_path}: {e}")
                        continue
                        
            if not font_registered:
                logger.warning("No Cyrillic font found, using default (may show squares)")

            # Генерируем QR-код
            qr_gen = QRCodeGenerator(request=self.request)
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
            if not qr_image_bytes:
                logger.warning("QR image generation failed, continuing without QR")

            # Создаём PDF
            buffer = BytesIO()
            c = canvas.Canvas(buffer, pagesize=A4)
            width, height = A4

            # Устанавливаем шрифт
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
            
            # Обработка длинных строк
            owner_name = assessment.owner.full_name
            if len(owner_name) > 50:
                owner_name = owner_name[:47] + "..."
            c.drawString(50, height - 160, f"ФИО: {owner_name}")
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
            c.drawString(50, height - 250, f"Получатель: {snt_details['name'][:60]}")
            c.drawString(50, height - 265, f"ИНН: {snt_details['inn']}")
            c.drawString(50, height - 280, f"Счёт: {snt_details['account']}")
            c.drawString(50, height - 295, f"Банк: {snt_details['bank_name'][:45]}")
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
                try:
                    qr_buffer = BytesIO(qr_image_bytes)
                    qr_img = ImageReader(qr_buffer)
                    c.drawImage(qr_img, width - 150, height - 200, width=100, height=100)
                    if font_registered:
                        c.setFont("RussianFont", 8)
                    else:
                        c.setFont("Helvetica", 8)
                    c.drawString(width - 140, height - 215, "Отсканируйте для оплаты")
                except Exception as e:
                    logger.warning(f"Error drawing QR code: {e}")

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
            
            logger.info(f"PDF generated successfully for assessment {pk}, size: {len(buffer.getvalue())} bytes")
            return response
            
        except Exception as e:
            logger.error(f"Error generating receipt PDF: {e}\n{traceback.format_exc()}")
            return Response(
                {'detail': f'Ошибка при генерации PDF: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @action(detail=False, methods=['get'], url_path='owner-receipts')
    def get_owner_receipts(self, request):
        """Получить все квитанции владельца за период"""
        try:
            owner_id = request.query_params.get('owner_id')
            period_id = request.query_params.get('period_id')
            
            logger.info(f"User {request.user.id} getting owner receipts for owner_id={owner_id}, period_id={period_id}")
            
            if not owner_id:
                logger.warning("owner_id not provided")
                return Response({'detail': 'Укажите owner_id'}, status=status.HTTP_400_BAD_REQUEST)
            
            assessments = Assessment.objects.filter(owner_id=owner_id)
            if period_id:
                assessments = assessments.filter(period_id=period_id)
            
            logger.debug(f"Found {assessments.count()} assessments")
            
            qr_gen = QRCodeGenerator(request=self.request)
            snt_gen = SNTDetailsGenerator(request=self.request)
            snt_details = snt_gen.get_details()
            
            receipts = []
            for assessment in assessments:
                try:
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
                except Exception as e:
                    logger.error(f"Error processing assessment {assessment.id}: {e}")
                    continue
            
            logger.info(f"Returning {len(receipts)} receipts for owner {owner_id}")
            return Response(receipts)
            
        except Exception as e:
            logger.error(f"Error in get_owner_receipts: {e}\n{traceback.format_exc()}")
            return Response(
                {'detail': f'Ошибка при получении квитанций: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @action(detail=False, methods=['post'], url_path='generate-for-owner')
    def generate_for_owner(self, request):
        """Генерирует начисления для конкретного владельца"""
        logger.info(f"User {request.user.id} generating assessments for owner")
        
        try:
            owner_id = request.data.get('owner_id')
            period_id = request.data.get('period_id')
            category_id = request.data.get('category_id')
            generate_receipts = request.data.get('generate_receipts', False)

            logger.debug(f"Params: owner_id={owner_id}, period_id={period_id}, category_id={category_id}, generate_receipts={generate_receipts}")

            if not all([owner_id, period_id, category_id]):
                logger.error("Missing required parameters")
                return Response(
                    {'detail': 'Укажите owner_id, period_id и category_id'}, 
                    status=status.HTTP_400_BAD_REQUEST
                )

            try:
                owner = Owner.objects.get(id=owner_id)
                period = PaymentPeriod.objects.get(id=period_id)
                category = PaymentCategory.objects.get(id=category_id)
                logger.info(f"Found owner: {owner.full_name}, period: {period}, category: {category.name}")
            except Owner.DoesNotExist:
                logger.error(f"Owner {owner_id} not found")
                return Response({'detail': 'Владелец не найден'}, status=status.HTTP_404_NOT_FOUND)
            except PaymentPeriod.DoesNotExist:
                logger.error(f"Period {period_id} not found")
                return Response({'detail': 'Период не найден'}, status=status.HTTP_404_NOT_FOUND)
            except PaymentCategory.DoesNotExist:
                logger.error(f"Category {category_id} not found")
                return Response({'detail': 'Категория не найдена'}, status=status.HTTP_404_NOT_FOUND)

            assessments_created = []
            plots = owner.land_plots.all()

            if not plots.exists():
                logger.warning(f"Owner {owner.full_name} has no plots")
                return Response({
                    'detail': f'У владельца {owner.full_name} нет привязанных участков',
                    'assessments': []
                }, status=status.HTTP_200_OK)

            logger.info(f"Owner has {plots.count()} plots")

            qr_gen = QRCodeGenerator(request=self.request)
            snt_gen = SNTDetailsGenerator(request=self.request)
            snt_details = snt_gen.get_details()
            receipts_data = []

            # Получаем максимальный ID для генерации UID
            max_id = Assessment.objects.aggregate(models.Max('id'))['id__max'] or 0
            next_uid_num = max_id + 1
            existing_uids = set(Assessment.objects.values_list('payment_uid', flat=True))

            with db_transaction.atomic():
                for plot in plots:
                    try:
                        amount, notes = category.calculate_amount(land_plot=plot)

                        if amount == 0:
                            logger.debug(f"Amount 0 for plot {plot.id}, skipping")
                            continue
                        
                        # Проверяем, нет ли уже начисления
                        exists = Assessment.objects.filter(
                            owner=owner,
                            land_plot=plot,
                            category=category,
                            period=period,
                        ).exists()

                        if not exists:
                            # Генерируем уникальный UID
                            uid = f"SNT-{next_uid_num:06d}"
                            while uid in existing_uids:
                                next_uid_num += 1
                                uid = f"SNT-{next_uid_num:06d}"

                            existing_uids.add(uid)

                            assessment = Assessment.objects.create(
                                owner=owner,
                                land_plot=plot,
                                category=category,
                                period=period,
                                amount=amount,
                                payment_uid=uid,
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

                            next_uid_num += 1
                            logger.info(f"Created assessment {assessment.id} for plot {plot.plot_number}")
                    except Exception as e:
                        logger.error(f"Error processing plot {plot.id}: {e}")
                        continue

            response_data = {
                'detail': f'Создано {len(assessments_created)} начислений для владельца {owner.full_name}',
                'assessments': assessments_created
            }

            if generate_receipts:
                response_data['receipts'] = receipts_data
                if request.data.get('format') == 'pdf':
                    logger.info("Generating PDF for owner receipts")
                    return generate_receipts_pdf(receipts_data, owner.full_name)

            logger.info(f"Generated {len(assessments_created)} assessments for owner {owner.full_name}")
            return Response(response_data)
            
        except Exception as e:
            logger.error(f"Error in generate_for_owner: {e}\n{traceback.format_exc()}")
            return Response(
                {'detail': f'Ошибка при генерации начислений: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


@csrf_exempt
@require_http_methods(["POST"])
def generate_combined_pdf(request):
    """Генерация объединённого PDF из HTML"""
    try:
        logger.info(f"User {request.user.id if request.user.is_authenticated else 'Anonymous'} generating combined PDF")
        
        data = json.loads(request.body)
        html_content = data.get('html', '')
        
        if not html_content:
            logger.warning("HTML content is empty")
            return JsonResponse({'error': 'HTML content is empty'}, status=400)
        
        from weasyprint import HTML
        pdf_file = HTML(string=html_content).write_pdf()
        
        response = HttpResponse(pdf_file, content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="combined_receipts_{date.today()}.pdf"'
        
        logger.info(f"Combined PDF generated, size: {len(pdf_file)} bytes")
        return response
        
    except ImportError as e:
        logger.error(f"WeasyPrint not installed: {e}")
        return JsonResponse({'error': 'WeasyPrint not installed'}, status=500)
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON: {e}")
        return JsonResponse({'error': 'Invalid JSON data'}, status=400)
    except Exception as e:
        logger.error(f"Error generating combined PDF: {e}\n{traceback.format_exc()}")
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
    
    def get_queryset(self):
        try:
            logger.debug(f"User {self.request.user.id} requesting payments queryset")
            queryset = super().get_queryset()
            
            if self.request.user.is_superuser or self.request.user.is_admin:
                logger.debug("Admin user, returning all payments")
                return queryset

            # Фильтрация по организации пользователя
            org = getattr(self.request, 'current_organization', None)
            if org:
                queryset = queryset.filter(
                    assessment__owner__memberships__organization=org,
                    assessment__owner__memberships__status='active'
                )
                logger.debug(f"Filtered by organization: {org}")
            else:
                # Если у пользователя нет организации, возвращаем пустой queryset
                logger.warning(f"User {self.request.user.id} has no organization")
                queryset = queryset.none()
            
            count = queryset.count()
            logger.debug(f"Payments queryset returned {count} records")
            return queryset.distinct()
        except Exception as e:
            logger.error(f"Error in PaymentViewSet.get_queryset: {e}")
            return Payment.objects.none()


class BankStatementViewSet(viewsets.ModelViewSet):
    queryset = BankStatement.objects.all()
    serializer_class = BankStatementSerializer
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = ['bank_name', 'status']
    ordering = ['-statement_date']

    @action(detail=False, methods=['post'], url_path='import')
    def import_statement(self, request):
        """Импорт банковской выписки из файла с автоматическим обновлением статуса"""
        logger.info(f"User {request.user.id} importing bank statement")
        
        try:
            file = request.FILES.get('file')
            if not file:
                logger.error("No file provided")
                return Response({'detail': 'Загрузите файл'}, status=status.HTTP_400_BAD_REQUEST)
        
            logger.info(f'Received file: {file.name}, size: {file.size} bytes, type: {file.content_type}')
        
            # Определяем банк по содержимому файла или имени
            bank_name = request.data.get('bank_name', '')
            
            # Если банк не указан, пытаемся определить автоматически
            if not bank_name:
                if 'alfa' in file.name.lower() or 'альфа' in file.name.lower():
                    bank_name = 'Альфа-Банк'
                elif 'sber' in file.name.lower() or 'сбер' in file.name.lower():
                    bank_name = 'Сбербанк'
                elif 'tinkoff' in file.name.lower() or 'тинькофф' in file.name.lower():
                    bank_name = 'Тинькофф'
                else:
                    # Пробуем прочитать первый килобайт файла для определения
                    try:
                        file.seek(0)
                        content = file.read(1024).decode('utf-8', errors='ignore')
                        file.seek(0)  # Сбрасываем позицию
                        
                        if 'АЛЬФА-БАНК' in content or 'Альфа-Банк' in content:
                            bank_name = 'Альфа-Банк'
                        elif 'Сбербанк' in content or 'СБЕРБАНК' in content:
                            bank_name = 'Сбербанк'
                        elif 'Тинькофф' in content or 'TINKOFF' in content:
                            bank_name = 'Тинькофф'
                        else:
                            bank_name = 'Неизвестный банк'
                    except Exception as e:
                        logger.warning(f"Error detecting bank from content: {e}")
                        bank_name = 'Неизвестный банк'
            
            logger.info(f"Detected bank: {bank_name}")

            # Сохраняем файл
            try:
                statement = BankStatement.objects.create(
                    bank_name=bank_name,
                    account_number=request.data.get('account_number', ''),
                    statement_date=date.today(),
                    file_original=file,
                    status=BankStatement.STATUS_IMPORTED,
                )
                logger.info(f'Created statement #{statement.id}, bank: {bank_name}')
            except Exception as e:
                logger.error(f"Error creating bank statement record: {e}")
                return Response({'detail': f'Ошибка сохранения выписки: {str(e)}'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        
            # Парсим файл
            parser = BankStatementParser(bank_name if bank_name != 'Неизвестный банк' else None)
        
            try:
                file_path = statement.file_original.path
                logger.info(f'File path: {file_path}')
                
                if not os.path.exists(file_path):
                    logger.error(f"File does not exist at path: {file_path}")
                    raise FileNotFoundError(f"File not found: {file_path}")
                
                transactions_data = parser.parse_file(file_path)
                logger.info(f'Parsed {len(transactions_data)} transactions')
                
            except FileNotFoundError as e:
                logger.error(f"File not found: {e}")
                statement.status = BankStatement.STATUS_ERROR
                statement.notes = str(e)
                statement.save()
                return Response({
                    'detail': f'Файл не найден: {str(e)}',
                    'statement_id': statement.id,
                    'matched': 0,
                }, status=status.HTTP_400_BAD_REQUEST)
            except Exception as e:
                logger.error(f'Parse error: {e}', exc_info=True)
                statement.status = BankStatement.STATUS_ERROR
                statement.notes = str(e)
                statement.save()
                return Response({
                    'detail': f'Ошибка парсинга: {str(e)}',
                    'statement_id': statement.id,
                    'matched': 0,
                }, status=status.HTTP_400_BAD_REQUEST)
        
            if not transactions_data:
                logger.warning("No transactions found in file")
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
            errors = []
        
            with db_transaction.atomic():
                for idx, trans_data in enumerate(transactions_data):
                    try:
                        logger.debug(f"Processing transaction {idx + 1}/{len(transactions_data)}")
                        
                        # Добавляем информацию о банке
                        trans_data['bank_name'] = statement.bank_name
        
                        # Создаём банковскую транзакцию
                        bank_trans = BankTransaction.objects.create(
                            statement=statement,
                            transaction_date=trans_data.get('transaction_date', date.today()),
                            amount=trans_data.get('amount', 0),
                            payer_name=trans_data.get('payer_name', '')[:200],
                            payer_account=trans_data.get('payer_account', '')[:30],
                            payer_inn=trans_data.get('payer_inn', '')[:12],
                            payment_purpose=trans_data.get('payment_purpose', '')[:500],
                        )
        
                        # Обрабатываем платеж
                        result = matcher.process_and_update_payments(trans_data)
        
                        if result and result.get('matched'):
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
                                    'assessment_id': result.get('matched_assessment_id'),
                                    'new_status': result.get('assessment_status'),
                                    'debt_remaining': result.get('new_debt'),
                                })
                                logger.info(f"Matched payment: {trans_data.get('payer_name')} - {trans_data['amount']} rub")
                            else:
                                logger.debug(f"Transaction matched but no payment created: {result.get('message')}")
        
                            bank_trans.save()
                        else:
                            error_msg = result.get('message', 'Unknown error') if result else 'No result'
                            logger.debug(f"Transaction {idx + 1} not matched: {error_msg}")
                            errors.append({
                                'transaction_index': idx + 1,
                                'payer': trans_data.get('payer_name', 'Unknown'),
                                'amount': str(trans_data['amount']),
                                'error': error_msg
                            })
                            
                    except Exception as e:
                        logger.error(f"Error processing transaction {idx + 1}: {e}")
                        errors.append({
                            'transaction_index': idx + 1,
                            'error': str(e)
                        })
                        continue
        
            statement.total_transactions = len(transactions_data)
            statement.matched_transactions = matched_count
            statement.status = BankStatement.STATUS_PROCESSED
            statement.save()
            
            logger.info(f"Import completed: {len(transactions_data)} transactions, {matched_count} matched, {len(errors)} errors")
        
            response_data = {
                'detail': f'Импортировано {len(transactions_data)} транзакций',
                'statement_id': statement.id,
                'matched': matched_count,
                'payments': payment_results,
            }
            
            if errors:
                response_data['errors'] = errors[:10]  # Return first 10 errors
            
            return Response(response_data)
            
        except Exception as e:
            logger.error(f"Unexpected error in import_statement: {e}\n{traceback.format_exc()}")
            return Response(
                {'detail': f'Внутренняя ошибка сервера: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


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
            logger.info(f"Verifying payment for assessment {assessment_id}")
            
            assessment = Assessment.objects.get(id=assessment_id)
            
            return Response({
                'assessment_id': assessment.id,
                'status': assessment.status,
                'status_display': assessment.get_status_display(),
                'amount': str(assessment.amount),
                'paid': str(assessment.paid_amount),
                'debt': str(assessment.debt),
                'payment_uid': assessment.payment_uid,
            })
        except Assessment.DoesNotExist:
            logger.warning(f"Assessment {assessment_id} not found")
            return Response({'status': 'not_found'}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            logger.error(f"Error verifying payment: {e}")
            return Response({'status': 'error', 'detail': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    @action(detail=False, methods=['post'], url_path='match-payment')
    def match_payment(self, request):
        """Ручное сопоставление платежа"""
        try:
            logger.info(f"Manually matching payment")
            
            transaction_id = request.data.get('transaction_id')
            assessment_id = request.data.get('assessment_id')
            amount = request.data.get('amount')
            
            if not all([transaction_id, assessment_id, amount]):
                logger.warning("Missing required fields")
                return Response({'detail': 'Все поля обязательны'}, status=status.HTTP_400_BAD_REQUEST)
            
            try:
                assessment = Assessment.objects.get(id=assessment_id)
                bank_trans = BankTransaction.objects.get(id=transaction_id)
            except Assessment.DoesNotExist:
                logger.error(f"Assessment {assessment_id} not found")
                return Response({'detail': 'Начисление не найдено'}, status=status.HTTP_404_NOT_FOUND)
            except BankTransaction.DoesNotExist:
                logger.error(f"Bank transaction {transaction_id} not found")
                return Response({'detail': 'Транзакция не найдена'}, status=status.HTTP_404_NOT_FOUND)
            
            # Проверяем, не создан ли уже платёж
            if bank_trans.matched_payment:
                logger.warning(f"Transaction {transaction_id} already has payment {bank_trans.matched_payment.id}")
                return Response({'detail': 'К этой транзакции уже привязан платёж'}, status=status.HTTP_400_BAD_REQUEST)
            
            # Создаём платёж
            try:
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
                logger.info(f"Payment {payment.id} created for assessment {assessment_id}")
            except Exception as e:
                logger.error(f"Error creating payment: {e}")
                return Response({'detail': f'Ошибка создания платежа: {str(e)}'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
            
            # Обновляем банковскую транзакцию
            bank_trans.matched_payment = payment
            bank_trans.matched_owner = assessment.owner
            bank_trans.is_matched = True
            bank_trans.match_confidence = 100
            bank_trans.save()
            
            logger.info(f"Payment {payment.id} matched to transaction {transaction_id}")
            
            return Response({
                'detail': 'Платёж успешно сопоставлен',
                'payment_id': payment.id,
                'payment': PaymentSerializer(payment).data,
            })
            
        except Exception as e:
            logger.error(f"Error in match_payment: {e}\n{traceback.format_exc()}")
            return Response(
                {'detail': f'Ошибка сопоставления платежа: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=False, methods=['get'], url_path='by-uid/(?P<uid>[^/.]+)')
    def get_by_uid(self, request, uid=None):
        """Поиск начисления по UID из квитанции"""
        try:
            logger.info(f"Searching assessment by UID: {uid}")
            
            assessment = Assessment.objects.get(payment_uid=uid)
            
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
        except Assessment.DoesNotExist:
            logger.warning(f"Assessment with UID {uid} not found")
            return Response({'detail': 'Начисление не найдено'}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            logger.error(f"Error searching by UID: {e}")
            return Response({'detail': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# Веб-представления
class PaymentsDashboardView(View):
    @method_decorator(login_required)
    @method_decorator(subscription_required(feature='payments', redirect_url='subscription_plans'))
    def get(self, request):
        try:
            logger.info(f"User {request.user.id} accessing payments dashboard")
            return render(request, 'payments/dashboard.html', {'active_page': 'payments'})
        except Exception as e:
            logger.error(f"Error in dashboard view: {e}")
            return render(request, 'error.html', {'error': str(e)}, status=500)


class AssessmentsListView(View):
    @method_decorator(login_required)
    @method_decorator(subscription_required(feature='assessments', redirect_url='subscription_plans'))
    def get(self, request):
        try:
            logger.info(f"User {request.user.id} accessing assessments list")
            return render(request, 'payments/assessments.html', {'active_page': 'assessments'})
        except Exception as e:
            logger.error(f"Error in assessments list view: {e}")
            return render(request, 'error.html', {'error': str(e)}, status=500)


class BankImportView(View):
    @method_decorator(login_required)
    @method_decorator(subscription_required(feature='bank_import', redirect_url='subscription_plans'))
    def get(self, request):
        try:
            logger.info(f"User {request.user.id} accessing bank import page")
            return render(request, 'payments/bank_import.html', {'active_page': 'bank-import'})
        except Exception as e:
            logger.error(f"Error in bank import view: {e}")
            return render(request, 'error.html', {'error': str(e)}, status=500)


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
        try:
            logger.info(f"User {request.user.id} generating consolidated from template")
            
            template_id = request.data.get('template_id')
            owner_id = request.data.get('owner_id')
            land_plot_id = request.data.get('land_plot_id')
            period_id = request.data.get('period_id')

            if not all([template_id, owner_id, land_plot_id, period_id]):
                logger.error("Missing required fields")
                return Response({'detail': 'Все поля обязательны'}, status=status.HTTP_400_BAD_REQUEST)

            try:
                template = ReceiptTemplate.objects.prefetch_related('lines__category').get(id=template_id)
                owner = Owner.objects.get(id=owner_id)
                land_plot = LandPlot.objects.get(id=land_plot_id)
                period = PaymentPeriod.objects.get(id=period_id)
                logger.info(f"Found template: {template.name}, owner: {owner.full_name}, plot: {land_plot.plot_number}")
            except ReceiptTemplate.DoesNotExist:
                logger.error(f"Template {template_id} not found")
                return Response({'detail': 'Шаблон не найден'}, status=status.HTTP_404_NOT_FOUND)
            except Owner.DoesNotExist:
                logger.error(f"Owner {owner_id} not found")
                return Response({'detail': 'Владелец не найден'}, status=status.HTTP_404_NOT_FOUND)
            except LandPlot.DoesNotExist:
                logger.error(f"Land plot {land_plot_id} not found")
                return Response({'detail': 'Участок не найден'}, status=status.HTTP_404_NOT_FOUND)
            except PaymentPeriod.DoesNotExist:
                logger.error(f"Period {period_id} not found")
                return Response({'detail': 'Период не найден'}, status=status.HTTP_404_NOT_FOUND)
            except Exception as e:
                logger.error(f"Error fetching objects: {e}")
                return Response({'detail': str(e)}, status=status.HTTP_404_NOT_FOUND)

            # Создаём составное начисление
            try:
                consolidated = ConsolidatedAssessment.objects.create(
                    owner=owner,
                    land_plot=land_plot,
                    period=period,
                    total_amount=0,
                )
                logger.info(f"Created consolidated assessment {consolidated.id}")
            except Exception as e:
                logger.error(f"Error creating consolidated assessment: {e}")
                return Response({'detail': f'Ошибка создания: {str(e)}'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

            total = 0
            lines_data = request.data.get('manual_lines', {})

            for line_template in template.lines.all():
                try:
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
                    logger.debug(f"Added line: {description} = {line_amount}")
                except Exception as e:
                    logger.error(f"Error processing template line {line_template.id}: {e}")
                    continue

            consolidated.total_amount = total
            consolidated.save()
            
            logger.info(f"Consolidated assessment {consolidated.id} created with total {total}")

            return Response(ConsolidatedAssessmentSerializer(consolidated).data, status=status.HTTP_201_CREATED)
            
        except Exception as e:
            logger.error(f"Error in generate_from_template: {e}\n{traceback.format_exc()}")
            return Response(
                {'detail': f'Ошибка при создании составной квитанции: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @action(detail=True, methods=['get'], url_path='receipt')
    def get_consolidated_receipt(self, request, pk=None):
        """Квитанция для составного начисления"""
        try:
            logger.info(f"User {request.user.id} getting consolidated receipt for {pk}")
            
            consolidated = self.get_object()
            if not consolidated:
                logger.error(f"Consolidated assessment {pk} not found")
                return Response({'detail': 'Составное начисление не найдено'}, status=status.HTTP_404_NOT_FOUND)

            qr_gen = QRCodeGenerator(request=self.request)
            
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

            qr_code = ""
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
                logger.debug(f"QR code generated for consolidated {pk}")
            except Exception as e:
                logger.warning(f"QR generation failed: {e}")

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
            
        except Exception as e:
            logger.error(f"Error getting consolidated receipt: {e}\n{traceback.format_exc()}")
            return Response(
                {'detail': f'Ошибка при получении квитанции: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def bulk_update_assessments(request):
    """
    Массовое обновление сумм начислений.
    
    Принимает список объектов:
    [
        {"id": 1, "amount": 5000.00},
        {"id": 2, "amount": 3000.00},
        ...
    ]
    """
    logger.info(f"User {request.user.id} bulk updating assessments")
    
    try:
        updates = request.data
        
        if not isinstance(updates, list):
            logger.error("Invalid data format: expected list")
            return Response(
                {'error': 'Ожидается список объектов с id и amount'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if not updates:
            logger.warning("Empty updates list")
            return Response({
                'success': True,
                'updated_count': 0,
                'error_count': 0,
                'updated': [],
                'errors': []
            })
        
        updated = []
        errors = []
        
        try:
            with db_transaction.atomic():
                for idx, item in enumerate(updates):
                    assessment_id = item.get('id')
                    new_amount = item.get('amount')
                    
                    if not assessment_id or new_amount is None:
                        errors.append({
                            'index': idx,
                            'id': assessment_id,
                            'error': 'Не указан id или amount'
                        })
                        logger.warning(f"Item {idx}: missing id or amount")
                        continue
                    
                    try:
                        assessment = Assessment.objects.select_for_update().get(id=assessment_id)
                        
                        # Сохраняем старую сумму для истории
                        old_amount = assessment.amount
                        
                        # Обновляем сумму
                        assessment.amount = Decimal(str(new_amount))
                        assessment.save()  # save() пересчитает статус
                        
                        updated.append({
                            'id': assessment.id,
                            'old_amount': str(old_amount),
                            'new_amount': str(assessment.amount),
                            'status': assessment.status,
                            'debt': str(assessment.debt)
                        })
                        
                        logger.debug(f"Updated assessment {assessment_id}: {old_amount} -> {new_amount}")
                        
                    except Assessment.DoesNotExist:
                        errors.append({
                            'index': idx,
                            'id': assessment_id,
                            'error': 'Начисление не найдено'
                        })
                        logger.warning(f"Assessment {assessment_id} not found")
                    except Exception as e:
                        errors.append({
                            'index': idx,
                            'id': assessment_id,
                            'error': str(e)
                        })
                        logger.error(f"Error updating assessment {assessment_id}: {e}")
        
        except Exception as e:
            logger.error(f"Database error in bulk update: {e}")
            return Response(
                {'error': f'Ошибка при обновлении: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
        
        logger.info(f"Bulk update completed: {len(updated)} updated, {len(errors)} errors")
        
        return Response({
            'success': True,
            'updated_count': len(updated),
            'error_count': len(errors),
            'updated': updated,
            'errors': errors
        })
        
    except Exception as e:
        logger.error(f"Unexpected error in bulk_update_assessments: {e}\n{traceback.format_exc()}")
        return Response(
            {'error': f'Внутренняя ошибка сервера: {str(e)}'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )