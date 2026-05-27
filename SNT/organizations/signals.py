from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import Organization, OrganizationStaffAssignment
from django.utils import timezone

@receiver(post_save, sender=Organization)
def assign_organization_to_staff(sender, instance, created, **kwargs):
    """При создании/изменении СНТ обновляем привязку сотрудников"""
    if instance.chairman:
        instance.chairman.organization = instance
        instance.chairman.save(update_fields=['organization'])
    
    if instance.accountant:
        instance.accountant.organization = instance
        instance.accountant.save(update_fields=['organization'])
        
@receiver(post_save, sender=Organization)
def create_staff_assignment_on_organization_save(sender, instance, created, **kwargs):
    """
    При создании или изменении председателя/бухгалтера в организации,
    создаем соответствующую запись в истории назначений.
    """
    # Обработка только если изменились поля chairman или accountant
    if not created and not kwargs.get('update_fields'):
        return
    
    changed_fields = kwargs.get('update_fields') or []
    
    # Председатель
    if created or 'chairman' in changed_fields:
        if instance.chairman:
            # Проверяем, есть ли уже активное назначение
            active_assignment = OrganizationStaffAssignment.objects.filter(
                organization=instance,
                user=instance.chairman,
                role='chairman',
                is_active=True
            ).first()
            
            if not active_assignment:
                # Деактивируем предыдущие назначения
                OrganizationStaffAssignment.objects.filter(
                    organization=instance,
                    role='chairman',
                    is_active=True
                ).update(is_active=False, assigned_until=timezone.now())
                
                # Создаем новое
                OrganizationStaffAssignment.objects.create(
                    organization=instance,
                    user=instance.chairman,
                    role='chairman',
                    position_title='Председатель правления',
                    is_active=True
                )
    
    # Бухгалтер
    if created or 'accountant' in changed_fields:
        if instance.accountant:
            active_assignment = OrganizationStaffAssignment.objects.filter(
                organization=instance,
                user=instance.accountant,
                role='accountant',
                is_active=True
            ).first()
            
            if not active_assignment:
                OrganizationStaffAssignment.objects.filter(
                    organization=instance,
                    role='accountant',
                    is_active=True
                ).update(is_active=False, assigned_until=timezone.now())
                
                OrganizationStaffAssignment.objects.create(
                    organization=instance,
                    user=instance.accountant,
                    role='accountant',
                    position_title='Бухгалтер',
                    is_active=True
                )