from rest_framework import serializers
from .models import Owner, Ownership, ContactInfo
from land.serializers import LandPlotListSerializer


class ContactInfoSerializer(serializers.ModelSerializer):
    """Сериализатор контактных данных."""
    type_display = serializers.CharField(source='get_type_display', read_only=True)

    class Meta:
        model = ContactInfo
        fields = [
            'id', 'type', 'type_display', 'value',
            'is_active', 'is_verified', 'note', 'created_at',
        ]
        read_only_fields = ['created_at']

    def validate(self, data):
        """
        Если создаётся новый активный контакт того же типа,
        деактивируем старые автоматически (опциональное поведение).
        """
        request = self.context.get('request')
        if request and request.method == 'POST':
            owner = data.get('owner') or (self.instance.owner if self.instance else None)
            ctype = data.get('type')
            if owner and ctype and data.get('is_active', True):
                ContactInfo.objects.filter(
                    owner=owner,
                    type=ctype,
                    is_active=True,
                ).exclude(pk=self.instance.pk if self.instance else None).update(
                    is_active=False,
                    note='Заменён новым контактом',
                )
        return data


class OwnershipSerializer(serializers.ModelSerializer):
    """Сериализатор права собственности."""
    land_plot_detail = LandPlotListSerializer(source='land_plot', read_only=True)
    owner_name = serializers.CharField(source='owner.full_name', read_only=True)

    class Meta:
        model = Ownership
        fields = [
            'id', 'owner', 'owner_name', 'land_plot', 'land_plot_detail',
            'share', 'ownership_since', 'document_basis',
        ]
        read_only_fields = ['owner_name', 'land_plot_detail']


class OwnerListSerializer(serializers.ModelSerializer):
    """Краткий сериализатор для списка владельцев."""
    primary_phone = serializers.CharField(read_only=True)
    plots_count = serializers.SerializerMethodField()

    class Meta:
        model = Owner
        fields = [
            'id', 'full_name', 'primary_phone', 'plots_count', 'created_at',
        ]

    def get_plots_count(self, obj):
        return obj.land_plots.count()


class OwnerDetailSerializer(serializers.ModelSerializer):
    """
    Полный сериализатор владельца:
    — все контакты,
    — все участки с долями,
    — основные телефоны / email.
    """
    contacts = ContactInfoSerializer(many=True, read_only=True)
    ownerships = OwnershipSerializer(many=True, read_only=True)
    primary_phone = serializers.CharField(read_only=True)
    primary_email = serializers.CharField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)

    class Meta:
        model = Owner
        fields = [
            'id', 'full_name',
            'primary_phone', 'primary_email',
            'contacts', 'ownerships',
            'created_at', 'updated_at',
        ]


class OwnerCreateUpdateSerializer(serializers.ModelSerializer):
    """
    Сериализатор для создания и редактирования владельца.
    Не включает связи — они управляются отдельными эндпоинтами.
    """
    class Meta:
        model = Owner
        fields = ['id', 'full_name']