# SNT/common/api_security_manager.py
from django.core.cache import cache
from django.conf import settings
import logging

logger = logging.getLogger(__name__)

class APISecurityManager:
    """
    Менеджер для управления настройками безопасности API
    """
    
    @classmethod
    def set_maintenance_mode(cls, enabled, user=None):
        """Включить/выключить режим обслуживания"""
        cache.set('api_maintenance_mode', enabled, timeout=None)
        
        if enabled:
            logger.warning(f"API maintenance mode ENABLED by {user.username if user else 'system'}")
        else:
            logger.info(f"API maintenance mode DISABLED by {user.username if user else 'system'}")
        
        return {'maintenance_mode': enabled}
    
    @classmethod
    def get_maintenance_mode(cls):
        """Получить статус режима обслуживания"""
        return cache.get('api_maintenance_mode', False)
    
    @classmethod
    def block_ip(cls, ip, user=None):
        """Заблокировать IP адрес"""
        blocked_ips = list(settings.BLOCKED_API_IPS)
        if ip not in blocked_ips:
            blocked_ips.append(ip)
            # Сохраняем в settings (в production используйте БД)
            logger.warning(f"IP {ip} BLOCKED by {user.username if user else 'system'}")
        return {'blocked_ip': ip}
    
    @classmethod
    def unblock_ip(cls, ip, user=None):
        """Разблокировать IP адрес"""
        blocked_ips = list(settings.BLOCKED_API_IPS)
        if ip in blocked_ips:
            blocked_ips.remove(ip)
            logger.info(f"IP {ip} UNBLOCKED by {user.username if user else 'system'}")
        return {'unblocked_ip': ip}