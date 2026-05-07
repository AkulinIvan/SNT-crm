from django.db import models
from django.core.exceptions import ValidationError
from django.core.validators import EmailValidator
from phonenumber_field.modelfields import PhoneNumberField
from land.models import LandPlot


class Owner(models.Model):
    """
    Владелец одного или нескольких участков в СНТ.
    
    Связь с участками через промежуточную модель Ownership,
    которая уточняет долю и дату вступления во владение.
    """
    full_name = models.CharField(
        'ФИО',
        max_length=150
    )
    land_plots = models.ManyToManyField(
        LandPlot,
        through='Ownership',
        related_name='owners'
    )
    created_at = models.DateTimeField(
        'Дата добавления',
        auto_now_add=True
    )
    updated_at = models.DateTimeField(
        'Дата обновления',
        auto_now=True
    )

    class Meta:
        verbose_name = 'Владелец'
        verbose_name_plural = 'Владельцы'
        ordering = ['full_name']

    def __str__(self):
        return self.full_name

    @property
    def primary_phone(self):
        """Основной активный телефон (первый найденный)."""
        phone = self.contacts.filter(
            type=ContactInfo.PHONE,
            is_active=True
        ).first()
        return phone.value if phone else None

    @property
    def primary_email(self):
        """Основной активный email (первый найденный)."""
        email = self.contacts.filter(
            type=ContactInfo.EMAIL,
            is_active=True
        ).first()
        return email.value if email else None

    @property
    def active_land_plots(self):
        """Активные участки владельца (не заброшенные)."""
        return self.land_plots.filter(status='active')

    @property
    def total_debt(self):
        """Общая задолженность владельца"""
        from payments.models import Assessment
        debt = 0
        for a in Assessment.objects.filter(
            owner=self,
            status__in=['pending', 'partial', 'overdue']
        ):
            debt += a.debt
        return debt

    @property
    def is_debtor(self):
        """Является ли должником"""
        return self.total_debt > 0

class Ownership(models.Model):
    """
    Промежуточная модель для связи Owner и LandPlot.
    
    Хранит долю владельца и дату вступления в права.
    """
    owner = models.ForeignKey(
        Owner,
        on_delete=models.CASCADE,
        related_name='ownerships'
    )
    land_plot = models.ForeignKey(
        LandPlot,
        on_delete=models.CASCADE,
        related_name='ownerships'
    )
    share = models.CharField(
        'Доля',
        max_length=20,
        default='1/1',
        help_text='Например: 1/1, 1/2, 1/3 и т.д.'
    )
    ownership_since = models.DateField(
        'Дата вступления во владение',
        null=True,
        blank=True
    )
    # Документ-основание (для истории)
    document_basis = models.CharField(
        'Документ-основание',
        max_length=255,
        blank=True,
        help_text='Номер свидетельства или выписки из ЕГРН'
    )

    class Meta:
        verbose_name = 'Право собственности'
        verbose_name_plural = 'Права собственности'
        unique_together = ('owner', 'land_plot')

    def __str__(self):
        return f"{self.owner.full_name} → Уч. №{self.land_plot.plot_number} ({self.share})"


class ContactInfo(models.Model):
    """
    Контактные данные владельца.
    
    Может быть несколько номеров телефона и email.
    Неактуальные помечаются is_active=False (история сохраняется).
    """
    PHONE = 'ph'
    EMAIL = 'em'
    CONTACT_TYPE_CHOICES = [
        (PHONE, 'Телефон'),
        (EMAIL, 'Email'),
    ]
    owner = models.ForeignKey(
        Owner,
        on_delete=models.CASCADE,
        related_name='contacts'
    )
    type = models.CharField(
        'Тип контакта',
        max_length=2,
        choices=CONTACT_TYPE_CHOICES
    )
    value = models.CharField(
        'Значение',
        max_length=100
    )
    is_active = models.BooleanField(
        'Актуальный',
        default=True,
        db_index=True
    )
    is_verified = models.BooleanField(
        'Подтверждён',
        default=False,
        help_text='Подтверждено ли, что контакт принадлежит владельцу'
    )
    note = models.CharField(
        'Примечание',
        max_length=100,
        blank=True
    )
    created_at = models.DateTimeField(
        'Дата добавления',
        auto_now_add=True
    )

    class Meta:
        verbose_name = 'Контактные данные'
        verbose_name_plural = 'Контактные данные'
        ordering = ['-is_active', '-created_at']
        indexes = [
            models.Index(fields=['owner', 'type', 'is_active']),
        ]

    def __str__(self):
        type_label = self.get_type_display()
        status = '✓' if self.is_active else '✗'
        return f"{type_label}: {self.value} [{status}]"

    def clean(self):
        """Валидация в зависимости от типа контакта."""
        if self.type == self.PHONE:
            # Простая проверка: номер должен содержать минимум 10 цифр
            digits = ''.join(c for c in self.value if c.isdigit())
            if len(digits) < 10:
                raise ValidationError({
                    'value': 'Номер телефона должен содержать не менее 10 цифр.'
                })
        elif self.type == self.EMAIL:
            validator = EmailValidator()
            try:
                validator(self.value)
            except ValidationError:
                raise ValidationError({
                    'value': 'Введите корректный email-адрес.'
                })

    def save(self, *args, **kwargs):
        """При сохранении — полная очистка."""
        self.full_clean()
        super().save(*args, **kwargs)