# subscriptions/views.py
import logging
import traceback
from decimal import Decimal
from typing import Optional, Dict, Any

from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.shortcuts import render, redirect, get_object_or_404
from django.views import View
from django.contrib.auth.decorators import login_required
from django.utils.decorators import method_decorator
from django.utils import timezone
from django.contrib import messages
from django.db import transaction

from .models import Tariff, Subscription, Payment, Invoice
from .serializers import TariffSerializer, SubscriptionSerializer
from .payment_service import PaymentService, PaymentSimulator
from accounts.permissions import IsManagerOrAbove

logger = logging.getLogger(__name__)


class TariffViewSet(viewsets.ReadOnlyModelViewSet):
    """ViewSet для просмотра тарифов"""
    queryset = Tariff.objects.filter(is_active=True)
    serializer_class = TariffSerializer
    permission_classes = [permissions.IsAuthenticated]

    def list(self, request, *args, **kwargs):
        """Получение списка тарифов с логированием"""
        try:
            logger.info(f"User {request.user.id} requesting tariff list")
            response = super().list(request, *args, **kwargs)
            logger.info(f"Returned {len(response.data)} tariffs")
            return response
        except Exception as e:
            logger.error(f"Error in tariff list: {e}\n{traceback.format_exc()}")
            return Response(
                {'detail': f'Ошибка при получении списка тарифов: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    def retrieve(self, request, *args, **kwargs):
        """Получение деталей тарифа с логированием"""
        try:
            logger.info(f"User {request.user.id} requesting tariff details for pk={kwargs.get('pk')}")
            response = super().retrieve(request, *args, **kwargs)
            logger.info(f"Tariff details retrieved successfully")
            return response
        except Exception as e:
            logger.error(f"Error in tariff retrieve: {e}\n{traceback.format_exc()}")
            return Response(
                {'detail': f'Ошибка при получении деталей тарифа: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class SubscriptionViewSet(viewsets.ViewSet):
    """
    ViewSet для работы с подписками.
    """
    permission_classes = [permissions.IsAuthenticated]
    
    def _get_organization(self, request):
        """
        Вспомогательный метод для получения организации пользователя.
        """
        try:
            # Определяем организацию
            if hasattr(request, 'current_organization') and request.current_organization:
                logger.debug(f"Organization from current_organization: {request.current_organization.id}")
                return request.current_organization
            elif hasattr(request.user, 'organization') and request.user.organization:
                logger.debug(f"Organization from user.organization: {request.user.organization.id}")
                return request.user.organization
            elif hasattr(request.user, 'current_organization'):
                logger.debug(f"Organization from user.current_organization: {request.user.current_organization.id}")
                return request.user.current_organization
            
            logger.warning(f"No organization found for user {request.user.id}")
            return None
        except Exception as e:
            logger.error(f"Error getting organization: {e}")
            return None
    
    @action(detail=False, methods=['get'], url_path='current')
    def current_subscription(self, request):
        """Получить текущую подписку пользователя"""
        logger.info(f"User {request.user.id} requesting current subscription")
        
        try:
            organization = self._get_organization(request)
            
            if not organization:
                logger.warning(f"Organization not found for user {request.user.id}")
                return Response(
                    {'detail': 'Организация не найдена', 'has_subscription': False},
                    status=status.HTTP_200_OK 
                )
            
            logger.debug(f"Organization found: id={organization.id}, name={organization.short_name}")
            
            subscription = Subscription.objects.filter(
                organization=organization,
                status__in=['active', 'trial']
            ).select_related('tariff').first()
            
            if not subscription:
                logger.info(f"No active subscription for organization {organization.id}")
                return Response({
                    'has_subscription': False,
                    'organization_id': organization.id,
                    'organization_name': organization.short_name,
                })
            
            days_left = (subscription.end_date - timezone.now()).days if subscription.end_date else None
            logger.info(f"Active subscription found: tariff={subscription.tariff.name}, days_left={days_left}")
            
            return Response({
                'id': subscription.id,
                'has_subscription': True,
                'tariff_name': subscription.tariff.name,
                'tariff_slug': subscription.tariff.slug,
                'status': subscription.status,
                'start_date': subscription.start_date,
                'end_date': subscription.end_date,
                'days_left': days_left,
                'organization_id': organization.id,
                'organization_name': organization.short_name,
            })
            
        except Exception as e:
            logger.error(f"Error in current_subscription: {e}\n{traceback.format_exc()}")
            return Response({
                'detail': str(e),
                'has_subscription': False,
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=['post'], url_path='upgrade')
    def upgrade(self, request):
        """Обновление тарифа"""
        logger.info("=" * 60)
        logger.info(f"UPGRADE REQUEST from user {request.user.id}")
        logger.info(f"Request data: {request.data}")
        logger.info(f"User: {request.user.email}")
        
        try:
            tariff_id = request.data.get('tariff_id')
            payment_method = request.data.get('payment_method', 'card')
            period = request.data.get('period', 'monthly')

            logger.info(f"Params: tariff_id={tariff_id}, period={period}, payment_method={payment_method}")

            # Валидация входных данных
            if not tariff_id:
                logger.error("tariff_id is missing")
                return Response(
                    {'detail': 'Укажите tariff_id'}, 
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Проверка организации
            organization = self._get_organization(request)
            if not organization:
                logger.error(f"Organization not found for user {request.user.id}")
                return Response(
                    {'detail': 'Организация не найдена'}, 
                    status=status.HTTP_404_NOT_FOUND
                )
            
            logger.info(f"Organization: id={organization.id}, name={organization.name}")

            # Поиск тарифа
            try:
                new_tariff = Tariff.objects.get(id=tariff_id, is_active=True)
                logger.info(f"Tariff found: id={new_tariff.id}, name={new_tariff.name}, price={new_tariff.price}")
            except Tariff.DoesNotExist:
                logger.error(f"Tariff with id {tariff_id} not found or inactive")
                return Response(
                    {'detail': 'Тариф не найден'}, 
                    status=status.HTTP_404_NOT_FOUND
                )

            # Рассчитываем сумму с учетом периода
            try:
                amount = float(new_tariff.price)
                logger.info(f"Base amount: {amount}")

                if period == 'yearly' and new_tariff.price > 0:
                    # Скидка 17% при оплате за год
                    amount = round(float(new_tariff.price) * 0.83, 0)
                    logger.info(f"Yearly discount applied, new amount: {amount}")

                    # Явная проверка для каждого тарифа
                    if new_tariff.price == 999:
                        amount = 833
                        logger.info("Basic tariff yearly price set to 833")
                    elif new_tariff.price == 2490:
                        amount = 2075
                        logger.info("Pro tariff yearly price set to 2075")
                    elif new_tariff.price == 5000:
                        amount = 4150
                        logger.info("Premium tariff yearly price set to 4150")
            except Exception as e:
                logger.error(f"Error calculating amount: {e}")
                return Response(
                    {'detail': f'Ошибка при расчете суммы: {str(e)}'},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Получаем или создаем подписку
            try:
                with transaction.atomic():
                    subscription, created = Subscription.objects.get_or_create(
                        organization=organization,
                        defaults={
                            'tariff': new_tariff,
                            'status': 'pending'
                        }
                    )
                    
                    logger.info(f"Subscription {'created' if created else 'retrieved'}, id={subscription.id}, status={subscription.status}")

                    # Если подписка уже существует, обновляем тариф
                    if not created:
                        old_tariff = subscription.tariff.name if subscription.tariff else None
                        subscription.tariff = new_tariff
                        subscription.status = 'pending'
                        subscription.save()
                        logger.info(f"Subscription updated: tariff changed from '{old_tariff}' to '{new_tariff.name}'")
                        
            except Exception as e:
                logger.error(f"Error with subscription: {e}")
                return Response(
                    {'detail': f'Ошибка при работе с подпиской: {str(e)}'},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )

            # Если тариф платный
            if amount > 0:
                try:
                    logger.info("Creating payment for paid tariff")
                    payment_service = PaymentService()
                    
                    payment = payment_service.create_payment(
                        subscription=subscription,
                        amount=amount,
                        payment_method=payment_method,
                        payer_data={
                            'name': request.user.full_name,
                            'email': request.user.email,
                            'phone': request.user.phone,
                        }
                    )

                    logger.info(f"Payment created: id={payment.id}, amount={payment.amount}, status={payment.status}")

                    # Создаем счет
                    try:
                        invoice = payment_service.create_invoice(organization, new_tariff, subscription)
                        logger.info(f"Invoice created: number={invoice.number}, amount={invoice.amount}")
                    except Exception as e:
                        logger.error(f"Error creating invoice: {e}")
                        # Продолжаем даже если счет не создался

                    response_data = {
                        'success': True,
                        'payment_id': payment.id,
                        'payment_url': PaymentSimulator.get_payment_url(payment),
                        'invoice_number': invoice.number if 'invoice' in locals() else None,
                        'amount': float(amount),
                        'tariff_name': new_tariff.name,
                        'period': period,
                        'message': f'Создан платеж для тарифа "{new_tariff.name}" на сумму {amount} ₽. Перейдите по ссылке для оплаты.'
                    }

                    logger.info(f"Payment created successfully, response prepared")
                    return Response(response_data)

                except Exception as e:
                    logger.error(f"Payment creation error: {e}\n{traceback.format_exc()}")
                    return Response({
                        'success': False,
                        'detail': f'Ошибка при создании платежа: {str(e)}'
                    }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
            else:
                # Бесплатный тариф
                try:
                    logger.info(f"Activating free tariff: {new_tariff.name}")
                    with transaction.atomic():
                        subscription.status = 'active'
                        subscription.start_date = timezone.now()

                        # Устанавливаем дату окончания
                        if new_tariff.price_period == 'month':
                            subscription.end_date = timezone.now() + timezone.timedelta(days=30)
                            logger.info("Subscription end date set to +30 days")
                        elif new_tariff.price_period == 'year':
                            subscription.end_date = timezone.now() + timezone.timedelta(days=365)
                            logger.info("Subscription end date set to +365 days")
                        else:
                            subscription.end_date = timezone.now() + timezone.timedelta(days=365 * 10)
                            logger.info("Subscription end date set to +10 years (lifetime)")

                        subscription.save()
                        
                        logger.info(f"Free tariff activated for organization {organization.id}")

                    return Response({
                        'success': True,
                        'message': f'Тариф "{new_tariff.name}" активирован',
                        'subscription': SubscriptionSerializer(subscription).data
                    })
                    
                except Exception as e:
                    logger.error(f"Error activating free tariff: {e}")
                    return Response({
                        'success': False,
                        'detail': f'Ошибка активации тарифа: {str(e)}'
                    }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
                    
        except Exception as e:
            logger.error(f"Unexpected error in upgrade: {e}\n{traceback.format_exc()}")
            return Response({
                'success': False,
                'detail': f'Внутренняя ошибка сервера: {str(e)}'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    @action(detail=False, methods=['get'], url_path='features')
    def features(self, request):
        """
        Получить возможности текущего тарифа.
        Этот эндпоинт запрашивается фронтендом.
        """
        logger.info(f"User {request.user.id} requesting tariff features")
        
        try:
            organization = self._get_organization(request)
            
            if not organization:
                logger.warning(f"No organization found for user {request.user.id}")
                # Возвращаем базовые возможности для пользователя без организации
                return Response({
                    'max_owners': 0,
                    'max_plots': 0,
                    'max_users': 1,
                    'has_finance_module': False,
                    'has_export': False,
                    'has_api_access': False,
                    'can_manage_assessments': False,
                    'can_manage_payments': False,
                    'can_import_bank': False,
                    'organization_exists': False,
                })
            
            logger.debug(f"Organization found: {organization.id}, {organization.name}")
            
            try:
                subscription = Subscription.objects.filter(
                    organization=organization,
                    status__in=['active', 'trial']
                ).select_related('tariff').first()
                
                if not subscription:
                    logger.info(f"No active subscription for organization {organization.id}")
                    # Нет подписки - базовые ограничения
                    return Response({
                        'organization_id': organization.id,
                        'organization_name': organization.short_name,
                        'max_owners': 0,
                        'max_plots': 0,
                        'max_users': 1,
                        'has_finance_module': False,
                        'has_export': False,
                        'has_api_access': False,
                        'can_manage_assessments': False,
                        'can_manage_payments': False,
                        'can_import_bank': False,
                        'subscription_active': False,
                    })
                
                tariff = subscription.tariff
                logger.info(f"Active subscription: tariff={tariff.name}, status={subscription.status}")
                
                days_left = (subscription.end_date - timezone.now()).days if subscription.end_date else None
                
                # ВАЖНО: Явно возвращаем все поля, которые проверяет фронтенд
                response_data = {
                    'organization_id': organization.id,
                    'organization_name': organization.short_name,
                    'tariff_name': tariff.name,
                    'tariff_slug': tariff.slug,
                    'max_owners': tariff.max_owners,
                    'max_plots': tariff.max_plots,
                    'max_users': tariff.max_users,
                    'has_finance_module': tariff.can_manage_payments,
                    'has_export': tariff.can_export_data,
                    'has_api_access': tariff.can_import_bank,
                    'can_manage_assessments': tariff.can_manage_assessments,
                    'can_manage_payments': tariff.can_manage_payments,
                    'can_import_bank': tariff.can_import_bank,
                    'subscription_active': True,
                    'subscription_status': subscription.status,
                    'days_left': days_left,
                }
                
                logger.info(f"Features returned for organization {organization.id}: max_owners={tariff.max_owners}")
                return Response(response_data)
                
            except Subscription.DoesNotExist:
                logger.warning(f"Subscription not found for organization {organization.id}")
                return Response({
                    'organization_exists': True,
                    'subscription_active': False,
                    'max_owners': 0,
                    'max_plots': 0,
                    'max_users': 1,
                    'has_finance_module': False,
                    'has_export': False,
                    'has_api_access': False,
                    'can_manage_assessments': False,
                    'can_manage_payments': False,
                    'can_import_bank': False,
                })
                
        except Exception as e:
            logger.error(f"Error in features: {e}\n{traceback.format_exc()}")
            return Response({
                'detail': str(e),
                'organization_exists': True,
                'subscription_active': False,
                'max_owners': 0,
                'max_plots': 0,
                'max_users': 1,
                'has_finance_module': False,
                'has_export': False,
                'has_api_access': False,
                'can_manage_assessments': False,
                'can_manage_payments': False,
                'can_import_bank': False,
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    
    @action(detail=False, methods=['post'], url_path='check-access')
    def check_access(self, request):
        """Проверить доступ к функции"""
        logger.info(f"User {request.user.id} checking access for feature")
        
        try:
            feature = request.data.get('feature')
            
            if not feature:
                logger.warning("Feature parameter missing")
                return Response(
                    {'detail': 'Укажите feature'}, 
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            logger.debug(f"Checking access for feature: {feature}")
            
            organization = self._get_organization(request)
            
            if not organization:
                logger.warning(f"No organization found for user {request.user.id}")
                return Response({
                    'has_access': False, 
                    'reason': 'Организация не найдена'
                })
            
            subscription = getattr(request.current_organization, 'subscription', None)
            
            if not subscription or not subscription.is_active:
                logger.warning(f"No active subscription for organization {organization.id}")
                return Response({
                    'has_access': False, 
                    'reason': 'Нет активной подписки'
                })
            
            tariff = subscription.tariff
            logger.debug(f"Tariff: {tariff.name}")
            
            feature_map = {
                'map': tariff.can_view_map,
                'payments': tariff.can_manage_payments,
                'bank_import': tariff.can_import_bank,
                'export': tariff.can_export_data,
                'assessments': tariff.can_manage_assessments,
            }
            
            has_access = feature_map.get(feature, False)
            logger.info(f"Feature '{feature}' access: {has_access}")
            
            return Response({
                'has_access': has_access,
                'tariff': tariff.name,
                'features': {
                    'map': tariff.can_view_map,
                    'payments': tariff.can_manage_payments,
                    'bank_import': tariff.can_import_bank,
                    'export': tariff.can_export_data,
                    'assessments': tariff.can_manage_assessments,
                }
            })
            
        except Exception as e:
            logger.error(f"Error in check_access: {e}\n{traceback.format_exc()}")
            return Response({
                'has_access': False,
                'reason': f'Ошибка проверки: {str(e)}'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class SubscriptionPlansView(View):
    """Страница с тарифными планами"""
    
    @method_decorator(login_required)
    def get(self, request):
        """Отображение страницы с тарифными планами"""
        logger.info(f"User {request.user.id} accessing subscription plans page")
        
        try:
            tariffs = Tariff.objects.filter(is_active=True)
            logger.debug(f"Found {tariffs.count()} active tariffs")
            
            current_subscription = None
            if hasattr(request, 'current_organization') and request.current_organization:
                current_subscription = getattr(request.current_organization, 'subscription', None)
                if current_subscription:
                    logger.debug(f"Current subscription: {current_subscription.tariff.name if current_subscription.tariff else 'None'}")
            
            return render(request, 'subscriptions/plans.html', {
                'tariffs': tariffs,
                'current_subscription': current_subscription,
                'active_page': 'subscriptions'
            })
            
        except Exception as e:
            logger.error(f"Error in subscription plans view: {e}\n{traceback.format_exc()}")
            messages.error(request, f'Ошибка при загрузке страницы тарифов: {str(e)}')
            return render(request, 'subscriptions/plans.html', {
                'tariffs': [],
                'current_subscription': None,
                'active_page': 'subscriptions',
                'error': str(e)
            })


class SubscriptionPaymentView(View):
    """Обработка платежа"""
    
    @method_decorator(login_required)
    def get(self, request, payment_id):
        """Отображение страницы оплаты"""
        logger.info(f"User {request.user.id} accessing payment page for payment {payment_id}")
        
        try:
            payment = get_object_or_404(Payment, id=payment_id)
            logger.debug(f"Payment found: id={payment.id}, amount={payment.amount}, status={payment.status}")
            
            # Симуляция успешного платежа
            if request.GET.get('simulate') == 'true':
                logger.info(f"Simulating successful payment for {payment_id}")
                result = PaymentSimulator.simulate_successful_payment(payment_id)
                
                if result['success']:
                    logger.info(f"Payment simulation successful for {payment_id}")
                    messages.success(request, 'Оплата успешно произведена! Тариф активирован.')
                    return redirect('subscription_plans')
                else:
                    logger.error(f"Payment simulation failed for {payment_id}: {result.get('error')}")
                    messages.error(request, 'Ошибка при обработке платежа')
                    return redirect('subscription_plans')
            
            # Страница оплаты
            return render(request, 'subscriptions/payment.html', {
                'payment': payment,
                'active_page': 'subscriptions'
            })
            
        except Payment.DoesNotExist:
            logger.error(f"Payment {payment_id} not found")
            messages.error(request, 'Платёж не найден')
            return redirect('subscription_plans')
            
        except Exception as e:
            logger.error(f"Error in payment GET: {e}\n{traceback.format_exc()}")
            messages.error(request, f'Ошибка при загрузке страницы оплаты: {str(e)}')
            return redirect('subscription_plans')
    
    @method_decorator(login_required)
    def post(self, request, payment_id):
        """Обработка POST запроса оплаты"""
        logger.info(f"User {request.user.id} processing payment POST for payment {payment_id}")
        
        try:
            payment = get_object_or_404(Payment, id=payment_id)
            logger.debug(f"Payment found: id={payment.id}, amount={payment.amount}")
            
            # Здесь интеграция с реальной платежной системой
            # Например: YooKassa, Tinkoff, Sberbank и т.д.
            
            # Для демо - симуляция
            logger.info(f"Simulating payment processing for {payment_id}")
            result = PaymentSimulator.simulate_successful_payment(payment_id)
            
            if result['success']:
                logger.info(f"Payment processed successfully for {payment_id}")
                messages.success(request, 'Оплата успешно произведена! Тариф активирован.')
                return redirect('subscription_plans')
            else:
                logger.error(f"Payment processing failed for {payment_id}: {result.get('error')}")
                messages.error(request, f'Ошибка при обработке платежа: {result.get("error", "Неизвестная ошибка")}')
                return redirect('subscription_payment', payment_id=payment_id)
                
        except Payment.DoesNotExist:
            logger.error(f"Payment {payment_id} not found in POST request")
            messages.error(request, 'Платёж не найден')
            return redirect('subscription_plans')
            
        except Exception as e:
            logger.error(f"Error in payment POST: {e}\n{traceback.format_exc()}")
            messages.error(request, f'Ошибка при обработке платежа: {str(e)}')
            return redirect('subscription_payment', payment_id=payment_id)