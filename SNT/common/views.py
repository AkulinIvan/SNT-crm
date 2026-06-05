from rest_framework import viewsets, status
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.response import Response
from rest_framework.permissions import AllowAny, IsAuthenticated
from django.core.cache import cache
from django.conf import settings
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

@api_view(['GET'])
@permission_classes([AllowAny])
def health_check(request):
    """Health check endpoint (всегда доступен)"""
    try:
        return Response({
            'status': 'healthy',
            'timestamp': datetime.now().isoformat(),
            'maintenance_mode': cache.get('api_maintenance_mode', False),
            'debug': settings.DEBUG
        })
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return Response(
            {'status': 'unhealthy', 'error': str(e)},
            status=500
        )

class APISecurityViewSet(viewsets.ViewSet):
    """
    API endpoints для управления безопасностью API
    Доступ только для администраторов
    """
    
    permission_classes = [IsAuthenticated]
    
    def _check_admin(self, request):
        """Проверка прав администратора"""
        if not (request.user.is_superuser or request.user.is_admin):
            return False
        return True
    
    @action(detail=False, methods=['post'], url_path='maintenance/toggle')
    def toggle_maintenance(self, request):
        """Включить/выключить режим обслуживания"""
        if not self._check_admin(request):
            return Response({'detail': 'Недостаточно прав'}, status=status.HTTP_403_FORBIDDEN)
        
        enabled = request.data.get('enabled', False)
        message = request.data.get('message', 'API на техническом обслуживании')
        
        # Сохраняем в кэш
        cache.set('api_maintenance_mode', enabled, timeout=None)
        cache.set('api_maintenance_message', message, timeout=None)
        
        logger.warning(f"Maintenance mode {'ENABLED' if enabled else 'DISABLED'} by {request.user.username}")
        
        return Response({
            'success': True,
            'maintenance_mode': enabled,
            'message': message
        })
    
    @action(detail=False, methods=['get'], url_path='maintenance/status')
    def maintenance_status(self, request):
        """Получить статус режима обслуживания"""
        enabled = cache.get('api_maintenance_mode', False)
        message = cache.get('api_maintenance_message', 'API на техническом обслуживании')
        
        return Response({
            'maintenance_mode': enabled,
            'message': message if enabled else None
        })
    
    @action(detail=False, methods=['post'], url_path='ip/block')
    def block_ip(self, request):
        """Заблокировать IP адрес"""
        if not self._check_admin(request):
            return Response({'detail': 'Недостаточно прав'}, status=status.HTTP_403_FORBIDDEN)
        
        ip = request.data.get('ip')
        reason = request.data.get('reason', '')
        
        if not ip:
            return Response({'detail': 'Укажите IP адрес'}, status=status.HTTP_400_BAD_REQUEST)
        
        # Получаем текущий список заблокированных IP
        blocked_ips = cache.get('blocked_api_ips', [])
        
        if ip not in blocked_ips:
            blocked_ips.append(ip)
            cache.set('blocked_api_ips', blocked_ips, timeout=None)
            logger.warning(f"IP {ip} BLOCKED by {request.user.username}. Reason: {reason}")
        
        return Response({
            'success': True,
            'blocked_ip': ip,
            'blocked_ips': blocked_ips
        })
    
    @action(detail=False, methods=['post'], url_path='ip/unblock')
    def unblock_ip(self, request):
        """Разблокировать IP адрес"""
        if not self._check_admin(request):
            return Response({'detail': 'Недостаточно прав'}, status=status.HTTP_403_FORBIDDEN)
        
        ip = request.data.get('ip')
        
        if not ip:
            return Response({'detail': 'Укажите IP адрес'}, status=status.HTTP_400_BAD_REQUEST)
        
        # Получаем текущий список заблокированных IP
        blocked_ips = cache.get('blocked_api_ips', [])
        
        if ip in blocked_ips:
            blocked_ips.remove(ip)
            cache.set('blocked_api_ips', blocked_ips, timeout=None)
            logger.info(f"IP {ip} UNBLOCKED by {request.user.username}")
        
        return Response({
            'success': True,
            'unblocked_ip': ip,
            'blocked_ips': blocked_ips
        })
    
    @action(detail=False, methods=['get'], url_path='ip/list')
    def list_blocked_ips(self, request):
        """Получить список заблокированных IP"""
        if not self._check_admin(request):
            return Response({'detail': 'Недостаточно прав'}, status=status.HTTP_403_FORBIDDEN)
        
        blocked_ips = cache.get('blocked_api_ips', [])
        allowed_ips = getattr(settings, 'ALLOWED_API_IPS', [])
        
        return Response({
            'blocked_ips': blocked_ips,
            'allowed_ips': allowed_ips
        })
    
    @action(detail=False, methods=['post'], url_path='rate-limit/update')
    def update_rate_limits(self, request):
        """Обновить лимиты частоты запросов"""
        if not self._check_admin(request):
            return Response({'detail': 'Недостаточно прав'}, status=status.HTTP_403_FORBIDDEN)
        
        limits = request.data.get('limits', {})
        
        if limits:
            cache.set('custom_rate_limits', limits, timeout=None)
            logger.info(f"Rate limits updated by {request.user.username}: {limits}")
        
        return Response({
            'success': True,
            'limits': limits
        })
    
    @action(detail=False, methods=['get'], url_path='stats')
    def security_stats(self, request):
        """Получить статистику безопасности API"""
        if not self._check_admin(request):
            return Response({'detail': 'Недостаточно прав'}, status=status.HTTP_403_FORBIDDEN)
        
        # Здесь можно добавить статистику из логов
        return Response({
            'maintenance_mode': cache.get('api_maintenance_mode', False),
            'blocked_ips_count': len(cache.get('blocked_api_ips', [])),
            'rate_limits': cache.get('custom_rate_limits', getattr(settings, 'API_RATE_LIMITS', {})),
        })

