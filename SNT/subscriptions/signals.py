# SNT/subscriptions/signals.py
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.core.mail import send_mail
from django.conf import settings
from .models import Subscription


@receiver(post_save, sender=Subscription)
def check_subscription_limits(sender, instance, created, **kwargs):
    """Проверка лимитов при активации подписки"""
    if instance.status == 'active' and (created or instance.tracker.has_changed('status')):
        organization = instance.organization
        tariff = instance.tariff
        
        warnings = []
        
        # Проверка лимита владельцев
        if organization.owners_count > tariff.max_owners:
            warnings.append(f"Количество владельцев ({organization.owners_count}) превышает лимит тарифа ({tariff.max_owners})")
        
        # Проверка лимита участков
        if organization.plots_count > tariff.max_plots:
            warnings.append(f"Количество участков ({organization.plots_count}) превышает лимит тарифа ({tariff.max_plots})")
        
        # Отправка уведомления если есть превышения
        if warnings and organization.email:
            send_mail(
                subject=f"Превышение лимитов тарифа '{tariff.name}'",
                message=f"Уважаемые руководители!\n\nПри активации тарифа '{tariff.name}' обнаружены превышения:\n\n" + 
                        "\n".join(f"• {w}" for w in warnings) +
                        f"\n\nРекомендуем перейти на более высокий тариф или удалить лишние данные.\n\n" +
                        f"С уважением,\nКоманда CRM СНТ",
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[organization.email],
                fail_silently=True
            )