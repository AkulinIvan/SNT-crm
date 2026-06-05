from django.shortcuts import get_object_or_404, render
from django.views import View
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter, OrderingFilter
from django.db.models import Count, Sum, Q
from django.utils import timezone
from django.db import models
import logging
import traceback
from rest_framework import permissions
from decimal import Decimal

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
        logger.info(f"User {request.user.id} requesting dashboard stats")
        
        try:
            from payments.models import Assessment
            from land.models import LandPlot
            
            # Получаем организацию пользователя
            org = None
            if not request.user.is_superuser and not request.user.is_admin:
                org = getattr(request, 'current_organization', None)
                if org:
                    logger.debug(f"Filtering by organization: {org.id}")
            
            # Статистика владельцев
            from users.models import Owner
            owners_query = Owner.objects.all()
            if org:
                owners_query = owners_query.filter(memberships__organization=org, memberships__status='active')
            owners_count = owners_query.count()
            logger.debug(f"Owners count: {owners_count}")
            
            # Статистика участков
            plots_query = LandPlot.objects.all()
            if org:
                plots_query = plots_query.filter(organization=org)
            plots_total = plots_query.count()
            plots_active = plots_query.filter(status='active').count()
            logger.debug(f"Plots: total={plots_total}, active={plots_active}")
            
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
            
            total_debt = Decimal('0')
            for a in assessments_query:
                total_debt += a.debt
            
            logger.info(f"Dashboard stats: owners={owners_count}, plots={plots_total}, debt={total_debt}")
            
            return Response({
                'owners_count': owners_count,
                'plots_total': plots_total,
                'plots_active': plots_active,
                'organizations_count': orgs_count,
                'total_debt': float(total_debt),
            })
            
        except Exception as e:
            logger.error(f"Error in dashboard_stats: {e}\n{traceback.format_exc()}")
            return Response(
                {'detail': f'Ошибка получения статистики: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class OwnerViewSet(OrganizationMixin, viewsets.ModelViewSet):
    """
    ViewSet для управления владельцами.
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
        """Права доступа"""
        if self.action in ('create', 'update', 'partial_update', 'destroy'):
            return [permissions.IsAuthenticated(), IsManagerOrAbove()]
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
        """Упрощённый queryset без сложных аннотаций"""
        logger.debug(f"User {self.request.user.id} fetching owners queryset")
        
        try:
            # Базовый queryset
            queryset = Owner.objects.all()  

            # Фильтрация по организации
            if not (self.request.user.is_superuser or self.request.user.is_admin):
                org = getattr(self.request, 'current_organization', None)
                if org:
                    queryset = queryset.filter(
                        memberships__organization=org, 
                        memberships__status='active'
                    )
                    logger.debug(f"Filtered by organization: {org.id}")
                else:
                    logger.warning(f"User {self.request.user.id} has no organization")
                    queryset = queryset.none()  

            # Простая аннотация количества участков
            queryset = queryset.annotate(
                plots_count=Count('land_plots', distinct=True)
            )   

            # Фильтрация по наличию участков
            has_plots = self.request.query_params.get('has_plots')
            if has_plots is not None:
                if has_plots.lower() == 'true':
                    queryset = queryset.filter(plots_count__gt=0)
                    logger.debug("Filtered: has_plots=true")
                else:
                    queryset = queryset.filter(plots_count=0)
                    logger.debug("Filtered: has_plots=false")

            # Фильтрация по количеству участков
            plots_count = self.request.query_params.get('plots_count')
            if plots_count:
                queryset = queryset.filter(plots_count=int(plots_count))
                logger.debug(f"Filtered by plots_count={plots_count}")

            plots_count_min = self.request.query_params.get('plots_count_min')
            if plots_count_min:
                queryset = queryset.filter(plots_count__gte=int(plots_count_min))
                logger.debug(f"Filtered by plots_count_min={plots_count_min}")

            # Фильтрация по организации (через членство)
            organization = self.request.query_params.get('organization')
            if organization:
                queryset = queryset.filter(
                    memberships__organization_id=organization,
                    memberships__status='active'
                )
                logger.debug(f"Filtered by organization_id={organization}")

            # Фильтрация по дате
            created_after = self.request.query_params.get('created_after')
            if created_after:
                queryset = queryset.filter(created_at__gte=created_after)
                logger.debug(f"Filtered by created_after={created_after}")

            created_before = self.request.query_params.get('created_before')
            if created_before:
                queryset = queryset.filter(created_at__lte=created_before)
                logger.debug(f"Filtered by created_before={created_before}")

            # Фильтрация по контактам
            has_contacts = self.request.query_params.get('has_contacts')
            if has_contacts is not None:
                if has_contacts.lower() == 'true':
                    queryset = queryset.filter(contacts__isnull=False).distinct()
                    logger.debug("Filtered: has_contacts=true")
                elif has_contacts.lower() == 'false':
                    queryset = queryset.filter(contacts__isnull=True)
                    logger.debug("Filtered: has_contacts=false")

            has_phone = self.request.query_params.get('has_phone')
            if has_phone is not None and has_phone.lower() == 'true':
                queryset = queryset.filter(contacts__type='ph', contacts__is_active=True).distinct()
                logger.debug("Filtered: has_phone=true")

            has_email = self.request.query_params.get('has_email')
            if has_email is not None and has_email.lower() == 'true':
                queryset = queryset.filter(contacts__type='em', contacts__is_active=True).distinct()
                logger.debug("Filtered: has_email=true")

            phone_contains = self.request.query_params.get('phone_contains')
            if phone_contains:
                queryset = queryset.filter(
                    contacts__type='ph', 
                    contacts__value__icontains=phone_contains,
                    contacts__is_active=True
                ).distinct()
                logger.debug(f"Filtered by phone_contains={phone_contains}")

            email_contains = self.request.query_params.get('email_contains')
            if email_contains:
                queryset = queryset.filter(
                    contacts__type='em', 
                    contacts__value__icontains=email_contains,
                    contacts__is_active=True
                ).distinct()
                logger.debug(f"Filtered by email_contains={email_contains}")

            # Сортировка
            ordering = self.request.query_params.get('ordering', '')
            if ordering:
                if ordering in ('total_debt', '-total_debt'):
                    ordering = ordering.replace('total_debt', 'full_name')
                queryset = queryset.order_by(ordering)
                logger.debug(f"Ordering by: {ordering}")
            else:
                queryset = queryset.order_by('full_name')

            count = queryset.count()
            logger.debug(f"Queryset returned {count} records")
            return queryset.distinct()
            
        except Exception as e:
            logger.error(f"Error in get_queryset: {e}\n{traceback.format_exc()}")
            return Owner.objects.none()

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['request'] = self.request
        return context
    
    def list(self, request, *args, **kwargs):
        """Расширенный list с дополнительной статистикой"""
        logger.info(f"User {request.user.id} listing owners")
        
        try:
            queryset = self.filter_queryset(self.get_queryset())
            count = queryset.count()
            logger.debug(f"Total owners after filtering: {count}")

            # Простая пагинация
            page = self.paginate_queryset(queryset)
            if page is not None:
                serializer = self.get_serializer(page, many=True)
                response = self.get_paginated_response(serializer.data)

                # Добавляем статистику
                response.data['stats'] = {
                    'total': count,
                    'debtors': 0,
                    'with_plots': queryset.filter(plots_count__gt=0).count(),
                    'without_plots': queryset.filter(plots_count=0).count(),
                }
                logger.debug(f"Returning paginated response with {len(serializer.data)} items")
                return response

            serializer = self.get_serializer(queryset, many=True)
            return Response({
                'results': serializer.data,
                'count': count,
                'stats': {
                    'total': count,
                    'debtors': 0,
                    'with_plots': queryset.filter(plots_count__gt=0).count(),
                    'without_plots': queryset.filter(plots_count=0).count(),
                }
            })
            
        except Exception as e:
            logger.error(f"Error in list: {e}\n{traceback.format_exc()}")
            return Response(
                {'detail': f'Ошибка получения списка владельцев: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    def destroy(self, request, *args, **kwargs):
        """Безопасное удаление с проверкой зависимостей"""
        logger.info(f"User {request.user.id} attempting to delete owner")
        
        try:
            owner = self.get_object()
            logger.debug(f"Owner to delete: id={owner.id}, name={owner.full_name}")
            
            # Проверяем наличие активных начислений
            try:
                from payments.models import Assessment
                has_active_assessments = Assessment.objects.filter(
                    owner=owner,
                    status__in=['pending', 'partial', 'overdue']
                ).exists()
                
                if has_active_assessments:
                    logger.warning(f"Cannot delete owner {owner.id} - has active assessments")
                    return Response(
                        {
                            'detail': 'Невозможно удалить владельца с неоплаченными начислениями.',
                            'code': 'has_active_assessments'
                        },
                        status=status.HTTP_409_CONFLICT
                    )
            except ImportError:
                logger.debug("Payments app not installed, skipping assessment check")
            
            # Логируем удаление
            logger.info(f'Deleting owner: {owner.full_name} (ID: {owner.id})')
            
            result = super().destroy(request, *args, **kwargs)
            logger.info(f'Owner {owner.id} deleted successfully')
            return result
            
        except Exception as e:
            logger.error(f"Error deleting owner: {e}\n{traceback.format_exc()}")
            return Response(
                {'detail': f'Ошибка удаления владельца: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @action(detail=True, methods=['post'], url_path='add-plot')
    def add_plot(self, request, pk=None):
        """Привязка участка к владельцу"""
        logger.info(f"User {request.user.id} adding plot to owner {pk}")
        
        try:
            owner = self.get_object()
            plot_id = request.data.get('land_plot_id')
            
            if not plot_id:
                logger.warning("Missing land_plot_id in request")
                return Response(
                    {'detail': 'Необходимо указать land_plot_id'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            
            land_plot = get_object_or_404(LandPlot, pk=plot_id)
            logger.debug(f"Plot found: id={plot_id}, number={land_plot.plot_number}")
            
            # Проверяем, что участок принадлежит организации пользователя
            if self.request.current_organization and land_plot.organization != self.request.current_organization:
                logger.warning(f"Plot {plot_id} does not belong to organization {self.request.current_organization.id}")
                return Response(
                    {'detail': 'Этот участок не принадлежит вашему СНТ'},
                    status=status.HTTP_403_FORBIDDEN
                )
                
            # Проверка на существующую связь
            if Ownership.objects.filter(owner=owner, land_plot=land_plot).exists():
                logger.warning(f"Owner {owner.id} already has plot {plot_id}")
                return Response(
                    {'detail': 'Этот участок уже привязан к владельцу.'},
                    status=status.HTTP_409_CONFLICT,
                )
            
            # Проверка доли
            share = request.data.get('share', '1/1')
            if not self._validate_share(share):
                logger.warning(f"Invalid share format: {share}")
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
            logger.info(f'Plot {plot_id} linked to owner {owner.id}')
            
            return Response(serializer.data, status=status.HTTP_201_CREATED)
            
        except Exception as e:
            logger.error(f"Error adding plot: {e}\n{traceback.format_exc()}")
            return Response(
                {'detail': f'Ошибка привязки участка: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @action(detail=True, methods=['post'], url_path='remove-plot')
    def remove_plot(self, request, pk=None):
        """Отвязка участка от владельца"""
        logger.info(f"User {request.user.id} removing plot from owner {pk}")
        
        try:
            owner = self.get_object()
            plot_id = request.data.get('land_plot_id')
            
            if not plot_id:
                logger.warning("Missing land_plot_id in request")
                return Response(
                    {'detail': 'Необходимо указать land_plot_id'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            
            ownership = get_object_or_404(Ownership, owner=owner, land_plot_id=plot_id)
            logger.debug(f"Ownership found: id={ownership.id}")
            
            # Проверяем, есть ли активные начисления на этот участок
            try:
                from payments.models import Assessment
                has_assessments = Assessment.objects.filter(
                    owner=owner,
                    land_plot_id=plot_id,
                    status__in=['pending', 'partial', 'overdue']
                ).exists()
                
                if has_assessments:
                    logger.warning(f"Cannot remove plot {plot_id} - has active assessments")
                    return Response(
                        {
                            'detail': 'Невозможно отвязать участок с неоплаченными начислениями.',
                            'code': 'has_active_assessments'
                        },
                        status=status.HTTP_409_CONFLICT,
                    )
            except ImportError:
                logger.debug("Payments app not installed, skipping assessment check")
            
            ownership.delete()
            logger.info(f'Plot {plot_id} unlinked from owner {owner.id}')
            
            return Response(status=status.HTTP_204_NO_CONTENT)
            
        except Exception as e:
            logger.error(f"Error removing plot: {e}\n{traceback.format_exc()}")
            return Response(
                {'detail': f'Ошибка отвязки участка: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @action(detail=True, methods=['post'], url_path='add-contact')
    def add_contact(self, request, pk=None):
        """Добавление контакта владельцу"""
        logger.info(f"User {request.user.id} adding contact to owner {pk}")
        
        try:
            owner = self.get_object()

            # Создаём копию данных с добавлением owner
            data = request.data.copy()
            data['owner'] = owner.id

            serializer = ContactInfoSerializer(
                data=data,
                context={'request': request},
            )

            if serializer.is_valid():
                contact = serializer.save(owner=owner)
                logger.info(f'Contact {contact.id} added for owner {owner.id}')
                return Response(serializer.data, status=status.HTTP_201_CREATED)

            logger.warning(f"Invalid contact data: {serializer.errors}")
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
            
        except Exception as e:
            logger.error(f"Error adding contact: {e}\n{traceback.format_exc()}")
            return Response(
                {'detail': f'Ошибка добавления контакта: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @action(detail=True, methods=['post'], url_path='deactivate-contact')
    def deactivate_contact(self, request, pk=None):
        """Деактивация контакта"""
        logger.info(f"User {request.user.id} deactivating contact for owner {pk}")
        
        try:
            owner = self.get_object()
            contact_id = request.data.get('contact_id')
            
            if not contact_id:
                logger.warning("Missing contact_id in request")
                return Response(
                    {'detail': 'Необходимо указать contact_id'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            
            contact = get_object_or_404(ContactInfo, pk=contact_id, owner=owner)
            
            contact.is_active = False
            contact.note = f'{contact.note} | Деактивирован {timezone.now().strftime("%d.%m.%Y %H:%M")}'.strip(' |')
            contact.save(update_fields=['is_active', 'note'])
            
            logger.info(f'Contact {contact_id} deactivated for owner {owner.id}')
            return Response({'detail': 'Контакт деактивирован.'})
            
        except Exception as e:
            logger.error(f"Error deactivating contact: {e}\n{traceback.format_exc()}")
            return Response(
                {'detail': f'Ошибка деактивации контакта: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @action(detail=True, methods=['get'], url_path='stats')
    def owner_stats(self, request, pk=None):
        """Статистика по владельцу"""
        logger.info(f"User {request.user.id} requesting stats for owner {pk}")
        
        try:
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
            
            logger.debug(f"Stats for owner {pk}: plots={stats['plots_count']}, debt={stats['total_debt']}")
            return Response(stats)
            
        except Exception as e:
            logger.error(f"Error getting owner stats: {e}\n{traceback.format_exc()}")
            return Response(
                {'detail': f'Ошибка получения статистики: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @action(detail=False, methods=['get'], url_path='export')
    def export_owners(self, request):
        """Экспорт владельцев в CSV"""
        logger.info(f"User {request.user.id} exporting owners to CSV")
        
        try:
            import csv
            from django.http import HttpResponse

            owners = self.filter_queryset(self.get_queryset())
            count = owners.count()
            logger.info(f"Exporting {count} owners")
            
            timestamp = timezone.now().strftime('%Y%m%d_%H%M%S')

            response = HttpResponse(content_type='text/csv; charset=utf-8')
            response['Content-Disposition'] = f'attachment; filename="owners_{timestamp}.csv"'

            # Добавляем BOM для корректного отображения кириллицы в Excel
            response.write('\ufeff')

            # Используем точку с запятой как разделитель
            writer = csv.writer(response, delimiter=';')

            # Заголовки
            writer.writerow([
                'ID', 'ФИО', 'Телефон', 'Email', 'Номера участков',
                'Кадастровые номера', 'Площади участков (м²)', 'СНТ',
                'Кол-во участков', 'Долг (₽)', 'Дата добавления'
            ])

            # Данные
            for owner in owners:
                try:
                    ownerships = owner.ownerships.select_related('land_plot').all()

                    if ownerships:
                        plot_numbers = []
                        cadastral_numbers = []
                        areas = []

                        for ownership in ownerships:
                            plot = ownership.land_plot
                            if plot:
                                plot_numbers.append(plot.plot_number)
                                cadastral_numbers.append(plot.cadastral_number or '')
                                areas.append(f"{plot.area_sqm:.2f}" if plot.area_sqm else '')

                        writer.writerow([
                            owner.id,
                            owner.full_name,
                            owner.primary_phone or '',
                            owner.primary_email or '',
                            ', '.join(plot_numbers),
                            ', '.join(cadastral_numbers),
                            ', '.join(areas),
                            owner.organization_name or '',
                            owner.plots_count,
                            f"{owner.total_debt:.2f}" if owner.total_debt else "0.00",
                            owner.created_at.strftime('%d.%m.%Y'),
                        ])
                    else:
                        writer.writerow([
                            owner.id,
                            owner.full_name,
                            owner.primary_phone or '',
                            owner.primary_email or '',
                            '', '', '', owner.organization_name or '',
                            owner.plots_count,
                            f"{owner.total_debt:.2f}" if owner.total_debt else "0.00",
                            owner.created_at.strftime('%d.%m.%Y'),
                        ])
                except Exception as e:
                    logger.error(f"Error exporting owner {owner.id}: {e}")
                    continue

            logger.info(f"Export completed for {count} owners")
            return response
            
        except Exception as e:
            logger.error(f"Error exporting owners: {e}\n{traceback.format_exc()}")
            return Response(
                {'detail': f'Ошибка экспорта: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    def _validate_share(self, share_str):
        """Валидация строки доли"""
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
        logger.info(f"User {request.user.id} creating new owner")
        
        try:
            organization = request.current_organization
            
            if organization:
                try:
                    is_allowed, current, max_limit, message = organization.check_tariff_limit('owners')
                    
                    if not is_allowed:
                        logger.warning(f"Tariff limit reached for organization {organization.id}: {message}")
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
                    logger.error(f"Error checking tariff limits: {e}")
            
            result = super().create(request, *args, **kwargs)
            logger.info(f"Owner created successfully, ID: {result.data.get('id') if hasattr(result, 'data') else 'unknown'}")
            return result
            
        except Exception as e:
            logger.error(f"Error creating owner: {e}\n{traceback.format_exc()}")
            return Response(
                {'detail': f'Ошибка создания владельца: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=False, methods=['get'], url_path='tariff-info')
    def tariff_info(self, request):
        """Получить информацию о тарифных лимитах"""
        logger.info(f"User {request.user.id} requesting tariff info")
        
        try:
            organization = request.current_organization
            
            if not organization:
                logger.warning(f"No organization found for user {request.user.id}")
                return Response({'detail': 'Организация не найдена'}, status=status.HTTP_404_NOT_FOUND)
            
            subscription = getattr(organization, 'subscription', None)
            
            if not subscription or not subscription.is_active:
                logger.warning(f"No active subscription for organization {organization.id}")
                return Response({
                    'has_subscription': False,
                    'message': 'Нет активной подписки'
                })
            
            tariff = subscription.tariff
            
            response_data = {
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
            }
            
            logger.debug(f"Tariff info returned for organization {organization.id}")
            return Response(response_data)
            
        except Exception as e:
            logger.error(f"Error getting tariff info: {e}\n{traceback.format_exc()}")
            return Response(
                {'detail': f'Ошибка получения информации о тарифе: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class ContactInfoViewSet(viewsets.ModelViewSet):
    """ViewSet для управления контактами"""
    
    queryset = ContactInfo.objects.select_related('owner').all()
    serializer_class = ContactInfoSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['owner', 'type', 'is_active', 'is_verified']

    def get_permissions(self):
        if self.action in ('create', 'update', 'partial_update', 'destroy', 'verify', 'unverify'):
            return [permissions.IsAuthenticated(), IsManagerOrAbove()]
        return [permissions.IsAuthenticated()]
    
    def get_queryset(self):
        logger.debug(f"User {self.request.user.id} fetching contacts")
        
        try:
            queryset = super().get_queryset()
            
            search = self.request.query_params.get('search')
            if search:
                queryset = queryset.filter(value__icontains=search)
                logger.debug(f"Searching contacts with: {search}")
            
            return queryset
            
        except Exception as e:
            logger.error(f"Error in ContactInfoViewSet.get_queryset: {e}")
            return ContactInfo.objects.none()

    @action(detail=True, methods=['post'], url_path='verify')
    def verify(self, request, pk=None):
        """Подтверждение контакта"""
        logger.info(f"User {request.user.id} verifying contact {pk}")
        
        try:
            contact = self.get_object()
            contact.is_verified = True
            contact.note = f'{contact.note} | Подтверждён {timezone.now().strftime("%d.%m.%Y")}'.strip(' |')
            contact.save(update_fields=['is_verified', 'note'])
            
            logger.info(f'Contact {contact.id} verified')
            return Response({'detail': 'Контакт подтверждён.'})
            
        except Exception as e:
            logger.error(f"Error verifying contact: {e}")
            return Response(
                {'detail': f'Ошибка подтверждения контакта: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @action(detail=True, methods=['post'], url_path='unverify')
    def unverify(self, request, pk=None):
        """Снятие подтверждения"""
        logger.info(f"User {request.user.id} unverifying contact {pk}")
        
        try:
            contact = self.get_object()
            contact.is_verified = False
            contact.note = f'{contact.note} | Подтверждение снято {timezone.now().strftime("%d.%m.%Y")}'.strip(' |')
            contact.save(update_fields=['is_verified', 'note'])
            
            logger.info(f'Contact {contact.id} unverified')
            return Response({'detail': 'Подтверждение снято.'})
            
        except Exception as e:
            logger.error(f"Error unverifying contact: {e}")
            return Response(
                {'detail': f'Ошибка снятия подтверждения: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class OwnershipViewSet(viewsets.ModelViewSet):
    """ViewSet для работы с правами собственности"""
    
    queryset = Ownership.objects.select_related('owner', 'land_plot').all()
    serializer_class = OwnershipSerializer
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = ['owner', 'land_plot']
    ordering_fields = ['ownership_since']

    def get_permissions(self):
        if self.action in ('create', 'update', 'partial_update', 'destroy'):
            return [permissions.IsAuthenticated(), IsManagerOrAbove()]
        return [permissions.IsAuthenticated()]
    
    def perform_update(self, serializer):
        """Логирование изменений"""
        try:
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
                logger.info(f'Ownership {instance.id} updated: {", ".join(changes)}')
                
        except Exception as e:
            logger.error(f"Error in perform_update: {e}")


class OwnerListView(View):
    """Страница со списком владельцев"""
    
    def get(self, request):
        logger.info(f"User {request.user.id} accessing owner list page")
        return render(request, 'users/list.html', {'active_page': 'owners'})


class OwnerDetailView(View):
    """Страница карточки владельца"""
    
    def get(self, request, pk):
        logger.info(f"User {request.user.id} accessing owner detail page for owner {pk}")
        return render(request, 'users/detail.html', {
            'active_page': 'owners',
            'owner_id': pk,
        })


class DashboardView(View):
    """Главная страница дашборда"""
    
    def get(self, request):
        logger.info(f"User {request.user.id} accessing dashboard")
        return render(request, 'users/dashboard.html', {'active_page': 'dashboard'})