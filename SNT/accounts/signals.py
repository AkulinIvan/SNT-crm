# accounts/signals.py - исправленная версия

from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth.models import Permission
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