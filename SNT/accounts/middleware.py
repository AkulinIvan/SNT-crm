import logging
import traceback
from datetime import datetime
from django.core.cache import cache
from django.utils import timezone
from django.utils.deprecation import MiddlewareMixin
from django.db import DatabaseError

logger = logging.getLogger(__name__)


class UserActivityMiddleware(MiddlewareMixin):
    """
    Middleware для отслеживания активности пользователя.
    Обновляет last_activity пользователя каждые 5 минут.
    """
    
    def __init__(self, get_response=None):
        super().__init__(get_response)
        self.logger = logging.getLogger(f'{__name__}.UserActivityMiddleware')
    
    def process_view(self, request, view_func, view_args, view_kwargs):
        try:
            if request.user.is_authenticated:
                if hasattr(request.user, 'update_activity'):
                    now = timezone.now()
                    last_activity = getattr(request.user, 'last_activity', None)
                    
                    # Обновляем активность если прошло более 5 минут
                    if not last_activity or (now - last_activity).seconds > 300:
                        try:
                            request.user.update_activity()
                            self.logger.debug(f"Обновлена активность пользователя {request.user.username}")
                        except DatabaseError as e:
                            self.logger.error(f"Ошибка базы данных при обновлении активности: {e}")
                        except Exception as e:
                            self.logger.error(f"Ошибка при обновлении активности: {e}")
        except Exception as e:
            self.logger.error(f"Критическая ошибка в UserActivityMiddleware: {e}", exc_info=True)
        
        return None


class OrganizationMiddleware(MiddlewareMixin):
    """
    Middleware для автоматической фильтрации запросов по организации.
    Определяет текущую организацию пользователя и кэширует результат на 60 секунд.
    """
    
    def __init__(self, get_response=None):
        super().__init__(get_response)
        self.logger = logging.getLogger(f'{__name__}.OrganizationMiddleware')
        self._cache_prefix = 'org_middleware'
    
    def process_request(self, request):
        """Определяем текущую организацию пользователя"""
        try:
            # Быстрый выход для неавторизованных
            if not request.user.is_authenticated:
                request.current_organization = None
                return None
            
            # Для суперпользователей и админов - не фильтруем
            if request.user.is_superuser or getattr(request.user, 'is_admin', False):
                request.current_organization = None
                return None
            
            # Проверяем кэш (только для GET запросов)
            cache_key = f"{self._cache_prefix}:{request.user.id}"
            if request.method == 'GET':
                cached_org_id = cache.get(cache_key)
                if cached_org_id is not None:
                    if cached_org_id == '__none__':
                        request.current_organization = None
                    else:
                        from organizations.models import Organization
                        try:
                            request.current_organization = Organization.objects.only('id', 'short_name').get(id=cached_org_id)
                        except Organization.DoesNotExist:
                            cache.delete(cache_key)
                            request.current_organization = self._find_organization(request)
                    return None
            
            # Определяем организацию
            request.current_organization = self._find_organization(request)
            
            # Кэшируем результат на 60 секунд
            if request.current_organization:
                cache.set(cache_key, request.current_organization.id, 60)
            else:
                cache.set(cache_key, '__none__', 60)
                
        except DatabaseError as e:
            self.logger.error(f"DB error in OrganizationMiddleware: {e}", exc_info=True)
            request.current_organization = None
        except Exception as e:
            self.logger.error(f"Critical error in OrganizationMiddleware: {e}", exc_info=True)
            request.current_organization = None
        
        return None
    
    def _find_organization(self, request):
        """Поиск организации пользователя (без дублирования логов)"""
        user = request.user
        
        # Способ 1: Основная организация пользователя (самый быстрый)
        if user.organization_id:
            return user.organization
        
        # Способ 2: Организация через назначения сотрудников
        org = self._get_from_staff_assignment(user)
        if org:
            return org
        
        # Способ 3: Организация через членство владельца
        org = self._get_from_membership(user)
        if org:
            return org
        
        # Способ 4: Председатель организации
        org = self._get_as_chairman(user)
        if org:
            return org
        
        # Способ 5: Бухгалтер организации
        org = self._get_as_accountant(user)
        if org:
            return org
        
        # Организация не найдена
        self.logger.debug(f"No organization found for user: {user.username} (ID: {user.id})")
        return None
    
    def _get_from_staff_assignment(self, user):
        """Получить организацию через назначения сотрудников"""
        try:
            from organizations.models import OrganizationStaffAssignment
            assignment = OrganizationStaffAssignment.objects.filter(
                user=user,
                is_active=True
            ).select_related('organization').only(
                'organization__id', 
                'organization__short_name'
            ).first()
            
            if assignment:
                return assignment.organization
        except Exception as e:
            self.logger.error(f"Error checking staff assignments for user {user.id}: {e}")
        return None
    
    def _get_from_membership(self, user):
        """Получить организацию через членство владельца"""
        try:
            owner = None
            
            if hasattr(user, 'owner_profile'):
                owner = user.owner_profile
            
            if not owner and user.email:
                try:
                    from users.models import Owner, ContactInfo
                    contact = ContactInfo.objects.filter(
                        type='em',
                        value=user.email,
                        is_active=True
                    ).only('owner_id').first()
                    if contact:
                        owner = contact.owner
                except Exception:
                    pass
            
            if not owner and user.phone:
                try:
                    from users.models import Owner, ContactInfo
                    contact = ContactInfo.objects.filter(
                        type='ph',
                        value=user.phone,
                        is_active=True
                    ).only('owner_id').first()
                    if contact:
                        owner = contact.owner
                except Exception:
                    pass
            
            if owner:
                from organizations.models import OrganizationMembership
                membership = OrganizationMembership.objects.filter(
                    owner=owner,
                    status='active'
                ).select_related('organization').only(
                    'organization__id',
                    'organization__short_name'
                ).first()
                
                if membership:
                    return membership.organization
        except Exception as e:
            self.logger.error(f"Error checking memberships for user {user.id}: {e}")
        return None
    
    def _get_as_chairman(self, user):
        """Получить организацию как председатель"""
        try:
            from organizations.models import Organization
            org = Organization.objects.filter(
                chairman=user,
                is_active=True
            ).only('id', 'short_name').first()
            
            if org:
                return org
        except Exception as e:
            self.logger.error(f"Error checking chairman for user {user.id}: {e}")
        return None
    
    def _get_as_accountant(self, user):
        """Получить организацию как бухгалтер"""
        try:
            from organizations.models import Organization
            org = Organization.objects.filter(
                accountant=user,
                is_active=True
            ).only('id', 'short_name').first()
            
            if org:
                return org
        except Exception as e:
            self.logger.error(f"Error checking accountant for user {user.id}: {e}")
        return None
    
    def process_view(self, request, view_func, view_args, view_kwargs):
        """Фильтрация queryset по организации (опционально)"""
        try:
            if not request.user.is_authenticated:
                return None
            
            if request.user.is_superuser or getattr(request.user, 'is_admin', False):
                return None
            
            if not request.current_organization:
                # Логируем только при проблемах
                if hasattr(request.user, 'role') and request.user.role in ['manager', 'accountant']:
                    self.logger.warning(
                        f"User {request.user.username} (role={request.user.role}) has no organization for path: {request.path}"
                    )
                return None
        except Exception as e:
            self.logger.error(f"Error in process_view: {e}", exc_info=True)
        
        return None


class RequestLoggingMiddleware(MiddlewareMixin):
    """
    Middleware для логирования всех запросов.
    """
    
    def __init__(self, get_response=None):
        super().__init__(get_response)
        self.logger = logging.getLogger(f'{__name__}.RequestLogging')
    
    def process_request(self, request):
        """Логирование входящего запроса"""
        try:
            request.start_time = datetime.now()
            
            # Логируем основные параметры запроса
            log_data = {
                'method': request.method,
                'path': request.path,
                'user': request.user.username if request.user.is_authenticated else 'anonymous',
                'ip': self._get_client_ip(request),
                'user_agent': request.META.get('HTTP_USER_AGENT', '')[:200],
            }
            
            if request.method in ['POST', 'PUT', 'PATCH']:
                # Для POST/PUT запросов логируем тело (без паролей)
                body = request.POST.copy() if hasattr(request, 'POST') else {}
                if 'password' in body:
                    body['password'] = '***'
                log_data['body'] = dict(body)
            
            self.logger.info(f"→ {log_data['method']} {log_data['path']} | User: {log_data['user']} | IP: {log_data['ip']}")
            
        except Exception as e:
            self.logger.error(f"Ошибка при логировании запроса: {e}")
        
        return None
    
    def process_response(self, request, response):
        """Логирование ответа"""
        try:
            if hasattr(request, 'start_time'):
                elapsed = (datetime.now() - request.start_time).total_seconds()
                status_code = response.status_code
                
                log_level = 'error' if status_code >= 500 else 'warning' if status_code >= 400 else 'info'
                
                log_func = getattr(self.logger, log_level)
                log_func(
                    f"← {request.method} {request.path} | "
                    f"Status: {status_code} | "
                    f"Time: {elapsed:.3f}s | "
                    f"User: {request.user.username if request.user.is_authenticated else 'anonymous'}"
                )
                
                # Детальное логирование ошибок
                if status_code >= 400:
                    self.logger.warning(
                        f"Response content for {request.path} [{status_code}]: "
                        f"{getattr(response, 'content', b'')[:500]}"
                    )
                    
        except Exception as e:
            self.logger.error(f"Ошибка при логировании ответа: {e}")
        
        return response
    
    def process_exception(self, request, exception):
        """Логирование исключений"""
        self.logger.error(
            f"! EXCEPTION in {request.method} {request.path}: "
            f"{exception.__class__.__name__}: {exception}\n"
            f"Traceback:\n{traceback.format_exc()}",
            exc_info=True
        )
        return None
    
    def _get_client_ip(self, request):
        """Безопасное получение IP"""
        try:
            x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
            if x_forwarded_for:
                return x_forwarded_for.split(',')[0].strip()
            return request.META.get('REMOTE_ADDR', '0.0.0.0')
        except Exception:
            return '0.0.0.0'