from django.core.cache import cache
from django.core.management import call_command
import logging

logger = logging.getLogger(__name__)


def clear_all_cache():
    """Очистка всего кэша"""
    cache.clear()
    logger.warning("All cache cleared")


def clear_api_cache():
    """Очистка API кэша"""
    cache.delete_pattern('api:*')
    logger.info("API cache cleared")


def clear_template_cache():
    """Очистка кэша шаблонов"""
    cache.delete_pattern('template:*')
    logger.info("Template cache cleared")


def get_cache_stats():
    """Получение статистики кэша"""
    try:
        # Для Redis кэша
        from django.core.cache import caches
        redis_cache = caches['redis']
        info = redis_cache.info()
        return {
            'total_keys': info.get('keyspace_hits', 0) + info.get('keyspace_misses', 0),
            'hits': info.get('keyspace_hits', 0),
            'misses': info.get('keyspace_misses', 0),
            'hit_rate': info.get('keyspace_hits', 0) / max(1, info.get('keyspace_hits', 0) + info.get('keyspace_misses', 0)) * 100,
            'used_memory': info.get('used_memory_human', 'N/A'),
        }
    except:
        return {
            'total_keys': 0,
            'hits': 0,
            'misses': 0,
            'hit_rate': 0,
            'used_memory': 'N/A',
        }


class CacheWarmer:
    """
    Класс для прогрева кэша.
    """
    
    def warm_land_plots_cache(self, organization_ids=None):
        """Прогрев кэша для участков"""
        from land.models import LandPlot
        
        logger.info("Warming land plots cache...")
        
        if organization_ids is None:
            from organizations.models import Organization
            organization_ids = Organization.objects.values_list('id', flat=True)
        
        for org_id in organization_ids:
            # Прогреваем список
            LandPlot.objects.filter(organization_id=org_id).count()
            
            # Прогреваем статистику
            LandPlot.objects.get_stats_cached(org_id)
            
            # Прогреваем геоданные
            cache_key = f'api:plots_geo:org_{org_id}'
            cache.get(cache_key)
        
        logger.info(f"Cache warmed for {len(organization_ids)} organizations")