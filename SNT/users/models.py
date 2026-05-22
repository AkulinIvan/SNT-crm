# SNT\users\models.py
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
        max_length=150,
        db_index=True
    )
    land_plots = models.ManyToManyField(
        LandPlot,
        through='Ownership',
        related_name='owners'
    )
    created_at = models.DateTimeField(
        'Дата добавления',
        auto_now_add=True,
        db_index=True
    )
    updated_at = models.DateTimeField(
        'Дата обновления',
        auto_now=True
    )
    # Связь с СНТ
    organization = models.ForeignKey(
        'organizations.Organization',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='owners',
        verbose_name='СНТ',
        help_text='СНТ, в котором владелец имеет участки'
    )
    
    class Meta:
        verbose_name = 'Владелец'
        verbose_name_plural = 'Владельцы'
        ordering = ['full_name']
        indexes = [
            models.Index(fields=['full_name']),
            models.Index(fields=['created_at']),
        ]

    def __str__(self):
        return self.full_name

    def clean(self):
        """Валидация модели"""
        super().clean()
        if not self.full_name.strip():
            raise ValidationError({'full_name': 'ФИО не может быть пустым'})

    def save(self, *args, **kwargs):
        """Нормализация данных перед сохранением"""
        if self.full_name:
            self.full_name = ' '.join(self.full_name.split()).title()
        super().save(*args, **kwargs)

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
        try:
            from payments.models import Assessment
            debt = 0
            for a in Assessment.objects.filter(
                owner=self,
                status__in=['pending', 'partial', 'overdue']
            ):
                debt += a.debt
            return debt
        except ImportError:
            return 0

    @property
    def is_debtor(self):
        """Является ли должником"""
        return self.total_debt > 0

    def get_ownerships_by_date(self):
        """Получение прав собственности, отсортированных по дате"""
        return self.ownerships.order_by('ownership_since')


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
        blank=True,
        db_index=True
    )
    document_basis = models.CharField(
        'Документ-основание',
        max_length=255,
        blank=True,
        help_text='Номер свидетельства или выписки из ЕГРН'
    )
    created_at = models.DateTimeField(
        'Дата создания записи',
        auto_now_add=True
    )
    updated_at = models.DateTimeField(
        'Дата обновления',
        auto_now=True
    )

    class Meta:
        verbose_name = 'Право собственности'
        verbose_name_plural = 'Права собственности'
        unique_together = ('owner', 'land_plot')
        indexes = [
            models.Index(fields=['owner', 'land_plot']),
            models.Index(fields=['ownership_since']),
        ]
        ordering = ['-ownership_since']

    def __str__(self):
        return f"{self.owner.full_name} → Уч. №{self.land_plot.plot_number} ({self.share})"

    def clean(self):
        """Валидация модели"""
        super().clean()
        # Проверка, что участок активен
        if self.land_plot.status != 'active':
            raise ValidationError({
                'land_plot': 'Нельзя привязать неактивный участок'
            })
        
        # Проверка даты
        from datetime import date
        if self.ownership_since and self.ownership_since > date.today():
            raise ValidationError({
                'ownership_since': 'Дата вступления не может быть в будущем'
            })

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


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
        choices=CONTACT_TYPE_CHOICES,
        db_index=True
    )
    value = models.CharField(
        'Значение',
        max_length=100,
        db_index=True
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
        max_length=255,
        blank=True
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
        verbose_name = 'Контактные данные'
        verbose_name_plural = 'Контактные данные'
        ordering = ['-is_active', '-created_at']
        indexes = [
            models.Index(fields=['owner', 'type', 'is_active']),
            models.Index(fields=['value']),
            models.Index(fields=['type', 'value']),
        ]

    def __str__(self):
        type_label = self.get_type_display()
        status = '✓' if self.is_active else '✗'
        verified = ' (П)' if self.is_verified else ''
        return f"{type_label}: {self.value} [{status}]{verified}"

    def clean(self):
        """Валидация в зависимости от типа контакта."""
        super().clean()
        
        # Очищаем значение от пробелов
        self.value = self.value.strip() if self.value else ''
        
        if self.type == self.PHONE:
            digits = ''.join(c for c in self.value if c.isdigit())
            if len(digits) < 10:
                raise ValidationError({
                    'value': 'Номер телефона должен содержать не менее 10 цифр.'
                })
            # Форматируем номер
            if len(digits) == 11:
                self.value = f'+7 ({digits[1:4]}) {digits[4:7]}-{digits[7:9]}-{digits[9:11]}'
            elif len(digits) == 10:
                self.value = f'+7 ({digits[0:3]}) {digits[3:6]}-{digits[6:8]}-{digits[8:10]}'
        elif self.type == self.EMAIL:
            validator = EmailValidator()
            try:
                validator(self.value)
            except ValidationError:
                raise ValidationError({
                    'value': 'Введите корректный email-адрес.'
                })
            self.value = self.value.lower()

        # Проверка на существующий активный контакт такого же типа
        if self.is_active and self.owner_id:
            duplicate = ContactInfo.objects.filter(
                owner_id=self.owner_id,
                type=self.type,
                value=self.value,
                is_active=True
            ).exclude(pk=self.pk).first()
            
            if duplicate:
                raise ValidationError({
                    'value': f'Такой контакт уже существует (ID: {duplicate.id})'
                })

    def save(self, *args, **kwargs):
        """При сохранении — полная очистка."""
        self.full_clean()
        super().save(*args, **kwargs)

    def deactivate(self, reason=''):
        """Деактивация контакта"""
        self.is_active = False
        if reason:
            self.note = f'{self.note} | {reason}'.strip(' |')
        self.save(update_fields=['is_active', 'note'])

    def verify(self):
        """Подтверждение контакта"""
        self.is_verified = True
        self.save(update_fields=['is_verified'])