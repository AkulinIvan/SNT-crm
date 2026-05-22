from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import Owner


@receiver(post_save, sender=Owner)
def auto_assign_organization(sender, instance, created, **kwargs):
    """При создании владельца через API с organization=None, пытаемся определить СНТ"""
    if created and not instance.organization:
        # Если у пользователя есть организация, привязываем к ней
        from accounts.models import User
        request = kwargs.get('request')
        if request and hasattr(request, 'user') and request.user.is_authenticated:
            if request.user.organization:
                instance.organization = request.user.organization
                instance.save(update_fields=['organization'])