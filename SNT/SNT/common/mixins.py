from django.db import models
from django.core.exceptions import PermissionDenied


class OrganizationMixin:
    """
    Миксин для автоматической фильтрации queryset'ов по организации.
    Используется в ViewSet и View.
    """
    
    def get_queryset(self):
        queryset = super().get_queryset()
        
        # Суперпользователи и админы видят всё
        if self.request.user.is_superuser or self.request.user.is_admin:
            return queryset
        
        # Для остальных пользователей - только их организация
        if hasattr(self.request, 'current_organization') and self.request.current_organization:
            # Фильтруем по полю organization
            if hasattr(queryset.model, 'organization'):
                return queryset.filter(organization=self.request.current_organization)
            
            # Для моделей, связанных через владельца
            if hasattr(queryset.model, 'owner') and hasattr(queryset.model.owner.field.model, 'organization'):
                return queryset.filter(owner__organization=self.request.current_organization)
            
            # Для моделей, связанных через участок
            if hasattr(queryset.model, 'land_plot') and hasattr(queryset.model.land_plot.field.model, 'owners'):
                return queryset.filter(land_plot__owners__organization=self.request.current_organization).distinct()
        
        return queryset.none()
    
    def perform_create(self, serializer):
        """При создании автоматически подставляем организацию"""
        if hasattr(self.request, 'current_organization') and self.request.current_organization:
            serializer.save(organization=self.request.current_organization)
        else:
            super().perform_create(serializer)