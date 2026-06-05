# users/signals.py
from django.db.models.signals import post_save, pre_save, pre_delete
from django.dispatch import receiver
from django.db import transaction
from .models import Owner, ContactInfo, Ownership
from organizations.models import OrganizationMembership
import threading
import logging
import traceback
from django.utils import timezone

logger = logging.getLogger(__name__)


class RequestStorage:
    """Хранилище request для доступа в сигналах"""
    _storage = threading.local()
    
    @classmethod
    def set_request(cls, request):
        cls._storage.request = request
    
    @classmethod
    def get_request(cls):
        return getattr(cls._storage, 'request', None)
    
    @classmethod
    def clear(cls):
        if hasattr(cls._storage, 'request'):
            del cls._storage.request


def get_current_request():
    """Получить текущий request из хранилища"""
    return RequestStorage.get_request()


@receiver(post_save, sender=Owner)
def auto_assign_membership(sender, instance, created, **kwargs):
    """
    При создании владельца автоматически создаем членство в СНТ текущего пользователя.
    """
    logger.info(f"=== AUTO_ASSIGN_MEMBERSHIP SIGNAL ===")
    logger.info(f"Signal triggered for Owner: {instance.full_name} (ID: {instance.id}), created={created}")
    
    try:
        if created:
            # Пытаемся получить request из различных источников
            request = None
            
            # 1. Из нашего хранилища
            request = get_current_request()
            if request:
                logger.debug("Request found in thread-local storage")
            
            # 2. Альтернативный способ - через threading.local
            if not request:
                request = getattr(threading.current_thread(), 'request', None)
                if request:
                    logger.debug("Request found in thread attribute")
            
            if request:
                logger.debug(f"Request user: {request.user}, authenticated: {request.user.is_authenticated}")
                
                if hasattr(request, 'current_organization') and request.current_organization:
                    org = request.current_organization
                    logger.info(f"Creating membership for owner {instance.id} in organization {org.id} ({org.name})")
                    
                    try:
                        with transaction.atomic():
                            membership, created_membership = OrganizationMembership.objects.get_or_create(
                                owner=instance,
                                organization=org,
                                defaults={'status': 'active', 'joined_at': timezone.now()}
                            )
                            
                            if created_membership:
                                logger.info(f"Membership created: ID={membership.id}, owner={instance.id}, org={org.id}")
                            else:
                                logger.info(f"Membership already exists: ID={membership.id}")
                                
                    except Exception as e:
                        logger.error(f"Error creating membership: {e}\n{traceback.format_exc()}")
                else:
                    logger.warning(f"No current_organization in request for user {request.user.id}")
            else:
                logger.warning(f"No request found in current thread when creating owner {instance.id}")
                logger.debug("Will try to assign membership later via background task")
                
                # Можно добавить отложенную задачу здесь
                # from .tasks import assign_membership_async
                # assign_membership_async.delay(instance.id)
        else:
            logger.debug(f"Owner {instance.id} updated, not creating membership")
            
    except Exception as e:
        logger.error(f"Error in auto_assign_membership signal: {e}\n{traceback.format_exc()}")


@receiver(pre_save, sender=Owner)
def log_owner_changes(sender, instance, **kwargs):
    """
    Логирование изменений владельца перед сохранением.
    """
    logger.debug(f"Pre-save signal for Owner {instance.id if instance.id else 'new'}")
    
    try:
        if instance.pk:
            try:
                old = sender.objects.get(pk=instance.pk)
                changes = []
                
                if old.full_name != instance.full_name:
                    changes.append(f"full_name: '{old.full_name}' -> '{instance.full_name}'")
                    
                if hasattr(old, 'organization') and hasattr(instance, 'organization'):
                    old_org_id = old.organization.id if old.organization else None
                    new_org_id = instance.organization.id if instance.organization else None
                    if old_org_id != new_org_id:
                        changes.append(f"organization_id: {old_org_id} -> {new_org_id}")
                
                if changes:
                    logger.info(f"Owner {instance.id} changes: {', '.join(changes)}")
                    
            except Owner.DoesNotExist:
                logger.debug(f"Owner {instance.id} not found in DB, probably new")
        else:
            logger.debug(f"Creating new owner: {instance.full_name}")
            
    except Exception as e:
        logger.error(f"Error in log_owner_changes: {e}")


@receiver(post_save, sender=ContactInfo)
def validate_contact_info(sender, instance, created, **kwargs):
    """
    Валидация и нормализация контактной информации после сохранения.
    """
    logger.info(f"ContactInfo post-save signal: ID={instance.id}, type={instance.type}, created={created}")
    
    try:
        # Нормализация телефонного номера
        if instance.type == 'ph' and instance.value:
            import re
            # Удаляем все нецифровые символы
            digits = re.sub(r'\D', '', instance.value)
            
            # Форматирование для российских номеров
            if len(digits) == 11 and digits.startswith('7'):
                formatted = f"+{digits[0]} ({digits[1:4]}) {digits[4:7]}-{digits[7:9]}-{digits[9:11]}"
                if instance.value != formatted:
                    logger.debug(f"Normalizing phone: {instance.value} -> {formatted}")
                    instance.value = formatted
                    instance.save(update_fields=['value'])
                    
        # Нормализация email
        elif instance.type == 'em' and instance.value:
            normalized = instance.value.lower().strip()
            if instance.value != normalized:
                logger.debug(f"Normalizing email: {instance.value} -> {normalized}")
                instance.value = normalized
                instance.save(update_fields=['value'])
                
        # Проверка на дубликаты активных контактов
        if created and instance.is_active:
            duplicates = ContactInfo.objects.filter(
                owner=instance.owner,
                type=instance.type,
                value=instance.value,
                is_active=True
            ).exclude(id=instance.id)
            
            if duplicates.exists():
                logger.warning(f"Duplicate contact found for owner {instance.owner.id}: "
                             f"type={instance.type}, value={instance.value}")
                
    except Exception as e:
        logger.error(f"Error in validate_contact_info: {e}\n{traceback.format_exc()}")


@receiver(pre_save, sender=Ownership)
def check_ownership_conflicts(sender, instance, **kwargs):
    """
    Проверка конфликтов прав собственности перед сохранением.
    """
    logger.debug(f"Pre-save signal for Ownership: owner={instance.owner_id}, plot={instance.land_plot_id}")
    
    try:
        # Проверка, не превышает ли сумма долей 1
        if instance.share != '1/1':
            try:
                numerator, denominator = map(int, instance.share.split('/'))
                if numerator > denominator:
                    logger.warning(f"Invalid share: {instance.share} for ownership {instance.id}")
                    raise ValueError(f"Доля {instance.share} не может превышать 1 (числитель не должен быть больше знаменателя)")
            except (ValueError, AttributeError):
                pass
                
    except Exception as e:
        logger.error(f"Error in check_ownership_conflicts: {e}")
        raise


@receiver(pre_delete, sender=Owner)
def check_owner_dependencies(sender, instance, **kwargs):
    """
    Проверка зависимостей перед удалением владельца.
    """
    logger.info(f"Pre-delete signal for Owner {instance.id}: {instance.full_name}")
    
    try:
        # Проверка наличия активных начислений
        try:
            from payments.models import Assessment
            active_assessments = Assessment.objects.filter(
                owner=instance,
                status__in=['pending', 'partial', 'overdue']
            ).count()
            
            if active_assessments > 0:
                logger.warning(f"Cannot delete owner {instance.id} - has {active_assessments} active assessments")
                raise Exception(f"Невозможно удалить владельца с {active_assessments} неоплаченными начислениями")
        except ImportError:
            logger.debug("Payments app not installed, skipping assessment check")
            
        # Проверка активных контактов
        active_contacts = instance.contacts.filter(is_active=True).count()
        if active_contacts > 0:
            logger.info(f"Owner {instance.id} has {active_contacts} active contacts that will be deleted")
            
        # Проверка прав собственности
        ownerships_count = instance.ownerships.count()
        if ownerships_count > 0:
            logger.info(f"Owner {instance.id} has {ownerships_count} ownership records that will be deleted")
            
        # Проверка членств в организациях
        memberships_count = instance.memberships.count()
        if memberships_count > 0:
            logger.info(f"Owner {instance.id} has {memberships_count} organization memberships that will be deleted")
            
    except Exception as e:
        logger.error(f"Error in check_owner_dependencies: {e}")
        raise


@receiver(post_save, sender=OrganizationMembership)
def log_membership_changes(sender, instance, created, **kwargs):
    """
    Логирование изменений членства в организации.
    """
    logger.info(f"Membership signal: owner={instance.owner_id}, org={instance.organization_id}, created={created}")
    
    try:
        if created:
            logger.info(f"New membership: Owner {instance.owner.full_name} joined organization {instance.organization.name}")
        else:
            # Проверка изменений статуса
            if hasattr(instance, 'tracker'):
                if instance.tracker.has_changed('status'):
                    old_status = instance.tracker.previous('status')
                    new_status = instance.status
                    logger.info(f"Membership {instance.id} status changed: {old_status} -> {new_status}")
                    
    except Exception as e:
        logger.error(f"Error in log_membership_changes: {e}")


# Middleware для установки request в сигналы
class RequestSignalMiddleware:
    """
    Middleware для сохранения request в thread-local storage для доступа в сигналах.
    """
    
    def __init__(self, get_response):
        self.get_response = get_response
        
    def __call__(self, request):
        # Сохраняем request перед обработкой
        RequestStorage.set_request(request)
        
        # Добавляем request в текущий поток для обратной совместимости
        threading.current_thread().request = request
        
        try:
            response = self.get_response(request)
            return response
        finally:
            # Очищаем после обработки
            RequestStorage.clear()
            if hasattr(threading.current_thread(), 'request'):
                del threading.current_thread().request


# Функция для ручного создания членства (если автоматическое не сработало)
def create_membership_manually(owner_id, organization_id):
    """
    Ручное создание членства в организации.
    
    Args:
        owner_id: ID владельца
        organization_id: ID организации
        
    Returns:
        bool: успех операции
    """
    logger.info(f"Manually creating membership: owner={owner_id}, organization={organization_id}")
    
    try:
        owner = Owner.objects.get(id=owner_id)
        from organizations.models import Organization
        organization = Organization.objects.get(id=organization_id)
        
        membership, created = OrganizationMembership.objects.get_or_create(
            owner=owner,
            organization=organization,
            defaults={'status': 'active', 'joined_at': timezone.now()}
        )
        
        if created:
            logger.info(f"Membership created manually: ID={membership.id}")
        else:
            logger.info(f"Membership already exists: ID={membership.id}")
            
        return True
        
    except Owner.DoesNotExist:
        logger.error(f"Owner {owner_id} not found")
        return False
    except Exception as e:
        logger.error(f"Error creating membership manually: {e}\n{traceback.format_exc()}")
        return False