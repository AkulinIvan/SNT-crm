from rest_framework import serializers
from django.core.validators import RegexValidator
from .models import Owner, Ownership, ContactInfo
from land.serializers import LandPlotListSerializer


class ContactInfoSerializer(serializers.ModelSerializer):
    """Сериализатор контактных данных."""
    type_display = serializers.CharField(source='get_type_display', read_only=True)
    owner_name = serializers.CharField(source='owner.full_name', read_only=True)
    
    owner = serializers.PrimaryKeyRelatedField(
        queryset=Owner.objects.all(),
        required=False,
        allow_null=True
    )

    class Meta:
        model = ContactInfo
        fields = [
            'id', 'owner', 'owner_name', 'type', 'type_display', 'value',
            'is_active', 'is_verified', 'note', 'created_at',
        ]
        read_only_fields = ['created_at', 'owner_name']

    def validate_value(self, value):
        """Дополнительная валидация в зависимости от типа"""
        # Получаем тип из разных источников
        contact_type = None
        if hasattr(self, 'initial_data') and self.initial_data.get('type'):
            contact_type = self.initial_data.get('type')
        elif self.instance:
            contact_type = self.instance.type
        
        if contact_type == ContactInfo.PHONE:
            # Очищаем номер от лишних символов
            cleaned = ''.join(c for c in value if c.isdigit() or c in '+()- ')
            digits = ''.join(c for c in cleaned if c.isdigit())
            if len(digits) < 10:
                raise serializers.ValidationError('Номер телефона должен содержать не менее 10 цифр')
            return cleaned
        elif contact_type == ContactInfo.EMAIL:
            from django.core.validators import EmailValidator
            validator = EmailValidator()
            try:
                validator(value)
            except:
                raise serializers.ValidationError('Введите корректный email-адрес')
        
        return value

    def validate(self, data):
        """Валидация контакта"""
        # Получаем owner
        owner = data.get('owner')
        if not owner and self.instance:
            owner = self.instance.owner
        
        # Получаем тип и значение
        contact_type = data.get('type')
        if not contact_type and self.instance:
            contact_type = self.instance.type
        
        value = data.get('value')
        if not value and self.instance:
            value = self.instance.value
        
        if owner and contact_type and value and data.get('is_active', True):
            # Проверяем дубликаты
            duplicate = ContactInfo.objects.filter(
                owner=owner,
                type=contact_type,
                value=value,
                is_active=True
            ).exclude(pk=self.instance.pk if self.instance else None).exists()
            
            if duplicate:
                raise serializers.ValidationError({
                    'value': 'Такой контакт уже существует и активен.'
                })
        
        return data


class OwnershipSerializer(serializers.ModelSerializer):
    """Сериализатор права собственности."""
    land_plot_detail = LandPlotListSerializer(source='land_plot', read_only=True)
    owner_name = serializers.CharField(source='owner.full_name', read_only=True)
    share_display = serializers.SerializerMethodField()

    class Meta:
        model = Ownership
        fields = [
            'id', 'owner', 'owner_name', 'land_plot', 'land_plot_detail',
            'share', 'share_display', 'ownership_since', 'document_basis',
        ]
        read_only_fields = ['owner_name', 'land_plot_detail']

    def get_share_display(self, obj):
        """Красивое отображение доли"""
        if obj.share == '1/1':
            return 'Полная собственность'
        try:
            num, den = obj.share.split('/')
            percent = int(num) / int(den) * 100
            return f'{obj.share} ({percent:.0f}%)'
        except:
            return obj.share

    def validate_share(self, value):
        """Валидация доли"""
        try:
            parts = value.split('/')
            if len(parts) == 2:
                num = int(parts[0])
                den = int(parts[1])
                if num > 0 and den > 0 and num <= den:
                    return value
        except:
            pass
        raise serializers.ValidationError(
            'Неверный формат доли. Используйте формат "числитель/знаменатель", например: 1/2'
        )


class OwnerListSerializer(serializers.ModelSerializer):
    """Краткий сериализатор для списка владельцев."""
    primary_phone = serializers.CharField(read_only=True)
    primary_email = serializers.CharField(read_only=True)
    plots_count = serializers.IntegerField(read_only=True)
    organization_name = serializers.SerializerMethodField()  # Изменено на метод

    class Meta:
        model = Owner
        fields = [
            'id', 'full_name', 'primary_phone', 'primary_email', 
            'plots_count', 'created_at', 'organization_name',
        ]

    def get_organization_name(self, obj):
        return obj.organization_name


class OwnerDetailSerializer(serializers.ModelSerializer):
    """
    Полный сериализатор владельца.
    """
    contacts = ContactInfoSerializer(many=True, read_only=True)
    ownerships = OwnershipSerializer(many=True, read_only=True)
    primary_phone = serializers.CharField(read_only=True)
    primary_email = serializers.CharField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True, format='%d.%m.%Y %H:%M')
    updated_at = serializers.DateTimeField(read_only=True, format='%d.%m.%Y %H:%M')
    total_debt = serializers.SerializerMethodField()
    is_debtor = serializers.SerializerMethodField()
    memberships = serializers.SerializerMethodField()  # Добавить членства

    class Meta:
        model = Owner
        fields = [
            'id', 'full_name',
            'primary_phone', 'primary_email',
            'contacts', 'ownerships',
            'total_debt', 'is_debtor', 'memberships',  # Добавить memberships
            'created_at', 'updated_at',
        ]

    def get_total_debt(self, obj):
        return float(obj.total_debt)

    def get_is_debtor(self, obj):
        return obj.is_debtor
    
    def get_memberships(self, obj):
        """Получить все членства владельца в СНТ"""
        from organizations.serializers import OrganizationMembershipSerializer
        memberships = obj.memberships.select_related('organization').all()
        return OrganizationMembershipSerializer(memberships, many=True).data


class OwnerCreateUpdateSerializer(serializers.ModelSerializer):
    """
    Сериализатор для создания и редактирования владельца.
    """
    full_name = serializers.CharField(
        max_length=150,
        validators=[
            RegexValidator(
                regex=r'^[а-яА-ЯёЁa-zA-Z\s\-]+$',
                message='ФИО может содержать только буквы, пробелы и дефисы'
            )
        ]
    )

    class Meta:
        model = Owner
        fields = ['id', 'full_name']

    def validate_full_name(self, value):
        """Нормализация ФИО"""
        value = ' '.join(value.split())
        return value.title()
    
    def create(self, validated_data):
        """Автоматически подставляем организацию из запроса"""
        request = self.context.get('request')
        if request and hasattr(request, 'current_organization') and request.current_organization:
            validated_data['organization'] = request.current_organization
        return super().create(validated_data)