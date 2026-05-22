from django.db import models


class OrganizationMixin:
    """
    Миксин для автоматической фильтрации queryset'ов по организации.
    """
    
    def get_queryset(self):
        queryset = super().get_queryset()
        
        # Суперпользователи и админы видят всё
        if self.request.user.is_superuser or self.request.user.is_admin:
            return queryset
        
        # Для остальных пользователей - только их организация
        if hasattr(self.request, 'current_organization') and self.request.current_organization:
            org = self.request.current_organization
            
            # Для модели LandPlot - фильтруем по полю organization
            if queryset.model.__name__ == 'LandPlot':
                return queryset.filter(organization=org)
            
            # Для модели Owner - фильтруем через членство (memberships)
            if queryset.model.__name__ == 'Owner':
                return queryset.filter(memberships__organization=org, memberships__status='active').distinct()
            
            # Для модели Assessment - фильтруем через владельца
            if hasattr(queryset.model, 'owner'):
                return queryset.filter(owner__memberships__organization=org, owner__memberships__status='active').distinct()
            
            # Для модели Organization - показываем только свое СНТ
            if queryset.model.__name__ == 'Organization':
                return queryset.filter(id=org.id)
        
        return queryset.none()
    
    def perform_create(self, serializer):
        """При создании - просто сохраняем (организация добавится через сигнал)"""
        serializer.save()