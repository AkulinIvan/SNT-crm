from rest_framework import serializers
from .models import Organization, OrganizationMembership, OrganizationStaffAssignment
from users.serializers import OwnerListSerializer
from accounts.models import User


class OrganizationSerializer(serializers.ModelSerializer):
    """Краткий сериализатор для списка СНТ"""
    chairman_name = serializers.CharField(source='chairman.full_name', read_only=True)
    accountant_name = serializers.CharField(source='accountant.full_name', read_only=True)
    
    class Meta:
        model = Organization
        fields = [
            'id', 'name', 'short_name', 'inn', 'chairman_name', 
            'accountant_name', 'is_active'
        ]


class OrganizationDetailSerializer(serializers.ModelSerializer):
    """Полный сериализатор СНТ"""
    chairman_name = serializers.CharField(source='chairman.full_name', read_only=True)
    accountant_name = serializers.CharField(source='accountant.full_name', read_only=True)
    chairman_has_account = serializers.SerializerMethodField()
    accountant_has_account = serializers.SerializerMethodField()
    chairman_id = serializers.PrimaryKeyRelatedField(
        source='chairman',
        queryset=User.objects.all(),
        write_only=True,
        required=False,
        allow_null=True
    )
    accountant_id = serializers.PrimaryKeyRelatedField(
        source='accountant',
        queryset=User.objects.all(),
        write_only=True,
        required=False,
        allow_null=True
    )
    
    class Meta:
        model = Organization
        fields = '__all__'
        read_only_fields = ['created_at', 'updated_at']
    
    def get_chairman_has_account(self, obj):
        """Проверяет, есть ли у председателя аккаунт в системе"""
        if obj.chairman:
            return obj.chairman.is_active
        return False
    
    def get_accountant_has_account(self, obj):
        """Проверяет, есть ли у бухгалтера аккаунт в системе"""
        if obj.accountant:
            return obj.accountant.is_active
        return False


class OrganizationMembershipSerializer(serializers.ModelSerializer):
    """Сериализатор членства в СНТ"""
    owner_name = serializers.CharField(source='owner.full_name', read_only=True)
    owner_info = OwnerListSerializer(source='owner', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    
    class Meta:
        model = OrganizationMembership
        fields = '__all__'


class OrganizationMembershipCreateSerializer(serializers.ModelSerializer):
    """Сериализатор для создания членства"""
    
    class Meta:
        model = OrganizationMembership
        fields = ['owner', 'member_since', 'member_card_number', 'notes', 'status']
        

class ChairmanAssignmentSerializer(serializers.Serializer):
    """Сериализатор для назначения председателя"""
    user_id = serializers.IntegerField(required=True)
    assignment_order = serializers.CharField(required=False, allow_blank=True)
    notes = serializers.CharField(required=False, allow_blank=True)


class StaffAssignmentSerializer(serializers.ModelSerializer):
    """Сериализатор истории назначений"""
    user_name = serializers.CharField(source='user.full_name', read_only=True)
    role_display = serializers.CharField(source='get_role_display', read_only=True)
    
    class Meta:
        model = OrganizationStaffAssignment
        fields = '__all__'
        read_only_fields = ['assigned_at', 'created_at', 'updated_at']