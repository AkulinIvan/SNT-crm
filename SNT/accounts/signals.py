from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth.models import Permission

from organizations.models import Organization, OrganizationStaffAssignment
from .models import User


@receiver(post_save, sender=User)
def assign_permissions_by_role(sender, instance, created, **kwargs):
    """Автоматически назначает разрешения в зависимости от роли"""
    
    # Обновляем права при создании И при изменении роли
    if not created and not hasattr(instance, '_role_changed'):
        return
    
    # Карта разрешений по ролям
    permissions_map = {
        'viewer': [
            'can_view_all_owners',
            'can_view_all_plots',
            'can_view_finances',
        ],
        'accountant': [
            'can_view_all_owners',
            'can_view_all_plots',
            'can_view_finances',
            'can_manage_finances',
            'can_export_data',
        ],
        'manager': [
            'can_view_all_owners',
            'can_edit_owners',
            'can_delete_owners',
            'can_view_all_plots',
            'can_edit_plots',
            'can_delete_plots',
            'can_view_finances',
            'can_manage_finances',
            'can_export_data',
            'can_manage_users',
            'can_view_audit_log',
        ],
        'admin': []  # Для админов назначаем все права отдельно
    }
    
    # Если суперпользователь - назначаем все права и роль admin
    if instance.is_superuser:
        instance.role = 'admin'
        instance.user_permissions.set(Permission.objects.all())
        instance.save(update_fields=['role'])
        return
    
    # Для админов - все права
    if instance.role == 'admin':
        instance.user_permissions.set(Permission.objects.all())
        return
    
    # Для остальных ролей - выборочные права
    codenames = permissions_map.get(instance.role, [])
    perms = Permission.objects.filter(codename__in=codenames)
    instance.user_permissions.set(perms)
    
    
@receiver(post_save, sender=Organization)
def assign_organization_to_staff(sender, instance, created, **kwargs):
    """При создании/изменении СНТ обновляем привязку сотрудников"""
    # Привязываем основную организацию пользователям
    if instance.chairman:
        if not instance.chairman.organization:
            instance.chairman.organization = instance
            instance.chairman.save(update_fields=['organization'])
        
        # Создаем запись в истории назначений
        OrganizationStaffAssignment.objects.get_or_create(
            organization=instance,
            user=instance.chairman,
            role='chairman',
            defaults={
                'position_title': 'Председатель правления',
                'is_active': True
            }
        )
    
    if instance.accountant:
        if not instance.accountant.organization:
            instance.accountant.organization = instance
            instance.accountant.save(update_fields=['organization'])
        
        OrganizationStaffAssignment.objects.get_or_create(
            organization=instance,
            user=instance.accountant,
            role='accountant',
            defaults={
                'position_title': 'Бухгалтер',
                'is_active': True
            }
        )
        
@receiver(post_save, sender=User)
def create_staff_assignment_for_new_user(sender, instance, created, **kwargs):
    """При создании пользователя с ролью manager создаем назначение"""
    if created and instance.role == 'manager' and instance.organization:
        from organizations.models import OrganizationStaffAssignment
        OrganizationStaffAssignment.objects.get_or_create(
            organization=instance.organization,
            user=instance,
            role='chairman',
            defaults={
                'position_title': 'Председатель правления',
                'is_active': True
            }
        )