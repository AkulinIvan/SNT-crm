# SNT/subscriptions/signals.py
import logging
import traceback
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django.core.mail import send_mail
from django.conf import settings
from django.utils import timezone
from .models import Subscription

logger = logging.getLogger(__name__)


@receiver(post_save, sender=Subscription)
def check_subscription_limits(sender, instance, created, **kwargs):
    """
    Проверка лимитов при активации подписки.
    
    Отправляет уведомление если количество владельцев или участков
    превышает лимиты выбранного тарифа.
    """
    logger.info(f"Checking subscription limits for subscription {instance.id}")
    logger.debug(f"Subscription: id={instance.id}, status={instance.status}, created={created}")
    
    try:
        # Проверяем, активирована ли подписка
        # Используем getattr для безопасной проверки изменений статуса
        status_changed = False
        
        # Проверяем, изменился ли статус (если есть трекер)
        if hasattr(instance, 'tracker'):
            status_changed = instance.tracker.has_changed('status')
        elif not created:
            # Если трекера нет, предполагаем что статус мог измениться
            status_changed = True
        
        # Если подписка активна и либо создана, либо статус изменился на active
        if instance.status == 'active' and (created or status_changed):
            logger.info(f"Subscription {instance.id} is now active, checking limits")
            
            organization = instance.organization
            tariff = instance.tariff
            
            if not organization:
                logger.error(f"Subscription {instance.id} has no organization")
                return
            
            if not tariff:
                logger.error(f"Subscription {instance.id} has no tariff")
                return
            
            logger.debug(f"Organization: id={organization.id}, name={organization.name}")
            logger.debug(f"Tariff: {tariff.name}, max_owners={tariff.max_owners}, max_plots={tariff.max_plots}")
            
            warnings = []
            
            # Проверка лимита владельцев
            try:
                owners_count = organization.owners_count
                logger.debug(f"Owners count: {owners_count}, limit: {tariff.max_owners}")
                
                if owners_count > tariff.max_owners:
                    warning_msg = (f"Количество владельцев ({owners_count}) превышает лимит тарифа "
                                  f"({tariff.max_owners}) на {owners_count - tariff.max_owners}")
                    warnings.append(warning_msg)
                    logger.warning(f"Owner limit exceeded for org {organization.id}: {warning_msg}")
                    
            except Exception as e:
                logger.error(f"Error checking owners count: {e}")
                warnings.append(f"Не удалось проверить лимит владельцев: {str(e)}")
            
            # Проверка лимита участков
            try:
                plots_count = organization.plots_count
                logger.debug(f"Plots count: {plots_count}, limit: {tariff.max_plots}")
                
                if plots_count > tariff.max_plots:
                    warning_msg = (f"Количество участков ({plots_count}) превышает лимит тарифа "
                                  f"({tariff.max_plots}) на {plots_count - tariff.max_plots}")
                    warnings.append(warning_msg)
                    logger.warning(f"Plot limit exceeded for org {organization.id}: {warning_msg}")
                    
            except Exception as e:
                logger.error(f"Error checking plots count: {e}")
                warnings.append(f"Не удалось проверить лимит участков: {str(e)}")
            
            # Отправка уведомления если есть превышения
            if warnings and organization.email:
                logger.info(f"Sending limit warning email to {organization.email}")
                
                try:
                    subject = f"Превышение лимитов тарифа '{tariff.name}'"
                    
                    # Формируем HTML сообщение
                    html_message = f"""
                    <!DOCTYPE html>
                    <html>
                    <head>
                        <meta charset="UTF-8">
                        <title>Превышение лимитов тарифа</title>
                        <style>
                            body {{ font-family: Arial, sans-serif; line-height: 1.6; }}
                            .warning {{ color: #856404; background-color: #fff3cd; padding: 10px; border-radius: 5px; }}
                            .list {{ margin: 10px 0; padding-left: 20px; }}
                            .footer {{ margin-top: 20px; font-size: 12px; color: #666; }}
                        </style>
                    </head>
                    <body>
                        <h2>Превышение лимитов тарифа "{tariff.name}"</h2>
                        <p>Уважаемые руководители!</p>
                        <p>При активации тарифа "<strong>{tariff.name}</strong>" обнаружены превышения:</p>
                        <div class="warning">
                            <ul class="list">
                                {''.join(f'<li>{w}</li>' for w in warnings)}
                            </ul>
                        </div>
                        <p><strong>Рекомендации:</strong></p>
                        <ul>
                            <li>Перейдите на более высокий тарифный план для снятия ограничений</li>
                            <li>Или удалите лишние данные для соответствия текущему тарифу</li>
                        </ul>
                        <p>С уважением,<br>Команда CRM СНТ</p>
                        <div class="footer">
                            Это автоматическое сообщение, пожалуйста, не отвечайте на него.
                        </div>
                    </body>
                    </html>
                    """
                    
                    # Простое текстовое сообщение
                    plain_message = (
                        f"Уважаемые руководители!\n\n"
                        f"При активации тарифа '{tariff.name}' обнаружены превышения:\n\n" + 
                        "\n".join(f"• {w}" for w in warnings) +
                        f"\n\nРекомендуем перейти на более высокий тариф или удалить лишние данные.\n\n" +
                        f"С уважением,\nКоманда CRM СНТ\n\n"
                        f"---\nЭто автоматическое сообщение, пожалуйста, не отвечайте на него."
                    )
                    
                    send_mail(
                        subject=subject,
                        message=plain_message,
                        from_email=settings.DEFAULT_FROM_EMAIL,
                        recipient_list=[organization.email],
                        html_message=html_message,
                        fail_silently=False  # Не скрываем ошибки для логирования
                    )
                    
                    logger.info(f"Limit warning email sent successfully to {organization.email}")
                    
                except Exception as e:
                    logger.error(f"Error sending limit warning email: {e}\n{traceback.format_exc()}")
            else:
                if not organization.email:
                    logger.warning(f"Organization {organization.id} has no email, cannot send notification")
                elif not warnings:
                    logger.info(f"No limit warnings for subscription {instance.id}")
            
            # Логируем итоговый результат
            if warnings:
                logger.warning(f"Subscription {instance.id} activated with {len(warnings)} warnings")
            else:
                logger.info(f"Subscription {instance.id} activated successfully, all limits OK")
                
        else:
            logger.debug(f"Subscription {instance.id} not active or status not changed, skipping limit check")
            
    except Exception as e:
        logger.error(f"Error in check_subscription_limits signal: {e}\n{traceback.format_exc()}")


@receiver(post_save, sender=Subscription)
def log_subscription_changes(sender, instance, created, **kwargs):
    """
    Логирование изменений подписки.
    """
    logger.debug(f"Logging subscription changes for subscription {instance.id}")
    
    try:
        if created:
            logger.info(f"New subscription created: id={instance.id}, "
                       f"organization={instance.organization.id if instance.organization else None}, "
                       f"tariff={instance.tariff.name if instance.tariff else None}, "
                       f"status={instance.status}")
        else:
            # Проверяем изменения, если есть трекер
            changes = []
            if hasattr(instance, 'tracker'):
                tracker = instance.tracker
                
                if tracker.has_changed('status'):
                    changes.append(f"status: {tracker.previous('status')} -> {instance.status}")
                if tracker.has_changed('tariff_id'):
                    changes.append(f"tariff: changed")
                if tracker.has_changed('end_date'):
                    changes.append(f"end_date: {tracker.previous('end_date')} -> {instance.end_date}")
            
            if changes:
                logger.info(f"Subscription {instance.id} updated: {', '.join(changes)}")
            else:
                logger.debug(f"Subscription {instance.id} saved with no significant changes")
                
    except Exception as e:
        logger.error(f"Error logging subscription changes: {e}")


@receiver(pre_save, sender=Subscription)
def check_subscription_dates(sender, instance, **kwargs):
    """
    Проверка и корректировка дат подписки перед сохранением.
    """
    logger.debug(f"Checking subscription dates for subscription {instance.id if instance.id else 'new'}")
    
    try:
        # Если подписка новая и даты не установлены
        if not instance.pk and not instance.start_date:
            instance.start_date = timezone.now()
            logger.debug(f"Set start_date to {instance.start_date} for new subscription")
        
        # Если статус active, но нет даты окончания
        if instance.status == 'active' and not instance.end_date and instance.tariff:
            if instance.tariff.price_period == 'month':
                instance.end_date = timezone.now() + timezone.timedelta(days=30)
                logger.debug(f"Set end_date to {instance.end_date} for monthly subscription")
            elif instance.tariff.price_period == 'year':
                instance.end_date = timezone.now() + timezone.timedelta(days=365)
                logger.debug(f"Set end_date to {instance.end_date} for yearly subscription")
            else:
                instance.end_date = timezone.now() + timezone.timedelta(days=365 * 10)
                logger.debug(f"Set end_date to {instance.end_date} for lifetime subscription")
        
        # Проверка: дата окончания не может быть раньше даты начала
        if instance.start_date and instance.end_date and instance.end_date < instance.start_date:
            logger.warning(f"End date {instance.end_date} is before start date {instance.start_date}")
            instance.end_date = instance.start_date + timezone.timedelta(days=30)
            logger.info(f"Corrected end_date to {instance.end_date}")
            
    except Exception as e:
        logger.error(f"Error checking subscription dates: {e}\n{traceback.format_exc()}")


# Функция для отправки уведомления о скором истечении подписки
def send_expiration_notifications():
    """
    Отправка уведомлений о скором истечении подписки.
    Запускается по расписанию (например, ежедневно через celery).
    """
    logger.info("Checking for expiring subscriptions")
    
    try:
        from django.utils import timezone
        from datetime import timedelta
        
        # Подписки, которые истекают через 7 дней
        expiring_soon = Subscription.objects.filter(
            status='active',
            end_date__lte=timezone.now() + timedelta(days=7),
            end_date__gt=timezone.now()
        )
        
        logger.info(f"Found {expiring_soon.count()} subscriptions expiring soon")
        
        for subscription in expiring_soon:
            days_left = (subscription.end_date - timezone.now()).days
            
            if days_left <= 3:
                logger.info(f"Subscription {subscription.id} expires in {days_left} days")
                
                try:
                    organization = subscription.organization
                    if organization and organization.email:
                        subject = f"Подписка истекает через {days_left} дней"
                        
                        message = (
                            f"Уважаемые руководители {organization.short_name}!\n\n"
                            f"Срок действия подписки на тариф '{subscription.tariff.name}' "
                            f"истекает через {days_left} дней ({subscription.end_date.strftime('%d.%m.%Y')}).\n\n"
                            f"Для продолжения работы с системой, пожалуйста, продлите подписку.\n\n"
                            f"С уважением,\nКоманда CRM СНТ"
                        )
                        
                        send_mail(
                            subject=subject,
                            message=message,
                            from_email=settings.DEFAULT_FROM_EMAIL,
                            recipient_list=[organization.email],
                            fail_silently=True
                        )
                        
                        logger.info(f"Expiration notification sent to {organization.email}")
                        
                except Exception as e:
                    logger.error(f"Error sending expiration notification for subscription {subscription.id}: {e}")
                    
    except Exception as e:
        logger.error(f"Error in send_expiration_notifications: {e}\n{traceback.format_exc()}")