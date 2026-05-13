# SNT/land/serializers.py
from rest_framework import serializers
from .models import LandPlot


class LandPlotListSerializer(serializers.ModelSerializer):
    """
    Краткий сериализатор для списка участков.
    Включает основную информацию и количество владельцев.
    """
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    owners_count = serializers.IntegerField(read_only=True)  # Только для чтения!
    has_coordinates = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = LandPlot
        fields = [
            'id', 'plot_number', 'cadastral_number',
            'area_sqm', 'status', 'status_display', 'address', 
            'latitude', 'longitude', 'has_coordinates',
            'owners_count', 'created_at',
        ]
        read_only_fields = ['id', 'created_at', 'owners_count', 'has_coordinates']

    def get_has_coordinates(self, obj):
        return obj.has_coordinates


class LandPlotDetailSerializer(serializers.ModelSerializer):
    """
    Полный сериализатор для детального просмотра / редактирования участка.
    """
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    created_at = serializers.DateTimeField(read_only=True, format='%d.%m.%Y %H:%M')
    updated_at = serializers.DateTimeField(read_only=True, format='%d.%m.%Y %H:%M')
    has_coordinates = serializers.SerializerMethodField(read_only=True)
    
    # Убираем owners_count из детального сериализатора
    # или делаем его только для чтения, если нужно
    owners_count = serializers.IntegerField(read_only=True, required=False)
    
    plot_number = serializers.CharField(
        max_length=10,
        help_text='Номер участка (может содержать буквы, например: 42А)'
    )

    class Meta:
        model = LandPlot
        fields = [
            'id', 'plot_number', 'cadastral_number', 'area_sqm', 'address',
            'latitude', 'longitude', 'status', 'status_display', 'notes',
            'created_at', 'updated_at', 'has_coordinates', 'owners_count'
        ]
        read_only_fields = ('id', 'created_at', 'updated_at', 'status_display', 
                           'has_coordinates', 'owners_count')

    def get_has_coordinates(self, obj):
        return obj.has_coordinates

    def validate_plot_number(self, value):
        """Нормализация и валидация номера участка"""
        if not value.strip():
            raise serializers.ValidationError('Номер участка не может быть пустым')
        return value.strip().upper()

    def validate_cadastral_number(self, value):
        """Расширенная валидация кадастрового номера"""
        value = value.strip()
        parts = value.split(':')
        
        if len(parts) != 4:
            raise serializers.ValidationError(
                'Кадастровый номер должен состоять из 4 групп цифр, разделённых двоеточием'
            )
        
        for i, part in enumerate(parts):
            if not part.isdigit():
                raise serializers.ValidationError(
                    f'Группа {i+1} должна содержать только цифры'
                )
        
        # Проверяем длины групп
        if len(parts[0]) != 2:
            raise serializers.ValidationError('Группа 1 (регион) должна содержать 2 цифры')
        
        if len(parts[1]) != 2:
            raise serializers.ValidationError('Группа 2 (район) должна содержать 2 цифры')
        
        if len(parts[2]) < 6 or len(parts[2]) > 7:
            raise serializers.ValidationError('Группа 3 (квартал) должна содержать 6-7 цифр')
        
        if len(parts[3]) < 1:
            raise serializers.ValidationError('Группа 4 (номер участка) должна содержать хотя бы 1 цифру')
        
        return value

    def validate_area_sqm(self, value):
        """Валидация площади"""
        if value <= 0:
            raise serializers.ValidationError('Площадь должна быть больше 0')
        if value > 1000000:
            raise serializers.ValidationError('Площадь не может превышать 1 000 000 м²')
        return round(value, 2)

    def validate_latitude(self, value):
        """Валидация широты"""
        if value is not None and not (-90 <= value <= 90):
            raise serializers.ValidationError('Широта должна быть от -90 до 90')
        return value

    def validate_longitude(self, value):
        """Валидация долготы"""
        if value is not None and not (-180 <= value <= 180):
            raise serializers.ValidationError('Долгота должна быть от -180 до 180')
        return value

    def validate(self, data):
        """Общая валидация: координаты либо обе заданы, либо обе None"""
        if self.instance:
            lat = data.get('latitude', self.instance.latitude)
            lon = data.get('longitude', self.instance.longitude)
        else:
            lat = data.get('latitude')
            lon = data.get('longitude')
        
        if (lat is None) != (lon is None):
            raise serializers.ValidationError({
                'coordinates': 'Координаты должны быть заданы обе одновременно (широта И долгота)'
            })
        
        return data


class LandPlotGeoSerializer(serializers.ModelSerializer):
    """
    Специальный сериализатор для отображения участков на карте.
    """
    owners_info = serializers.SerializerMethodField(read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model = LandPlot
        fields = [
            'id', 'plot_number', 'latitude', 'longitude', 
            'status', 'status_display', 'owners_info', 'area_sqm'
        ]

    def get_owners_info(self, obj):
        """Информация о владельцах для отображения на карте"""
        owners = []
        for ownership in obj.ownerships.all():
            owners.append({
                'id': ownership.owner.id,
                'name': ownership.owner.full_name,
                'share': ownership.share,
            })
        return owners