# organizations/views.py
from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter, OrderingFilter
from django.views.generic import TemplateView
from django.contrib.auth import get_user_model  # Добавляем импорт

from .models import Organization, OrganizationMembership
from .serializers import (
    OrganizationSerializer, 
    OrganizationDetailSerializer,
    OrganizationMembershipSerializer,
    OrganizationMembershipCreateSerializer
)
from accounts.permissions import IsAdminOrSuperuser, IsManagerOrAbove

# Получаем модель User
User = get_user_model()


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

    def update(self, request, *args, **kwargs):
        """Обновление с поддержкой chairman_id и accountant_id"""
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        
        # Обрабатываем специальные поля
        data = request.data.copy()
        
        # Обработка председателя
        if 'chairman_id' in data:
            chairman_id = data.pop('chairman_id')
            if chairman_id:
                try:
                    chairman = User.objects.get(id=chairman_id)
                    data['chairman'] = chairman.id
                except User.DoesNotExist:
                    # Если пользователь не найден, игнорируем
                    pass
            else:
                data['chairman'] = None
        
        # Обработка бухгалтера
        if 'accountant_id' in data:
            accountant_id = data.pop('accountant_id')
            if accountant_id:
                try:
                    accountant = User.objects.get(id=accountant_id)
                    data['accountant'] = accountant.id
                except User.DoesNotExist:
                    pass
            else:
                data['accountant'] = None
        
        serializer = self.get_serializer(instance, data=data, partial=partial)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        
        if getattr(instance, '_prefetched_objects_cache', None):
            instance._prefetched_objects_cache = {}
        
        return Response(serializer.data)

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

        # Получаем владельцев через членство
        active_memberships = organization.memberships.filter(status='active')
        owners = [m.owner for m in active_memberships]

        # Получаем участки через владельцев
        from land.models import LandPlot
        plots = LandPlot.objects.filter(owners__in=owners).distinct()

        stats = {
            'id': organization.id,
            'name': organization.short_name,
            'total_members': active_memberships.count(),
            'total_plots': plots.count(),
            'total_owners': len(owners),
            'staff_count': organization.staff_members.count(),
        }

        # Сумма задолженностей
        try:
            from payments.models import Assessment
            total_debt = 0
            overdue_count = 0
            for owner in owners:
                debt = owner.total_debt
                total_debt += debt

                # Считаем просроченные начисления
                overdue = Assessment.objects.filter(
                    owner=owner,
                    status='overdue'
                ).count()
                overdue_count += overdue

            stats['total_debt'] = float(total_debt)
            stats['overdue_count'] = overdue_count
        except ImportError:
            stats['total_debt'] = 0
            stats['overdue_count'] = 0

        return Response(stats)
    
    def get_queryset(self):
        queryset = super().get_queryset()
        
        # Не-админы видят только свое СНТ
        if not self.request.user.is_superuser and not self.request.user.is_admin:
            if hasattr(self.request, 'current_organization') and self.request.current_organization:
                queryset = queryset.filter(id=self.request.current_organization.id)
            else:
                queryset = queryset.none()
        
        return queryset


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