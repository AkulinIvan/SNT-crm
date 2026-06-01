from django.shortcuts import get_object_or_404, render
from django.views import View
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter, OrderingFilter
from django.db.models import Count
from django.utils import timezone
from django.db import models
import logging
from rest_framework import permissions

from common.mixins import OrganizationMixin
from accounts.permissions import IsManagerOrAbove
from .models import Owner, Ownership, ContactInfo
from .serializers import (
    OwnerListSerializer,
    OwnerDetailSerializer,
    OwnerCreateUpdateSerializer,
    OwnershipSerializer,
    ContactInfoSerializer,
)
from land.models import LandPlot

logger = logging.getLogger(__name__)

class DashboardViewSet(viewsets.ViewSet):
    """ViewSet для получения данных дашборда"""
    
    @action(detail=False, methods=['get'], url_path='stats')
    def dashboard_stats(self, request):
        """Получить статистику для дашборда с учетом организации"""
        from payments.models import Assessment
        from land.models import LandPlot
        
        # Получаем организацию пользователя
        org = None
        if not request.user.is_superuser and not request.user.is_admin:
            org = getattr(request, 'current_organization', None)
        
        # Статистика владельцев
        from users.models import Owner
        owners_query = Owner.objects.all()
        if org:
            owners_query = owners_query.filter(memberships__organization=org, memberships__status='active')
        owners_count = owners_query.count()
        
        # Статистика участков
        plots_query = LandPlot.objects.all()
        if org:
            plots_query = plots_query.filter(organization=org)
        plots_total = plots_query.count()
        plots_active = plots_query.filter(status='active').count()
        
        # Статистика СНТ
        from organizations.models import Organization
        orgs_query = Organization.objects.all()
        if not request.user.is_superuser and not request.user.is_admin and org:
            orgs_query = orgs_query.filter(id=org.id)
        orgs_count = orgs_query.count()
        
        # Задолженность
        assessments_query = Assessment.objects.filter(status__in=['pending', 'partial', 'overdue'])
        if org:
            assessments_query = assessments_query.filter(owner__memberships__organization=org, owner__memberships__status='active')
        total_debt = 0
        for a in assessments_query:
            total_debt += a.debt
        
        return Response({
            'owners_count': owners_count,
            'plots_total': plots_total,
            'plots_active': plots_active,
            'organizations_count': orgs_count,
            'total_debt': float(total_debt),
        })
        
class OwnerViewSet(OrganizationMixin, viewsets.ModelViewSet):
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
    {id}/stats/           — статистика владельца
    search?q=Иванов       — поиск владельца
    """
    queryset = Owner.objects.prefetch_related(
        'contacts', 
        'ownerships__land_plot'
    ).annotate(
        plots_count=Count('land_plots', distinct=True)
    )
    serializer_class = OwnerDetailSerializer
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    search_fields = ['full_name', 'contacts__value']
    ordering_fields = ['full_name', 'created_at', 'plots_count']
    ordering = ['full_name']

    def get_permissions(self):
        """
        Права доступа:
        - Чтение (list, retrieve): все авторизованные
        - Создание, изменение, удаление: только менеджеры и выше
        """
        if self.action in ('create', 'update', 'partial_update', 'destroy'):
            return [permissions.IsAuthenticated(), IsManagerOrAbove()]
        # Дополнительные действия
        if self.action in ('add_plot', 'remove_plot', 'add_contact', 'deactivate_contact'):
            return [permissions.IsAuthenticated(), IsManagerOrAbove()]
        return [permissions.IsAuthenticated()]
    
    def get_serializer_class(self):
        if self.action == 'list':
            return OwnerListSerializer
        elif self.action in ('create', 'update', 'partial_update'):
            return OwnerCreateUpdateSerializer
        return OwnerDetailSerializer

    def get_queryset(self):
        """Оптимизация запросов с фильтрацией"""
        queryset = super().get_queryset()

        # Админы видят всех
        if self.request.user.is_superuser or self.request.user.is_admin:
            pass  # Не фильтруем
        else:
            # Фильтруем по организации пользователя
            org = getattr(self.request, 'current_organization', None)
            if org:
                queryset = queryset.filter(
                    memberships__organization=org, 
                    memberships__status='active'
                )
            else:
                # Если организации нет - показываем пустой список
                queryset = queryset.filter(memberships__isnull=True)

        # Фильтрация по наличию участков
        has_plots = self.request.query_params.get('has_plots')
        if has_plots is not None:
            if has_plots.lower() == 'true':
                queryset = queryset.filter(plots_count__gt=0)
            else:
                queryset = queryset.filter(plots_count=0)

        # Фильтрация по должникам
        is_debtor = self.request.query_params.get('is_debtor')
        if is_debtor is not None:
            from payments.models import Assessment
            if is_debtor.lower() == 'true':
                # Находим владельцев с долгами
                debtor_ids = Assessment.objects.filter(
                    status__in=['pending', 'partial', 'overdue']
                ).values_list('owner_id', flat=True).distinct()
                queryset = queryset.filter(id__in=debtor_ids)
            else:
                # Находим владельцев без долгов
                debtor_ids = Assessment.objects.filter(
                    status__in=['pending', 'partial', 'overdue']
                ).values_list('owner_id', flat=True).distinct()
                queryset = queryset.exclude(id__in=debtor_ids)

        # Фильтрация по минимальной сумме долга (через подзапрос)
        debt_min = self.request.query_params.get('debt_min')
        if debt_min:
            from payments.models import Assessment
            # Находим владельцев с суммой долга >= debt_min
            owner_ids_with_debt = Assessment.objects.filter(
                status__in=['pending', 'partial', 'overdue']
            ).values('owner_id').annotate(
                total_debt=models.Sum('debt')
            ).filter(total_debt__gte=float(debt_min)).values_list('owner_id', flat=True)
            queryset = queryset.filter(id__in=owner_ids_with_debt)

        # Фильтрация по максимальной сумме долга
        debt_max = self.request.query_params.get('debt_max')
        if debt_max:
            from payments.models import Assessment
            owner_ids_with_debt = Assessment.objects.filter(
                status__in=['pending', 'partial', 'overdue']
            ).values('owner_id').annotate(
                total_debt=models.Sum('debt')
            ).filter(total_debt__lte=float(debt_max)).values_list('owner_id', flat=True)
            queryset = queryset.filter(id__in=owner_ids_with_debt)

        # Фильтрация по количеству участков
        plots_count = self.request.query_params.get('plots_count')
        if plots_count:
            queryset = queryset.annotate(plots_cnt=Count('land_plots')).filter(plots_cnt=int(plots_count))

        plots_count_min = self.request.query_params.get('plots_count_min')
        if plots_count_min:
            queryset = queryset.annotate(plots_cnt=Count('land_plots')).filter(plots_cnt__gte=int(plots_count_min))

        # Фильтрация по организации (через членство)
        organization = self.request.query_params.get('organization')
        if organization:
            queryset = queryset.filter(
                memberships__organization_id=organization,
                memberships__status='active'
            )

        # Фильтрация по статусу членства
        membership_status = self.request.query_params.get('membership_status')
        if membership_status:
            queryset = queryset.filter(memberships__status=membership_status)

        # Фильтрация по дате создания
        created_after = self.request.query_params.get('created_after')
        if created_after:
            queryset = queryset.filter(created_at__gte=created_after)

        created_before = self.request.query_params.get('created_before')
        if created_before:
            queryset = queryset.filter(created_at__lte=created_before)

        # Фильтрация по наличию контактов
        has_contacts = self.request.query_params.get('has_contacts')
        if has_contacts is not None:
            if has_contacts.lower() == 'true':
                queryset = queryset.filter(contacts__isnull=False).distinct()
            elif has_contacts.lower() == 'false':
                queryset = queryset.filter(contacts__isnull=True)

        # Фильтрация по наличию телефона
        has_phone = self.request.query_params.get('has_phone')
        if has_phone is not None and has_phone.lower() == 'true':
            queryset = queryset.filter(contacts__type='ph', contacts__is_active=True).distinct()

        # Фильтрация по наличию email
        has_email = self.request.query_params.get('has_email')
        if has_email is not None and has_email.lower() == 'true':
            queryset = queryset.filter(contacts__type='em', contacts__is_active=True).distinct()

        # Поиск по номеру телефона
        phone_contains = self.request.query_params.get('phone_contains')
        if phone_contains:
            queryset = queryset.filter(
                contacts__type='ph', 
                contacts__value__icontains=phone_contains,
                contacts__is_active=True
            ).distinct()

        # Поиск по email
        email_contains = self.request.query_params.get('email_contains')
        if email_contains:
            queryset = queryset.filter(
                contacts__type='em', 
                contacts__value__icontains=email_contains,
                contacts__is_active=True
            ).distinct()

        # Сортировка по сумме долга (добавляем аннотацию)
        ordering = self.request.query_params.get('ordering', '')
        if ordering in ('total_debt', '-total_debt'):
            from payments.models import Assessment
            # Аннотируем сумму долга
            debt_subquery = Assessment.objects.filter(
                owner=models.OuterRef('pk'),
                status__in=['pending', 'partial', 'overdue']
            ).values('owner_id').annotate(
                total=models.Sum('debt')
            ).values('total')

            queryset = queryset.annotate(
                total_debt_annotated=models.Subquery(debt_subquery, output_field=models.DecimalField())
            ).order_by(
                ordering.replace('total_debt', 'total_debt_annotated')
            )

        return queryset.distinct()

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['request'] = self.request
        return context
    
    def list(self, request, *args, **kwargs):
        """Расширенный list с дополнительной статистикой"""
        queryset = self.filter_queryset(self.get_queryset())
        
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            response = self.get_paginated_response(serializer.data)
            
            # Добавляем статистику в ответ
            total_debtors = sum(1 for owner in queryset if owner.is_debtor)
            response.data['stats'] = {
                'total': queryset.count(),
                'debtors': total_debtors,
                'with_plots': queryset.filter(plots_count__gt=0).count(),
                'without_plots': queryset.filter(plots_count=0).count(),
            }
            return response
        
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    def destroy(self, request, *args, **kwargs):
        """Безопасное удаление с проверкой зависимостей"""
        owner = self.get_object()
        
        # Проверяем наличие активных начислений
        try:
            from payments.models import Assessment
            has_active_assessments = Assessment.objects.filter(
                owner=owner,
                status__in=['pending', 'partial', 'overdue']
            ).exists()
        except ImportError:
            has_active_assessments = False
        
        if has_active_assessments:
            return Response(
                {
                    'detail': 'Невозможно удалить владельца с неоплаченными начислениями.',
                    'code': 'has_active_assessments'
                },
                status=status.HTTP_409_CONFLICT
            )
        
        # Логируем удаление
        logger.info(f'Удаление владельца: {owner.full_name} (ID: {owner.id})')
        
        return super().destroy(request, *args, **kwargs)

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
        
        # Проверяем, что участок принадлежит организации пользователя
        if self.request.current_organization and land_plot.organization != self.request.current_organization:
            return Response(
                {'detail': 'Этот участок не принадлежит вашему СНТ'},
                status=status.HTTP_403_FORBIDDEN
            )
            
        # Проверка на существующую связь
        if Ownership.objects.filter(owner=owner, land_plot=land_plot).exists():
            return Response(
                {'detail': 'Этот участок уже привязан к владельцу.'},
                status=status.HTTP_409_CONFLICT,
            )
        
        # Проверка доли (должна быть валидной дробью)
        share = request.data.get('share', '1/1')
        if not self._validate_share(share):
            return Response(
                {'detail': 'Неверный формат доли. Пример: 1/1, 1/2, 2/3'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        ownership = Ownership.objects.create(
            owner=owner,
            land_plot=land_plot,
            share=share,
            ownership_since=request.data.get('ownership_since'),
            document_basis=request.data.get('document_basis', ''),
        )
        
        serializer = OwnershipSerializer(ownership)
        logger.info(f'Участок {plot_id} привязан к владельцу {owner.id}')
        
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
        
        ownership = get_object_or_404(Ownership, owner=owner, land_plot_id=plot_id)
        
        # Проверяем, есть ли активные начисления на этот участок
        try:
            from payments.models import Assessment
            has_assessments = Assessment.objects.filter(
                owner=owner,
                land_plot_id=plot_id,
                status__in=['pending', 'partial', 'overdue']
            ).exists()
        except ImportError:
            has_assessments = False
        
        if has_assessments:
            return Response(
                {
                    'detail': 'Невозможно отвязать участок с неоплаченными начислениями.',
                    'code': 'has_active_assessments'
                },
                status=status.HTTP_409_CONFLICT,
            )
        
        ownership.delete()
        logger.info(f'Участок {plot_id} отвязан от владельца {owner.id}')
        
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
        
        # Фильтрация по активности
        is_active = request.query_params.get('is_active')
        if is_active is not None:
            contacts = contacts.filter(is_active=is_active.lower() == 'true')
        
        # Фильтрация по типу
        contact_type = request.query_params.get('type')
        if contact_type:
            contacts = contacts.filter(type=contact_type)
        
        serializer = ContactInfoSerializer(contacts, many=True)
        return Response({
            'count': contacts.count(),
            'results': serializer.data
        })

    @action(detail=True, methods=['post'], url_path='add-contact')
    def add_contact(self, request, pk=None):
        """
        POST /api/owners/{id}/add-contact/
        Тело: {"type": "ph", "value": "+79161234567", "note": "Личный"}
        """
        owner = self.get_object()

        # Создаём копию данных с добавлением owner
        data = request.data.copy()
        data['owner'] = owner.id

        serializer = ContactInfoSerializer(
            data=data,
            context={'request': request},
        )

        if serializer.is_valid():
            serializer.save(owner=owner)
            logger.info(f'Контакт добавлен для владельца {owner.id}')
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
        contact.note = f'{contact.note} | Деактивирован {timezone.now().strftime("%d.%m.%Y %H:%M")}'.strip(' |')
        contact.save(update_fields=['is_active', 'note'])
        
        logger.info(f'Контакт {contact_id} деактивирован для владельца {owner.id}')
        return Response({'detail': 'Контакт деактивирован.'})

    # ------------------------------------------------------------------
    # Статистика и дополнительные действия
    # ------------------------------------------------------------------

    @action(detail=True, methods=['get'], url_path='stats')
    def owner_stats(self, request, pk=None):
        """
        GET /api/owners/{id}/stats/
        Расширенная статистика по владельцу.
        """
        owner = self.get_object()
        
        stats = {
            'plots_count': owner.land_plots.count(),
            'active_plots': owner.active_land_plots.count(),
            'contacts_count': owner.contacts.count(),
            'active_contacts': owner.contacts.filter(is_active=True).count(),
            'total_debt': float(owner.total_debt),
            'is_debtor': owner.is_debtor,
        }
        
        # Добавляем количество звонков, если есть приложение calls
        try:
            from calls.models import Call
            stats['calls_count'] = Call.objects.filter(owner=owner).count()
            stats['unprocessed_calls'] = Call.objects.filter(
                owner=owner, 
                status='new'
            ).count()
        except ImportError:
            stats['calls_count'] = 0
            stats['unprocessed_calls'] = 0
        
        return Response(stats)

    @action(detail=False, methods=['get'], url_path='export')
    def export_owners(self, request):
        """
        GET /api/owners/export/?format=csv
        Экспорт списка владельцев в CSV.
        """
        import csv
        from django.http import HttpResponse
        
        owners = self.filter_queryset(self.get_queryset())
        
        response = HttpResponse(content_type='text/csv')
        timestamp = timezone.now().strftime('%Y%m%d_%H%M%S')
        response['Content-Disposition'] = f'attachment; filename="owners_{timestamp}.csv"'
        
        writer = csv.writer(response)
        writer.writerow(['ID', 'ФИО', 'Телефон', 'Email', 'Участков', 'Долг', 'Дата добавления'])
        
        for owner in owners:
            writer.writerow([
                owner.id,
                owner.full_name,
                owner.primary_phone or '',
                owner.primary_email or '',
                owner.plots_count,
                owner.total_debt,
                owner.created_at.strftime('%d.%m.%Y'),
            ])
        
        return response

    def _validate_share(self, share_str):
        """Валидация строки доли (например, '1/2' или '1/1')"""
        if share_str == '1/1':
            return True
        try:
            parts = share_str.split('/')
            if len(parts) == 2:
                numerator = int(parts[0])
                denominator = int(parts[1])
                return numerator > 0 and denominator > 0 and numerator <= denominator
        except (ValueError, IndexError):
            pass
        return False
    
    def create(self, request, *args, **kwargs):
        """Создание владельца с проверкой лимитов тарифа"""
        organization = request.current_organization
        
        if organization:
            try:
                is_allowed, current, max_limit, message = organization.check_tariff_limit('owners')
                
                if not is_allowed:
                    return Response(
                        {
                            'detail': message,
                            'code': 'tariff_limit_reached',
                            'current': current,
                            'max': max_limit,
                            'tariff': organization.subscription.tariff.name if hasattr(organization, 'subscription') and organization.subscription else None
                        },
                        status=status.HTTP_403_FORBIDDEN
                    )
            except Exception as e:
                # Логируем ошибку, но не блокируем создание
                logger.error(f"Ошибка проверки лимитов тарифа: {e}")
        
        return super().create(request, *args, **kwargs)
    
    @action(detail=False, methods=['get'], url_path='tariff-info')
    def tariff_info(self, request):
        """Получить информацию о тарифных лимитах"""
        organization = request.current_organization
        
        if not organization:
            return Response({'detail': 'Организация не найдена'}, status=status.HTTP_404_NOT_FOUND)
        
        subscription = getattr(organization, 'subscription', None)
        
        if not subscription or not subscription.is_active:
            return Response({
                'has_subscription': False,
                'message': 'Нет активной подписки'
            })
        
        tariff = subscription.tariff
        
        return Response({
            'has_subscription': True,
            'tariff': {
                'id': tariff.id,
                'name': tariff.name,
                'slug': tariff.slug,
            },
            'limits': {
                'owners': {
                    'current': organization.owners_count,
                    'max': tariff.max_owners,
                    'remaining': max(0, tariff.max_owners - organization.owners_count),
                    'is_reached': organization.owners_count >= tariff.max_owners
                },
                'plots': {
                    'current': organization.plots_count,
                    'max': tariff.max_plots,
                    'remaining': max(0, tariff.max_plots - organization.plots_count),
                    'is_reached': organization.plots_count >= tariff.max_plots
                },
                'users': {
                    'current': organization.users_count,
                    'max': tariff.max_users,
                    'remaining': max(0, tariff.max_users - organization.users_count),
                    'is_reached': organization.users_count >= tariff.max_users
                }
            },
            'subscription': {
                'end_date': subscription.end_date,
                'days_left': subscription.days_left,
                'status': subscription.status
            }
        })


class ContactInfoViewSet(viewsets.ModelViewSet):
    """
    Отдельный ViewSet для управления контактами.
    Позволяет редактировать / удалять / подтверждать контакты напрямую.
    """
    queryset = ContactInfo.objects.select_related('owner').all()
    serializer_class = ContactInfoSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['owner', 'type', 'is_active', 'is_verified']

    def get_permissions(self):
        """Только менеджеры и выше могут изменять контакты"""
        if self.action in ('create', 'update', 'partial_update', 'destroy', 'verify', 'unverify'):
            return [permissions.IsAuthenticated(), IsManagerOrAbove()]
        return [permissions.IsAuthenticated()]
    
    def get_queryset(self):
        """Оптимизация запросов"""
        queryset = super().get_queryset()
        
        # Поиск по значению контакта
        search = self.request.query_params.get('search')
        if search:
            queryset = queryset.filter(value__icontains=search)
        
        return queryset

    @action(detail=True, methods=['post'], url_path='verify')
    def verify(self, request, pk=None):
        """
        POST /api/contacts/{id}/verify/
        Подтверждение контакта.
        """
        contact = self.get_object()
        contact.is_verified = True
        contact.note = f'{contact.note} | Подтверждён {timezone.now().strftime("%d.%m.%Y")}'.strip(' |')
        contact.save(update_fields=['is_verified', 'note'])
        
        logger.info(f'Контакт {contact.id} подтверждён')
        return Response({'detail': 'Контакт подтверждён.'})

    @action(detail=True, methods=['post'], url_path='unverify')
    def unverify(self, request, pk=None):
        """Снятие подтверждения."""
        contact = self.get_object()
        contact.is_verified = False
        contact.note = f'{contact.note} | Подтверждение снято {timezone.now().strftime("%d.%m.%Y")}'.strip(' |')
        contact.save(update_fields=['is_verified', 'note'])
        
        logger.info(f'Подтверждение контакта {contact.id} снято')
        return Response({'detail': 'Подтверждение снято.'})

    @action(detail=False, methods=['post'], url_path='deactivate-duplicates')
    def deactivate_duplicates(self, request):
        """
        POST /api/contacts/deactivate-duplicates/
        Автоматическая деактивация дубликатов контактов.
        """
        from django.db.models import Count
        
        duplicates = ContactInfo.objects.values(
            'owner', 'type', 'value'
        ).annotate(
            count=Count('id')
        ).filter(count__gt=1, is_active=True)
        
        deactivated_count = 0
        for dup in duplicates:
            contacts = ContactInfo.objects.filter(
                owner_id=dup['owner'],
                type=dup['type'],
                value=dup['value'],
                is_active=True
            ).order_by('created_at')
            
            # Оставляем первый активным, остальные деактивируем
            for contact in contacts[1:]:
                contact.is_active = False
                contact.note = f'{contact.note} | Дубликат, авто-деактивация {timezone.now().strftime("%d.%m.%Y")}'.strip(' |')
                contact.save(update_fields=['is_active', 'note'])
                deactivated_count += 1
        
        logger.info(f'Деактивировано дубликатов: {deactivated_count}')
        return Response({
            'detail': f'Деактивировано дубликатов: {deactivated_count}',
            'count': deactivated_count,
        })


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

    def get_permissions(self):
        """Только менеджеры и выше могут изменять права собственности"""
        if self.action in ('create', 'update', 'partial_update', 'destroy'):
            return [permissions.IsAuthenticated(), IsManagerOrAbove()]
        return [permissions.IsAuthenticated()]
    
    def get_queryset(self):
        """Расширенная фильтрация"""
        queryset = super().get_queryset()
        
        # Фильтрация по организации через членство
        organization = self.request.query_params.get('organization')
        if organization:
            queryset = queryset.filter(memberships__organization_id=organization, memberships__status='active')
        
        # Фильтрация по нескольким участкам
        land_plots = self.request.query_params.get('land_plot__in')
        if land_plots:
            plot_ids = [int(id) for id in land_plots.split(',') if id.isdigit()]
            queryset = queryset.filter(land_plot_id__in=plot_ids)
        
        # Фильтрация по нескольким владельцам
        owners = self.request.query_params.get('owner__in')
        if owners:
            owner_ids = [int(id) for id in owners.split(',') if id.isdigit()]
            queryset = queryset.filter(owner_id__in=owner_ids)
        
        return queryset

    def perform_update(self, serializer):
        """Логирование изменений права собственности"""
        instance = self.get_object()
        old_data = {
            'share': instance.share,
            'ownership_since': instance.ownership_since,
            'document_basis': instance.document_basis,
        }
        updated = serializer.save()
        
        changes = []
        for field in old_data:
            new_value = getattr(updated, field)
            if old_data[field] != new_value:
                changes.append(f'{field}: {old_data[field]} -> {new_value}')
        
        if changes:
            logger.info(f'Изменено право собственности {instance.id}: {", ".join(changes)}')


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