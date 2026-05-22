# SNT/common/mixins.py
from django.core.exceptions import PermissionDenied

from users.models import Owner


class OrganizationMixin:
    """
    Миксин для автоматической фильтрации queryset'ов через членство в СНТ.
    """
    
    def get_queryset(self):
        queryset = super().get_queryset()
        
        # Суперпользователи и админы видят всё
        if self.request.user.is_superuser or self.request.user.is_admin:
            return queryset
        
        # Для остальных пользователей - только их организация
        if hasattr(self.request, 'current_organization') and self.request.current_organization:
            org = self.request.current_organization
            
            # Для модели Owner - фильтруем через членство
            if queryset.model.__name__ == 'Owner':
                return queryset.filter(memberships__organization=org, memberships__status='active').distinct()
            
            # Для модели LandPlot - фильтруем через владельцев
            if queryset.model.__name__ == 'LandPlot':
                return queryset.filter(owners__memberships__organization=org, owners__memberships__status='active').distinct()
            
            # Для модели Assessment - фильтруем через владельца
            if hasattr(queryset.model, 'owner'):
                return queryset.filter(owner__memberships__organization=org, owner__memberships__status='active').distinct()
        
        return queryset.none()
    
    def perform_create(self, serializer):
        """При создании автоматически добавляем членство в организацию"""
        instance = serializer.save()
        
        # Для Owner - автоматически создаем членство в организации пользователя
        if isinstance(instance, Owner) and hasattr(self.request, 'current_organization') and self.request.current_organization:
            from organizations.models import OrganizationMembership
            OrganizationMembership.objects.get_or_create(
                owner=instance,
                organization=self.request.current_organization,
                defaults={'status': 'active'}
            )
        
        return instance