from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import Organization


@receiver(post_save, sender=Organization)
def assign_organization_to_staff(sender, instance, created, **kwargs):
    """При создании/изменении СНТ обновляем привязку сотрудников"""
    if instance.chairman:
        instance.chairman.organization = instance
        instance.chairman.save(update_fields=['organization'])
    
    if instance.accountant:
        instance.accountant.organization = instance
        instance.accountant.save(update_fields=['organization'])