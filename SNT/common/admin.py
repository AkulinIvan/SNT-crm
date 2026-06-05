# SNT/common/admin.py
from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html
from django.core.cache import cache
from django.conf import settings
from .api_security_manager import APISecurityManager

@admin.register(admin.models.LogEntry)
class LogEntryAdmin(admin.ModelAdmin):
    list_display = ['action_time', 'user', 'content_type', 'object_repr', 'action_flag', 'change_message']
    list_filter = ['action_flag', 'content_type']
    search_fields = ['object_repr', 'change_message']
    readonly_fields = ['action_time', 'user', 'content_type', 'object_id', 'object_repr', 'action_flag', 'change_message']
    
    def has_add_permission(self, request):
        return False
    
    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser


class APISecurityAdmin(admin.AdminSite):
    """Админка для управления безопасностью API"""
    
    site_header = 'API Security Management'
    site_title = 'API Security'
    
    def get_urls(self):
        from django.urls import path
        urls = super().get_urls()
        custom_urls = [
            path('api-security/', self.admin_view(self.api_security_dashboard), name='api-security'),
            path('api-security/toggle-maintenance/', self.admin_view(self.toggle_maintenance), name='toggle-maintenance'),
        ]
        return custom_urls + urls
    
    def api_security_dashboard(self, request):
        from django.shortcuts import render
        context = {
            'maintenance_mode': APISecurityManager.get_maintenance_mode(),
            'blocked_ips': settings.BLOCKED_API_IPS,
            'allowed_ips': settings.ALLOWED_API_IPS,
            'rate_limits': settings.API_RATE_LIMITS,
        }
        return render(request, 'admin/api_security.html', context)
    
    def toggle_maintenance(self, request):
        if request.method == 'POST':
            enabled = request.POST.get('enabled') == 'true'
            APISecurityManager.set_maintenance_mode(enabled, request.user)
            from django.http import JsonResponse
            return JsonResponse({'success': True, 'maintenance_mode': enabled})
        from django.http import HttpResponseNotAllowed
        return HttpResponseNotAllowed(['POST'])


# Создаём экземпляр админки
api_security_admin = APISecurityAdmin(name='api_security')