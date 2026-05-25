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


class TariffViewSet(viewsets.ReadOnlyModelViewSet):
    """ViewSet для просмотра тарифов"""
    queryset = Tariff.objects.filter(is_active=True)
    serializer_class = TariffSerializer
    permission_classes = [permissions.IsAuthenticated]


class SubscriptionViewSet(viewsets.ViewSet):
    """ViewSet для управления подпиской"""
    permission_classes = [permissions.IsAuthenticated, IsManagerOrAbove]
    
    @action(detail=False, methods=['get'], url_path='current')
    def current_subscription(self, request):
        """Получить текущую подписку организации"""
        if not hasattr(request, 'current_organization') or not request.current_organization:
            return Response({'detail': 'Организация не найдена'}, status=status.HTTP_404_NOT_FOUND)
        
        subscription = getattr(request.current_organization, 'subscription', None)
        if subscription:
            serializer = SubscriptionSerializer(subscription)
            return Response(serializer.data)
        
        return Response({'detail': 'Активная подписка не найдена'}, status=status.HTTP_404_NOT_FOUND)
    
    @action(detail=False, methods=['post'], url_path='upgrade')
    def upgrade(self, request):
        """Обновление тарифа"""
        tariff_id = request.data.get('tariff_id')
        payment_method = request.data.get('payment_method', 'card')

        if not tariff_id:
            return Response({'detail': 'Укажите tariff_id'}, status=status.HTTP_400_BAD_REQUEST)

        if not hasattr(request, 'current_organization') or not request.current_organization:
            return Response({'detail': 'Организация не найдена'}, status=status.HTTP_404_NOT_FOUND)

        try:
            new_tariff = Tariff.objects.get(id=tariff_id, is_active=True)
        except Tariff.DoesNotExist:
            return Response({'detail': 'Тариф не найден'}, status=status.HTTP_404_NOT_FOUND)

        organization = request.current_organization

        # Получаем текущую подписку
        subscription, created = Subscription.objects.get_or_create(
            organization=organization,
            defaults={
                'tariff': new_tariff,
                'status': 'pending'
            }
        )

        # Если подписка уже существует, обновляем тариф
        if not created:
            subscription.tariff = new_tariff
            subscription.status = 'pending'
            subscription.save()

        # Если тариф платный
        if new_tariff.price > 0:
            try:
                payment_service = PaymentService()
                payment = payment_service.create_payment(
                    subscription=subscription,
                    amount=new_tariff.price,
                    payment_method=payment_method,
                    payer_data={
                        'name': request.user.full_name,
                        'email': request.user.email,
                        'phone': request.user.phone,
                    }
                )

                # Создаем счет
                invoice = payment_service.create_invoice(organization, new_tariff, subscription)

                return Response({
                    'success': True,
                    'payment_id': payment.id,
                    'payment_url': PaymentSimulator.get_payment_url(payment),
                    'invoice_number': invoice.number,
                    'amount': float(new_tariff.price),
                    'tariff_name': new_tariff.name,
                    'message': f'Создан платеж для тарифа "{new_tariff.name}". Перейдите по ссылке для оплаты.'
                })
            except Exception as e:
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
    def available_features(self, request):
        """Получить доступные функции для текущего тарифа"""
        if not hasattr(request, 'current_organization') or not request.current_organization:
            return Response({'detail': 'Организация не найдена'}, status=status.HTTP_404_NOT_FOUND)
        
        subscription = getattr(request.current_organization, 'subscription', None)
        
        if not subscription or not subscription.is_active:
            return Response({
                'has_subscription': False,
                'features': {
                    'map': False,
                    'payments': False,
                    'bank_import': False,
                    'export': False,
                    'assessments': False,
                },
                'tariff': None
            })
        
        tariff = subscription.tariff
        
        return Response({
            'has_subscription': True,
            'tariff': {
                'id': tariff.id,
                'name': tariff.name,
                'slug': tariff.slug,
            },
            'features': {
                'map': tariff.can_view_map,
                'payments': tariff.can_manage_payments,
                'bank_import': tariff.can_import_bank,
                'export': tariff.can_export_data,
                'assessments': tariff.can_manage_assessments,
            },
            'subscription': {
                'end_date': subscription.end_date,
                'days_left': subscription.days_left,
            }
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