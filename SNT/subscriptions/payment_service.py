# subscriptions/payment_service.py
import uuid
import re
from decimal import Decimal
from django.utils import timezone
from django.core.mail import send_mail
from django.conf import settings
from django.template.loader import render_to_string
from .models import Payment, Subscription, Tariff, Invoice


class PaymentService:
    """Сервис для обработки платежей"""
    
    @staticmethod
    def create_payment(subscription, amount, payment_method='card', payer_data=None):
        """Создание платежа"""
        payment = Payment.objects.create(
            subscription=subscription,
            amount=amount,
            payment_method=payment_method,
            transaction_id=f"PAY-{uuid.uuid4().hex[:12].upper()}",
            payer_name=payer_data.get('name', '') if payer_data else '',
            payer_email=payer_data.get('email', '') if payer_data else '',
            payer_phone=payer_data.get('phone', '') if payer_data else '',
            description=f"Оплата тарифа '{subscription.tariff.name}' для {subscription.organization.short_name}"
        )
        return payment
    
    @staticmethod
    def process_payment(payment_id, payment_data):
        """Обработка платежа (симуляция)"""
        try:
            payment = Payment.objects.get(id=payment_id)
            
            # Симуляция успешного платежа
            payment.status = 'success'
            payment.paid_at = timezone.now()
            payment.save()
            
            # Обновляем подписку
            subscription = payment.subscription
            
            subscription.status = 'active'
            subscription.payment_id = payment.transaction_id
            subscription.payment_amount = payment.amount  # Используем сумму из платежа
            subscription.payment_date = timezone.now()
            subscription.payment_method = payment.payment_method
            
            # Рассчитываем дату окончания
            # Определяем период по сумме платежа
            tariff = subscription.tariff
            price = float(tariff.price)
            paid_amount = float(payment.amount)
            
            # Если сумма меньше обычной цены - это годовая скидка
            if paid_amount < price and price > 0:
                # Годовая подписка со скидкой
                subscription.end_date = timezone.now() + timezone.timedelta(days=365)
            elif tariff.price_period == 'month':
                subscription.end_date = timezone.now() + timezone.timedelta(days=30)
            elif tariff.price_period == 'year':
                subscription.end_date = timezone.now() + timezone.timedelta(days=365)
            else:  # once
                subscription.end_date = timezone.now() + timezone.timedelta(days=365 * 10)
            
            subscription.save()
            
            # Отправляем уведомление
            PaymentService._send_payment_notification(subscription, payment)
            
            return {'success': True, 'payment': payment}
        except Payment.DoesNotExist:
            return {'success': False, 'error': 'Платёж не найден'}
    
    @staticmethod
    def _send_payment_notification(subscription, payment):
        """Отправка уведомления об оплате"""
        subject = f"Подтверждение оплаты тарифа '{subscription.tariff.name}'"
        
        context = {
            'organization': subscription.organization,
            'tariff': subscription.tariff,
            'payment': payment,
            'end_date': subscription.end_date,
            'now': timezone.now(),
            'request': None,
        }
        
        try:
            html_message = render_to_string('subscriptions/email_payment_confirmation.html', context)
            plain_message = f"""
            Уважаемые руководители {subscription.organization.short_name}!
            
            Оплата тарифа "{subscription.tariff.name}" успешно произведена.
            
            Сумма: {payment.amount} ₽
            Дата оплаты: {payment.paid_at.strftime('%d.%m.%Y %H:%M')}
            
            Подписка активна до: {subscription.end_date.strftime('%d.%m.%Y')}
            
            С уважением,
            Команда CRM СНТ
            """
            
            # Отправляем на email организации
            recipient_email = subscription.organization.email or getattr(settings, 'DEFAULT_FROM_EMAIL')
            
            send_mail(
                subject=subject,
                message=plain_message,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[recipient_email],
                html_message=html_message,
                fail_silently=True
            )
        except Exception as e:
            print(f"Error sending email: {e}")
    
    @staticmethod
    def create_invoice(organization, tariff, subscription=None):
        """Создание счета на оплату"""
        from datetime import timedelta
        import uuid

        invoice = Invoice.objects.create(
            number=f"СНТ-{timezone.now().year}-{uuid.uuid4().hex[:8].upper()}",
            organization=organization,
            subscription=subscription,
            amount=tariff.price,
            due_date=timezone.now().date() + timedelta(days=10),
            description=f"Оплата тарифа '{tariff.name}'"
        )

        return invoice


class PaymentSimulator:
    """Симулятор платежей для тестирования"""
    
    @staticmethod
    def get_payment_url(payment):
        """Получить URL для оплаты (симуляция)"""
        return f"/subscription/pay/{payment.id}/"
    
    @staticmethod
    def simulate_successful_payment(payment_id):
        """Симуляция успешного платежа"""
        payment_service = PaymentService()
        return payment_service.process_payment(payment_id, {})