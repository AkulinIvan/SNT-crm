import logging
import traceback
from rest_framework import serializers
from django.core.validators import RegexValidator, EmailValidator
from decimal import Decimal
from .models import Owner, Ownership, ContactInfo
from land.serializers import LandPlotListSerializer

logger = logging.getLogger(__name__)


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
        logger.debug(f"Validating contact value: {value}")
        
        try:
            contact_type = None
            if hasattr(self, 'initial_data') and self.initial_data.get('type'):
                contact_type = self.initial_data.get('type')
            elif self.instance:
                contact_type = self.instance.type
            
            if contact_type == ContactInfo.PHONE:
                # Очистка номера телефона
                cleaned = ''.join(c for c in value if c.isdigit() or c in '+()- ')
                digits = ''.join(c for c in cleaned if c.isdigit())
                
                if len(digits) < 10:
                    logger.warning(f"Phone number has only {len(digits)} digits: {value}")
                    raise serializers.ValidationError('Номер телефона должен содержать не менее 10 цифр')
                
                if len(digits) > 11:
                    logger.warning(f"Phone number has {len(digits)} digits (max 11): {value}")
                    raise serializers.ValidationError('Номер телефона должен содержать не более 11 цифр')
                
                logger.debug(f"Phone number validated: {cleaned}")
                return cleaned
                
            elif contact_type == ContactInfo.EMAIL:
                validator = EmailValidator()
                try:
                    validator(value)
                    logger.debug(f"Email validated: {value}")
                except Exception as e:
                    logger.warning(f"Invalid email: {value}, error: {e}")
                    raise serializers.ValidationError('Введите корректный email-адрес')
            
            return value
            
        except serializers.ValidationError:
            raise
        except Exception as e:
            logger.error(f"Error validating contact value: {e}\n{traceback.format_exc()}")
            raise serializers.ValidationError(f'Ошибка валидации: {str(e)}')

    def validate(self, data):
        """Валидация контакта"""
        logger.debug(f"Validating contact data: {data}")
        
        try:
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
                # Проверка на дубликаты
                duplicate_qs = ContactInfo.objects.filter(
                    owner=owner,
                    type=contact_type,
                    value=value,
                    is_active=True
                )
                
                if self.instance and self.instance.pk:
                    duplicate_qs = duplicate_qs.exclude(pk=self.instance.pk)
                
                duplicate = duplicate_qs.exists()
                
                if duplicate:
                    logger.warning(f"Duplicate contact found: owner={owner.id}, type={contact_type}, value={value}")
                    raise serializers.ValidationError({
                        'value': 'Такой контакт уже существует и активен.'
                    })
            
            return data
            
        except serializers.ValidationError:
            raise
        except Exception as e:
            logger.error(f"Error in contact validation: {e}\n{traceback.format_exc()}")
            raise serializers.ValidationError(f'Ошибка валидации: {str(e)}')

    def create(self, validated_data):
        """Создание контакта с логированием"""
        logger.info(f"Creating new contact: {validated_data.get('type')} for owner {validated_data.get('owner')}")
        
        try:
            contact = super().create(validated_data)
            logger.info(f"Contact created: ID={contact.id}, type={contact.type}, value={contact.value}")
            return contact
        except Exception as e:
            logger.error(f"Error creating contact: {e}\n{traceback.format_exc()}")
            raise

    def update(self, instance, validated_data):
        """Обновление контакта с логированием"""
        logger.info(f"Updating contact {instance.id}")
        
        try:
            old_value = instance.value
            new_value = validated_data.get('value', old_value)
            
            instance = super().update(instance, validated_data)
            
            if old_value != new_value:
                logger.info(f"Contact {instance.id} value changed: '{old_value}' -> '{new_value}'")
            
            return instance
        except Exception as e:
            logger.error(f"Error updating contact: {e}\n{traceback.format_exc()}")
            raise


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
        """Отображение доли в читаемом формате"""
        try:
            if obj.share == '1/1':
                return 'Полная собственность'
            
            num, den = obj.share.split('/')
            percent = int(num) / int(den) * 100
            return f'{obj.share} ({percent:.0f}%)'
        except Exception as e:
            logger.warning(f"Error formatting share display for {obj.id}: {e}")
            return obj.share

    def validate_share(self, value):
        """Валидация доли собственности"""
        logger.debug(f"Validating share: {value}")
        
        try:
            parts = value.split('/')
            if len(parts) == 2:
                num = int(parts[0])
                den = int(parts[1])
                if num > 0 and den > 0 and num <= den:
                    logger.debug(f"Share validated: {value}")
                    return value
        except (ValueError, AttributeError) as e:
            logger.warning(f"Invalid share format: {value}, error: {e}")
        
        raise serializers.ValidationError(
            'Неверный формат доли. Используйте формат "числитель/знаменатель", например: 1/2'
        )

    def create(self, validated_data):
        """Создание права собственности с логированием"""
        logger.info(f"Creating ownership: owner={validated_data.get('owner')}, plot={validated_data.get('land_plot')}")
        
        try:
            ownership = super().create(validated_data)
            logger.info(f"Ownership created: ID={ownership.id}, share={ownership.share}")
            return ownership
        except Exception as e:
            logger.error(f"Error creating ownership: {e}\n{traceback.format_exc()}")
            raise


class OwnerListSerializer(serializers.ModelSerializer):
    """Сериализатор для списка владельцев (краткий)"""
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
        try:
            contact = obj.contacts.filter(type='ph', is_active=True).first()
            return contact.value if contact else None
        except Exception as e:
            logger.error(f"Error getting primary phone for owner {obj.id}: {e}")
            return None
    
    def get_primary_email(self, obj):
        try:
            contact = obj.contacts.filter(type='em', is_active=True).first()
            return contact.value if contact else None
        except Exception as e:
            logger.error(f"Error getting primary email for owner {obj.id}: {e}")
            return None
    
    def get_plots_count(self, obj):
        try:
            return obj.land_plots.count()
        except Exception as e:
            logger.error(f"Error getting plots count for owner {obj.id}: {e}")
            return 0
    
    def get_total_debt(self, obj):
        """Расчёт общей задолженности владельца"""
        try:
            return float(obj.total_debt)
        except Exception as e:
            logger.error(f"Error calculating debt for owner {obj.id}: {e}")
            return 0
    
    def get_organization_name(self, obj):
        try:
            membership = obj.memberships.filter(status='active').first()
            return membership.organization.short_name if membership else None
        except Exception as e:
            logger.error(f"Error getting organization name for owner {obj.id}: {e}")
            return None


class OwnerDetailSerializer(serializers.ModelSerializer):
    """
    Полный сериализатор владельца.
    """
    contacts = ContactInfoSerializer(many=True, read_only=True)
    ownerships = OwnershipSerializer(many=True, read_only=True)
    primary_phone = serializers.SerializerMethodField()
    primary_email = serializers.SerializerMethodField()
    created_at = serializers.DateTimeField(read_only=True, format='%d.%m.%Y %H:%M')
    updated_at = serializers.DateTimeField(read_only=True, format='%d.%m.%Y %H:%M')
    total_debt = serializers.SerializerMethodField()
    is_debtor = serializers.SerializerMethodField()
    memberships = serializers.SerializerMethodField()
    tariff_limits = serializers.SerializerMethodField()
    plots = serializers.SerializerMethodField()
    assessments = serializers.SerializerMethodField()
    organizations = serializers.SerializerMethodField()
    
    class Meta:
        model = Owner
        fields = [
            'id', 'full_name',
            'primary_phone', 'primary_email',
            'contacts', 'ownerships', 'plots',
            'total_debt', 'is_debtor', 'memberships',
            'created_at', 'updated_at', 'tariff_limits',
            'assessments', 'organizations',
        ]

    def get_primary_phone(self, obj):
        try:
            contact = obj.contacts.filter(type='ph', is_active=True).first()
            return contact.value if contact else None
        except Exception as e:
            logger.error(f"Error getting primary phone for owner {obj.id}: {e}")
            return None

    def get_primary_email(self, obj):
        try:
            contact = obj.contacts.filter(type='em', is_active=True).first()
            return contact.value if contact else None
        except Exception as e:
            logger.error(f"Error getting primary email for owner {obj.id}: {e}")
            return None

    def get_total_debt(self, obj):
        try:
            return float(obj.total_debt)
        except Exception as e:
            logger.error(f"Error calculating total debt for owner {obj.id}: {e}")
            return 0.0

    def get_is_debtor(self, obj):
        try:
            return obj.is_debtor
        except Exception as e:
            logger.error(f"Error checking debtor status for owner {obj.id}: {e}")
            return False
    
    def get_memberships(self, obj):
        """Получить все членства владельца в СНТ"""
        try:
            from organizations.serializers import OrganizationMembershipSerializer
            memberships = obj.memberships.select_related('organization').all()
            return OrganizationMembershipSerializer(memberships, many=True).data
        except ImportError as e:
            logger.warning(f"Organizations app not available: {e}")
            return []
        except Exception as e:
            logger.error(f"Error getting memberships for owner {obj.id}: {e}")
            return []

    def get_tariff_limits(self, obj):
        """Информация о лимитах тарифа для организации"""
        try:
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
        except Exception as e:
            logger.error(f"Error getting tariff limits for owner {obj.id}: {e}")
            return None
        
    def get_contacts(self, obj):
        """Получение активных контактов"""
        try:
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
        except Exception as e:
            logger.error(f"Error getting contacts for owner {obj.id}: {e}")
            return []
    
    def get_plots(self, obj):
        """Получение участков владельца"""
        try:
            from land.serializers import LandPlotListSerializer
            plots = obj.land_plots.all()
            return LandPlotListSerializer(plots, many=True).data
        except ImportError as e:
            logger.warning(f"Land app not available: {e}")
            return []
        except Exception as e:
            logger.error(f"Error getting plots for owner {obj.id}: {e}")
            return []
    
    def get_assessments(self, obj):
        """Получение активных начислений"""
        try:
            from payments.serializers import AssessmentListSerializer
            assessments = obj.assessments.filter(status__in=['pending', 'partial', 'overdue'])
            return AssessmentListSerializer(assessments, many=True).data
        except ImportError as e:
            logger.warning(f"Payments app not available: {e}")
            return []
        except Exception as e:
            logger.error(f"Error getting assessments for owner {obj.id}: {e}")
            return []
    
    def get_organizations(self, obj):
        """Получение организаций владельца"""
        try:
            memberships = obj.memberships.filter(status='active')
            result = []
            for m in memberships:
                org_data = {
                    'id': m.organization.id,
                    'name': m.organization.name,
                    'short_name': m.organization.short_name,
                }
                # Используем правильные поля модели
                if hasattr(m, 'member_since') and m.member_since:
                    org_data['joined_at'] = m.member_since
                elif hasattr(m, 'created_at') and m.created_at:
                    org_data['joined_at'] = m.created_at.date()
                else:
                    org_data['joined_at'] = None
                
                result.append(org_data)
            return result
        except Exception as e:
            logger.error(f"Error getting organizations for owner {obj.id}: {e}")
            return []


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
        logger.debug(f"Validating full_name: {value}")
        
        try:
            # Удаляем лишние пробелы
            value = ' '.join(value.split())
            
            # Приводим к формату "Имя Фамилия Отчество"
            parts = value.split()
            if len(parts) < 2:
                logger.warning(f"Full name has only {len(parts)} parts: {value}")
                raise serializers.ValidationError('Укажите минимум фамилию и имя')
            
            # Каждую часть с заглавной буквы
            normalized = ' '.join(part.capitalize() for part in parts)
            
            if normalized != value:
                logger.debug(f"Normalized full_name: '{value}' -> '{normalized}'")
            
            return normalized
            
        except serializers.ValidationError:
            raise
        except Exception as e:
            logger.error(f"Error validating full_name: {e}")
            raise serializers.ValidationError(f'Ошибка валидации ФИО: {str(e)}')

    def create(self, validated_data):
        """Создание владельца с логированием"""
        logger.info(f"Creating new owner: {validated_data.get('full_name')}")
        
        try:
            owner = super().create(validated_data)
            logger.info(f"Owner created: ID={owner.id}, name={owner.full_name}")
            return owner
        except Exception as e:
            logger.error(f"Error creating owner: {e}\n{traceback.format_exc()}")
            raise

    def update(self, instance, validated_data):
        """Обновление владельца с логированием"""
        logger.info(f"Updating owner {instance.id}: {instance.full_name} -> {validated_data.get('full_name', instance.full_name)}")
        
        try:
            old_name = instance.full_name
            instance = super().update(instance, validated_data)
            
            if old_name != instance.full_name:
                logger.info(f"Owner {instance.id} name changed: '{old_name}' -> '{instance.full_name}'")
            
            return instance
        except Exception as e:
            logger.error(f"Error updating owner: {e}\n{traceback.format_exc()}")
            raise