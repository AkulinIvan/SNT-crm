import logging

from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth.models import Permission
from django.db import transaction, DatabaseError, IntegrityError

from organizations.models import Organization, OrganizationStaffAssignment
from .models import User

logger = logging.getLogger(__name__)


@receiver(post_save, sender=User)
def assign_permissions_by_role(sender, instance, created, **kwargs):
    """Автоматически назначает разрешения в зависимости от роли"""
    
    try:
        # Обновляем права при создании И при изменении роли
        if not created and not hasattr(instance, '_role_changed'):
            return
        
        logger.info(f"Назначение прав для пользователя {instance.username} (роль: {instance.role})")
        
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
        
        with transaction.atomic():
            # Если суперпользователь - назначаем все права и роль admin
            if instance.is_superuser:
                instance.role = 'admin'
                instance.user_permissions.set(Permission.objects.all())
                instance.save(update_fields=['role'])
                logger.info(f"Назначены все права суперпользователю {instance.username}")
                return
            
            # Для админов - все права
            if instance.role == 'admin':
                instance.user_permissions.set(Permission.objects.all())
                logger.info(f"Назначены все права администратору {instance.username}")
                return
            
            # Для остальных ролей - выборочные права
            codenames = permissions_map.get(instance.role, [])
            if codenames:
                perms = Permission.objects.filter(codename__in=codenames)
                instance.user_permissions.set(perms)
                logger.info(f"Назначены права для {instance.role}: {codenames}")
            else:
                logger.warning(f"Не найдены права для роли {instance.role}")
                
    except DatabaseError as e:
        logger.error(f"Ошибка базы данных при назначении прав пользователю {instance.username}: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"Критическая ошибка при назначении прав пользователю {instance.username}: {e}", exc_info=True)
    
    
@receiver(post_save, sender=Organization)
def assign_organization_to_staff(sender, instance, created, **kwargs):
    """При создании/изменении СНТ обновляем привязку сотрудников"""
    
    try:
        if instance.chairman:
            logger.debug(f"Обработка председателя {instance.chairman.username} для организации {instance.name}")
            
            if not instance.chairman.organization:
                instance.chairman.organization = instance
                instance.chairman.save(update_fields=['organization'])
                logger.info(f"Председатель {instance.chairman.username} привязан к организации {instance.name}")
            
            # Создаем запись в истории назначений
            assignment, created = OrganizationStaffAssignment.objects.get_or_create(
                organization=instance,
                user=instance.chairman,
                role='chairman',
                defaults={
                    'position_title': 'Председатель правления',
                    'is_active': True
                }
            )
            
            if created:
                logger.info(f"Создано назначение председателя: {assignment}")
            else:
                logger.debug(f"Назначение председателя уже существует: {assignment}")
        
        if instance.accountant:
            logger.debug(f"Обработка бухгалтера {instance.accountant.username} для организации {instance.name}")
            
            if not instance.accountant.organization:
                instance.accountant.organization = instance
                instance.accountant.save(update_fields=['organization'])
                logger.info(f"Бухгалтер {instance.accountant.username} привязан к организации {instance.name}")
            
            # Создаем запись в истории назначений
            assignment, created = OrganizationStaffAssignment.objects.get_or_create(
                organization=instance,
                user=instance.accountant,
                role='accountant',
                defaults={
                    'position_title': 'Бухгалтер',
                    'is_active': True
                }
            )
            
            if created:
                logger.info(f"Создано назначение бухгалтера: {assignment}")
            else:
                logger.debug(f"Назначение бухгалтера уже существует: {assignment}")
                
    except IntegrityError as e:
        logger.error(f"Ошибка целостности данных при назначении сотрудников: {e}", exc_info=True)
    except DatabaseError as e:
        logger.error(f"Ошибка базы данных при назначении сотрудников: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"Критическая ошибка при назначении сотрудников: {e}", exc_info=True)
        
        
@receiver(post_save, sender=User)
def create_staff_assignment_for_new_user(sender, instance, created, **kwargs):
    """При создании пользователя с ролью manager создаем назначение"""
    
    try:
        if created and instance.role == 'manager' and instance.organization:
            logger.info(f"Создание назначения для нового менеджера {instance.username}")
            
            assignment, created = OrganizationStaffAssignment.objects.get_or_create(
                organization=instance.organization,
                user=instance,
                role='chairman',
                defaults={
                    'position_title': 'Председатель правления',
                    'is_active': True
                }
            )
            
            if created:
                logger.info(f"Создано автоматическое назначение председателя: {assignment}")
            else:
                logger.debug(f"Назначение уже существует: {assignment}")
                
    except IntegrityError as e:
        logger.error(f"Ошибка целостности данных при создании назначения для {instance.username}: {e}", exc_info=True)
    except DatabaseError as e:
        logger.error(f"Ошибка базы данных при создании назначения для {instance.username}: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"Критическая ошибка при создании назначения для {instance.username}: {e}", exc_info=True)