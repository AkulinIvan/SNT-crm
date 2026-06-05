# subscriptions/payment_service.py
import uuid
import re
import logging
import traceback
from decimal import Decimal
from typing import Dict, Any, Optional
from django.utils import timezone
from django.core.mail import send_mail
from django.conf import settings
from django.template.loader import render_to_string
from .models import Payment, Subscription, Tariff, Invoice

logger = logging.getLogger(__name__)


class PaymentService:
    """Сервис для обработки платежей"""
    
    @staticmethod
    def create_payment(subscription, amount, payment_method='card', payer_data: Optional[Dict] = None) -> Optional[Payment]:
        """
        Создание платежа.
        
        Args:
            subscription: Объект подписки
            amount: Сумма платежа
            payment_method: Способ оплаты (card, bank, etc.)
            payer_data: Данные плательщика (name, email, phone)
            
        Returns:
            Объект Payment или None при ошибке
        """
        logger.info(f"Creating payment for subscription {subscription.id}")
        logger.debug(f"Amount: {amount}, method: {payment_method}, payer_data: {payer_data}")
        
        try:
            # Валидация входных данных
            if not subscription:
                logger.error("Subscription is required for payment creation")
                raise ValueError("Subscription is required")
            
            if amount <= 0:
                logger.error(f"Invalid amount: {amount}")
                raise ValueError(f"Amount must be greater than 0, got {amount}")
            
            # Генерация уникального transaction_id
            transaction_id = f"PAY-{uuid.uuid4().hex[:12].upper()}"
            logger.debug(f"Generated transaction_id: {transaction_id}")
            
            # Подготовка данных плательщика
            payer_name = payer_data.get('name', '') if payer_data else ''
            payer_email = payer_data.get('email', '') if payer_data else ''
            payer_phone = payer_data.get('phone', '') if payer_data else ''
            
            # Создание платежа
            payment = Payment.objects.create(
                subscription=subscription,
                amount=Decimal(str(amount)),
                payment_method=payment_method,
                transaction_id=transaction_id,
                payer_name=payer_name[:100] if payer_name else '',  # Ограничиваем длину
                payer_email=payer_email[:100] if payer_email else '',
                payer_phone=payer_phone[:20] if payer_phone else '',
                description=f"Оплата тарифа '{subscription.tariff.name}' для {subscription.organization.short_name}",
                status='pending',  # Начальный статус
                created_at=timezone.now()
            )
            
            logger.info(f"Payment created successfully: id={payment.id}, transaction_id={transaction_id}, amount={amount}")
            logger.debug(f"Payment details: status={payment.status}, description={payment.description[:50]}...")
            
            return payment
            
        except ValueError as e:
            logger.error(f"Validation error in create_payment: {e}")
            return None
        except Exception as e:
            logger.error(f"Error creating payment: {e}\n{traceback.format_exc()}")
            return None
    
    @staticmethod
    def process_payment(payment_id: int, payment_data: Dict) -> Dict[str, Any]:
        """
        Обработка платежа (симуляция).
        
        Args:
            payment_id: ID платежа
            payment_data: Данные платежа от платежной системы
            
        Returns:
            Словарь с результатом обработки
        """
        logger.info(f"Processing payment {payment_id}")
        logger.debug(f"Payment data: {payment_data}")
        
        try:
            # Поиск платежа
            try:
                payment = Payment.objects.get(id=payment_id)
                logger.info(f"Payment found: id={payment.id}, transaction_id={payment.transaction_id}, amount={payment.amount}")
            except Payment.DoesNotExist:
                logger.error(f"Payment {payment_id} not found")
                return {'success': False, 'error': 'Платёж не найден'}
            
            # Проверка статуса платежа
            if payment.status == 'success':
                logger.warning(f"Payment {payment_id} already processed successfully")
                return {'success': True, 'payment': payment, 'message': 'Платёж уже обработан'}
            
            if payment.status == 'failed':
                logger.warning(f"Payment {payment_id} already failed")
                return {'success': False, 'error': 'Платёж уже помечен как неудачный'}
            
            # Симуляция успешного платежа
            logger.info(f"Simulating successful payment for {payment_id}")
            
            try:
                payment.status = 'success'
                payment.paid_at = timezone.now()
                payment.save()
                logger.info(f"Payment status updated to 'success'")
            except Exception as e:
                logger.error(f"Error updating payment status: {e}")
                return {'success': False, 'error': f'Ошибка обновления статуса: {str(e)}'}
            
            # Обновляем подписку
            try:
                subscription = payment.subscription
                logger.info(f"Updating subscription {subscription.id} for payment {payment_id}")
                
                subscription.status = 'active'
                subscription.payment_id = payment.transaction_id
                subscription.payment_amount = payment.amount
                subscription.payment_date = timezone.now()
                subscription.payment_method = payment.payment_method
                
                # Рассчитываем дату окончания
                tariff = subscription.tariff
                price = float(tariff.price)
                paid_amount = float(payment.amount)
                
                logger.debug(f"Tariff: {tariff.name}, price: {price}, paid: {paid_amount}")
                
                # Если сумма меньше обычной цены - это годовая скидка
                if paid_amount < price and price > 0:
                    # Годовая подписка со скидкой
                    subscription.end_date = timezone.now() + timezone.timedelta(days=365)
                    logger.info(f"Yearly subscription with discount activated, ends at {subscription.end_date}")
                elif tariff.price_period == 'month':
                    subscription.end_date = timezone.now() + timezone.timedelta(days=30)
                    logger.info(f"Monthly subscription activated, ends at {subscription.end_date}")
                elif tariff.price_period == 'year':
                    subscription.end_date = timezone.now() + timezone.timedelta(days=365)
                    logger.info(f"Yearly subscription activated, ends at {subscription.end_date}")
                else:  # once
                    subscription.end_date = timezone.now() + timezone.timedelta(days=365 * 10)
                    logger.info(f"Lifetime subscription activated, ends at {subscription.end_date}")
                
                subscription.save()
                logger.info(f"Subscription {subscription.id} updated successfully")
                
            except Exception as e:
                logger.error(f"Error updating subscription: {e}\n{traceback.format_exc()}")
                return {'success': False, 'error': f'Ошибка обновления подписки: {str(e)}'}
            
            # Отправляем уведомление (не блокируем основную операцию)
            try:
                PaymentService._send_payment_notification(subscription, payment)
                logger.info(f"Payment notification sent for subscription {subscription.id}")
            except Exception as e:
                logger.error(f"Error sending notification: {e}")
                # Не возвращаем ошибку, так как платеж уже обработан
            
            return {
                'success': True, 
                'payment': payment,
                'subscription': subscription,
                'message': 'Платёж успешно обработан'
            }
            
        except Exception as e:
            logger.error(f"Unexpected error in process_payment: {e}\n{traceback.format_exc()}")
            return {'success': False, 'error': f'Внутренняя ошибка: {str(e)}'}
    
    @staticmethod
    def _send_payment_notification(subscription, payment):
        """
        Отправка уведомления об оплате.
        
        Args:
            subscription: Объект подписки
            payment: Объект платежа
        """
        logger.info(f"Sending payment notification for subscription {subscription.id}")
        
        try:
            subject = f"Подтверждение оплаты тарифа '{subscription.tariff.name}'"
            logger.debug(f"Email subject: {subject}")
            
            context = {
                'organization': subscription.organization,
                'tariff': subscription.tariff,
                'payment': payment,
                'end_date': subscription.end_date,
                'now': timezone.now(),
                'request': None,
            }
            
            # Генерация HTML письма
            try:
                html_message = render_to_string('subscriptions/email_payment_confirmation.html', context)
                logger.debug("HTML message generated successfully")
            except Exception as e:
                logger.error(f"Error rendering HTML template: {e}")
                html_message = None
            
            # Формирование plain text сообщения
            plain_message = f"""
            Уважаемые руководители {subscription.organization.short_name}!
            
            Оплата тарифа "{subscription.tariff.name}" успешно произведена.
            
            Сумма: {payment.amount} ₽
            Дата оплаты: {payment.paid_at.strftime('%d.%m.%Y %H:%M') if payment.paid_at else '—'}
            
            Подписка активна до: {subscription.end_date.strftime('%d.%m.%Y')}
            
            С уважением,
            Команда CRM СНТ
            """
            
            # Определяем email получателя
            recipient_email = subscription.organization.email or getattr(settings, 'DEFAULT_FROM_EMAIL', None)
            
            if not recipient_email:
                logger.warning(f"No recipient email found for organization {subscription.organization.id}")
                return
            
            logger.debug(f"Sending email to: {recipient_email}")
            
            # Отправка письма
            send_mail(
                subject=subject,
                message=plain_message,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[recipient_email],
                html_message=html_message,
                fail_silently=False  # Не скрываем ошибки в логах
            )
            
            logger.info(f"Payment notification sent successfully to {recipient_email}")
            
        except Exception as e:
            logger.error(f"Error sending payment notification: {e}\n{traceback.format_exc()}")
            # Не перевыбрасываем исключение, чтобы не блокировать основной процесс
    
    @staticmethod
    def create_invoice(organization, tariff, subscription=None) -> Optional[Invoice]:
        """
        Создание счета на оплату.
        
        Args:
            organization: Организация
            tariff: Тариф
            subscription: Подписка (опционально)
            
        Returns:
            Объект Invoice или None при ошибке
        """
        from datetime import timedelta
        
        logger.info(f"Creating invoice for organization {organization.id}, tariff {tariff.name}")
        
        try:
            # Валидация
            if not organization:
                logger.error("Organization is required for invoice creation")
                raise ValueError("Organization is required")
            
            if not tariff:
                logger.error("Tariff is required for invoice creation")
                raise ValueError("Tariff is required")
            
            # Генерация номера счета
            invoice_number = f"СНТ-{timezone.now().year}-{uuid.uuid4().hex[:8].upper()}"
            logger.debug(f"Generated invoice number: {invoice_number}")
            
            # Расчет даты оплаты (через 10 дней)
            due_date = timezone.now().date() + timedelta(days=10)
            logger.debug(f"Due date: {due_date}")
            
            # Создание счета
            invoice = Invoice.objects.create(
                number=invoice_number,
                organization=organization,
                subscription=subscription,
                amount=tariff.price,
                due_date=due_date,
                description=f"Оплата тарифа '{tariff.name}'",
                status='pending',
                created_at=timezone.now()
            )
            
            logger.info(f"Invoice created successfully: id={invoice.id}, number={invoice_number}, amount={tariff.price}")
            
            return invoice
            
        except ValueError as e:
            logger.error(f"Validation error in create_invoice: {e}")
            return None
        except Exception as e:
            logger.error(f"Error creating invoice: {e}\n{traceback.format_exc()}")
            return None
    
    @staticmethod
    def get_payment_status(payment_id: int) -> Dict[str, Any]:
        """
        Получение статуса платежа.
        
        Args:
            payment_id: ID платежа
            
        Returns:
            Словарь с информацией о статусе платежа
        """
        logger.info(f"Getting payment status for payment {payment_id}")
        
        try:
            payment = Payment.objects.select_related('subscription', 'subscription__tariff').get(id=payment_id)
            
            status_info = {
                'id': payment.id,
                'transaction_id': payment.transaction_id,
                'status': payment.status,
                'amount': str(payment.amount),
                'payment_method': payment.payment_method,
                'created_at': payment.created_at,
                'paid_at': payment.paid_at,
                'subscription_id': payment.subscription.id if payment.subscription else None,
                'tariff_name': payment.subscription.tariff.name if payment.subscription and payment.subscription.tariff else None,
            }
            
            logger.info(f"Payment status retrieved: {payment.status}")
            return status_info
            
        except Payment.DoesNotExist:
            logger.error(f"Payment {payment_id} not found")
            return {'error': 'Платёж не найден', 'status': 'not_found'}
        except Exception as e:
            logger.error(f"Error getting payment status: {e}\n{traceback.format_exc()}")
            return {'error': str(e), 'status': 'error'}


class PaymentSimulator:
    """Симулятор платежей для тестирования"""
    
    @staticmethod
    def get_payment_url(payment) -> str:
        """
        Получить URL для оплаты (симуляция).
        
        Args:
            payment: Объект платежа
            
        Returns:
            URL для оплаты
        """
        logger.debug(f"Generating payment URL for payment {payment.id}")
        url = f"/subscription/pay/{payment.id}/"
        logger.debug(f"Payment URL: {url}")
        return url
    
    @staticmethod
    def simulate_successful_payment(payment_id: int) -> Dict[str, Any]:
        """
        Симуляция успешного платежа.
        
        Args:
            payment_id: ID платежа
            
        Returns:
            Результат обработки платежа
        """
        logger.info(f"Simulating successful payment for payment {payment_id}")
        
        try:
            payment_service = PaymentService()
            result = payment_service.process_payment(payment_id, {})
            
            if result.get('success'):
                logger.info(f"Payment {payment_id} simulation completed successfully")
            else:
                logger.error(f"Payment {payment_id} simulation failed: {result.get('error')}")
            
            return result
            
        except Exception as e:
            logger.error(f"Error in payment simulation: {e}\n{traceback.format_exc()}")
            return {'success': False, 'error': f'Ошибка симуляции: {str(e)}'}
    
    @staticmethod
    def simulate_failed_payment(payment_id: int) -> Dict[str, Any]:
        """
        Симуляция неуспешного платежа для тестирования.
        
        Args:
            payment_id: ID платежа
            
        Returns:
            Результат обработки платежа
        """
        logger.info(f"Simulating failed payment for payment {payment_id}")
        
        try:
            payment = Payment.objects.get(id=payment_id)
            
            payment.status = 'failed'
            payment.save()
            
            logger.info(f"Payment {payment_id} marked as failed")
            
            return {
                'success': False,
                'error': 'Платёж отклонён',
                'payment_id': payment_id,
                'status': 'failed'
            }
            
        except Payment.DoesNotExist:
            logger.error(f"Payment {payment_id} not found")
            return {'success': False, 'error': 'Платёж не найден'}
        except Exception as e:
            logger.error(f"Error in failed payment simulation: {e}")
            return {'success': False, 'error': str(e)}