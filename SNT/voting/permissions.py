from rest_framework import permissions


class CanManageVoting(permissions.BasePermission):
    """Разрешение на управление голосованиями"""
    
    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False
        
        if request.user.is_superuser or request.user.is_admin:
            return True
        
        return request.user.has_perm('voting.can_manage_voting')
    
    def has_object_permission(self, request, view, obj):
        if request.user.is_superuser or request.user.is_admin:
            return True
        
        # Менеджер может управлять голосованиями в своей организации
        if hasattr(request.user, 'organization') and request.user.organization:
            return obj.organization == request.user.organization
        
        return request.user.has_perm('voting.can_manage_voting')


class CanVote(permissions.BasePermission):
    """Разрешение на голосование"""
    
    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False
        
        # Администраторы тоже могут голосовать
        return True