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