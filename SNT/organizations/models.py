from django.db import models
from django.core.validators import RegexValidator
from django.conf import settings


class Organization(models.Model):
    """
    Модель СНТ (юридического лица).
    """
    # Основные реквизиты
    name = models.CharField(
        'Полное наименование',
        max_length=200,
        unique=True,
        help_text='Например: Садоводческое некоммерческое товарищество "Строитель-43"'
    )
    short_name = models.CharField(
        'Краткое наименование',
        max_length=100,
        unique=True,
        help_text='Например: СНТ "Строитель-43"'
    )
    inn = models.CharField(
        'ИНН',
        max_length=12,
        unique=True,
        validators=[RegexValidator(r'^\d{10}$|^\d{12}$', 'ИНН должен содержать 10 или 12 цифр')]
    )
    kpp = models.CharField(
        'КПП',
        max_length=9,
        blank=True,
        validators=[RegexValidator(r'^\d{9}$', 'КПП должен содержать 9 цифр')]
    )
    ogrn = models.CharField(
        'ОГРН',
        max_length=15,
        blank=True,
        validators=[RegexValidator(r'^\d{13}$|^\d{15}$', 'ОГРН должен содержать 13 или 15 цифр')]
    )

    # Юридический адрес
    legal_address = models.TextField(
        'Юридический адрес',
        help_text='Полный юридический адрес'
    )
    actual_address = models.TextField(
        'Фактический адрес',
        blank=True,
        help_text='Если отличается от юридического'
    )

    # Банковские реквизиты
    bank_name = models.CharField('Банк', max_length=200)
    bank_bik = models.CharField('БИК банка', max_length=9)
    bank_account = models.CharField('Расчётный счёт', max_length=20)
    bank_corr_account = models.CharField('Корреспондентский счёт', max_length=20)

    # Контактные данные СНТ
    phone = models.CharField('Телефон', max_length=20, blank=True)
    email = models.EmailField('Email', blank=True)
    website = models.URLField('Сайт', blank=True)

    # Руководство
    chairman = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='chaired_organization',
        verbose_name='Председатель',
        help_text='Председатель правления СНТ (пользователь системы)'
    )
    accountant = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='accountant_organizations',
        verbose_name='Бухгалтер',
        help_text='Бухгалтер, привязанный к СНТ'
    )

    # Статус
    is_active = models.BooleanField('Активно', default=True)

    # Служебные поля
    created_at = models.DateTimeField('Дата создания', auto_now_add=True)
    updated_at = models.DateTimeField('Дата обновления', auto_now=True)

    class Meta:
        verbose_name = 'СНТ'
        verbose_name_plural = 'СНТ'
        ordering = ['short_name']
        permissions = [
            ('can_view_organization_finances', 'Может просматривать финансы СНТ'),
            ('can_manage_organization', 'Может управлять СНТ'),
        ]

    def __str__(self):
        return self.short_name

    def save(self, *args, **kwargs):
        # Очистка номеров от лишних символов
        if self.phone:
            self.phone = ''.join(c for c in self.phone if c.isdigit() or c in '+()- ')
        super().save(*args, **kwargs)


class OrganizationMembership(models.Model):
    """
    Модель членства владельца в СНТ.
    """
    MEMBERSHIP_STATUS = [
        ('active', 'Активный член'),
        ('inactive', 'Неактивный член'),
        ('excluded', 'Исключён'),
        ('deceased', 'Умер'),
        ('left', 'Вышел добровольно'),
    ]

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name='memberships',
        verbose_name='СНТ'
    )
    owner = models.ForeignKey(
        'users.Owner',
        on_delete=models.CASCADE,
        related_name='memberships',
        verbose_name='Владелец'
    )
    member_since = models.DateField('Член с', null=True, blank=True)
    member_until = models.DateField('Член до', null=True, blank=True)
    status = models.CharField(
        'Статус членства',
        max_length=20,
        choices=MEMBERSHIP_STATUS,
        default='active'
    )
    member_card_number = models.CharField(
        'Номер членского билета',
        max_length=50,
        blank=True
    )
    notes = models.TextField('Примечания', blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Членство в СНТ'
        verbose_name_plural = 'Членства в СНТ'
        unique_together = ['organization', 'owner']
        indexes = [
            models.Index(fields=['organization', 'status']),
            models.Index(fields=['owner', 'status']),
        ]

    def __str__(self):
        return f'{self.owner.full_name} - {self.organization.short_name} ({self.get_status_display()})'