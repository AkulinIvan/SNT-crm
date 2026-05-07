from rest_framework import serializers
from .models import LandPlot


class LandPlotListSerializer(serializers.ModelSerializer):
    """
    Краткий сериализатор для списка участков.
    Не тянет лишнего, только основные идентификационные поля.
    """
    class Meta:
        model = LandPlot
        fields = [
            'id', 'plot_number', 'cadastral_number',
            'area_sqm', 'status', 'address', 'latitude', 'longitude',
        ]


class LandPlotDetailSerializer(serializers.ModelSerializer):
    """
    Полный сериализатор для детального просмотра / редактирования участка.
    """
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)

    class Meta:
        model = LandPlot
        fields = '__all__'
        read_only_fields = ('id', 'created_at', 'updated_at')

    def validate_cadastral_number(self, value):
        """
        Дополнительная валидация кадастрового номера.
        Простейшая проверка формата: 4 группы цифр, разделённых двоеточием.
        """
        parts = value.strip().split(':')
        if len(parts) != 4:
            raise serializers.ValidationError(
                'Кадастровый номер должен состоять из 4 групп цифр, разделённых двоеточием'
            )
        for part in parts:
            if not part.isdigit():
                raise serializers.ValidationError(
                    'Кадастровый номер должен содержать только цифры и двоеточия'
                )
        return value

    def validate(self, data):
        """
        Общая валидация координат: либо обе заданы, либо ни одной.
        """
        lat = data.get('latitude')
        lon = data.get('longitude')
        if (lat is None) != (lon is None):
            raise serializers.ValidationError(
                'Координаты должны быть заданы обе одновременно (широта И долгота)'
            )
        return data


class LandPlotGeoSerializer(serializers.ModelSerializer):
    """
    Специальный сериализатор для отображения участков на карте.
    Возвращает только те поля, которые нужны для отрисовки меток.
    """
    class Meta:
        model = LandPlot
        fields = ['id', 'plot_number', 'latitude', 'longitude', 'status']