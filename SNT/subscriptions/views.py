# subscriptions/views.py
from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.shortcuts import render, redirect, get_object_or_404
from django.views import View
from django.contrib.auth.decorators import login_required
from django.utils.decorators import method_decorator
from django.utils import timezone
from django.contrib import messages
from decimal import Decimal

from .models import Tariff, Subscription, Payment, Invoice
from .serializers import TariffSerializer, SubscriptionSerializer
from .payment_service import PaymentService, PaymentSimulator
from accounts.permissions import IsManagerOrAbove

import logging
logger = logging.getLogger(__name__)

class TariffViewSet(viewsets.ReadOnlyModelViewSet):
    """ViewSet для просмотра тарифов"""
    queryset = Tariff.objects.filter(is_active=True)
    serializer_class = TariffSerializer
    permission_classes = [permissions.IsAuthenticated]


class SubscriptionViewSet(viewsets.ViewSet):
    """
    ViewSet для работы с подписками.
    """
    permission_classes = [permissions.IsAuthenticated]
    
    @action(detail=False, methods=['get'], url_path='current')
    def current_subscription(self, request):
        """Получить текущую подписку пользователя"""
        organization = None
        
        # Определяем организацию
        if hasattr(request, 'current_organization') and request.current_organization:
            organization = request.current_organization
        elif hasattr(request.user, 'organization') and request.user.organization:
            organization = request.user.organization
        elif hasattr(request.user, 'current_organization'):
            organization = request.user.current_organization
        
        if not organization:
            return Response(
                {'detail': 'Организация не найдена', 'has_subscription': False},
                status=status.HTTP_200_OK 
            )
        
        try:
            subscription = Subscription.objects.filter(
                organization=organization,
                status__in=['active', 'trial']
            ).select_related('tariff').first()
            
            if not subscription:
                return Response({
                    'has_subscription': False,
                    'organization_id': organization.id,
                    'organization_name': organization.short_name,
                })
            
            return Response({
                'id': subscription.id,
                'has_subscription': True,
                'tariff_name': subscription.tariff.name,
                'tariff_slug': subscription.tariff.slug,
                'status': subscription.status,
                'start_date': subscription.start_date,
                'end_date': subscription.end_date,
                'days_left': (subscription.end_date - timezone.now()).days if subscription.end_date else None,
                'organization_id': organization.id,
                'organization_name': organization.short_name,
            })
        except Exception as e:
            return Response({
                'detail': str(e),
                'has_subscription': False,
            })



    @action(detail=False, methods=['post'], url_path='upgrade')
    def upgrade(self, request):
        """Обновление тарифа"""
        logger.info(f"=== UPGRADE REQUEST ===")
        logger.info(f"Request data: {request.data}")
        logger.info(f"User: {request.user}")
        logger.info(f"Current organization: {hasattr(request, 'current_organization')}")

        tariff_id = request.data.get('tariff_id')
        payment_method = request.data.get('payment_method', 'card')
        period = request.data.get('period', 'monthly')

        logger.info(f"Tariff ID: {tariff_id}, Period: {period}")

        if not tariff_id:
            return Response({'detail': 'Укажите tariff_id'}, status=status.HTTP_400_BAD_REQUEST)    

        if not hasattr(request, 'current_organization') or not request.current_organization:
            return Response({'detail': 'Организация не найдена'}, status=status.HTTP_404_NOT_FOUND) 

        try:
            new_tariff = Tariff.objects.get(id=tariff_id, is_active=True)
            logger.info(f"Found tariff: {new_tariff.name}, price: {new_tariff.price}")
        except Tariff.DoesNotExist:
            logger.error(f"Tariff with id {tariff_id} not found")
            return Response({'detail': 'Тариф не найден'}, status=status.HTTP_404_NOT_FOUND)    

        organization = request.current_organization

        # Рассчитываем сумму с учетом периода
        amount = float(new_tariff.price)
        logger.info(f"Base amount: {amount}")

        if period == 'yearly' and new_tariff.price > 0:
            # Скидка 17% при оплате за год
            amount = round(float(new_tariff.price) * 0.83, 0)
            logger.info(f"Yearly discount applied: {amount}")

            # Явная проверка для каждого тарифа
            if new_tariff.price == 999:
                amount = 833
                logger.info(f"Basic tariff yearly price: {amount}")
            elif new_tariff.price == 2490:
                amount = 2075
                logger.info(f"Pro tariff yearly price: {amount}")
            elif new_tariff.price == 5000:
                amount = 4150
                logger.info(f"Premium tariff yearly price: {amount}")   

        # Получаем текущую подписку
        subscription, created = Subscription.objects.get_or_create(
            organization=organization,
            defaults={
                'tariff': new_tariff,
                'status': 'pending'
            }
        )

        logger.info(f"Subscription created: {created}, status: {subscription.status}")  

        # Если подписка уже существует, обновляем тариф
        if not created:
            subscription.tariff = new_tariff
            subscription.status = 'pending'
            subscription.save()
            logger.info(f"Subscription updated with tariff: {new_tariff.name}") 

        # Если тариф платный
        if amount > 0:
            try:
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

                logger.info(f"Payment created: id={payment.id}, amount={payment.amount}")   

                # Создаем счет
                invoice = payment_service.create_invoice(organization, new_tariff, subscription)    

                response_data = {
                    'success': True,
                    'payment_id': payment.id,
                    'payment_url': PaymentSimulator.get_payment_url(payment),
                    'invoice_number': invoice.number,
                    'amount': float(amount),
                    'tariff_name': new_tariff.name,
                    'period': period,
                    'message': f'Создан платеж для тарифа "{new_tariff.name}" на сумму {amount} ₽. Перейдите по ссылке для оплаты.'
                }

                logger.info(f"Response: {response_data}")
                return Response(response_data)

            except Exception as e:
                logger.error(f"Payment creation error: {str(e)}", exc_info=True)
                return Response({
                    'success': False,
                    'detail': f'Ошибка при создании платежа: {str(e)}'
                }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        else:
            # Бесплатный тариф
            subscription.status = 'active'
            subscription.start_date = timezone.now()    

            if new_tariff.price_period == 'month':
                subscription.end_date = timezone.now() + timezone.timedelta(days=30)
            elif new_tariff.price_period == 'year':
                subscription.end_date = timezone.now() + timezone.timedelta(days=365)
            else:
                subscription.end_date = timezone.now() + timezone.timedelta(days=365 * 10)  

            subscription.save() 

            return Response({
                'success': True,
                'message': f'Тариф "{new_tariff.name}" активирован',
                'subscription': SubscriptionSerializer(subscription).data
            })
    
    @action(detail=False, methods=['get'], url_path='features')
    def features(self, request):
        """
        Получить возможности текущего тарифа.
        Этот эндпоинт запрашивается фронтендом.
        """
        organization = None
        
        # Определяем организацию
        if hasattr(request, 'current_organization') and request.current_organization:
            organization = request.current_organization
        elif hasattr(request.user, 'organization') and request.user.organization:
            organization = request.user.organization
        elif hasattr(request.user, 'current_organization'):
            organization = request.user.current_organization
        
        if not organization:
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
        
        try:
            subscription = Subscription.objects.filter(
                organization=organization,
                status__in=['active', 'trial']
            ).select_related('tariff').first()
            
            if not subscription:
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
            
            # ВАЖНО: Явно возвращаем все поля, которые проверяет фронтенд
            return Response({
                'organization_id': organization.id,
                'organization_name': organization.short_name,
                'tariff_name': tariff.name,
                'tariff_slug': tariff.slug,
                'max_owners': tariff.max_owners,
                'max_plots': tariff.max_plots,
                'max_users': tariff.max_users,
                'has_finance_module': tariff.can_manage_payments,  # Управление платежами
                'has_export': tariff.can_export_data,
                'has_api_access': tariff.can_import_bank,  # API доступ = импорт из банка
                'can_manage_assessments': tariff.can_manage_assessments,
                'can_manage_payments': tariff.can_manage_payments,
                'can_import_bank': tariff.can_import_bank,
                'subscription_active': True,
                'subscription_status': subscription.status,
                'days_left': (subscription.end_date - timezone.now()).days if subscription.end_date else None,
            })
        except Exception as e:
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
            })

    
    @action(detail=False, methods=['get'], url_path='check-access')
    def check_access(self, request):
        """Проверить доступ к функции"""
        feature = request.data.get('feature')
        
        if not feature:
            return Response({'detail': 'Укажите feature'}, status=status.HTTP_400_BAD_REQUEST)
        
        if not hasattr(request, 'current_organization') or not request.current_organization:
            return Response({'has_access': False, 'reason': 'Организация не найдена'})
        
        subscription = getattr(request.current_organization, 'subscription', None)
        
        if not subscription or not subscription.is_active:
            return Response({'has_access': False, 'reason': 'Нет активной подписки'})
        
        tariff = subscription.tariff
        has_access = False
        
        feature_map = {
            'map': tariff.can_view_map,
            'payments': tariff.can_manage_payments,
            'bank_import': tariff.can_import_bank,
            'export': tariff.can_export_data,
            'assessments': tariff.can_manage_assessments,
        }
        
        has_access = feature_map.get(feature, False)
        
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


class SubscriptionPlansView(View):
    """Страница с тарифными планами"""
    @method_decorator(login_required)
    def get(self, request):
        tariffs = Tariff.objects.filter(is_active=True)
        
        current_subscription = None
        if hasattr(request, 'current_organization') and request.current_organization:
            current_subscription = getattr(request.current_organization, 'subscription', None)
        
        return render(request, 'subscriptions/plans.html', {
            'tariffs': tariffs,
            'current_subscription': current_subscription,
            'active_page': 'subscriptions'
        })


class SubscriptionPaymentView(View):
    """Обработка платежа"""
    @method_decorator(login_required)
    def get(self, request, payment_id):
        payment = get_object_or_404(Payment, id=payment_id)
        
        # Симуляция успешного платежа
        if request.GET.get('simulate') == 'true':
            result = PaymentSimulator.simulate_successful_payment(payment_id)
            
            if result['success']:
                messages.success(request, 'Оплата успешно произведена! Тариф активирован.')
                return redirect('subscription_plans')
            else:
                messages.error(request, 'Ошибка при обработке платежа')
                return redirect('subscription_plans')
        
        # Страница оплаты
        return render(request, 'subscriptions/payment.html', {
            'payment': payment,
            'active_page': 'subscriptions'
        })
    
    @method_decorator(login_required)
    def post(self, request, payment_id):
        """Обработка POST запроса оплаты"""
        payment = get_object_or_404(Payment, id=payment_id)
        
        # Здесь интеграция с реальной платежной системой
        # Например: YooKassa, Tinkoff, Sberbank и т.д.
        
        # Для демо - симуляция
        result = PaymentSimulator.simulate_successful_payment(payment_id)
        
        if result['success']:
            messages.success(request, 'Оплата успешно произведена!')
            return redirect('subscription_plans')
        else:
            messages.error(request, 'Ошибка при обработке платежа')
            return redirect('subscription_payment', payment_id=payment_id)