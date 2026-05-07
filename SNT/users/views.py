from django.shortcuts import get_object_or_404, render
from django.views import View
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter, OrderingFilter

from .models import Owner, Ownership, ContactInfo
from .serializers import (
    OwnerListSerializer,
    OwnerDetailSerializer,
    OwnerCreateUpdateSerializer,
    OwnershipSerializer,
    ContactInfoSerializer,
)
from land.models import LandPlot


class OwnerViewSet(viewsets.ModelViewSet):
    """
    ViewSet для управления владельцами.
    
    list      — список владельцев (краткий)
    retrieve  — карточка владельца (полная, с контактами и участками)
    create    — создание
    update    — обновление
    destroy   — удаление
    
    Дополнительные actions:
    {id}/add_plot/        — привязать участок к владельцу
    {id}/remove_plot/     — отвязать участок
    {id}/contacts/        — контакты владельца
    {id}/add_contact/     — добавить контакт
    search?q=Иванов       — поиск владельца
    """
    queryset = Owner.objects.prefetch_related('contacts', 'ownerships__land_plot')
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    search_fields = ['full_name']
    ordering_fields = ['full_name', 'created_at']
    ordering = ['full_name']

    def get_serializer_class(self):
        if self.action == 'list':
            return OwnerListSerializer
        elif self.action in ('create', 'update', 'partial_update'):
            return OwnerCreateUpdateSerializer
        return OwnerDetailSerializer

    # ------------------------------------------------------------------
    # Управление связью с участками
    # ------------------------------------------------------------------

    @action(detail=True, methods=['post'], url_path='add-plot')
    def add_plot(self, request, pk=None):
        """
        POST /api/owners/{id}/add-plot/
        Тело: {"land_plot_id": 1, "share": "1/2", "ownership_since": "2024-01-15"}
        """
        owner = self.get_object()
        plot_id = request.data.get('land_plot_id')
        if not plot_id:
            return Response(
                {'detail': 'Необходимо указать land_plot_id'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        land_plot = get_object_or_404(LandPlot, pk=plot_id)
        if Ownership.objects.filter(owner=owner, land_plot=land_plot).exists():
            return Response(
                {'detail': 'Этот участок уже привязан к владельцу.'},
                status=status.HTTP_409_CONFLICT,
            )
        ownership = Ownership.objects.create(
            owner=owner,
            land_plot=land_plot,
            share=request.data.get('share', '1/1'),
            ownership_since=request.data.get('ownership_since'),
            document_basis=request.data.get('document_basis', ''),
        )
        serializer = OwnershipSerializer(ownership)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='remove-plot')
    def remove_plot(self, request, pk=None):
        """
        POST /api/owners/{id}/remove-plot/
        Тело: {"land_plot_id": 1}
        Удаляет связь владельца с участком.
        """
        owner = self.get_object()
        plot_id = request.data.get('land_plot_id')
        if not plot_id:
            return Response(
                {'detail': 'Необходимо указать land_plot_id'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        ownership = get_object_or_404(
            Ownership, owner=owner, land_plot_id=plot_id
        )
        ownership.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    # ------------------------------------------------------------------
    # Управление контактами
    # ------------------------------------------------------------------

    @action(detail=True, methods=['get'], url_path='contacts')
    def list_contacts(self, request, pk=None):
        """
        GET /api/owners/{id}/contacts/
        Список всех контактов владельца.
        """
        owner = self.get_object()
        contacts = owner.contacts.all()
        serializer = ContactInfoSerializer(contacts, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['post'], url_path='add-contact')
    def add_contact(self, request, pk=None):
        """
        POST /api/owners/{id}/add-contact/
        Тело: {"type": "ph", "value": "+79161234567"}
        """
        owner = self.get_object()
        serializer = ContactInfoSerializer(
            data=request.data,
            context={'request': request},
        )
        if serializer.is_valid():
            serializer.save(owner=owner)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['post'], url_path='deactivate-contact')
    def deactivate_contact(self, request, pk=None):
        """
        POST /api/owners/{id}/deactivate-contact/
        Тело: {"contact_id": 5}
        Деактивация контакта (не удаление).
        """
        owner = self.get_object()
        contact_id = request.data.get('contact_id')
        if not contact_id:
            return Response(
                {'detail': 'Необходимо указать contact_id'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        contact = get_object_or_404(ContactInfo, pk=contact_id, owner=owner)
        contact.is_active = False
        contact.save(update_fields=['is_active'])
        return Response({'detail': 'Контакт деактивирован.'})


class ContactInfoViewSet(viewsets.ModelViewSet):
    """
    Отдельный ViewSet для управления контактами (если нужно вне контекста владельца).
    Позволяет редактировать / удалять / подтверждать контакты напрямую.
    """
    queryset = ContactInfo.objects.select_related('owner').all()
    serializer_class = ContactInfoSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['owner', 'type', 'is_active', 'is_verified']

    @action(detail=True, methods=['post'], url_path='verify')
    def verify(self, request, pk=None):
        """
        POST /api/contacts/{id}/verify/
        Подтверждение контакта.
        """
        contact = self.get_object()
        contact.is_verified = True
        contact.save(update_fields=['is_verified'])
        return Response({'detail': 'Контакт подтверждён.'})

    @action(detail=True, methods=['post'], url_path='unverify')
    def unverify(self, request, pk=None):
        """Снятие подтверждения."""
        contact = self.get_object()
        contact.is_verified = False
        contact.save(update_fields=['is_verified'])
        return Response({'detail': 'Подтверждение снято.'})


class OwnershipViewSet(viewsets.ModelViewSet):
    """
    ViewSet для работы с правами собственности.
    Позволяет обновлять долю, дату и документ-основание.
    """
    queryset = Ownership.objects.select_related('owner', 'land_plot').all()
    serializer_class = OwnershipSerializer
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = ['owner', 'land_plot']
    ordering_fields = ['ownership_since']
    

class OwnerListView(View):
    """Страница со списком владельцев."""
    def get(self, request):
        return render(request, 'users/list.html', {'active_page': 'owners'})


class OwnerDetailView(View):
    """Страница карточки владельца."""
    def get(self, request, pk):
        return render(request, 'users/detail.html', {
            'active_page': 'owners',
            'owner_id': pk,
        })


class DashboardView(View):
    """Главная страница дашборда."""
    def get(self, request):
        return render(request, 'users/dashboard.html', {'active_page': 'dashboard'})