from rest_framework import serializers
from .models import CallRecord
from users.models import Owner
from land.models import LandPlot


class CallRecordListSerializer(serializers.ModelSerializer):
    """
    Краткий сериализатор для списка звонков.
    """
    caller_display = serializers.CharField(read_only=True)
    duration_display = serializers.CharField(read_only=True)
    has_recording = serializers.BooleanField(read_only=True)
    owner_name = serializers.CharField(source='owner.full_name', read_only=True, default=None)
    land_plot_number = serializers.CharField(source='land_plot.plot_number', read_only=True, default=None)
    tags_list = serializers.ListField(child=serializers.CharField(), read_only=True)

    class Meta:
        model = CallRecord
        fields = [
            'id', 'caller_display', 'caller_number', 'direction',
            'status', 'started_at', 'duration_seconds', 'duration_display',
            'has_recording', 'is_important', 'owner', 'owner_name',
            'land_plot', 'land_plot_number', 'tags_list',
        ]


class CallRecordDetailSerializer(serializers.ModelSerializer):
    """
    Полный сериализатор для детального просмотра / редактирования.
    """
    caller_display = serializers.CharField(read_only=True)
    duration_display = serializers.CharField(read_only=True)
    has_recording = serializers.BooleanField(read_only=True)
    direction_display = serializers.CharField(source='get_direction_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    tags_list = serializers.ListField(child=serializers.CharField(), read_only=True)
    owner_name = serializers.CharField(source='owner.full_name', read_only=True, default=None)
    land_plot_number = serializers.CharField(source='land_plot.plot_number', read_only=True, default=None)
    owner_contacts = serializers.SerializerMethodField()

    class Meta:
        model = CallRecord
        fields = [
            'id', 'owner', 'owner_name', 'owner_contacts',
            'land_plot', 'land_plot_number',
            'caller_number', 'called_number', 'direction', 'direction_display',
            'status', 'status_display',
            'started_at', 'answered_at', 'ended_at',
            'duration_seconds', 'duration_display',
            'audio_file', 'has_recording',
            'operator_note', 'tags', 'tags_list',
            'is_important', 'asterisk_uniqueid', 'asterisk_channel',
            'created_at', 'updated_at',
        ]
        read_only_fields = [
            'caller_number', 'called_number', 'direction', 'started_at',
            'answered_at', 'ended_at', 'duration_seconds', 'audio_file',
            'asterisk_uniqueid', 'asterisk_channel', 'created_at', 'updated_at',
        ]

    def get_owner_contacts(self, obj):
        if obj.owner:
            return list(obj.owner.contacts.filter(is_active=True).values('type', 'value'))
        return []


class CallRecordCreateSerializer(serializers.ModelSerializer):
    """
    Сериализатор для создания звонка (например, из вебхука Asterisk).
    """
    class Meta:
        model = CallRecord
        fields = [
            'caller_number', 'called_number', 'direction', 'started_at',
            'answered_at', 'ended_at', 'duration_seconds',
            'asterisk_uniqueid', 'asterisk_channel', 'asterisk_recording_file',
        ]

    def create(self, validated_data):
        # Пытаемся найти владельца по номеру телефона
        caller_number = validated_data.get('caller_number', '')
        owner = self._find_owner_by_number(caller_number)
        if owner:
            validated_data['owner'] = owner
        return super().create(validated_data)

    def _find_owner_by_number(self, number: str):
        """
        Поиск владельца по номеру телефона.
        Ищем точное совпадение последних 10 цифр.
        """
        from users.models import ContactInfo
        clean_number = ''.join(c for c in number if c.isdigit())[-10:]
        if not clean_number:
            return None
        contacts = ContactInfo.objects.filter(
            type=ContactInfo.PHONE,
            is_active=True,
        ).select_related('owner')
        for contact in contacts:
            contact_clean = ''.join(c for c in contact.value if c.isdigit())[-10:]
            if contact_clean == clean_number:
                return contact.owner
        return None


class CallRecordUpdateSerializer(serializers.ModelSerializer):
    """
    Для редактирования оператором: заметки, теги, важность.
    """
    class Meta:
        model = CallRecord
        fields = [
            'operator_note', 'tags', 'is_important', 'status',
            'owner', 'land_plot',
        ]