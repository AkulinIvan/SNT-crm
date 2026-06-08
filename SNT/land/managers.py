from django.db import models
from django.core.cache import cache
import logging

logger = logging.getLogger(__name__)


class CachedLandPlotManager(models.Manager):
    """
    Менеджер с кэшированием часто используемых запросов.
    """
    
    def get_cached_by_id(self, plot_id, timeout=300):
        """Получение участка по ID с кэшированием"""
        cache_key = f'land_plot:{plot_id}'
        plot = cache.get(cache_key)
        
        if plot is None:
            try:
                plot = self.get(id=plot_id)
                cache.set(cache_key, plot, timeout)
                logger.debug(f"Cached land plot {plot_id}")
            except self.model.DoesNotExist:
                cache.set(cache_key, None, 60)  # Кэшируем отсутствие на 1 минуту
                return None
        
        return plot
    
    def get_cached_by_cadastral(self, cadastral_number, timeout=86400):
        """Получение участка по кадастровому номеру с долгим кэшем"""
        cache_key = f'land_plot:cadastral:{cadastral_number}'
        plot = cache.get(cache_key)
        
        if plot is None:
            plot = self.filter(cadastral_number=cadastral_number).first()
            if plot:
                cache.set(cache_key, plot, timeout)
            else:
                cache.set(cache_key, None, 3600)  # Кэшируем отсутствие на 1 час
        
        return plot
    
    def get_stats_cached(self, organization_id=None, timeout=300):
        """Получение статистики с кэшированием"""
        cache_key = f'land_plots_stats:org_{organization_id or "all"}'
        stats = cache.get(cache_key)
        
        if stats is None:
            queryset = self.all()
            if organization_id:
                queryset = queryset.filter(organization_id=organization_id)
            
            stats = {
                'total': queryset.count(),
                'active': queryset.filter(status='active').count(),
                'abandoned': queryset.filter(status='abandoned').count(),
                'disputed': queryset.filter(status='disputed').count(),
                'total_area': queryset.aggregate(total=models.Sum('area_sqm'))['total'] or 0,
                'with_coordinates': queryset.filter(
                    latitude__isnull=False, longitude__isnull=False
                ).count(),
                'with_boundaries': queryset.filter(
                    boundaries__isnull=False
                ).exclude(boundaries=[]).count(),
            }
            cache.set(cache_key, stats, timeout)
        
        return stats
    
    def invalidate_plot_cache(self, plot_id):
        """Инвалидация кэша для участка"""
        cache_keys = [
            f'land_plot:{plot_id}',
            'land_plots_stats:*',
            'api:plots_list:*',
            'api:plots_stats:*',
            'api:plots_geo:*',
        ]
        for key in cache_keys:
            cache.delete(key)
            cache.delete_pattern(key)
        logger.info(f"Invalidated cache for plot {plot_id}")