from decimal import Decimal

from django.shortcuts import render, redirect
from django.views import View
from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.utils.decorators import method_decorator
from django.contrib import messages
from rest_framework import viewsets, status, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter, OrderingFilter
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from django.utils.decorators import method_decorator

from .models import User, UserActionLog
from .serializers import (
    UserLoginSerializer,
    UserChangePasswordSerializer,
    UserListSerializer,
    UserDetailSerializer,
    UserCreateSerializer,
    UserUpdateSerializer,
    UserActionLogSerializer,
)
from .permissions import IsAdminOrSuperuser, CanManageUsers, CanViewAuditLog


# ==================== API ViewSets ====================

class AuthViewSet(viewsets.ViewSet):
    """
    ViewSet для аутентификации.
    """
    permission_classes = [AllowAny]
    
    @action(detail=False, methods=['post'], url_path='login')
    def login_view(self, request):
        """POST /api/auth/login/"""
        serializer = UserLoginSerializer(data=request.data)
        if serializer.is_valid():
            user = serializer.validated_data['user']
            login(request, user)
            user.update_activity()
            
            # Логируем вход
            UserActionLog.objects.create(
                user=user,
                action='login',
                details=f'Вход в систему',
                ip_address=self._get_client_ip(request),
                user_agent=request.META.get('HTTP_USER_AGENT', ''),
            )
            
            return Response({
                'detail': 'Вход выполнен успешно',
                'user': UserDetailSerializer(user).data,
            })
        return Response(serializer.errors, status=status.HTTP_401_UNAUTHORIZED)
    
    @action(detail=False, methods=['post'], url_path='logout')
    def logout_view(self, request):
        """POST /api/auth/logout/"""
        if request.user.is_authenticated:
            UserActionLog.objects.create(
                user=request.user,
                action='logout',
                details='Выход из системы',
                ip_address=self._get_client_ip(request),
            )
        logout(request)
        return Response({'detail': 'Выход выполнен успешно'})
    
    @action(detail=False, methods=['get'], url_path='me')
    def me(self, request):
        """GET /api/auth/me/ — текущий пользователь"""
        if not request.user.is_authenticated:
            return Response(
                {'detail': 'Не авторизован'},
                status=status.HTTP_401_UNAUTHORIZED
            )
        return Response(UserDetailSerializer(request.user).data)
    
    @action(detail=False, methods=['post'], url_path='change-password')
    def change_password(self, request):
        """POST /api/auth/change-password/"""
        if not request.user.is_authenticated:
            return Response(
                {'detail': 'Не авторизован'},
                status=status.HTTP_401_UNAUTHORIZED
            )
        
        serializer = UserChangePasswordSerializer(
            data=request.data,
            context={'request': request}
        )
        if serializer.is_valid():
            request.user.set_password(serializer.validated_data['new_password'])
            request.user.save()
            update_session_auth_hash(request, request.user)
            
            UserActionLog.objects.create(
                user=request.user,
                action='update',
                details='Смена пароля',
                ip_address=self._get_client_ip(request),
            )
            
            return Response({'detail': 'Пароль изменён'})
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    def _get_client_ip(self, request):
        """Получение IP-адреса клиента"""
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            return x_forwarded_for.split(',')[0].strip()
        return request.META.get('REMOTE_ADDR')


class UserViewSet(viewsets.ModelViewSet):
    """
    ViewSet для управления пользователями системы.
    """
    queryset = User.objects.all()
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ['role', 'is_active', 'is_superuser']
    search_fields = ['username', 'first_name', 'last_name', 'email', 'phone']
    ordering_fields = ['username', 'last_name', 'role', 'date_joined', 'last_activity']
    ordering = ['last_name']
    
    def get_permissions(self):
        if self.action in ('create', 'destroy'):
            return [IsAuthenticated(), IsAdminOrSuperuser()]  # Только админы
        if self.action in ('update', 'partial_update'):
            return [IsAuthenticated(), CanManageUsers()]  # Менеджеры могут управлять пользователями
        if self.action in ('reset_password', 'deactivate', 'activate'):
            return [IsAuthenticated(), IsAdminOrSuperuser()]  # Только админы
        return [IsAuthenticated()]
    
    def get_serializer_class(self):
        if self.action == 'list':
            return UserListSerializer
        if self.action == 'create':
            return UserCreateSerializer
        if self.action in ('update', 'partial_update'):
            return UserUpdateSerializer
        return UserDetailSerializer
    
    def perform_create(self, serializer):
        """Автоматический расчёт суммы при создании начисления"""
        category = serializer.validated_data.get('category')
        land_plot = serializer.validated_data.get('land_plot')
        amount = serializer.validated_data.get('amount', 0)
        
        # Если сумма не указана или = 0 — рассчитываем автоматически
        if not amount or amount == 0:
            if category.unit == 'сотка' and (category.rate_per_unit or category.default_amount):
                rate = category.rate_per_unit or category.default_amount
                area_sotka = land_plot.area_sqm / 100
                amount = Decimal(str(area_sotka * float(rate))).quantize(Decimal('0.01'))
            elif category.default_amount:
                amount = category.default_amount
        
        serializer.save(amount=amount)
    
    def perform_update(self, serializer):
        user = serializer.save()
        UserActionLog.objects.create(
            user=self.request.user,
            action='update',
            model_name='User',
            object_id=user.id,
            details=f'Обновлён пользователь: {user}',
            ip_address=self._get_client_ip(),
        )
    
    def perform_destroy(self, instance):
        UserActionLog.objects.create(
            user=self.request.user,
            action='delete',
            model_name='User',
            object_id=instance.id,
            details=f'Удалён пользователь: {instance}',
            ip_address=self._get_client_ip(),
        )
        instance.delete()
    
    @action(detail=True, methods=['post'], url_path='deactivate')
    def deactivate(self, request, pk=None):
        """Деактивация пользователя"""
        user = self.get_object()
        user.is_active = False
        user.save(update_fields=['is_active'])
        
        UserActionLog.objects.create(
            user=request.user,
            action='update',
            model_name='User',
            object_id=user.id,
            details=f'Деактивирован пользователь: {user}',
            ip_address=self._get_client_ip(),
        )
        
        return Response({'detail': 'Пользователь деактивирован'})
    
    @action(detail=True, methods=['post'], url_path='activate')
    def activate(self, request, pk=None):
        """Активация пользователя"""
        user = self.get_object()
        user.is_active = True
        user.save(update_fields=['is_active'])
        
        UserActionLog.objects.create(
            user=request.user,
            action='update',
            model_name='User',
            object_id=user.id,
            details=f'Активирован пользователь: {user}',
            ip_address=self._get_client_ip(),
        )
        
        return Response({'detail': 'Пользователь активирован'})
    
    @action(detail=True, methods=['post'], url_path='reset-password')
    def reset_password(self, request, pk=None):
        """Сброс пароля пользователя (только для админов)"""
        if not request.user.is_admin:
            return Response(
                {'detail': 'Недостаточно прав'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        user = self.get_object()
        new_password = User.objects.make_random_password()
        user.set_password(new_password)
        user.save()
        
        UserActionLog.objects.create(
            user=request.user,
            action='update',
            model_name='User',
            object_id=user.id,
            details=f'Сброшен пароль пользователя: {user}',
            ip_address=self._get_client_ip(),
        )
        
        return Response({
            'detail': 'Пароль сброшен',
            'new_password': new_password,
        })
    
    def _get_client_ip(self):
        request = self.request
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            return x_forwarded_for.split(',')[0].strip()
        return request.META.get('REMOTE_ADDR')


class UserActionLogViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet для просмотра логов действий (только чтение).
    """
    queryset = UserActionLog.objects.select_related('user').all()
    serializer_class = UserActionLogSerializer
    permission_classes = [IsAuthenticated, CanViewAuditLog]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ['user', 'action', 'model_name']
    search_fields = ['details', 'user__username', 'user__last_name']
    ordering_fields = ['created_at', 'action']
    ordering = ['-created_at']


# ==================== Веб-вьюхи ====================
@method_decorator(ensure_csrf_cookie, name='dispatch')
class LoginView(View):
    """Страница входа"""
    def get(self, request):
        if request.user.is_authenticated:
            return redirect('dashboard')
        return render(request, 'accounts/login.html')


class ProfileView(View):
    """Страница профиля пользователя"""
    @method_decorator(login_required)
    def get(self, request):
        return render(request, 'accounts/profile.html')


class UsersListView(View):
    @method_decorator(login_required)
    def get(self, request):
        users = User.objects.all().order_by('username')
        if not (request.user.is_admin or request.user.has_perm('accounts.can_manage_users')):
            messages.error(request, 'У вас нет прав для просмотра этой страницы')
            return redirect('dashboard')
        return render(request, 'accounts/users_list.html', {
            'users': users,
            'active_page': 'users'
        })