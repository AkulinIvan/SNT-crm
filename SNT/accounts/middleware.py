# accounts/middleware.py
from django.utils import timezone
from django.utils.deprecation import MiddlewareMixin


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
        if request.user.is_authenticated:
            organization = None
            
            # Способ 1: через поле organization (если есть)
            if hasattr(request.user, 'organization') and request.user.organization_id:
                organization = request.user.organization
            
            # Способ 2: через свойство current_organization
            if not organization and hasattr(request.user, 'current_organization'):
                org = request.user.current_organization
                if org:
                    organization = org
            
            # Способ 3: через OrganizationStaffAssignment (новый способ)
            if not organization:
                try:
                    from organizations.models import OrganizationStaffAssignment
                    assignment = OrganizationStaffAssignment.objects.filter(
                        user=request.user,
                        is_active=True
                    ).select_related('organization').first()
                    
                    if assignment:
                        organization = assignment.organization
                except Exception as e:
                    print(f"[OrganizationMiddleware] Error checking staff assignments: {e}")
            
            # Способ 4: через OrganizationMembership (если пользователь связан с Owner)
            # ВАЖНО: OrganizationMembership.owner - это Owner, а не User!
            # Нужно сначала найти Owner, связанного с User
            if not organization:
                try:
                    # Проверяем, есть ли у User связанная модель Owner
                    if hasattr(request.user, 'owner_profile'):
                        # Если User имеет OneToOneField к Owner
                        owner = request.user.owner_profile
                        from organizations.models import OrganizationMembership
                        membership = OrganizationMembership.objects.filter(
                            owner=owner,
                            status='active'
                        ).select_related('organization').first()
                        
                        if membership:
                            organization = membership.organization
                except Exception as e:
                    print(f"[OrganizationMiddleware] Error checking memberships: {e}")
            
            # Способ 5: пользователь - председатель (через поле chairman)
            if not organization:
                try:
                    from organizations.models import Organization
                    org = Organization.objects.filter(
                        chairman=request.user,
                        is_active=True
                    ).first()
                    if org:
                        organization = org
                except Exception as e:
                    print(f"[OrganizationMiddleware] Error checking chairman: {e}")
            
            # Способ 6: пользователь - бухгалтер
            if not organization:
                try:
                    from organizations.models import Organization
                    org = Organization.objects.filter(
                        accountant=request.user,
                        is_active=True
                    ).first()
                    if org:
                        organization = org
                except Exception as e:
                    print(f"[OrganizationMiddleware] Error checking accountant: {e}")
            
            request.current_organization = organization
            
            # Отладка
            if organization:
                print(f"[OrganizationMiddleware] User '{request.user}' -> Organization: {organization.short_name}")
            else:
                print(f"[OrganizationMiddleware] User '{request.user}' -> No organization found")
        else:
            request.current_organization = None
        
        return None
    
    def process_view(self, request, view_func, view_args, view_kwargs):
        """Фильтрация queryset по организации (опционально)"""
        # Пропускаем, если пользователь не аутентифицирован
        if not request.user.is_authenticated:
            return None
        
        # Админы и суперпользователи видят всё
        if request.user.is_superuser or getattr(request.user, 'is_admin', False):
            return None
        
        # Если у пользователя нет организации - не фильтруем (пусть видят что могут)
        if not request.current_organization:
            return None
        
        return None