from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter, OrderingFilter
from django.views.generic import TemplateView

from .models import Organization, OrganizationMembership
from .serializers import (
    OrganizationSerializer, 
    OrganizationDetailSerializer,
    OrganizationMembershipSerializer,
    OrganizationMembershipCreateSerializer
)
from accounts.permissions import IsAdminOrSuperuser, IsManagerOrAbove


class OrganizationViewSet(viewsets.ModelViewSet):
    """
    ViewSet для управления СНТ.
    """
    queryset = Organization.objects.all()
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    search_fields = ['name', 'short_name', 'inn']
    ordering_fields = ['name', 'created_at', 'is_active']
    ordering = ['name']

    def get_permissions(self):
        if self.action in ('create', 'update', 'partial_update', 'destroy'):
            return [permissions.IsAuthenticated(), IsAdminOrSuperuser()]
        return [permissions.IsAuthenticated()]

    def get_serializer_class(self):
        if self.action == 'list':
            return OrganizationSerializer
        return OrganizationDetailSerializer

    @action(detail=True, methods=['get'], url_path='members')
    def get_members(self, request, pk=None):
        """Получить список членов СНТ"""
        organization = self.get_object()
        memberships = organization.memberships.select_related('owner').all()
        
        # Фильтрация по статусу
        status_filter = request.query_params.get('status')
        if status_filter:
            memberships = memberships.filter(status=status_filter)
        
        serializer = OrganizationMembershipSerializer(memberships, many=True)
        return Response({
            'count': memberships.count(),
            'results': serializer.data
        })

    @action(detail=True, methods=['post'], url_path='add-member')
    def add_member(self, request, pk=None):
        """Добавить владельца в члены СНТ"""
        organization = self.get_object()
        serializer = OrganizationMembershipCreateSerializer(
            data=request.data,
            context={'organization': organization}
        )
        
        if serializer.is_valid():
            membership = serializer.save(organization=organization)
            return Response(
                OrganizationMembershipSerializer(membership).data,
                status=status.HTTP_201_CREATED
            )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['get'], url_path='stats')
    def stats(self, request, pk=None):
        """Статистика по СНТ"""
        organization = self.get_object()
        
        stats = {
            'id': organization.id,
            'name': organization.short_name,
            'total_members': organization.memberships.filter(status='active').count(),
            'total_plots': 0,  # Можно добавить связь с участками
            'total_owners': organization.owners.count(),
            'staff_count': organization.staff_members.count(),
        }
        
        # Сумма задолженностей (если есть связь)
        try:
            from payments.models import Assessment
            total_debt = 0
            for owner in organization.owners.all():
                total_debt += owner.total_debt
            stats['total_debt'] = float(total_debt)
        except ImportError:
            stats['total_debt'] = 0
        
        return Response(stats)
    
class OrganizationListView(TemplateView):
    template_name = 'organizations/list.html'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['active_page'] = 'organizations'
        return context


class OrganizationDetailView(TemplateView):
    template_name = 'organizations/detail.html'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['active_page'] = 'organizations'
        context['organization_id'] = self.kwargs.get('organization_id')
        return context
    
class OrganizationMembershipViewSet(viewsets.ModelViewSet):
    queryset = OrganizationMembership.objects.select_related('owner', 'organization').all()
    serializer_class = OrganizationMembershipSerializer
    permission_classes = [permissions.IsAuthenticated, IsManagerOrAbove]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['organization', 'owner', 'status']