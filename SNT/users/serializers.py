from rest_framework import serializers
from django.core.validators import RegexValidator
from decimal import Decimal
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
        contact_type = None
        if hasattr(self, 'initial_data') and self.initial_data.get('type'):
            contact_type = self.initial_data.get('type')
        elif self.instance:
            contact_type = self.instance.type
        
        if contact_type == ContactInfo.PHONE:
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
        owner = data.get('owner')
        if not owner and self.instance:
            owner = self.instance.owner
        
        contact_type = data.get('type')
        if not contact_type and self.instance:
            contact_type = self.instance.type
        
        value = data.get('value')
        if not value and self.instance:
            value = self.instance.value
        
        if owner and contact_type and value and data.get('is_active', True):
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
        if obj.share == '1/1':
            return 'Полная собственность'
        try:
            num, den = obj.share.split('/')
            percent = int(num) / int(den) * 100
            return f'{obj.share} ({percent:.0f}%)'
        except:
            return obj.share

    def validate_share(self, value):
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
    primary_phone = serializers.SerializerMethodField()
    primary_email = serializers.SerializerMethodField()
    plots_count = serializers.SerializerMethodField()
    total_debt = serializers.SerializerMethodField()
    organization_name = serializers.SerializerMethodField()
    
    class Meta:
        model = Owner
        fields = [
            'id', 'full_name', 'primary_phone', 'primary_email', 
            'plots_count', 'total_debt', 'organization_name', 'created_at'
        ]
    
    def get_primary_phone(self, obj):
        contact = obj.contacts.filter(type='ph', is_active=True).first()
        return contact.value if contact else None
    
    def get_primary_email(self, obj):
        contact = obj.contacts.filter(type='em', is_active=True).first()
        return contact.value if contact else None
    
    def get_plots_count(self, obj):
        return obj.land_plots.count()
    
    def get_total_debt(self, obj):
        """Расчёт общей задолженности владельца - используем свойство модели"""
        try:
            return float(obj.total_debt)  # Используем property из модели
        except Exception as e:
            print(f"Error calculating debt for owner {obj.id}: {e}")
            return 0
    
    def get_organization_name(self, obj):
        membership = obj.memberships.filter(status='active').first()
        return membership.organization.short_name if membership else None

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
    memberships = serializers.SerializerMethodField()
    tariff_limits = serializers.SerializerMethodField()
    
    class Meta:
        model = Owner
        fields = [
            'id', 'full_name',
            'primary_phone', 'primary_email',
            'contacts', 'ownerships',
            'total_debt', 'is_debtor', 'memberships',
            'created_at', 'updated_at', 'tariff_limits',
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

    def get_tariff_limits(self, obj):
        """Информация о лимитах тарифа для организации"""
        org = obj.organization
        if not org:
            return None
        
        subscription = getattr(org, 'subscription', None)
        if not subscription or not subscription.is_active:
            return {
                'has_subscription': False,
                'message': 'Нет активной подписки'
            }
        
        tariff = subscription.tariff
        
        return {
            'has_subscription': True,
            'tariff_name': tariff.name,
            'owners_limit': {
                'current': org.owners_count,
                'max': tariff.max_owners,
                'remaining': max(0, tariff.max_owners - org.owners_count)
            },
            'plots_limit': {
                'current': org.plots_count,
                'max': tariff.max_plots,
                'remaining': max(0, tariff.max_plots - org.plots_count)
            }
        }
        
    def get_contacts(self, obj):
        contacts = obj.contacts.filter(is_active=True)
        return [
            {
                'id': c.id,
                'type': c.get_type_display(),
                'type_code': c.type,
                'value': c.value,
                'is_verified': c.is_verified,
                'note': c.note
            }
            for c in contacts
        ]
    
    def get_plots(self, obj):
        from land.serializers import LandPlotListSerializer
        plots = obj.land_plots.all()
        return LandPlotListSerializer(plots, many=True).data
    
    def get_assessments(self, obj):
        from payments.serializers import AssessmentListSerializer
        assessments = obj.assessments.filter(status__in=['pending', 'partial', 'overdue'])
        return AssessmentListSerializer(assessments, many=True).data
    
    def get_total_debt(self, obj):
        """Расчёт общей задолженности"""
        from payments.models import Assessment
        
        assessments = Assessment.objects.filter(
            owner=obj,
            status__in=['pending', 'partial', 'overdue']
        )
        
        total_debt = Decimal('0')
        for assessment in assessments:
            total_debt += assessment.debt
        
        return float(total_debt)
    
    def get_organizations(self, obj):
        memberships = obj.memberships.filter(status='active')
        return [
            {
                'id': m.organization.id,
                'name': m.organization.name,
                'short_name': m.organization.short_name,
                'joined_at': m.joined_at
            }
            for m in memberships
        ]
    
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