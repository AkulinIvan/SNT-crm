import logging

from django.utils import timezone
from django.utils.deprecation import MiddlewareMixin

logger = logging.getLogger(__name__)

class UserActivityMiddleware(MiddlewareMixin):
    """
    Middleware для отслеживания активности пользователя.
    """
    def process_view(self, request, view_func, view_args, view_kwargs):
        if request.user.is_authenticated:
            if hasattr(request.user, 'update_activity'):
                now = timezone.now()
                last_activity = getattr(request.user, 'last_activity', None)
                if not last_activity or (now - last_activity).seconds > 300:
                    request.user.update_activity()
        return None


class OrganizationMiddleware(MiddlewareMixin):
    """
    Middleware для автоматической фильтрации запросов по организации.
    Определяет текущую организацию пользователя.
    """
    
    def process_request(self, request):
        """Определяем текущую организацию пользователя"""
        if not request.user.is_authenticated:
            request.current_organization = None
            return None
        
        # Для суперпользователей и админов - не фильтруем по организации
        if request.user.is_superuser or getattr(request.user, 'is_admin', False):
            request.current_organization = None
            logger.debug(f"[OrganizationMiddleware] Admin user '{request.user}' - showing all organizations")
            return None
        
        organization = None
        
        # Способ 1: через поле organization
        if hasattr(request.user, 'organization') and request.user.organization_id:
            organization = request.user.organization
            logger.debug(f"[OrganizationMiddleware] Found via user.organization: {organization.short_name}")
        
        # Способ 2: через OrganizationStaffAssignment
        if not organization:
            try:
                from organizations.models import OrganizationStaffAssignment
                assignment = OrganizationStaffAssignment.objects.filter(
                    user=request.user,
                    is_active=True
                ).select_related('organization').first()
                
                if assignment:
                    organization = assignment.organization
                    logger.debug(f"[OrganizationMiddleware] Found via staff assignment: {organization.short_name}")
            except Exception as e:
                logger.error(f"[OrganizationMiddleware] Error checking staff assignments: {e}")
        
        # Способ 3: через OrganizationMembership (если пользователь связан с Owner)
        if not organization:
            try:
                # Проверяем, есть ли у User связанная модель Owner
                owner = None
                
                # Вариант 1: через owner_profile (если есть OneToOneField)
                if hasattr(request.user, 'owner_profile'):
                    owner = request.user.owner_profile
                
                # Вариант 2: поиск Owner с таким же email или телефоном
                if not owner and request.user.email:
                    from users.models import Owner, ContactInfo
                    contact = ContactInfo.objects.filter(
                        type='em',
                        value=request.user.email,
                        is_active=True
                    ).first()
                    if contact:
                        owner = contact.owner
                
                if not owner and request.user.phone:
                    from users.models import Owner, ContactInfo
                    contact = ContactInfo.objects.filter(
                        type='ph',
                        value=request.user.phone,
                        is_active=True
                    ).first()
                    if contact:
                        owner = contact.owner
                
                if owner:
                    from organizations.models import OrganizationMembership
                    membership = OrganizationMembership.objects.filter(
                        owner=owner,
                        status='active'
                    ).select_related('organization').first()
                    
                    if membership:
                        organization = membership.organization
                        logger.debug(f"[OrganizationMiddleware] Found via membership: {organization.short_name}")
            except Exception as e:
                logger.error(f"[OrganizationMiddleware] Error checking memberships: {e}")
        
        # Способ 4: пользователь - председатель
        if not organization:
            try:
                from organizations.models import Organization
                org = Organization.objects.filter(
                    chairman=request.user,
                    is_active=True
                ).first()
                if org:
                    organization = org
                    logger.debug(f"[OrganizationMiddleware] Found via chairman: {organization.short_name}")
            except Exception as e:
                logger.error(f"[OrganizationMiddleware] Error checking chairman: {e}")
        
        # Способ 5: пользователь - бухгалтер
        if not organization:
            try:
                from organizations.models import Organization
                org = Organization.objects.filter(
                    accountant=request.user,
                    is_active=True
                ).first()
                if org:
                    organization = org
                    logger.debug(f"[OrganizationMiddleware] Found via accountant: {organization.short_name}")
            except Exception as e:
                logger.error(f"[OrganizationMiddleware] Error checking accountant: {e}")
        
        request.current_organization = organization
        
        if organization:
            logger.debug(f"[OrganizationMiddleware] User '{request.user}' -> Organization: {organization.short_name}")
        else:
            logger.debug(f"[OrganizationMiddleware] User '{request.user}' -> No organization found")
        
        return None
    
    def process_view(self, request, view_func, view_args, view_kwargs):
        """Фильтрация queryset по организации (опционально)"""
        if not request.user.is_authenticated:
            return None
        
        # Админы и суперпользователи видят всё
        if request.user.is_superuser or getattr(request.user, 'is_admin', False):
            return None
        
        # Если у пользователя нет организации - не фильтруем
        if not request.current_organization:
            return None
        
        return None