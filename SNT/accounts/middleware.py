# SNT/accounts/middleware.py
from django.utils import timezone
from django.utils.deprecation import MiddlewareMixin


class UserActivityMiddleware(MiddlewareMixin):
    """
    Middleware для отслеживания активности пользователя.
    """

    def process_view(self, request, view_func, view_args, view_kwargs):
        if request.user.is_authenticated:
            # Проверяем, что у пользователя есть метод update_activity
            if hasattr(request.user, 'update_activity'):
                now = timezone.now()
                last_activity = getattr(request.user, 'last_activity', None)

                # Обновляем время активности раз в 5 минут
                if not last_activity or (now - last_activity).seconds > 300:
                    request.user.update_activity()

        return None
    
    
class OrganizationMiddleware(MiddlewareMixin):
    """
    Middleware для автоматической фильтрации запросов по организации.
    """
    
    def process_request(self, request):
        # Сохраняем организацию пользователя в request
        if request.user.is_authenticated:
            if hasattr(request.user, 'organization') and request.user.organization:
                request.current_organization = request.user.organization
            else:
                request.current_organization = None
        else:
            request.current_organization = None
        
        return None
    
    def process_view(self, request, view_func, view_args, view_kwargs):
        # Для списка пользователей - показываем только из своей организации
        if request.user.is_authenticated and not request.user.is_superuser and not request.user.is_admin:
            if hasattr(view_func, 'view_class') and hasattr(view_func.view_class, 'queryset'):
                if view_func.view_class.queryset.model.__name__ == 'User':
                    if hasattr(view_func.view_class, 'queryset'):
                        view_func.view_class.queryset = view_func.view_class.queryset.filter(
                            organization=request.current_organization
                        )
        return None