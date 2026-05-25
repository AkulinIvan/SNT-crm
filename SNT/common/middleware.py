import threading
import logging
from django.http import JsonResponse
from django.utils.deprecation import MiddlewareMixin

logger = logging.getLogger(__name__)

class RequestMiddleware:
    """
    Middleware для сохранения request в текущем потоке.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Сохраняем request в текущем потоке
        threading.current_thread().request = request
        response = self.get_response(request)
        # Очищаем после обработки
        if hasattr(threading.current_thread(), 'request'):
            del threading.current_thread().request
        return response
    
    
class TariffLimitMiddleware(MiddlewareMixin):
    """
    Middleware для проверки лимитов тарифа при POST/PUT запросах
    """
    
    # URL patterns, которые требуют проверки лимитов
    CHECK_PATHS = [
        '/api/owners/',
        '/api/plots/',
    ]
    
    # Типы ресурсов для разных URL
    RESOURCE_TYPES = {
        '/api/owners/': 'owners',
        '/api/plots/': 'plots',
    }
    
    def process_request(self, request):
        # Проверяем только POST и PUT запросы
        if request.method not in ['POST', 'PUT']:
            return None
        
        # Получаем текущий путь
        path = request.path
        
        # Проверяем, нужно ли проверять лимиты для этого пути
        resource_type = None
        for check_path, res_type in self.RESOURCE_TYPES.items():
            if path.startswith(check_path):
                resource_type = res_type
                break
        
        if not resource_type:
            return None
        
        # Получаем организацию из запроса
        organization = getattr(request, 'current_organization', None)
        
        if not organization:
            return None
        
        # Проверяем лимиты
        try:
            is_allowed, current, max_limit, message = organization.check_tariff_limit(resource_type)
            
            if not is_allowed:
                return JsonResponse(
                    {
                        'detail': message,
                        'code': 'tariff_limit_reached',
                        'current': current,
                        'max': max_limit,
                        'resource_type': resource_type,
                    },
                    status=403
                )
        except Exception as e:
            logger.error(f"Ошибка в TariffLimitMiddleware: {e}")
        
        return None