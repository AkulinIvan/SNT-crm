# SNT/common/middleware.py
import threading
import logging
import re
from datetime import datetime
from ipaddress import ip_address, ip_network
from django.http import JsonResponse
from django.utils.deprecation import MiddlewareMixin
from django.core.cache import cache
from django.conf import settings
from django.urls import resolve

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


class APIAccessLoggingMiddleware(MiddlewareMixin):
    """
    Middleware для логирования всех API запросов
    """
    
    def process_request(self, request):
        if request.path.startswith('/api/'):
            # Логируем начало запроса
            logger.info(f"API Request START: {request.method} {request.path} from {self._get_client_ip(request)}")
            request._api_start_time = datetime.now()
        return None
    
    def process_response(self, request, response):
        if hasattr(request, '_api_start_time') and request.path.startswith('/api/'):
            duration = (datetime.now() - request._api_start_time).total_seconds()
            logger.info(
                f"API Request END: {request.method} {request.path} "
                f"- Status: {response.status_code} - Duration: {duration:.3f}s"
            )
        return response
    
    def _get_client_ip(self, request):
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            return x_forwarded_for.split(',')[0].strip()
        return request.META.get('REMOTE_ADDR', '0.0.0.0')


class APIRateLimitMiddleware(MiddlewareMixin):
    """
    Middleware для ограничения частоты запросов к API
    """
    
    # Настройки лимитов (запросов в минуту)
    RATE_LIMITS = {
        'default': 90,           # 90 запросов в минуту по умолчанию
        'auth': 10,              # 10 попыток входа в минуту
        'create': 30,            # 30 созданий в минуту
        'export': 5,             # 5 экспортов в минуту
        'import': 3,             # 3 импорта в минуту
        'payment': 20,           # 20 платежных операций в минуту
        'voting': 20,            # 20 голосований в минуту
    }
    
    # Эндпоинты, которые НЕ ограничиваем
    EXCLUDED_PATHS = [
        '/api/auth/login/',
        '/api/auth/register/',
        '/api/health/',
    ]
    
    def process_request(self, request):
        if not request.path.startswith('/api/'):
            return None
        
        # Проверяем исключения
        if any(request.path.startswith(path) for path in self.EXCLUDED_PATHS):
            return None
        
        # Определяем тип лимита
        limit_type = self._get_limit_type(request)
        limit = self.RATE_LIMITS.get(limit_type, self.RATE_LIMITS['default'])
        
        # Ключ для кэша (по IP или по пользователю)
        if request.user.is_authenticated:
            client_id = f"user_{request.user.id}"
        else:
            client_id = f"ip_{self._get_client_ip(request)}"
        
        cache_key = f'api_rate_{limit_type}_{client_id}'
        
        # Получаем текущее количество запросов
        requests_count = cache.get(cache_key, 0)
        
        if requests_count >= limit:
            logger.warning(f"Rate limit exceeded for {client_id} on {request.path}")
            return JsonResponse(
                {
                    'detail': f'Превышен лимит запросов. Максимум {limit} в минуту.',
                    'code': 'rate_limit_exceeded',
                    'limit': limit,
                    'retry_after': 60
                },
                status=429
            )
        
        # Увеличиваем счётчик
        cache.set(cache_key, requests_count + 1, 60)
        return None
    
    def _get_limit_type(self, request):
        """Определение типа лимита по URL"""
        path = request.path
        
        if '/auth/login' in path or '/auth/register' in path:
            return 'auth'
        elif '/export' in path:
            return 'export'
        elif '/import' in path or '/import-excel' in path:
            return 'import'
        elif '/payment' in path or '/pay' in path:
            return 'payment'
        elif '/voting' in path or '/vote' in path:
            return 'voting'
        elif request.method in ['POST', 'PUT', 'PATCH']:
            return 'create'
        else:
            return 'default'
    
    def _get_client_ip(self, request):
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            return x_forwarded_for.split(',')[0].strip()
        return request.META.get('REMOTE_ADDR', '0.0.0.0')


class APIIPAccessMiddleware(MiddlewareMixin):
    """
    Middleware для ограничения доступа к API по IP адресу
    """
    
    def get_blocked_ips(self):
        """Получение списка заблокированных IP из кэша или settings"""
        # Пытаемся получить из кэша (динамические блокировки)
        blocked = cache.get('blocked_api_ips')
        if blocked is not None:
            return blocked
        
        # Fallback на settings
        from django.conf import settings
        return getattr(settings, 'BLOCKED_API_IPS', [])
    
    def get_allowed_ips(self):
        """Получение списка разрешённых IP из кэша или settings"""
        allowed = cache.get('allowed_api_ips')
        if allowed is not None:
            return allowed
        
        from django.conf import settings
        return getattr(settings, 'ALLOWED_API_IPS', [])
    
    def process_request(self, request):
        if not request.path.startswith('/api/'):
            return None
        
        # Разрешаем health check всегда
        if request.path == '/api/health/':
            return None
        
        client_ip = self._get_client_ip(request)
        
        # Получаем актуальные списки
        blocked_ips = self.get_blocked_ips()
        allowed_ips = self.get_allowed_ips()
        
        # Проверка чёрного списка
        if self._is_ip_blocked(client_ip, blocked_ips):
            logger.warning(f"Blocked IP attempt: {client_ip} tried to access {request.path}")
            return JsonResponse(
                {'detail': 'Доступ запрещён. Ваш IP заблокирован.', 'code': 'ip_blocked'},
                status=403
            )
        
        # Проверка белого списка (если он не пустой)
        if allowed_ips and not self._is_ip_allowed(client_ip, allowed_ips):
            logger.warning(f"IP not allowed: {client_ip} tried to access {request.path}")
            return JsonResponse(
                {'detail': 'Доступ с вашего IP запрещён. Обратитесь к администратору.', 'code': 'ip_not_allowed'},
                status=403
            )
        
        return None
    
    def _is_ip_allowed(self, ip, allowed_ips):
        """Проверка, разрешён ли IP"""
        try:
            ip_obj = ip_address(ip)
            for allowed in allowed_ips:
                if '/' in allowed:
                    if ip_obj in ip_network(allowed, strict=False):
                        return True
                elif ip == allowed:
                    return True
            return False
        except Exception as e:
            logger.error(f"Error checking IP allow: {e}")
            return False
    
    def _is_ip_blocked(self, ip, blocked_ips):
        """Проверка, заблокирован ли IP"""
        try:
            ip_obj = ip_address(ip)
            for blocked in blocked_ips:
                if '/' in blocked:
                    if ip_obj in ip_network(blocked, strict=False):
                        return True
                elif ip == blocked:
                    return True
            return False
        except Exception as e:
            logger.error(f"Error checking IP block: {e}")
            return False
    
    def _get_client_ip(self, request):
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            return x_forwarded_for.split(',')[0].strip()
        return request.META.get('REMOTE_ADDR', '0.0.0.0')


class APITimeRestrictionMiddleware(MiddlewareMixin):
    """
    Middleware для ограничения доступа к API по времени
    """
    
    # Рабочие часы (можно настроить для разных эндпоинтов)
    TIME_RESTRICTIONS = {
        'default': {'start': 6, 'end': 23},  # 6:00 - 23:00
        'export': {'start': 8, 'end': 20},   # 8:00 - 20:00 для экспорта
        'import': {'start': 8, 'end': 18},   # 8:00 - 18:00 для импорта
        'critical': {'start': 0, 'end': 24},  # круглосуточно для критических
    }
    
    # Исключения (эндпоинты, доступные всегда)
    ALWAYS_ALLOWED = [
        '/api/health/',
        '/api/auth/login/',
        '/api/auth/register/',
        '/api/auth/me/',
        '/api/quick-payment/verify/',
    ]
    
    def process_request(self, request):
        if not request.path.startswith('/api/'):
            return None
        
        # Проверяем исключения
        if any(request.path.startswith(path) for path in self.ALWAYS_ALLOWED):
            return None
        
        # Определяем тип ограничения
        restriction_type = self._get_restriction_type(request)
        hours = self.TIME_RESTRICTIONS.get(restriction_type, self.TIME_RESTRICTIONS['default'])
        
        current_hour = datetime.now().hour
        
        if current_hour < hours['start'] or current_hour >= hours['end']:
            return JsonResponse(
                {
                    'detail': f'API доступно с {hours["start"]}:00 до {hours["end"]}:00',
                    'code': 'api_offline',
                    'available_from': hours['start'],
                    'available_until': hours['end']
                },
                status=403
            )
        
        return None
    
    def _get_restriction_type(self, request):
        """Определение типа ограничения по URL"""
        if '/export' in request.path:
            return 'export'
        if '/import' in request.path:
            return 'import'
        if '/payment' in request.path or '/pay' in request.path:
            return 'critical'
        return 'default'


class TariffLimitMiddleware(MiddlewareMixin):
    """
    Middleware для проверки лимитов тарифа при POST/PUT запросах
    """
    
    # URL patterns, которые требуют проверки лимитов
    CHECK_PATHS = [
        '/api/owners/',
        '/api/plots/',
        '/api/users/',
    ]
    
    # Типы ресурсов для разных URL
    RESOURCE_TYPES = {
        '/api/owners/': 'owners',
        '/api/plots/': 'plots',
        '/api/users/': 'users',
    }
    
    def process_request(self, request):
        # Проверяем только API запросы
        if not request.path.startswith('/api/'):
            return None
        
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
                logger.warning(
                    f"Tariff limit reached for {organization.short_name}: "
                    f"{resource_type} {current}/{max_limit}"
                )
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
            logger.error(f"Error in TariffLimitMiddleware: {e}")
        
        return None


class APIMaintenanceMiddleware(MiddlewareMixin):
    """
    Middleware для режима обслуживания API
    """
    
    def process_request(self, request):
        if not request.path.startswith('/api/'):
            return None
        
        # Используем settings для хранения статуса (более надёжно)
        # В production используйте БД или Redis
        from django.conf import settings
        
        # Проверяем через settings или cache с fallback
        maintenance_mode = getattr(settings, 'API_MAINTENANCE_MODE', False)
        
        # Также проверяем cache (для динамического изменения)
        cache_mode = cache.get('api_maintenance_mode')
        if cache_mode is not None:
            maintenance_mode = cache_mode
        
        if maintenance_mode:
            # Разрешаем доступ только админам и суперпользователям
            is_admin = (
                request.user.is_authenticated and 
                (request.user.is_superuser or request.user.is_admin or 
                 request.user.role == 'admin')
            )
            
            if not is_admin:
                logger.warning(f"Maintenance mode: Blocked request from {request.user.username if request.user.is_authenticated else 'anonymous'}")
                return JsonResponse(
                    {
                        'detail': 'API на техническом обслуживании. Пожалуйста, попробуйте позже.',
                        'code': 'maintenance_mode',
                        'estimated_time': '30 минут'
                    },
                    status=503
                )
        
        return None


class APIRequestSizeMiddleware(MiddlewareMixin):
    """
    Middleware для ограничения размера запроса
    """
    
    MAX_BODY_SIZE = 10 * 1024 * 1024  # 10 МБ для обычных запросов
    MAX_BODY_SIZE_IMPORT = 50 * 1024 * 1024  # 50 МБ для импорта
    
    def process_request(self, request):
        if not request.path.startswith('/api/'):
            return None
        
        # Проверяем размер запроса
        if request.method in ['POST', 'PUT', 'PATCH']:
            content_length = request.META.get('CONTENT_LENGTH')
            
            if content_length:
                content_length = int(content_length)
                
                # Для импорта - больший лимит
                if '/import' in request.path or '/import-excel' in request.path:
                    if content_length > self.MAX_BODY_SIZE_IMPORT:
                        return JsonResponse(
                            {
                                'detail': f'Размер файла не должен превышать {self.MAX_BODY_SIZE_IMPORT // (1024*1024)} МБ',
                                'code': 'request_too_large'
                            },
                            status=413
                        )
                else:
                    if content_length > self.MAX_BODY_SIZE:
                        return JsonResponse(
                            {
                                'detail': f'Размер запроса не должен превышать {self.MAX_BODY_SIZE // (1024*1024)} МБ',
                                'code': 'request_too_large'
                            },
                            status=413
                        )
        
        return None


class APISecurityHeadersMiddleware(MiddlewareMixin):
    """
    Middleware для добавления security headers ко всем API ответам
    """
    
    def process_response(self, request, response):
        if request.path.startswith('/api/'):
            # Защита от XSS
            response['X-XSS-Protection'] = '1; mode=block'
            # Защита от clickjacking
            response['X-Frame-Options'] = 'DENY'
            # Защита от MIME type sniffing
            response['X-Content-Type-Options'] = 'nosniff'
            # Referrer policy
            response['Referrer-Policy'] = 'strict-origin-when-cross-origin'
            
            # CORS headers (если API используется с других доменов)
            if hasattr(settings, 'CORS_ALLOWED_ORIGINS'):
                origin = request.META.get('HTTP_ORIGIN')
                if origin in settings.CORS_ALLOWED_ORIGINS:
                    response['Access-Control-Allow-Origin'] = origin
                    response['Access-Control-Allow-Credentials'] = 'true'
                    response['Access-Control-Allow-Methods'] = 'GET, POST, PUT, PATCH, DELETE, OPTIONS'
                    response['Access-Control-Allow-Headers'] = 'Content-Type, Authorization, X-CSRFToken, X-API-Key'
        
        return response


class APICSRFProtectionMiddleware(MiddlewareMixin):
    """
    Middleware для защиты API от CSRF атак
    """
    
    # Эндпоинты, которые не требуют CSRF токен
    EXCLUDED_PATHS = [
        '/api/auth/login/',
        '/api/auth/register/',
        '/api/auth/logout/',
        '/api/quick-payment/verify/',
        '/api/quick-payment/match-payment/',
        '/api/health/',
        '/api/test-rate-limit/',
        '/api/security/maintenance/toggle/',  # Добавляем security endpoints
        '/api/security/ip/block/',            # Добавляем
        '/api/security/ip/unblock/',          # Добавляем
        '/api/security/ip/list/',             # Добавляем
        '/api/security/stats/',               # Добавляем
    ]
    
    # GET, HEAD, OPTIONS методы всегда разрешены без CSRF
    SAFE_METHODS = ['GET', 'HEAD', 'OPTIONS']
    
    def process_request(self, request):
        if not request.path.startswith('/api/'):
            return None
        
        # SAFE методы (GET, HEAD, OPTIONS) не требуют CSRF
        if request.method in self.SAFE_METHODS:
            return None
        
        # Проверяем, не исключён ли путь
        for excluded_path in self.EXCLUDED_PATHS:
            if request.path == excluded_path or request.path.startswith(excluded_path):
                return None
        
        # Для остальных POST/PUT/PATCH/DELETE требуем CSRF токен
        csrf_token = request.META.get('HTTP_X_CSRFTOKEN')
        
        if not csrf_token:
            logger.warning(f"CSRF token missing for {request.path}")
            return JsonResponse(
                {'detail': 'CSRF токен отсутствует', 'code': 'csrf_token_missing'},
                status=403
            )
        
        # Проверяем CSRF токен
        from django.middleware.csrf import _compare_salted_tokens
        request_csrf_token = request.COOKIES.get('csrftoken', '')
        
        if not _compare_salted_tokens(request_csrf_token, csrf_token):
            logger.warning(f"Invalid CSRF token for {request.path}")
            return JsonResponse(
                {'detail': 'Неверный CSRF токен', 'code': 'csrf_token_invalid'},
                status=403
            )
        
        return None