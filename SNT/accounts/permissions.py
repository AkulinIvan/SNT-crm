from rest_framework import permissions
from rest_framework.permissions import BasePermission, SAFE_METHODS


class IsAdminOrSuperuser(BasePermission):
    """Доступ только для администраторов и суперпользователей"""
    
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.is_admin


class IsManagerOrAbove(BasePermission):
    """Доступ для менеджеров и выше"""
    
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.is_manager

class IsManagerOrAdmin(BasePermission):
    """Доступ для менеджеров и администраторов (не наблюдателей)"""
    
    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False
        # Суперпользователь всегда имеет доступ
        if request.user.is_superuser:
            return True
        # Менеджеры и админы имеют доступ
        return request.user.role in ['admin', 'manager']

class IsAccountantOrAbove(BasePermission):
    """Доступ для бухгалтеров и выше"""
    
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.is_accountant


class ReadOnlyOrAdmin(BasePermission):
    """Чтение всем аутентифицированным, запись только админам"""
    
    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return request.user.is_authenticated
        return request.user.is_authenticated and request.user.is_admin


class CanManageUsers(BasePermission):
    """Разрешение на управление пользователями"""
    
    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False
        if request.user.is_admin:
            return True
        return request.user.has_perm('accounts.can_manage_users')


class CanViewFinances(BasePermission):
    """Разрешение на просмотр финансов"""
    
    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False
        if request.user.is_admin or request.user.is_accountant:
            return True
        return request.user.has_perm('accounts.can_view_finances')


class CanManageFinances(BasePermission):
    """Разрешение на управление финансами"""
    
    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False
        if request.user.is_admin or request.user.is_accountant:
            return True
        return request.user.has_perm('accounts.can_manage_finances')


class CanExportData(BasePermission):
    """Разрешение на экспорт данных"""
    
    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False
        if request.user.is_manager:
            return True
        return request.user.has_perm('accounts.can_export_data')


class CanViewAuditLog(BasePermission):
    """Разрешение на просмотр логов"""
    
    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False
        if request.user.is_admin:
            return True
        return request.user.has_perm('accounts.can_view_audit_log')