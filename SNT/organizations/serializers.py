from rest_framework import serializers
from .models import Organization, OrganizationMembership
from users.serializers import OwnerListSerializer


class OrganizationSerializer(serializers.ModelSerializer):
    """Краткий сериализатор для списка СНТ"""
    chairman_name = serializers.CharField(source='chairman.full_name', read_only=True)
    
    class Meta:
        model = Organization
        fields = [
            'id', 'name', 'short_name', 'inn', 'chairman_name', 'is_active'
        ]


class OrganizationDetailSerializer(serializers.ModelSerializer):
    """Полный сериализатор СНТ"""
    chairman_name = serializers.CharField(source='chairman.full_name', read_only=True)
    accountant_name = serializers.CharField(source='accountant.full_name', read_only=True)
    
    class Meta:
        model = Organization
        fields = '__all__'
        read_only_fields = ['created_at', 'updated_at']


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