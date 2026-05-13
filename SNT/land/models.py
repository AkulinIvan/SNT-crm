# SNT\land\models.py
from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator
from django.core.exceptions import ValidationError
import re


class LandPlot(models.Model):
    """
    Модель земельного участка.
    Кадастровый номер — уникальный идентификатор, 
    номер участка — удобное локальное обозначение.
    """
    cadastral_number = models.CharField(
        'Кадастровый номер',
        max_length=30,
        unique=True,
        help_text='Формат: XX:XX:XXXXXXXX:XXX',
        db_index=True
    )
    plot_number = models.CharField(
        'Номер участка',
        max_length=10,
        db_index=True,
        help_text='Номер участка (может содержать буквы, например: 42А)'
    )
    area_sqm = models.FloatField(
        'Площадь (кв.м)',
        validators=[
            MinValueValidator(0.01, message='Площадь должна быть больше 0'),
            MaxValueValidator(1000000, message='Площадь не может превышать 1 000 000 м²')
        ],
        help_text='Площадь участка в квадратных метрах'
    )
    address = models.CharField(
        'Расположение',
        max_length=255,
        blank=True,
        help_text='Описание местоположения: улица, линия и т.п.',
        db_index=True
    )
    # Координаты для отображения на карте
    latitude = models.FloatField(
        'Широта',
        null=True,
        blank=True,
        validators=[
            MinValueValidator(-90, message='Широта должна быть от -90 до 90'),
            MaxValueValidator(90, message='Широта должна быть от -90 до 90')
        ]
    )
    longitude = models.FloatField(
        'Долгота',
        null=True,
        blank=True,
        validators=[
            MinValueValidator(-180, message='Долгота должна быть от -180 до 180'),
            MaxValueValidator(180, message='Долгота должна быть от -180 до 180')
        ]
    )
    # Статус участка
    STATUS_CHOICES = [
        ('active', 'Активный'),
        ('abandoned', 'Заброшенный'),
        ('disputed', 'Спорный'),
    ]
    status = models.CharField(
        'Статус',
        max_length=10,
        choices=STATUS_CHOICES,
        default='active',
        db_index=True
    )
    # Служебные отметки
    notes = models.TextField(
        'Примечания',
        blank=True,
        help_text='Внутренние заметки, не видны владельцам'
    )
    created_at = models.DateTimeField(
        'Дата создания',
        auto_now_add=True,
        db_index=True
    )
    updated_at = models.DateTimeField(
        'Дата обновления',
        auto_now=True
    )

    class Meta:
        verbose_name = 'Земельный участок'
        verbose_name_plural = 'Земельные участки'
        ordering = ['plot_number']
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['cadastral_number']),
            models.Index(fields=['plot_number', 'status']),
            models.Index(fields=['latitude', 'longitude']),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(latitude__isnull=True, longitude__isnull=True)
                    | models.Q(latitude__isnull=False, longitude__isnull=False)
                ),
                name='both_coordinates_set_or_none'
            )
        ]

    def __str__(self):
        return f"Уч. №{self.plot_number} ({self.cadastral_number})"

    @property
    def has_coordinates(self):
        """Проверка, заданы ли координаты для карты."""
        return self.latitude is not None and self.longitude is not None

    @property
    def owners_count(self):
        """Количество владельцев участка"""
        return getattr(self, '_owners_count', self.ownerships.count())

    @property
    def primary_address(self):
        """Основной адрес (или номер участка, если адрес не задан)"""
        return self.address or f'Участок №{self.plot_number}'

    def clean(self):
        """Расширенная валидация модели"""
        super().clean()

        # Валидация номера участка
        if self.plot_number:
            self.plot_number = self.plot_number.strip().upper()
            if not self.plot_number:
                raise ValidationError({'plot_number': 'Номер участка не может быть пустым'})

        # Валидация кадастрового номера (гибкая)
        if self.cadastral_number:
            self.cadastral_number = self.cadastral_number.strip()
            parts = self.cadastral_number.split(':')

            if len(parts) != 4:
                raise ValidationError({
                    'cadastral_number': 'Кадастровый номер должен состоять из 4 групп цифр, разделённых двоеточием'
                })

            for i, part in enumerate(parts):
                if not part.isdigit():
                    raise ValidationError({
                        'cadastral_number': f'Группа {i+1} должна содержать только цифры'
                    })

            # Проверка длин (гибкая)
            if len(parts[0]) != 2:
                raise ValidationError({
                    'cadastral_number': 'Группа 1 (регион) должна содержать ровно 2 цифры'
                })

            if len(parts[1]) != 2:
                raise ValidationError({
                    'cadastral_number': 'Группа 2 (район) должна содержать ровно 2 цифры'
                })

            if len(parts[2]) < 6 or len(parts[2]) > 7:
                raise ValidationError({
                    'cadastral_number': 'Группа 3 (квартал) должна содержать 6-7 цифр'
                })

            # Группа 4: от 1 цифры (ИСПРАВЛЕНО)
            if len(parts[3]) < 1:
                raise ValidationError({
                    'cadastral_number': 'Группа 4 (номер участка) должна содержать хотя бы 1 цифру'
                })

        # Проверка координат
        if self.latitude is not None and self.longitude is not None:
            if not (-90 <= self.latitude <= 90):
                raise ValidationError({'latitude': 'Широта должна быть от -90 до 90'})
            if not (-180 <= self.longitude <= 180):
                raise ValidationError({'longitude': 'Долгота должна быть от -180 до 180'})
        elif (self.latitude is None) != (self.longitude is None):
            raise ValidationError(
                'Координаты должны быть указаны обе одновременно'
            )

    def save(self, *args, **kwargs):
        """Нормализация данных перед сохранением"""
        if self.plot_number:
            self.plot_number = self.plot_number.strip().upper()
        if self.cadastral_number:
            self.cadastral_number = self.cadastral_number.strip()
        if self.area_sqm:
            self.area_sqm = round(self.area_sqm, 2)
        
        self.full_clean()
        super().save(*args, **kwargs)

    def get_owners_list(self):
        """Получение списка владельцев с долями"""
        return self.ownerships.select_related('owner').all()

    def get_nearby_plots(self, radius=0.01):
        """Поиск соседних участков (если заданы координаты)"""
        if not self.has_coordinates:
            return LandPlot.objects.none()
        
        return LandPlot.objects.filter(
            latitude__isnull=False,
            longitude__isnull=False,
            latitude__gte=self.latitude - radius,
            latitude__lte=self.latitude + radius,
            longitude__gte=self.longitude - radius,
            longitude__lte=self.longitude + radius,
        ).exclude(pk=self.pk)