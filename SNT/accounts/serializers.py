import logging

from rest_framework import serializers
from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import validate_password
from .models import User, UserActionLog
logger = logging.getLogger(__name__)

class UserLoginSerializer(serializers.Serializer):
    """Сериализатор для входа в систему"""
    username = serializers.CharField(label='Логин')
    password = serializers.CharField(
        label='Пароль',
        style={'input_type': 'password'},
        write_only=True
    )
    remember_me = serializers.BooleanField(default=False, required=False)

    def validate(self, data):
        try:
            username = data.get('username')
            password = data.get('password')
            
            if username and password:
                user = authenticate(username=username, password=password)
                if not user:
                    logger.warning(f"Неудачная попытка аутентификации: username={username}")
                    raise serializers.ValidationError('Неверный логин или пароль')
                if not user.is_active:
                    logger.warning(f"Попытка входа деактивированного пользователя: {username}")
                    raise serializers.ValidationError('Учетная запись деактивирована')
            else:
                raise serializers.ValidationError('Необходимо указать логин и пароль')
            
            data['user'] = user
            return data
            
        except serializers.ValidationError:
            raise
        except Exception as e:
            logger.error(f"Ошибка при валидации входа: {e}", exc_info=True)
            raise serializers.ValidationError('Ошибка при проверке данных')


class UserChangePasswordSerializer(serializers.Serializer):
    """Сериализатор для смены пароля"""
    old_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True, validators=[validate_password])
    confirm_password = serializers.CharField(write_only=True)

    def validate_old_password(self, value):
        user = self.context['request'].user
        if not user.check_password(value):
            raise serializers.ValidationError('Неверный текущий пароль')
        return value

    def validate(self, data):
        if data['new_password'] != data['confirm_password']:
            raise serializers.ValidationError({
                'confirm_password': 'Пароли не совпадают'
            })
        return data


class UserListSerializer(serializers.ModelSerializer):
    """Краткий сериализатор для списка пользователей"""
    full_name = serializers.CharField(read_only=True)
    role_display = serializers.CharField(source='get_role_display', read_only=True)

    class Meta:
        model = User
        fields = [
            'id', 'username', 'full_name', 'email',
            'role', 'role_display', 'position', 'phone',
            'is_active', 'last_activity', 'date_joined',
        ]


class UserDetailSerializer(serializers.ModelSerializer):
    """Полный сериализатор пользователя"""
    full_name = serializers.CharField(read_only=True)
    role_display = serializers.CharField(source='get_role_display', read_only=True)
    permissions = serializers.SerializerMethodField()
    groups = serializers.StringRelatedField(many=True, read_only=True)

    class Meta:
        model = User
        fields = [
            'id', 'username', 'first_name', 'last_name', 'middle_name',
            'full_name', 'email', 'phone', 'position',
            'role', 'role_display', 'is_active', 'is_superuser',
            'permissions', 'groups', 'avatar', 'notes',
            'last_activity', 'date_joined', 'created_at', 'updated_at',
        ]
        read_only_fields = ['date_joined', 'created_at', 'updated_at', 'last_activity']

    def get_permissions(self, obj):
        return obj.get_permissions_list()


class UserCreateSerializer(serializers.ModelSerializer):
    """Сериализатор для создания пользователя"""
    password = serializers.CharField(write_only=True, validators=[validate_password])
    confirm_password = serializers.CharField(write_only=True)

    class Meta:
        model = User
        fields = [
            'username', 'password', 'confirm_password',
            'first_name', 'last_name', 'middle_name',
            'email', 'phone', 'position', 'role',
            'is_active', 'notes',
        ]

    def validate(self, data):
        if data['password'] != data.pop('confirm_password'):
            raise serializers.ValidationError({
                'confirm_password': 'Пароли не совпадают'
            })
        return data

    def create(self, validated_data):
        password = validated_data.pop('password')
        user = User.objects.create(**validated_data)
        user.set_password(password)
        user.save()
        return user


class UserUpdateSerializer(serializers.ModelSerializer):
    """Сериализатор для редактирования пользователя"""
    
    class Meta:
        model = User
        fields = [
            'first_name', 'last_name', 'middle_name',
            'email', 'phone', 'position', 'role',
            'is_active', 'avatar', 'notes',
        ]


class UserActionLogSerializer(serializers.ModelSerializer):
    """Сериализатор лога действий"""
    user_name = serializers.CharField(source='user.full_name', read_only=True)
    action_display = serializers.CharField(source='get_action_display', read_only=True)

    class Meta:
        model = UserActionLog
        fields = [
            'id', 'user', 'user_name', 'action', 'action_display',
            'model_name', 'object_id', 'details',
            'ip_address', 'created_at',
        ]