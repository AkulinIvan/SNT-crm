# organizations/views.py
import logging
import re
from typing import Optional

from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter, OrderingFilter
from django.views.generic import TemplateView
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.db import transaction, DatabaseError, IntegrityError
from django.core.exceptions import ValidationError, PermissionDenied

from accounts.models import UserActionLog
from .models import Organization, OrganizationMembership, OrganizationStaffAssignment
from .serializers import (
    OrganizationSerializer, 
    OrganizationDetailSerializer,
    OrganizationMembershipSerializer,
    OrganizationMembershipCreateSerializer
)
from accounts.permissions import IsAdminOrSuperuser, IsManagerOrAbove
from users.models import Owner

User = get_user_model()
logger = logging.getLogger(__name__)


class OrganizationViewSet(viewsets.ModelViewSet):
    """
    ViewSet для управления СНТ.
    
    Endpoints:
    - GET /api/organizations/ - список СНТ
    - POST /api/organizations/ - создание СНТ
    - GET /api/organizations/{id}/ - детали СНТ
    - PUT/PATCH /api/organizations/{id}/ - обновление СНТ
    - DELETE /api/organizations/{id}/ - удаление СНТ
    - GET /api/organizations/{id}/members/ - члены СНТ
    - POST /api/organizations/{id}/add-member/ - добавить члена
    - GET /api/organizations/{id}/stats/ - статистика СНТ
    - POST /api/organizations/{id}/assign-chairman/ - назначить председателя
    - GET /api/organizations/{id}/staff-history/ - история назначений
    """
    queryset = Organization.objects.all()
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    search_fields = ['name', 'short_name', 'inn']
    ordering_fields = ['name', 'created_at', 'is_active']
    ordering = ['name']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._logger = logging.getLogger(f'{__name__}.OrganizationViewSet')

    def get_permissions(self):
        """Определение прав доступа"""
        try:
            if self.action in ('create', 'update', 'partial_update', 'destroy'):
                return [permissions.IsAuthenticated(), IsAdminOrSuperuser()]
            return [permissions.IsAuthenticated()]
        except Exception as e:
            self._logger.error(f"Error determining permissions: {e}", exc_info=True)
            return [permissions.IsAuthenticated()]

    def get_serializer_class(self):
        """Выбор сериализатора"""
        if self.action == 'list':
            return OrganizationSerializer
        return OrganizationDetailSerializer

    def get_queryset(self):
        """
        Фильтрация организаций:
        - Админы видят все
        - Остальные видят только свои организации
        """
        try:
            queryset = super().get_queryset()
            user = self.request.user
            
            # Админы и суперпользователи видят все
            if user.is_superuser or user.is_admin:
                self._logger.debug(f"Admin user '{user.username}' sees all organizations")
                return queryset
            
            # Собираем все организации пользователя
            organization_ids = self._get_user_organization_ids(user)
            
            if organization_ids:
                self._logger.debug(
                    f"User '{user.username}' sees {len(organization_ids)} organizations"
                )
                return queryset.filter(id__in=organization_ids)
            
            self._logger.debug(f"User '{user.username}' has no organizations")
            return queryset.none()
            
        except DatabaseError as e:
            self._logger.error(f"Database error in get_queryset: {e}", exc_info=True)
            return Organization.objects.none()
        except Exception as e:
            self._logger.error(f"Critical error in get_queryset: {e}", exc_info=True)
            return Organization.objects.none()

    def _get_user_organization_ids(self, user) -> set:
        """Собрать все ID организаций пользователя"""
        organization_ids = set()
        
        try:
            # 1. Организации, где пользователь - сотрудник
            staff_orgs = OrganizationStaffAssignment.objects.filter(
                user=user,
                is_active=True
            ).values_list('organization_id', flat=True)
            organization_ids.update(staff_orgs)
            self._logger.debug(f"Staff organizations: {list(staff_orgs)}")
            
            # 2. Организация, где пользователь - председатель
            if hasattr(user, 'chaired_organizations'):
                chaired = user.chaired_organizations.all().values_list('id', flat=True)
                organization_ids.update(chaired)
            elif hasattr(user, 'chairman_profile') and user.chairman_profile:
                organization_ids.add(user.chairman_profile.id)
            
            # 3. Организация, где пользователь - бухгалтер
            if hasattr(user, 'accountant_organizations'):
                accountant_orgs = user.accountant_organizations.all().values_list('id', flat=True)
                organization_ids.update(accountant_orgs)
            
            # 4. Организация через поле organization
            if user.organization_id:
                organization_ids.add(user.organization.id)
            
            # 5. Организации, где пользователь - владелец (член СНТ)
            owner = getattr(user, 'owner_profile', None)
            if owner:
                member_orgs = OrganizationMembership.objects.filter(
                    owner=owner,
                    status='active'
                ).values_list('organization_id', flat=True)
                organization_ids.update(member_orgs)
                
        except Exception as e:
            self._logger.error(f"Error collecting organization IDs: {e}", exc_info=True)
        
        return organization_ids

    def list(self, request, *args, **kwargs):
        """Список организаций с логированием"""
        self._logger.info(f"Listing organizations for user: {request.user.username}")
        
        try:
            return super().list(request, *args, **kwargs)
        except DatabaseError as e:
            self._logger.error(f"Database error in list: {e}", exc_info=True)
            return Response(
                {'detail': 'Ошибка базы данных при получении списка организаций'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
        except Exception as e:
            self._logger.error(f"Error in list: {e}", exc_info=True)
            return Response(
                {'detail': 'Внутренняя ошибка сервера'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    def create(self, request, *args, **kwargs):
        """Создание организации с логированием"""
        self._logger.info(
            f"Creating organization by user: {request.user.username}, "
            f"name: {request.data.get('short_name', 'unknown')}"
        )
        
        try:
            with transaction.atomic():
                response = super().create(request, *args, **kwargs)
                
                if response.status_code == status.HTTP_201_CREATED:
                    org_id = response.data.get('id')
                    org_name = response.data.get('short_name')
                    self._logger.info(f"Organization created: {org_name} (ID: {org_id})")
                    
                    # Логируем действие
                    self._create_action_log(
                        action='create',
                        object_id=org_id,
                        details=f'Создано СНТ: {org_name}'
                    )
                
                return response
                
        except IntegrityError as e:
            self._logger.error(f"Integrity error creating organization: {e}", exc_info=True)
            return Response(
                {'detail': 'Организация с такими данными уже существует'},
                status=status.HTTP_409_CONFLICT
            )
        except ValidationError as e:
            self._logger.warning(f"Validation error: {e}")
            return Response(
                {'detail': str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )
        except Exception as e:
            self._logger.error(f"Error creating organization: {e}", exc_info=True)
            return Response(
                {'detail': 'Ошибка при создании организации'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    def update(self, request, *args, **kwargs):
        """Обновление с поддержкой chairman_id и accountant_id"""
        instance = self.get_object()
        
        self._logger.info(
            f"Updating organization: {instance.short_name} (ID: {instance.id}) "
            f"by user: {request.user.username}"
        )
        
        try:
            partial = kwargs.pop('partial', False)
            data = request.data.copy()
            
            # Обработка председателя
            if 'chairman_id' in data:
                self._handle_chairman_update(data, instance)
            
            # Обработка бухгалтера
            if 'accountant_id' in data:
                self._handle_accountant_update(data, instance)
            
            with transaction.atomic():
                serializer = self.get_serializer(instance, data=data, partial=partial)
                serializer.is_valid(raise_exception=True)
                self.perform_update(serializer)
                
                if getattr(instance, '_prefetched_objects_cache', None):
                    instance._prefetched_objects_cache = {}
                
                self._logger.info(f"Organization updated: {instance.short_name}")
                
                return Response(serializer.data)
                
        except ValidationError as e:
            self._logger.warning(f"Validation error updating organization: {e}")
            return Response(
                {'detail': str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )
        except Exception as e:
            self._logger.error(f"Error updating organization: {e}", exc_info=True)
            return Response(
                {'detail': 'Ошибка при обновлении организации'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    def _handle_chairman_update(self, data: dict, instance: Organization):
        """Обработка обновления председателя"""
        chairman_id = data.pop('chairman_id')
        
        if chairman_id:
            try:
                chairman = User.objects.get(id=chairman_id, is_active=True)
                # Создаем запись в истории назначений
                OrganizationStaffAssignment.assign_staff(
                    organization=instance,
                    user=chairman,
                    role='chairman',
                    position_title='Председатель правления'
                )
                data['chairman'] = chairman.id
                self._logger.info(f"Chairman assigned: {chairman.full_name}")
            except User.DoesNotExist:
                self._logger.warning(f"Chairman user {chairman_id} not found")
        else:
            # Деактивируем текущего председателя
            OrganizationStaffAssignment.objects.filter(
                organization=instance,
                role='chairman',
                is_active=True
            ).update(is_active=False, assigned_until=timezone.now())
            data['chairman'] = None
            self._logger.info(f"Chairman removed from {instance.short_name}")

    def _handle_accountant_update(self, data: dict, instance: Organization):
        """Обработка обновления бухгалтера"""
        accountant_id = data.pop('accountant_id')
        
        if accountant_id:
            try:
                accountant = User.objects.get(id=accountant_id, is_active=True)
                OrganizationStaffAssignment.assign_staff(
                    organization=instance,
                    user=accountant,
                    role='accountant',
                    position_title='Бухгалтер'
                )
                data['accountant'] = accountant.id
                self._logger.info(f"Accountant assigned: {accountant.full_name}")
            except User.DoesNotExist:
                self._logger.warning(f"Accountant user {accountant_id} not found")
        else:
            OrganizationStaffAssignment.objects.filter(
                organization=instance,
                role='accountant',
                is_active=True
            ).update(is_active=False, assigned_until=timezone.now())
            data['accountant'] = None
            self._logger.info(f"Accountant removed from {instance.short_name}")

    def destroy(self, request, *args, **kwargs):
        """Удаление организации с проверками"""
        instance = self.get_object()
        
        self._logger.warning(
            f"Attempting to delete organization: {instance.short_name} "
            f"(ID: {instance.id}) by user: {request.user.username}"
        )
        
        try:
            # Проверяем наличие связанных данных
            plots_count = instance.land_plots.count()
            members_count = instance.memberships.count()
            
            if plots_count > 0 or members_count > 0:
                self._logger.warning(
                    f"Cannot delete organization {instance.short_name}: "
                    f"has {plots_count} plots and {members_count} members"
                )
                return Response(
                    {
                        'detail': 'Невозможно удалить СНТ с участками или членами.',
                        'code': 'has_related_data',
                        'plots_count': plots_count,
                        'members_count': members_count,
                    },
                    status=status.HTTP_409_CONFLICT
                )
            
            with transaction.atomic():
                org_name = instance.short_name
                instance.delete()
                
                self._create_action_log(
                    action='delete',
                    object_id=instance.id,
                    details=f'Удалено СНТ: {org_name}'
                )
                
                self._logger.info(f"Organization deleted: {org_name}")
                
                return Response(status=status.HTTP_204_NO_CONTENT)
                
        except IntegrityError as e:
            self._logger.error(f"Integrity error deleting organization: {e}", exc_info=True)
            return Response(
                {'detail': 'Невозможно удалить организацию: есть связанные данные'},
                status=status.HTTP_409_CONFLICT
            )
        except Exception as e:
            self._logger.error(f"Error deleting organization: {e}", exc_info=True)
            return Response(
                {'detail': 'Ошибка при удалении организации'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @action(detail=True, methods=['get'], url_path='members')
    def get_members(self, request, pk=None):
        """Получить список членов СНТ"""
        organization = self.get_object()
        
        self._logger.info(
            f"Getting members for organization: {organization.short_name}"
        )
        
        try:
            memberships = organization.memberships.select_related('owner').all()
            
            # Фильтрация по статусу
            status_filter = request.query_params.get('status')
            if status_filter:
                memberships = memberships.filter(status=status_filter)
                self._logger.debug(f"Filtered members by status: {status_filter}")
            
            count = memberships.count()
            self._logger.info(f"Found {count} members")
            
            serializer = OrganizationMembershipSerializer(memberships, many=True)
            return Response({
                'count': count,
                'results': serializer.data
            })
            
        except Exception as e:
            self._logger.error(f"Error getting members: {e}", exc_info=True)
            return Response(
                {'detail': 'Ошибка при получении списка членов'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @action(detail=True, methods=['post'], url_path='add-member')
    def add_member(self, request, pk=None):
        """Добавить владельца в члены СНТ"""
        organization = self.get_object()
        
        owner_id = request.data.get('owner')
        self._logger.info(
            f"Adding member to {organization.short_name}: owner_id={owner_id}"
        )
        
        try:
            serializer = OrganizationMembershipCreateSerializer(
                data=request.data,
                context={'organization': organization}
            )
            
            if serializer.is_valid():
                with transaction.atomic():
                    membership = serializer.save(organization=organization)
                    
                    self._logger.info(
                        f"Member added: {membership.owner.full_name} -> {organization.short_name}"
                    )
                    
                    return Response(
                        OrganizationMembershipSerializer(membership).data,
                        status=status.HTTP_201_CREATED
                    )
            
            self._logger.warning(f"Invalid membership data: {serializer.errors}")
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
            
        except IntegrityError as e:
            self._logger.error(f"Integrity error adding member: {e}", exc_info=True)
            return Response(
                {'detail': 'Этот владелец уже является членом СНТ'},
                status=status.HTTP_409_CONFLICT
            )
        except Exception as e:
            self._logger.error(f"Error adding member: {e}", exc_info=True)
            return Response(
                {'detail': 'Ошибка при добавлении члена'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @action(detail=True, methods=['get'], url_path='stats')
    def stats(self, request, pk=None):
        """Статистика по СНТ"""
        organization = self.get_object()
        
        self._logger.info(f"Getting stats for organization: {organization.short_name}")
        
        try:
            active_memberships = organization.memberships.filter(status='active')
            owners = [m.owner for m in active_memberships]
            owner_ids = [o.id for o in owners]
            
            from land.models import LandPlot
            plots = LandPlot.objects.filter(ownerships__owner__id__in=owner_ids).distinct()
            
            stats = {
                'id': organization.id,
                'name': organization.short_name,
                'total_members': active_memberships.count(),
                'total_plots': plots.count(),
                'total_owners': len(owners),
                'staff_count': organization.staff_members.filter(is_active=True).count(),
            }
            
            # Расчет задолженностей
            debt_info = self._calculate_debt_stats(owners)
            stats.update(debt_info)
            
            self._logger.info(f"Stats for {organization.short_name}: {stats}")
            return Response(stats)
            
        except Exception as e:
            self._logger.error(f"Error calculating stats: {e}", exc_info=True)
            return Response(
                {'detail': 'Ошибка при расчете статистики'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    def _calculate_debt_stats(self, owners: list) -> dict:
        """Расчет статистики задолженностей"""
        try:
            from payments.models import Assessment
            
            total_debt = 0
            overdue_count = 0
            
            for owner in owners:
                debt = getattr(owner, 'total_debt', 0)
                total_debt += debt
                
                overdue = Assessment.objects.filter(
                    owner=owner,
                    status='overdue'
                ).count()
                overdue_count += overdue
            
            return {
                'total_debt': float(total_debt),
                'overdue_count': overdue_count,
            }
        except ImportError:
            self._logger.debug("Payments module not available")
            return {'total_debt': 0, 'overdue_count': 0}
        except Exception as e:
            self._logger.error(f"Error calculating debt: {e}", exc_info=True)
            return {'total_debt': 0, 'overdue_count': 0}

    @action(detail=True, methods=['post'], url_path='assign-chairman')
    def assign_chairman(self, request, pk=None):
        """
        Назначить нового председателя.
        Автоматически деактивирует предыдущего.
        """
        organization = self.get_object()
        
        self._logger.info(
            f"Assigning chairman for {organization.short_name} "
            f"by user: {request.user.username}"
        )
        
        try:
            user_id = request.data.get('user_id')
            if not user_id:
                return Response(
                    {'detail': 'Укажите user_id'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            user = User.objects.get(id=user_id, is_active=True)
            
            assignment_order = request.data.get('assignment_order', '')
            
            with transaction.atomic():
                # Создаем новое назначение (старое деактивируется автоматически)
                assignment = OrganizationStaffAssignment.assign_staff(
                    organization=organization,
                    user=user,
                    role='chairman',
                    position_title='Председатель правления',
                    assignment_order=assignment_order
                )
                
                # Логируем
                self._create_action_log(
                    action='update',
                    object_id=organization.id,
                    details=f'Назначен новый председатель: {user.full_name}'
                )
                
                self._logger.info(
                    f"Chairman assigned: {user.full_name} -> {organization.short_name}"
                )
                
                return Response({
                    'detail': 'Председатель назначен',
                    'chairman': {
                        'id': user.id,
                        'full_name': user.full_name,
                    },
                    'assigned_at': assignment.assigned_at,
                })
                
        except User.DoesNotExist:
            self._logger.warning(f"User {user_id} not found for chairman assignment")
            return Response(
                {'detail': 'Пользователь не найден'},
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            self._logger.error(f"Error assigning chairman: {e}", exc_info=True)
            return Response(
                {'detail': 'Ошибка при назначении председателя'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @action(detail=True, methods=['post'], url_path='assign-chairman-from-owner')
    def assign_chairman_from_owner(self, request, pk=None):
        """
        Назначить председателя из владельцев с опциональным созданием аккаунта.
        """
        organization = self.get_object()
        
        self._logger.info(
            f"Assigning chairman from owner for {organization.short_name}"
        )
        
        try:
            owner_id = request.data.get('owner_id')
            if not owner_id:
                return Response(
                    {'detail': 'Укажите owner_id'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            owner = Owner.objects.get(id=owner_id)
            
            create_account = request.data.get('create_account', False)
            
            user = None
            account_created = False
            generated_password = None
            
            if create_account:
                user, account_created, generated_password = self._create_user_for_owner(
                    owner, organization, role='manager'
                )
            
            with transaction.atomic():
                if user:
                    OrganizationStaffAssignment.assign_staff(
                        organization=organization,
                        user=user,
                        role='chairman',
                        position_title='Председатель правления'
                    )
                
                # Логируем
                self._create_action_log(
                    action='update',
                    object_id=organization.id,
                    details=f'Назначен председатель из владельца: {owner.full_name}'
                )
                
                self._logger.info(
                    f"Chairman from owner assigned: {owner.full_name} "
                    f"-> {organization.short_name}"
                )
                
                response_data = {
                    'detail': 'Председатель назначен',
                    'owner_id': owner.id,
                    'owner_name': owner.full_name,
                }
                
                if account_created and user:
                    response_data.update({
                        'account_created': True,
                        'user_id': user.id,
                        'username': user.username,
                        'password': generated_password,
                    })
                
                return Response(response_data)
                
        except Owner.DoesNotExist:
            return Response(
                {'detail': 'Владелец не найден'},
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            self._logger.error(f"Error assigning chairman from owner: {e}", exc_info=True)
            return Response(
                {'detail': 'Ошибка при назначении председателя'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @action(detail=True, methods=['post'], url_path='assign-accountant-from-owner')
    def assign_accountant_from_owner(self, request, pk=None):
        """
        Назначить бухгалтера из владельцев с опциональным созданием аккаунта.
        """
        organization = self.get_object()
        
        self._logger.info(
            f"Assigning accountant from owner for {organization.short_name}"
        )
        
        try:
            owner_id = request.data.get('owner_id')
            if not owner_id:
                return Response(
                    {'detail': 'Укажите owner_id'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            owner = Owner.objects.get(id=owner_id)
            
            create_account = request.data.get('create_account', False)
            
            user = None
            account_created = False
            generated_password = None
            
            if create_account:
                user, account_created, generated_password = self._create_user_for_owner(
                    owner, organization, role='accountant'
                )
                # Обновляем роль если пользователь уже существует
                if user and not account_created and user.role not in ['admin', 'manager', 'accountant']:
                    user.role = 'accountant'
                    user.save(update_fields=['role'])
            
            with transaction.atomic():
                if user:
                    OrganizationStaffAssignment.assign_staff(
                        organization=organization,
                        user=user,
                        role='accountant',
                        position_title='Бухгалтер'
                    )
                
                self._create_action_log(
                    action='update',
                    object_id=organization.id,
                    details=f'Назначен бухгалтер из владельца: {owner.full_name}'
                )
                
                self._logger.info(
                    f"Accountant from owner assigned: {owner.full_name} "
                    f"-> {organization.short_name}"
                )
                
                response_data = {
                    'detail': 'Бухгалтер назначен',
                    'owner_id': owner.id,
                    'owner_name': owner.full_name,
                }
                
                if account_created and user:
                    response_data.update({
                        'account_created': True,
                        'user_id': user.id,
                        'username': user.username,
                        'password': generated_password,
                    })
                
                return Response(response_data)
                
        except Owner.DoesNotExist:
            return Response(
                {'detail': 'Владелец не найден'},
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            self._logger.error(f"Error assigning accountant from owner: {e}", exc_info=True)
            return Response(
                {'detail': 'Ошибка при назначении бухгалтера'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    def _create_user_for_owner(self, owner: Owner, organization: Organization, role: str = 'manager') -> tuple:
        """
        Создать или найти пользователя для владельца.
        
        Returns:
            tuple: (user, account_created, password)
        """
        owner_email = owner.primary_email
        owner_phone = owner.primary_phone
        
        # Ищем существующего пользователя
        existing_user = None
        if owner_email:
            existing_user = User.objects.filter(email=owner_email).first()
        
        if not existing_user and owner_phone:
            existing_user = User.objects.filter(phone=owner_phone).first()
        
        if existing_user:
            self._logger.info(f"Found existing user for owner {owner.full_name}: {existing_user.username}")
            return existing_user, False, None
        
        # Генерируем логин
        username = owner_phone or owner.full_name.lower().replace(' ', '.')
        username = re.sub(r'[^\w.]', '', username)
        
        base_username = username
        counter = 1
        while User.objects.filter(username=username).exists():
            username = f"{base_username}{counter}"
            counter += 1
        
        # Генерируем пароль
        password = User.objects.make_random_password()
        
        # Разбираем ФИО
        name_parts = owner.full_name.split()
        last_name = name_parts[0] if name_parts else owner.full_name
        first_name = name_parts[1] if len(name_parts) > 1 else ''
        
        # Создаем пользователя
        user = User.objects.create_user(
            username=username,
            email=owner_email,
            password=password,
            first_name=first_name,
            last_name=last_name,
            phone=owner_phone,
            role=role,
            is_active=True,
            organization=organization,
        )
        
        self._logger.info(f"Created user for owner {owner.full_name}: {username}")
        return user, True, password

    @action(detail=True, methods=['get'], url_path='staff-history')
    def staff_history(self, request, pk=None):
        """История всех назначений сотрудников"""
        organization = self.get_object()
        
        self._logger.info(f"Getting staff history for {organization.short_name}")
        
        try:
            assignments = organization.staff_assignments.select_related('user').all()
            
            # Фильтрация
            role = request.query_params.get('role')
            if role:
                assignments = assignments.filter(role=role)
            
            is_active = request.query_params.get('is_active')
            if is_active is not None:
                assignments = assignments.filter(is_active=is_active.lower() == 'true')
            
            data = []
            for assignment in assignments:
                data.append({
                    'id': assignment.id,
                    'user_id': assignment.user_id,
                    'user_name': assignment.user.full_name,
                    'role': assignment.role,
                    'role_display': assignment.get_role_display(),
                    'position_title': assignment.position_title,
                    'assigned_at': assignment.assigned_at,
                    'assigned_until': assignment.assigned_until,
                    'is_active': assignment.is_active,
                    'assignment_order': assignment.assignment_order,
                })
            
            self._logger.info(f"Staff history: {len(data)} records")
            
            return Response({
                'count': len(data),
                'results': data,
            })
            
        except Exception as e:
            self._logger.error(f"Error getting staff history: {e}", exc_info=True)
            return Response(
                {'detail': 'Ошибка при получении истории назначений'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @action(detail=True, methods=['get'], url_path='board-members')
    def board_members(self, request, pk=None):
        """Получить список членов правления"""
        organization = self.get_object()
        
        self._logger.info(f"Getting board members for {organization.short_name}")
        
        try:
            assignments = organization.staff_assignments.filter(
                is_active=True,
                role__in=['manager', 'secretary', 'other']
            ).select_related('user')
            
            data = []
            for assignment in assignments:
                owner = None
                if hasattr(assignment.user, 'owner_profile'):
                    owner = assignment.user.owner_profile
                
                data.append({
                    'id': assignment.id,
                    'user_id': assignment.user_id,
                    'owner_id': owner.id if owner else None,
                    'owner_name': assignment.user.full_name,
                    'position': assignment.position_title or assignment.get_role_display(),
                    'has_account': True,
                    'username': assignment.user.username,
                    'role': assignment.role,
                })
            
            return Response({
                'count': len(data),
                'results': data,
            })
            
        except Exception as e:
            self._logger.error(f"Error getting board members: {e}", exc_info=True)
            return Response(
                {'detail': 'Ошибка при получении списка правления'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @action(detail=True, methods=['post'], url_path='add-board-member')
    def add_board_member(self, request, pk=None):
        """Добавить члена правления"""
        organization = self.get_object()
        
        self._logger.info(f"Adding board member to {organization.short_name}")
        
        try:
            owner_id = request.data.get('owner_id')
            position = request.data.get('position', 'Член правления')
            create_account = request.data.get('create_account', False)
            
            owner = Owner.objects.get(id=owner_id)
            
            user = None
            account_created = False
            generated_password = None
            
            if create_account:
                user, account_created, generated_password = self._create_user_for_owner(
                    owner, organization, role='viewer'
                )
            
            with transaction.atomic():
                if user:
                    assignment = OrganizationStaffAssignment.assign_staff(
                        organization=organization,
                        user=user,
                        role='other',
                        position_title=position
                    )
                
                response_data = {
                    'detail': 'Член правления добавлен',
                    'owner_id': owner.id,
                    'owner_name': owner.full_name,
                }
                
                if account_created and user:
                    response_data.update({
                        'account_created': True,
                        'user_id': user.id,
                        'username': user.username,
                        'password': generated_password,
                    })
                
                self._logger.info(f"Board member added: {owner.full_name}")
                return Response(response_data, status=status.HTTP_201_CREATED)
                
        except Owner.DoesNotExist:
            return Response(
                {'detail': 'Владелец не найден'},
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            self._logger.error(f"Error adding board member: {e}", exc_info=True)
            return Response(
                {'detail': 'Ошибка при добавлении члена правления'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @action(detail=True, methods=['post'], url_path='remove-board-member/(?P<member_id>[^/.]+)')
    def remove_board_member(self, request, pk=None, member_id=None):
        """Удалить члена правления"""
        self._logger.info(f"Removing board member {member_id} from organization {pk}")
        
        try:
            assignment = OrganizationStaffAssignment.objects.get(
                id=member_id,
                organization_id=pk
            )
            
            assignment.deactivate()
            
            self._create_action_log(
                action='update',
                object_id=pk,
                details=f'Удален член правления: {assignment.user.full_name}'
            )
            
            self._logger.info(f"Board member removed: {assignment.user.full_name}")
            return Response({'detail': 'Член правления удален'})
            
        except OrganizationStaffAssignment.DoesNotExist:
            return Response(
                {'detail': 'Член правления не найден'},
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            self._logger.error(f"Error removing board member: {e}", exc_info=True)
            return Response(
                {'detail': 'Ошибка при удалении члена правления'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    def _create_action_log(self, action: str, object_id: int, details: str):
        """Создать запись в логе действий"""
        try:
            UserActionLog.objects.create(
                user=self.request.user,
                action=action,
                model_name='Organization',
                object_id=object_id,
                details=details,
                ip_address=self._get_client_ip(self.request),
            )
        except Exception as e:
            self._logger.error(f"Error creating action log: {e}", exc_info=True)

    def _get_client_ip(self, request) -> str:
        """Получить IP-адрес клиента"""
        try:
            x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
            if x_forwarded_for:
                return x_forwarded_for.split(',')[0].strip()
            return request.META.get('REMOTE_ADDR', '0.0.0.0')
        except Exception:
            return '0.0.0.0'


class OrganizationListView(TemplateView):
    """Страница списка организаций"""
    template_name = 'organizations/list.html'
    _logger = logging.getLogger(f'{__name__}.OrganizationListView')
    
    def get_context_data(self, **kwargs):
        try:
            context = super().get_context_data(**kwargs)
            context['active_page'] = 'organizations'
            return context
        except Exception as e:
            self._logger.error(f"Error in OrganizationListView: {e}", exc_info=True)
            return {'active_page': 'organizations', 'error': 'Ошибка загрузки'}


class OrganizationDetailView(TemplateView):
    """Страница деталей организации"""
    template_name = 'organizations/detail.html'
    _logger = logging.getLogger(f'{__name__}.OrganizationDetailView')
    
    def get_context_data(self, **kwargs):
        try:
            context = super().get_context_data(**kwargs)
            context['active_page'] = 'organizations'
            context['organization_id'] = self.kwargs.get('organization_id')
            return context
        except Exception as e:
            self._logger.error(f"Error in OrganizationDetailView: {e}", exc_info=True)
            return {
                'active_page': 'organizations',
                'error': 'Ошибка загрузки'
            }


class OrganizationMembershipViewSet(viewsets.ModelViewSet):
    """ViewSet для управления членством в СНТ"""
    queryset = OrganizationMembership.objects.select_related('owner', 'organization').all()
    serializer_class = OrganizationMembershipSerializer
    permission_classes = [permissions.IsAuthenticated, IsManagerOrAbove]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['organization', 'owner', 'status']
    
    _logger = logging.getLogger(f'{__name__}.OrganizationMembershipViewSet')
    
    def create(self, request, *args, **kwargs):
        """Создание членства с логированием"""
        self._logger.info(
            f"Creating membership: org={request.data.get('organization')}, "
            f"owner={request.data.get('owner')}"
        )
        
        try:
            with transaction.atomic():
                response = super().create(request, *args, **kwargs)
                return response
        except IntegrityError as e:
            self._logger.error(f"Integrity error: {e}", exc_info=True)
            return Response(
                {'detail': 'Такое членство уже существует'},
                status=status.HTTP_409_CONFLICT
            )
        except Exception as e:
            self._logger.error(f"Error creating membership: {e}", exc_info=True)
            return Response(
                {'detail': 'Ошибка при создании членства'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )