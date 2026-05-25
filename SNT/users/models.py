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

    @property
    def organization(self):
        """Получить основное СНТ владельца (первое активное членство)"""
        membership = self.memberships.filter(status='active').first()
        return membership.organization if membership else None
    
    @property
    def organization_name(self):
        """Название СНТ владельца"""
        org = self.organization
        return org.short_name if org else None
    
    @property
    def can_add_owner(self):
        """Проверка, можно ли добавить нового владельца согласно тарифу"""
        subscription = getattr(self.organization, 'subscription', None)
        if not subscription or not subscription.is_active:
            return False
        
        tariff = subscription.tariff
        current_count = self.organization.owners_count
        
        return current_count < tariff.max_owners    

    @property
    def can_add_plot(self):
        """Проверка, можно ли добавить новый участок согласно тарифу"""
        subscription = getattr(self.organization, 'subscription', None)
        if not subscription or not subscription.is_active:
            return False
        
        tariff = subscription.tariff
        current_count = self.organization.plots_count
        
        return current_count < tariff.max_plots 

    @property
    def remaining_owners_slots(self):
        """Осталось мест для владельцев"""
        subscription = getattr(self.organization, 'subscription', None)
        if not subscription or not subscription.is_active:
            return 0
        
        tariff = subscription.tariff
        current_count = self.organization.owners_count
        
        return max(0, tariff.max_owners - current_count)    

    @property
    def remaining_plots_slots(self):
        """Осталось мест для участков"""
        subscription = getattr(self.organization, 'subscription', None)
        if not subscription or not subscription.is_active:
            return 0
        
        tariff = subscription.tariff
        current_count = self.organization.plots_count
        
        return max(0, tariff.max_plots - current_count)

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
            # Извлекаем только цифры
            digits = ''.join(c for c in self.value if c.isdigit())

            if len(digits) < 10:
                raise ValidationError({
                    'value': 'Номер телефона должен содержать не менее 10 цифр.'
                })

            # Приводим к единому формату
            if len(digits) == 11:
                if digits.startswith('8'):
                    digits = '7' + digits[1:]
                elif not digits.startswith('7'):
                    raise ValidationError({
                        'value': 'Номер должен начинаться с +7 или 8'
                    })
            elif len(digits) == 10:
                digits = '7' + digits
            else:
                # Для номеров длиннее 11 цифр - возможно добавочный номер
                pass
            
            # Форматируем номер в единый вид
            if len(digits) >= 11 and digits.startswith('7'):
                if len(digits) == 11:
                    self.value = f"+7 ({digits[1:4]}) {digits[4:7]}-{digits[7:9]}-{digits[9:11]}"
                else:
                    # Номер с добавочным
                    main = f"+7 ({digits[1:4]}) {digits[4:7]}-{digits[7:9]}-{digits[9:11]}"
                    extra = digits[11:]
                    self.value = f"{main} доб.{extra}"


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