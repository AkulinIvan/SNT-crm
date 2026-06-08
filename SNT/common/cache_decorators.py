# SNT/common/cache_decorators.py
from functools import wraps
from django.core.cache import cache
from django.conf import settings
from django.utils.decorators import method_decorator
from django.views.decorators.cache import cache_page
from django.views.decorators.vary import vary_on_headers, vary_on_cookie
from rest_framework.response import Response
import hashlib
import json
import logging

logger = logging.getLogger(__name__)


def cached_api(timeout=300, key_prefix=None, vary_on_user=False, vary_on_org=True):
    """
    Декоратор для кэширования API методов.
    Кэширует данные ответа, а не сам Response объект.
    """
    def decorator(func):
        @wraps(func)
        def wrapper(self, request, *args, **kwargs):
            # Проверяем, нужно ли кэшировать
            if request.method not in ('GET', 'HEAD'):
                return func(self, request, *args, **kwargs)
            
            # Формируем ключ кэша
            key_parts = [key_prefix or func.__name__]
            
            # Добавляем пользователя если нужно
            if vary_on_user and request.user.is_authenticated:
                key_parts.append(f'user_{request.user.id}')
            
            # Добавляем организацию если нужно
            if vary_on_org and hasattr(request, 'current_organization'):
                org_id = getattr(request.current_organization, 'id', None)
                if org_id:
                    key_parts.append(f'org_{org_id}')
            
            # Добавляем параметры запроса
            query_params = dict(request.query_params)
            if query_params:
                # Сортируем для стабильности
                sorted_params = sorted(query_params.items())
                params_hash = hashlib.md5(
                    json.dumps(sorted_params, sort_keys=True).encode()
                ).hexdigest()[:8]
                key_parts.append(params_hash)
            
            # Добавляем kwargs
            if kwargs:
                kwargs_hash = hashlib.md5(
                    json.dumps(kwargs, sort_keys=True).encode()
                ).hexdigest()[:8]
                key_parts.append(kwargs_hash)
            
            cache_key = f'api:{":".join(key_parts)}'
            
            # Пытаемся получить из кэша
            cached_data = cache.get(cache_key)
            if cached_data is not None:
                logger.debug(f"Cache HIT: {cache_key}")
                # Восстанавливаем Response из кэшированных данных
                return Response(
                    data=cached_data.get('data'),
                    status=cached_data.get('status', 200),
                    headers=cached_data.get('headers', {})
                )
            
            # Выполняем функцию
            logger.debug(f"Cache MISS: {cache_key}")
            response = func(self, request, *args, **kwargs)
            
            # Кэшируем только успешные ответы
            if response and hasattr(response, 'status_code') and response.status_code == 200:
                # Извлекаем данные для кэширования
                cache_data = {
                    'data': response.data,
                    'status': response.status_code,
                    'headers': dict(response.headers) if hasattr(response, 'headers') else {}
                }
                cache.set(cache_key, cache_data, timeout)
                logger.debug(f"Cache SET: {cache_key} (timeout={timeout}s)")
            
            return response
        
        return wrapper
    return decorator


def invalidate_cache_on_change(*cache_patterns):
    """
    Декоратор для инвалидации кэша при изменении данных.
    """
    def decorator(func):
        @wraps(func)
        def wrapper(self, request, *args, **kwargs):
            response = func(self, request, *args, **kwargs)
            
            # Инвалидируем кэш если операция успешна
            if response and hasattr(response, 'status_code') and response.status_code < 400:
                for pattern in cache_patterns:
                    try:
                        # Для Redis кэша используем delete_pattern
                        if hasattr(cache, 'delete_pattern'):
                            cache.delete_pattern(pattern)
                        else:
                            # Для locmem кэша просто логируем
                            logger.info(f"Cache pattern invalidation not supported: {pattern}")
                        
                        # Удаляем точные ключи по шаблону
                        if '*' in pattern:
                            # Для простых кэшей пытаемся найти и удалить
                            base_key = pattern.replace('*', '')
                            for key in list(cache._cache.keys()):
                                if key.startswith(base_key):
                                    cache.delete(key)
                        else:
                            cache.delete(pattern)
                        
                        logger.info(f"Cache invalidated: {pattern}")
                    except Exception as e:
                        logger.warning(f"Error invalidating cache {pattern}: {e}")
            
            return response
        return wrapper
    return decorator


class CacheControlMixin:
    """
    Миксин для добавления Cache-Control заголовков.
    Не мешает кэшированию данных.
    """
    cache_timeout = 300
    
    def dispatch(self, request, *args, **kwargs):
        response = super().dispatch(request, *args, **kwargs)
        
        if request.method == 'GET':
            response['Cache-Control'] = f'public, max-age={self.cache_timeout}'
            response['Vary'] = 'Accept, Cookie'
        else:
            response['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        
        return response