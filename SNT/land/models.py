from django.db import models
from django.core.validators import MinValueValidator


class LandPlot(models.Model):
    """
    Модель земельного участка.
    Кадастровый номер — уникальный идентификатор, номер участка — удобное локальное обозначение.
    """
    cadastral_number = models.CharField(
        'Кадастровый номер',
        max_length=30,
        unique=True,
        help_text='Формат: XX:XX:XXXXXXXX:XXX'
    )
    plot_number = models.CharField(
        'Номер участка',
        max_length=10,
        db_index=True
    )
    area_sqm = models.FloatField(
        'Площадь (кв.м)',
        validators=[MinValueValidator(0.01)]
    )
    address = models.CharField(
        'Расположение',
        max_length=255,
        blank=True,
        help_text='Описание местоположения: улица, линия и т.п.'
    )
    # Координаты для отображения на карте
    latitude = models.FloatField(
        'Широта',
        null=True,
        blank=True
    )
    longitude = models.FloatField(
        'Долгота',
        null=True,
        blank=True
    )
    # Статус участка: обрабатывается / заброшен и т.д.
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
        auto_now_add=True
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
        ]
        # Дополнительно — constraint на координаты: либо обе заданы, либо обе None
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

    def clean(self):
        """Валидация на уровне модели."""
        from django.core.exceptions import ValidationError
        if self.latitude is not None and not (-90 <= self.latitude <= 90):
            raise ValidationError({'latitude': 'Широта должна быть в диапазоне от -90 до 90'})
        if self.longitude is not None and not (-180 <= self.longitude <= 180):
            raise ValidationError({'longitude': 'Долгота должна быть в диапазоне от -180 до 180'})