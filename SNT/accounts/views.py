from decimal import Decimal
import logging

from django.shortcuts import render, redirect
from django.contrib.auth.forms import UserCreationForm
from django.urls import reverse_lazy
from django.views.generic import CreateView
from django.views import View
from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.utils.decorators import method_decorator
from django.contrib import messages
from django.db import transaction, DatabaseError, IntegrityError
from django.core.exceptions import PermissionDenied, ValidationError
from rest_framework import viewsets, status, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter, OrderingFilter
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from django.utils.decorators import method_decorator

from subscriptions.models import Subscription, Tariff
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
from organizations.models import Organization

logger = logging.getLogger(__name__)

# ==================== API ViewSets ====================

class AuthViewSet(viewsets.ViewSet):
    """
    ViewSet для аутентификации.
    """
    permission_classes = [AllowAny]
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.logger = logging.getLogger(f'{__name__}.AuthViewSet')
    
    @action(detail=False, methods=['post'], url_path='login')
    def login_view(self, request):
        """POST /api/auth/login/"""
        self.logger.info(f"Попытка входа: username={request.data.get('username')}, ip={self._get_client_ip(request)}")
        
        try:
            serializer = UserLoginSerializer(data=request.data)
            if serializer.is_valid():
                user = serializer.validated_data['user']
                login(request, user)
                user.update_activity()
                
                # Логируем вход
                try:
                    UserActionLog.objects.create(
                        user=user,
                        action='login',
                        details=f'Вход в систему',
                        ip_address=self._get_client_ip(request),
                        user_agent=request.META.get('HTTP_USER_AGENT', ''),
                    )
                    self.logger.info(f"Пользователь {user.username} успешно вошел в систему")
                except Exception as e:
                    self.logger.error(f"Ошибка при создании лога входа: {e}", exc_info=True)
                
                return Response({
                    'detail': 'Вход выполнен успешно',
                    'user': UserDetailSerializer(user).data,
                })
            
            self.logger.warning(f"Неудачная попытка входа: {serializer.errors}")
            return Response(serializer.errors, status=status.HTTP_401_UNAUTHORIZED)
            
        except Exception as e:
            self.logger.error(f"Критическая ошибка при входе: {e}", exc_info=True)
            return Response(
                {'detail': 'Внутренняя ошибка сервера'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=False, methods=['post'], url_path='logout')
    def logout_view(self, request):
        """POST /api/auth/logout/"""
        username = request.user.username if request.user.is_authenticated else 'anonymous'
        self.logger.info(f"Выход пользователя: {username}")
        
        try:
            if request.user.is_authenticated:
                try:
                    UserActionLog.objects.create(
                        user=request.user,
                        action='logout',
                        details='Выход из системы',
                        ip_address=self._get_client_ip(request),
                    )
                except Exception as e:
                    self.logger.error(f"Ошибка при создании лога выхода: {e}", exc_info=True)
            
            logout(request)
            return Response({'detail': 'Выход выполнен успешно'})
            
        except Exception as e:
            self.logger.error(f"Ошибка при выходе: {e}", exc_info=True)
            return Response(
                {'detail': 'Ошибка при выходе из системы'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=False, methods=['get'], url_path='me')
    def me(self, request):
        """GET /api/auth/me/ — текущий пользователь"""
        try:
            if not request.user.is_authenticated:
                return Response(
                    {'detail': 'Не авторизован'},
                    status=status.HTTP_401_UNAUTHORIZED
                )
            
            self.logger.debug(f"Запрос информации о пользователе: {request.user.username}")
            return Response(UserDetailSerializer(request.user).data)
            
        except Exception as e:
            self.logger.error(f"Ошибка при получении данных пользователя: {e}", exc_info=True)
            return Response(
                {'detail': 'Ошибка при получении данных'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=False, methods=['post'], url_path='change-password')
    def change_password(self, request):
        """POST /api/auth/change-password/"""
        if not request.user.is_authenticated:
            return Response(
                {'detail': 'Не авторизован'},
                status=status.HTTP_401_UNAUTHORIZED
            )
        
        self.logger.info(f"Смена пароля пользователем: {request.user.username}")
        
        try:
            serializer = UserChangePasswordSerializer(
                data=request.data,
                context={'request': request}
            )
            if serializer.is_valid():
                request.user.set_password(serializer.validated_data['new_password'])
                request.user.save()
                update_session_auth_hash(request, request.user)
                
                try:
                    UserActionLog.objects.create(
                        user=request.user,
                        action='update',
                        details='Смена пароля',
                        ip_address=self._get_client_ip(request),
                    )
                except Exception as e:
                    self.logger.error(f"Ошибка при логировании смены пароля: {e}", exc_info=True)
                
                self.logger.info(f"Пароль успешно изменен для пользователя {request.user.username}")
                return Response({'detail': 'Пароль изменён'})
            
            self.logger.warning(f"Ошибка валидации при смене пароля: {serializer.errors}")
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
            
        except Exception as e:
            self.logger.error(f"Критическая ошибка при смене пароля: {e}", exc_info=True)
            return Response(
                {'detail': 'Ошибка при смене пароля'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    def _get_client_ip(self, request):
        """Получение IP-адреса клиента"""
        try:
            x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
            if x_forwarded_for:
                ip = x_forwarded_for.split(',')[0].strip()
                return ip
            return request.META.get('REMOTE_ADDR', '0.0.0.0')
        except Exception as e:
            self.logger.error(f"Ошибка при получении IP: {e}")
            return '0.0.0.0'

    @action(detail=False, methods=['post'], url_path='register')
    def register(self, request):
        """
        POST /api/auth/register/
        Регистрация нового председателя с созданием СНТ.
        """
        self.logger.info(f"Новая регистрация: username={request.data.get('username')}, email={request.data.get('email')}")
        
        try:
            data = request.data
            org_data = data.get('organization', {})
            
            # Валидация обязательных полей
            required_fields = ['username', 'password', 'email']
            for field in required_fields:
                if not data.get(field):
                    error_msg = f'Поле {field} обязательно для заполнения'
                    self.logger.warning(f"Регистрация отклонена: {error_msg}")
                    return Response(
                        {'detail': error_msg},
                        status=status.HTTP_400_BAD_REQUEST
                    )
            
            if not org_data.get('name') or not org_data.get('inn'):
                error_msg = 'Название и ИНН организации обязательны'
                self.logger.warning(f"Регистрация отклонена: {error_msg}")
                return Response(
                    {'detail': error_msg},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Проверяем существование пользователя
            if User.objects.filter(username=data.get('username')).exists():
                error_msg = 'Пользователь с таким логином уже существует'
                self.logger.warning(f"Регистрация отклонена: {error_msg}")
                return Response(
                    {'detail': error_msg},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            if data.get('email') and User.objects.filter(email=data.get('email')).exists():
                error_msg = 'Пользователь с таким email уже существует'
                self.logger.warning(f"Регистрация отклонена: {error_msg}")
                return Response(
                    {'detail': error_msg},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Проверяем существование СНТ
            if Organization.objects.filter(inn=org_data.get('inn')).exists():
                error_msg = 'СНТ с таким ИНН уже зарегистрировано'
                self.logger.warning(f"Регистрация отклонена: {error_msg}")
                return Response(
                    {'detail': error_msg},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Используем транзакцию для атомарности
            with transaction.atomic():
                try:
                    # Создаем пользователя
                    user = User.objects.create_user(
                        username=data.get('username'),
                        email=data.get('email'),
                        password=data.get('password'),
                        first_name=data.get('first_name', ''),
                        last_name=data.get('last_name', ''),
                        phone=data.get('phone', ''),
                        role='manager',
                        is_active=True
                    )
                    
                    if data.get('middle_name'):
                        user.middle_name = data.get('middle_name')
                        user.save()
                    
                    self.logger.info(f"Создан пользователь {user.username} (ID: {user.id})")
                    
                    # Создаем организацию
                    organization = Organization.objects.create(
                        name=org_data.get('name'),
                        short_name=org_data.get('short_name'),
                        inn=org_data.get('inn'),
                        kpp=org_data.get('kpp', ''),
                        legal_address=org_data.get('legal_address'),
                        bank_name=org_data.get('bank_name'),
                        bank_bik=org_data.get('bank_bik'),
                        bank_account=org_data.get('bank_account'),
                        bank_corr_account=org_data.get('bank_corr_account'),
                        chairman=user,
                        is_active=True
                    )
                    
                    self.logger.info(f"Создана организация {organization.name} (ID: {organization.id})")
                    
                    # Привязываем пользователя к организации
                    user.organization = organization
                    user.save()
                    
                    # Создаем подписку
                    tariff = Tariff.objects.filter(slug='basic', is_active=True).first()
                    if tariff:
                        Subscription.objects.create(
                            organization=organization,
                            tariff=tariff,
                            status='trial',
                            start_date=timezone.now(),
                            end_date=timezone.now() + timezone.timedelta(days=tariff.trial_days)
                        )
                        self.logger.info(f"Создана подписка для организации {organization.name}")
                    else:
                        self.logger.warning(f"Базовый тариф не найден для организации {organization.name}")
                    
                    # Логируем регистрацию
                    try:
                        UserActionLog.objects.create(
                            user=user,
                            action='create',
                            details=f'Регистрация нового СНТ: {organization.short_name}',
                            ip_address=self._get_client_ip(request),
                            user_agent=request.META.get('HTTP_USER_AGENT', ''),
                        )
                    except Exception as e:
                        self.logger.error(f"Ошибка при создании лога регистрации: {e}", exc_info=True)
                    
                    self.logger.info(f"Регистрация успешно завершена: user_id={user.id}, org_id={organization.id}")
                    
                    return Response({
                        'detail': 'Регистрация успешно завершена',
                        'user_id': user.id,
                        'organization_id': organization.id,
                    }, status=status.HTTP_201_CREATED)
                    
                except IntegrityError as e:
                    self.logger.error(f"Ошибка целостности данных при регистрации: {e}", exc_info=True)
                    transaction.set_rollback(True)
                    return Response(
                        {'detail': 'Ошибка при создании: нарушение целостности данных'},
                        status=status.HTTP_400_BAD_REQUEST
                    )
                    
                except DatabaseError as e:
                    self.logger.error(f"Ошибка базы данных при регистрации: {e}", exc_info=True)
                    transaction.set_rollback(True)
                    return Response(
                        {'detail': 'Ошибка базы данных'},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR
                    )
                    
        except Exception as e:
            self.logger.error(f"Критическая ошибка при регистрации: {e}", exc_info=True)
            return Response(
                {'detail': 'Внутренняя ошибка сервера при регистрации'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

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
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.logger = logging.getLogger(f'{__name__}.UserViewSet')
    
    def get_permissions(self):
        try:
            if self.action in ('create', 'destroy'):
                return [IsAuthenticated(), IsAdminOrSuperuser()]
            if self.action in ('update', 'partial_update'):
                return [IsAuthenticated(), CanManageUsers()]
            if self.action in ('reset_password', 'deactivate', 'activate'):
                return [IsAuthenticated(), IsAdminOrSuperuser()]
            return [IsAuthenticated()]
        except Exception as e:
            self.logger.error(f"Ошибка при определении прав доступа: {e}", exc_info=True)
            return [IsAuthenticated()]
    
    def get_serializer_class(self):
        try:
            if self.action == 'list':
                return UserListSerializer
            if self.action == 'create':
                return UserCreateSerializer
            if self.action in ('update', 'partial_update'):
                return UserUpdateSerializer
            return UserDetailSerializer
        except Exception as e:
            self.logger.error(f"Ошибка при выборе сериализатора: {e}", exc_info=True)
            return UserDetailSerializer
    
    def list(self, request, *args, **kwargs):
        """GET /api/users/ — список пользователей с логированием"""
        self.logger.info(f"Запрос списка пользователей от {request.user.username}")
        try:
            response = super().list(request, *args, **kwargs)
            self.logger.debug(f"Успешно получен список пользователей: {len(response.data)} записей")
            return response
        except Exception as e:
            self.logger.error(f"Ошибка при получении списка пользователей: {e}", exc_info=True)
            return Response(
                {'detail': 'Ошибка при получении списка пользователей'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    def create(self, request, *args, **kwargs):
        """POST /api/users/ — создание пользователя с логированием"""
        self.logger.info(f"Создание нового пользователя администратором {request.user.username}")
        try:
            with transaction.atomic():
                serializer = self.get_serializer(data=request.data)
                if serializer.is_valid():
                    user = serializer.save()
                    
                    try:
                        UserActionLog.objects.create(
                            user=request.user,
                            action='create',
                            model_name='User',
                            object_id=user.id,
                            details=f'Создан пользователь: {user.username} ({user.full_name})',
                            ip_address=self._get_client_ip(),
                        )
                        self.logger.info(f"Пользователь {user.username} успешно создан")
                    except Exception as e:
                        self.logger.error(f"Ошибка при логировании создания пользователя: {e}", exc_info=True)
                    
                    headers = self.get_success_headers(serializer.data)
                    return Response(
                        UserDetailSerializer(user).data,
                        status=status.HTTP_201_CREATED,
                        headers=headers
                    )
                
                self.logger.warning(f"Ошибка валидации при создании пользователя: {serializer.errors}")
                return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
                
        except IntegrityError as e:
            self.logger.error(f"Ошибка целостности при создании пользователя: {e}", exc_info=True)
            return Response(
                {'detail': 'Пользователь с такими данными уже существует'},
                status=status.HTTP_400_BAD_REQUEST
            )
        except DatabaseError as e:
            self.logger.error(f"Ошибка базы данных при создании пользователя: {e}", exc_info=True)
            return Response(
                {'detail': 'Ошибка базы данных'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
        except Exception as e:
            self.logger.error(f"Критическая ошибка при создании пользователя: {e}", exc_info=True)
            return Response(
                {'detail': 'Внутренняя ошибка сервера'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    def retrieve(self, request, *args, **kwargs):
        """GET /api/users/{id}/ — получение пользователя"""
        try:
            user = self.get_object()
            self.logger.debug(f"Запрос данных пользователя {user.username} от {request.user.username}")
            serializer = self.get_serializer(user)
            return Response(serializer.data)
        except User.DoesNotExist:
            self.logger.warning(f"Пользователь с ID {kwargs.get('pk')} не найден")
            return Response(
                {'detail': 'Пользователь не найден'},
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            self.logger.error(f"Ошибка при получении пользователя: {e}", exc_info=True)
            return Response(
                {'detail': 'Ошибка при получении данных пользователя'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    def perform_update(self, serializer):
        try:
            user = serializer.save()
            UserActionLog.objects.create(
                user=self.request.user,
                action='update',
                model_name='User',
                object_id=user.id,
                details=f'Обновлён пользователь: {user}',
                ip_address=self._get_client_ip(),
            )
            self.logger.info(f"Пользователь {user.username} обновлен администратором {self.request.user.username}")
        except Exception as e:
            self.logger.error(f"Ошибка при обновлении пользователя: {e}", exc_info=True)
            raise
    
    def perform_destroy(self, instance):
        try:
            username = instance.username
            user_id = instance.id
            
            UserActionLog.objects.create(
                user=self.request.user,
                action='delete',
                model_name='User',
                object_id=user_id,
                details=f'Удалён пользователь: {instance}',
                ip_address=self._get_client_ip(),
            )
            
            instance.delete()
            self.logger.info(f"Пользователь {username} удален администратором {self.request.user.username}")
            
        except IntegrityError as e:
            self.logger.error(f"Ошибка целостности при удалении пользователя {instance.username}: {e}", exc_info=True)
            raise ValidationError('Невозможно удалить пользователя: есть связанные данные')
        except Exception as e:
            self.logger.error(f"Ошибка при удалении пользователя {instance.username}: {e}", exc_info=True)
            raise
    
    @action(detail=True, methods=['post'], url_path='deactivate')
    def deactivate(self, request, pk=None):
        """Деактивация пользователя"""
        try:
            user = self.get_object()
            
            if user == request.user:
                self.logger.warning(f"Пользователь {request.user.username} попытался деактивировать сам себя")
                return Response(
                    {'detail': 'Нельзя деактивировать самого себя'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
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
            
            self.logger.info(f"Пользователь {user.username} деактивирован администратором {request.user.username}")
            return Response({'detail': 'Пользователь деактивирован'})
            
        except User.DoesNotExist:
            self.logger.error(f"Пользователь с ID {pk} не найден при деактивации")
            return Response(
                {'detail': 'Пользователь не найден'},
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            self.logger.error(f"Ошибка при деактивации пользователя: {e}", exc_info=True)
            return Response(
                {'detail': 'Ошибка при деактивации пользователя'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=True, methods=['post'], url_path='activate')
    def activate(self, request, pk=None):
        """Активация пользователя"""
        try:
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
            
            self.logger.info(f"Пользователь {user.username} активирован администратором {request.user.username}")
            return Response({'detail': 'Пользователь активирован'})
            
        except User.DoesNotExist:
            self.logger.error(f"Пользователь с ID {pk} не найден при активации")
            return Response(
                {'detail': 'Пользователь не найден'},
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            self.logger.error(f"Ошибка при активации пользователя: {e}", exc_info=True)
            return Response(
                {'detail': 'Ошибка при активации пользователя'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=True, methods=['post'], url_path='reset-password')
    def reset_password(self, request, pk=None):
        """Сброс пароля пользователя (только для админов)"""
        if not request.user.is_admin:
            self.logger.warning(f"Пользователь {request.user.username} попытался сбросить пароль без прав")
            return Response(
                {'detail': 'Недостаточно прав'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        try:
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
            
            self.logger.info(f"Пароль пользователя {user.username} сброшен администратором {request.user.username}")
            
            return Response({
                'detail': 'Пароль сброшен',
                'new_password': new_password,
            })
            
        except User.DoesNotExist:
            self.logger.error(f"Пользователь с ID {pk} не найден при сбросе пароля")
            return Response(
                {'detail': 'Пользователь не найден'},
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            self.logger.error(f"Ошибка при сбросе пароля: {e}", exc_info=True)
            return Response(
                {'detail': 'Ошибка при сбросе пароля'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    def _get_client_ip(self):
        """Безопасное получение IP-адреса"""
        try:
            request = self.request
            x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
            if x_forwarded_for:
                return x_forwarded_for.split(',')[0].strip()
            return request.META.get('REMOTE_ADDR', '0.0.0.0')
        except Exception as e:
            self.logger.error(f"Ошибка при получении IP: {e}")
            return '0.0.0.0'


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
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.logger = logging.getLogger(f'{__name__}.UserActionLogViewSet')
    
    def list(self, request, *args, **kwargs):
        """GET /api/audit-log/ — получение логов с проверкой прав"""
        self.logger.info(f"Запрос логов аудита от пользователя {request.user.username}")
        
        if not request.user.is_admin and not request.user.has_perm('accounts.can_view_audit_log'):
            self.logger.warning(f"Пользователь {request.user.username} попытался получить доступ к логам без прав")
            raise PermissionDenied('У вас нет прав на просмотр логов')
        
        try:
            response = super().list(request, *args, **kwargs)
            self.logger.debug(f"Успешно получены логи аудита: {len(response.data)} записей")
            return response
        except Exception as e:
            self.logger.error(f"Ошибка при получении логов: {e}", exc_info=True)
            return Response(
                {'detail': 'Ошибка при получении логов'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


# ==================== Веб-вьюхи ====================
@method_decorator(ensure_csrf_cookie, name='dispatch')
class LoginView(View):
    """Страница входа"""
    logger = logging.getLogger(f'{__name__}.LoginView')
    
    def get(self, request):
        try:
            if request.user.is_authenticated:
                self.logger.debug(f"Пользователь {request.user.username} уже авторизован, перенаправление на dashboard")
                return redirect('dashboard')
            return render(request, 'accounts/login.html')
        except Exception as e:
            self.logger.error(f"Ошибка при отображении страницы входа: {e}", exc_info=True)
            return render(request, 'accounts/login.html', {'error': 'Произошла ошибка'})


class ProfileView(View):
    """Страница профиля пользователя"""
    logger = logging.getLogger(f'{__name__}.ProfileView')
    
    @method_decorator(login_required)
    def get(self, request):
        try:
            self.logger.debug(f"Просмотр профиля пользователем {request.user.username}")
            return render(request, 'accounts/profile.html')
        except Exception as e:
            self.logger.error(f"Ошибка при отображении профиля: {e}", exc_info=True)
            messages.error(request, 'Ошибка при загрузке профиля')
            return redirect('dashboard')


class UsersListView(View):
    """Список пользователей"""
    logger = logging.getLogger(f'{__name__}.UsersListView')
    
    @method_decorator(login_required)
    def get(self, request):
        try:
            if not (request.user.is_admin or request.user.has_perm('accounts.can_manage_users')):
                self.logger.warning(f"Пользователь {request.user.username} попытался получить доступ к списку пользователей без прав")
                messages.error(request, 'У вас нет прав для просмотра этой страницы')
                return redirect('dashboard')
            
            self.logger.info(f"Просмотр списка пользователей администратором {request.user.username}")
            users = User.objects.all().order_by('username')
            
            return render(request, 'accounts/users_list.html', {
                'users': users,
                'active_page': 'users'
            })
            
        except DatabaseError as e:
            self.logger.error(f"Ошибка базы данных при получении списка пользователей: {e}", exc_info=True)
            messages.error(request, 'Ошибка при загрузке списка пользователей')
            return redirect('dashboard')
        except Exception as e:
            self.logger.error(f"Критическая ошибка при отображении списка пользователей: {e}", exc_info=True)
            messages.error(request, 'Произошла непредвиденная ошибка')
            return redirect('dashboard')
        

class ChairmanRegistrationView(CreateView):
    """
    Регистрация председателя с автоматическим созданием СНТ.
    """
    template_name = 'accounts/register.html'
    success_url = reverse_lazy('login')
    logger = logging.getLogger(f'{__name__}.ChairmanRegistrationView')
    
    def get_form_class(self):
        return UserCreationForm
    
    def get_context_data(self, **kwargs):
        try:
            context = super().get_context_data(**kwargs)
            context['active_page'] = 'register'
            return context
        except Exception as e:
            self.logger.error(f"Ошибка при формировании контекста регистрации: {e}", exc_info=True)
            return {}
    
    def form_valid(self, form):
        self.logger.info(f"Новая веб-регистрация председателя: {form.cleaned_data.get('username')}")
        
        try:
            with transaction.atomic():
                # Создаем пользователя
                user = form.save(commit=False)
                user.role = 'manager'
                user.is_active = True
                user.save()
                
                self.logger.info(f"Создан пользователь {user.username} через веб-форму")
                
                # Получаем данные организации
                org_name = self.request.POST.get('organization_name', '')
                org_inn = self.request.POST.get('organization_inn', '')
                
                if not org_name or not org_inn:
                    raise ValidationError('Название и ИНН организации обязательны')
                
                # Создаем СНТ
                organization = Organization.objects.create(
                    name=org_name,
                    short_name=self.request.POST.get('organization_short_name', ''),
                    inn=org_inn,
                    legal_address=self.request.POST.get('organization_address', ''),
                    bank_name=self.request.POST.get('organization_bank', ''),
                    bank_bik=self.request.POST.get('organization_bik', ''),
                    bank_account=self.request.POST.get('organization_account', ''),
                    bank_corr_account=self.request.POST.get('organization_corr', ''),
                    chairman=user,
                    is_active=True
                )
                
                self.logger.info(f"Создана организация {organization.name} через веб-форму")
                
                # Привязываем пользователя
                user.organization = organization
                user.save()
                
                self.logger.info(f"Регистрация через веб-форму успешно завершена: user={user.username}, org={organization.name}")
                
                return super().form_valid(form)
                
        except IntegrityError as e:
            self.logger.error(f"Ошибка целостности данных при веб-регистрации: {e}", exc_info=True)
            messages.error(self.request, 'Пользователь или организация с такими данными уже существуют')
            return self.form_invalid(form)
            
        except DatabaseError as e:
            self.logger.error(f"Ошибка базы данных при веб-регистрации: {e}", exc_info=True)
            messages.error(self.request, 'Ошибка базы данных при регистрации')
            return self.form_invalid(form)
            
        except ValidationError as e:
            self.logger.warning(f"Ошибка валидации при веб-регистрации: {e}")
            messages.error(self.request, str(e))
            return self.form_invalid(form)
            
        except Exception as e:
            self.logger.error(f"Критическая ошибка при веб-регистрации: {e}", exc_info=True)
            messages.error(self.request, 'Произошла ошибка при регистрации')
            return self.form_invalid(form)
    
    def form_invalid(self, form):
        self.logger.warning(f"Форма регистрации невалидна: {form.errors}")
        return super().form_invalid(form)