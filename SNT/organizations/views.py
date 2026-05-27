# organizations/views.py
from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter, OrderingFilter
from django.views.generic import TemplateView
from django.contrib.auth import get_user_model
import re
from django.utils import timezone

from accounts.models import UserActionLog
from .models import Organization, OrganizationMembership, OrganizationStaffAssignment
from .serializers import (
    OrganizationSerializer, 
    OrganizationDetailSerializer,
    OrganizationMembershipSerializer,
    OrganizationMembershipCreateSerializer
)
from accounts.permissions import IsAdminOrSuperuser, IsManagerOrAbove
from accounts.models import UserActionLog
from users.models import Owner

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
        owner_ids = [o.id for o in owners]

        # Получаем участки через владельцев
        from land.models import LandPlot
        plots = LandPlot.objects.filter(owners__id__in=owner_ids).distinct()

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
                debt = owner.total_debt if hasattr(owner, 'total_debt') else 0
                total_debt += debt

                # Считаем просроченные начисления
                overdue = Assessment.objects.filter(
                    owner=owner,
                    status='overdue'
                ).count()
                overdue_count += overdue

            stats['total_debt'] = float(total_debt)
            stats['overdue_count'] = overdue_count
        except Exception as e:
            print(f"Error calculating debt: {e}")
            stats['total_debt'] = 0
            stats['overdue_count'] = 0

        return Response(stats)
    
    def get_queryset(self):
        """
        Фильтрация организаций:
        - Админы видят все
        - Остальные видят только свои организации
        """
        queryset = super().get_queryset()
        user = self.request.user
        
        # Админы и суперпользователи видят все
        if user.is_superuser or user.is_admin:
            return queryset
        
        # Собираем все организации пользователя
        
        
        organization_ids = set()
        
        # 1. Организации, где пользователь - сотрудник
        staff_orgs = OrganizationStaffAssignment.objects.filter(
            user=user,
            is_active=True
        ).values_list('organization_id', flat=True)
        organization_ids.update(staff_orgs)
        
        # 2. Организация, где пользователь - председатель
        if hasattr(user, 'chaired_organizations'):
            chaired = user.chaired_organizations.all().values_list('id', flat=True)
            organization_ids.update(chaired)
        elif user.chairman_profile:  # Старый способ
            organization_ids.add(user.chairman_profile.id)
        
        # 3. Организация, где пользователь - бухгалтер
        if hasattr(user, 'accountant_organizations'):
            accountant_orgs = user.accountant_organizations.all().values_list('id', flat=True)
            organization_ids.update(accountant_orgs)
        
        # 4. Организация через поле organization (старый способ)
        if user.organization:
            organization_ids.add(user.organization.id)
        
        # 5. Организации, где пользователь - владелец (член СНТ)
        owner = getattr(user, 'owner_profile', None)
        if owner:
            member_orgs = OrganizationMembership.objects.filter(
                owner=owner,
                status='active'
            ).values_list('organization_id', flat=True)
            organization_ids.update(member_orgs)
        
        # Если пользователь хоть где-то состоит
        if organization_ids:
            return queryset.filter(id__in=organization_ids)
        
        # Если нигде не состоит - не показываем ничего
        return queryset.none()

    @action(detail=True, methods=['post'], url_path='assign-chairman')
    def assign_chairman(self, request, pk=None):
        """
        Назначить нового председателя.
        Автоматически деактивирует предыдущего.
        """
        organization = self.get_object()

        user_id = request.data.get('user_id')
        if not user_id:
            return Response(
                {'detail': 'Укажите user_id'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            user = User.objects.get(id=user_id, is_active=True)
        except User.DoesNotExist:
            return Response(
                {'detail': 'Пользователь не найден'},
                status=status.HTTP_404_NOT_FOUND
            )

        assignment_order = request.data.get('assignment_order', '')

        # Создаем новое назначение (старое деактивируется автоматически)
        assignment = OrganizationStaffAssignment.assign_staff(
            organization=organization,
            user=user,
            role='chairman',
            position_title='Председатель правления',
            assignment_order=assignment_order
        )

        # Логируем
        
        UserActionLog.objects.create(
            user=request.user,
            action='update',
            model_name='Organization',
            object_id=organization.id,
            details=f'Назначен новый председатель: {user.full_name}',
            ip_address=self._get_client_ip(request),
        )

        return Response({
            'detail': 'Председатель назначен',
            'chairman': {
                'id': user.id,
                'full_name': user.full_name,
            },
            'assigned_at': assignment.assigned_at,
        })
    
    @action(detail=True, methods=['get'], url_path='staff-history')
    def staff_history(self, request, pk=None):
        """История всех назначений сотрудников"""
        organization = self.get_object()
        assignments = organization.staff_assignments.select_related('user').all()

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

        return Response({
            'count': len(data),
            'results': data,
        })
    
    def update(self, request, *args, **kwargs):
        """Обновление с поддержкой chairman_id и accountant_id"""
        partial = kwargs.pop('partial', False)
        instance = self.get_object()

        data = request.data.copy()

        # Обработка председателя
        if 'chairman_id' in data:
            chairman_id = data.pop('chairman_id')
            if chairman_id:
                try:
                    chairman = User.objects.get(id=chairman_id)
                    # Создаем запись в истории назначений
                    OrganizationStaffAssignment.assign_staff(
                        organization=instance,
                        user=chairman,
                        role='chairman',
                        position_title='Председатель правления'
                    )
                    data['chairman'] = chairman.id
                except User.DoesNotExist:
                    pass
            else:
                # Деактивируем текущего председателя
                OrganizationStaffAssignment.objects.filter(
                    organization=instance,
                    role='chairman',
                    is_active=True
                ).update(is_active=False, assigned_until=timezone.now())
                data['chairman'] = None

        # Обработка бухгалтера
        if 'accountant_id' in data:
            accountant_id = data.pop('accountant_id')
            if accountant_id:
                try:
                    accountant = User.objects.get(id=accountant_id)
                    OrganizationStaffAssignment.assign_staff(
                        organization=instance,
                        user=accountant,
                        role='accountant',
                        position_title='Бухгалтер'
                    )
                    data['accountant'] = accountant.id
                except User.DoesNotExist:
                    pass
            else:
                OrganizationStaffAssignment.objects.filter(
                    organization=instance,
                    role='accountant',
                    is_active=True
                ).update(is_active=False, assigned_until=timezone.now())
                data['accountant'] = None

        serializer = self.get_serializer(instance, data=data, partial=partial)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)

        if getattr(instance, '_prefetched_objects_cache', None):
            instance._prefetched_objects_cache = {}

        return Response(serializer.data)

    
    def _get_client_ip(self, request):
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            return x_forwarded_for.split(',')[0].strip()
        return request.META.get('REMOTE_ADDR')
    
    @action(detail=True, methods=['post'], url_path='assign-chairman-from-owner')
    def assign_chairman_from_owner(self, request, pk=None):
        """
        Назначить председателя из владельцев с опциональным созданием аккаунта.
        """
        organization = self.get_object()

        owner_id = request.data.get('owner_id')
        if not owner_id:
            return Response(
                {'detail': 'Укажите owner_id'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            owner = Owner.objects.get(id=owner_id)
        except Owner.DoesNotExist:
            return Response(
                {'detail': 'Владелец не найден'},
                status=status.HTTP_404_NOT_FOUND
            )

        create_account = request.data.get('create_account', False)
        username = request.data.get('username')
        password = request.data.get('password')

        user = None
        account_created = False

        if create_account:
            # Проверяем, есть ли уже пользователь, связанный с этим владельцем
            # Ищем по email или телефону
            owner_email = owner.primary_email
            owner_phone = owner.primary_phone

            existing_user = None
            if owner_email:
                existing_user = User.objects.filter(email=owner_email).first()

            if not existing_user and owner_phone:
                # Ищем по телефону (если есть такое поле)
                existing_user = User.objects.filter(phone=owner_phone).first()

            if existing_user:
                user = existing_user
            else:
                # Создаем нового пользователя
                if not username:
                    # Генерируем логин из ФИО или телефона
                    username = owner_phone or owner.full_name.lower().replace(' ', '.')
                    # Убираем спецсимволы
                    username = re.sub(r'[^\w.]', '', username)
                    # Если такой уже есть, добавляем цифру
                    base_username = username
                    counter = 1
                    while User.objects.filter(username=username).exists():
                        username = f"{base_username}{counter}"
                        counter += 1

                if not password:
                    password = User.objects.make_random_password()

                user = User.objects.create_user(
                    username=username,
                    email=owner_email,
                    password=password,
                    first_name=owner.full_name.split()[1] if len(owner.full_name.split()) > 1 else '',
                    last_name=owner.full_name.split()[0] if owner.full_name.split() else owner.full_name,
                    phone=owner_phone,
                    role='manager',  # Председатель - manager
                    is_active=True,
                    organization=organization,
                )
                account_created = True

        # Назначаем председателем через OrganizationStaffAssignment
        if user:
            OrganizationStaffAssignment.assign_staff(
                organization=organization,
                user=user,
                role='chairman',
                position_title='Председатель правления'
            )
        else:
            # Если аккаунт не создаем, просто обновляем поле chairman
            # Но для этого нужен User. Можно создать временного или использовать существующего
            pass
        
        # Логируем
        UserActionLog.objects.create(
            user=request.user,
            action='update',
            model_name='Organization',
            object_id=organization.id,
            details=f'Назначен председатель из владельца: {owner.full_name}' + 
                    (f', создан аккаунт: {user.username}' if account_created else ''),
            ip_address=self._get_client_ip(request),
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
                'password': password,
            })

        return Response(response_data)  

    @action(detail=True, methods=['post'], url_path='assign-accountant-from-owner')
    def assign_accountant_from_owner(self, request, pk=None):
        """
        Назначить бухгалтера из владельцев с опциональным созданием аккаунта.
        """
        organization = self.get_object()

        owner_id = request.data.get('owner_id')
        if not owner_id:
            return Response(
                {'detail': 'Укажите owner_id'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            owner = Owner.objects.get(id=owner_id)
        except Owner.DoesNotExist:
            return Response(
                {'detail': 'Владелец не найден'},
                status=status.HTTP_404_NOT_FOUND
            )

        create_account = request.data.get('create_account', False)
        username = request.data.get('username')
        password = request.data.get('password')

        user = None
        account_created = False

        if create_account:
            owner_email = owner.primary_email
            owner_phone = owner.primary_phone

            existing_user = None
            if owner_email:
                existing_user = User.objects.filter(email=owner_email).first()

            if not existing_user and owner_phone:
                existing_user = User.objects.filter(phone=owner_phone).first()

            if existing_user:
                user = existing_user
                # Обновляем роль, если нужно
                if user.role not in ['admin', 'manager', 'accountant']:
                    user.role = 'accountant'
                    user.save(update_fields=['role'])
            else:
                if not username:
                    username = owner_phone or owner.full_name.lower().replace(' ', '.')
                    username = re.sub(r'[^\w.]', '', username)
                    base_username = username
                    counter = 1
                    while User.objects.filter(username=username).exists():
                        username = f"{base_username}{counter}"
                        counter += 1

                if not password:
                    password = User.objects.make_random_password()

                user = User.objects.create_user(
                    username=username,
                    email=owner_email,
                    password=password,
                    first_name=owner.full_name.split()[1] if len(owner.full_name.split()) > 1 else '',
                    last_name=owner.full_name.split()[0] if owner.full_name.split() else owner.full_name,
                    phone=owner_phone,
                    role='accountant',
                    is_active=True,
                    organization=organization,
                )
                account_created = True

        if user:
            OrganizationStaffAssignment.assign_staff(
                organization=organization,
                user=user,
                role='accountant',
                position_title='Бухгалтер'
            )

        UserActionLog.objects.create(
            user=request.user,
            action='update',
            model_name='Organization',
            object_id=organization.id,
            details=f'Назначен бухгалтер из владельца: {owner.full_name}',
            ip_address=self._get_client_ip(request),
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
                'password': password,
            })

        return Response(response_data)  

    @action(detail=True, methods=['get'], url_path='board-members')
    def board_members(self, request, pk=None):
        """Получить список членов правления"""
        organization = self.get_object()

        # Члены правления - это сотрудники с ролью 'other' или специальные назначения
        assignments = organization.staff_assignments.filter(
            is_active=True,
            role__in=['manager', 'secretary', 'other']
        ).select_related('user')

        data = []
        for assignment in assignments:
            # Ищем связанного владельца
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

    @action(detail=True, methods=['post'], url_path='add-board-member')
    def add_board_member(self, request, pk=None):
        """Добавить члена правления"""
        organization = self.get_object()

        owner_id = request.data.get('owner_id')
        position = request.data.get('position', 'Член правления')
        create_account = request.data.get('create_account', False)
        username = request.data.get('username')
        password = request.data.get('password')

        try:
            owner = Owner.objects.get(id=owner_id)
        except Owner.DoesNotExist:
            return Response(
                {'detail': 'Владелец не найден'},
                status=status.HTTP_404_NOT_FOUND
            )

        user = None
        account_created = False

        if create_account:
            owner_email = owner.primary_email
            owner_phone = owner.primary_phone

            existing_user = None
            if owner_email:
                existing_user = User.objects.filter(email=owner_email).first()

            if not existing_user:
                if not username:
                    username = owner_phone or owner.full_name.lower().replace(' ', '.')
                    username = re.sub(r'[^\w.]', '', username)
                    base_username = username
                    counter = 1
                    while User.objects.filter(username=username).exists():
                        username = f"{base_username}{counter}"
                        counter += 1

                if not password:
                    password = User.objects.make_random_password()

                user = User.objects.create_user(
                    username=username,
                    email=owner_email,
                    password=password,
                    first_name=owner.full_name.split()[1] if len(owner.full_name.split()) > 1 else '',
                    last_name=owner.full_name.split()[0] if owner.full_name.split() else owner.full_name,
                    phone=owner_phone,
                    role='viewer',  # Член правления - наблюдатель с расширенными правами
                    is_active=True,
                    organization=organization,
                )
                account_created = True
            else:
                user = existing_user

        # Создаем назначение
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
                'password': password,
            })

        return Response(response_data, status=status.HTTP_201_CREATED)  

    @action(detail=True, methods=['post'], url_path='remove-board-member/(?P<member_id>[^/.]+)')
    def remove_board_member(self, request, pk=None, member_id=None):
        """Удалить члена правления"""
        try:
            assignment = OrganizationStaffAssignment.objects.get(
                id=member_id,
                organization_id=pk
            )
        except OrganizationStaffAssignment.DoesNotExist:
            return Response(
                {'detail': 'Член правления не найден'},
                status=status.HTTP_404_NOT_FOUND
            )

        assignment.deactivate()

        return Response({'detail': 'Член правления удален'})    

    @action(detail=True, methods=['post'], url_path='create-board-member-account/(?P<member_id>[^/.]+)')
    def create_board_member_account(self, request, pk=None, member_id=None):
        """Создать аккаунт для существующего члена правления"""
        try:
            assignment = OrganizationStaffAssignment.objects.get(
                id=member_id,
                organization_id=pk
            )
        except OrganizationStaffAssignment.DoesNotExist:
            return Response(
                {'detail': 'Член правления не найден'},
                status=status.HTTP_404_NOT_FOUND
            )

        username = request.data.get('username')
        password = request.data.get('password')

        if not password:
            password = User.objects.make_random_password()

        # Обновляем пользователя
        user = assignment.user
        if username:
            user.username = username
        user.set_password(password)
        user.is_active = True
        user.save()

        return Response({
            'detail': 'Аккаунт создан',
            'username': user.username,
            'password': password,
        })

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