# subscriptions/models.py
from django.db import models
from django.conf import settings
from django.utils import timezone
from decimal import Decimal
import uuid


class Tariff(models.Model):
    """Модель тарифного плана"""
    name = models.CharField('Название тарифа', max_length=100)
    slug = models.SlugField('Идентификатор', unique=True)
    description = models.TextField('Описание', blank=True)
    price = models.DecimalField('Цена', max_digits=10, decimal_places=2, default=0)
    price_period = models.CharField(
        'Период оплаты',
        max_length=20,
        choices=[
            ('month', 'Месяц'),
            ('year', 'Год'),
            ('once', 'Разово'),
        ],
        default='year'
    )
    
    # Возможности тарифа
    can_view_map = models.BooleanField('Доступ к карте', default=False)
    can_manage_payments = models.BooleanField('Управление платежами', default=False)
    can_import_bank = models.BooleanField('Импорт из банка', default=False)
    can_export_data = models.BooleanField('Экспорт данных', default=False)
    can_manage_assessments = models.BooleanField('Управление начислениями', default=False)
    
    # Количество пользователей
    max_users = models.IntegerField('Максимум пользователей', default=1)
    max_owners = models.IntegerField('Максимум владельцев', default=50)
    max_plots = models.IntegerField('Максимум участков', default=50)
    
    # Пробный период (дней)
    trial_days = models.IntegerField('Пробный период (дней)', default=30)
    
    is_active = models.BooleanField('Активен', default=True)
    order = models.IntegerField('Порядок', default=0)
    
    class Meta:
        verbose_name = 'Тарифный план'
        verbose_name_plural = 'Тарифные планы'
        ordering = ['order', 'price']
    
    def __str__(self):
        return f"{self.name} - {self.price} ₽/{self.get_price_period_display()}"
    
    @property
    def price_display(self):
        period_map = {'month': 'месяц', 'year': 'год', 'once': 'разово'}
        return f"{self.price} ₽/{period_map.get(self.price_period, '')}"


class Subscription(models.Model):
    """Подписка организации на тариф"""
    STATUS_CHOICES = [
        ('active', 'Активна'),
        ('expired', 'Истекла'),
        ('cancelled', 'Отменена'),
        ('pending', 'Ожидает оплаты'),
        ('trial', 'Пробный период'),
    ]
    
    organization = models.OneToOneField(
        'organizations.Organization',
        on_delete=models.CASCADE,
        related_name='subscription',
        verbose_name='СНТ'
    )
    tariff = models.ForeignKey(
        Tariff,
        on_delete=models.PROTECT,
        related_name='subscriptions',
        verbose_name='Тариф'
    )
    status = models.CharField(
        'Статус',
        max_length=20,
        choices=STATUS_CHOICES,
        default='trial'
    )
    start_date = models.DateTimeField('Дата начала', default=timezone.now)
    end_date = models.DateTimeField('Дата окончания', null=True, blank=True)
    auto_renew = models.BooleanField('Автопродление', default=False)
    
    # Платежные данные
    payment_id = models.CharField('ID платежа', max_length=100, blank=True)
    payment_amount = models.DecimalField('Сумма платежа', max_digits=10, decimal_places=2, null=True, blank=True)
    payment_date = models.DateTimeField('Дата платежа', null=True, blank=True)
    payment_method = models.CharField('Способ оплаты', max_length=50, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = 'Подписка'
        verbose_name_plural = 'Подписки'
    
    def __str__(self):
        return f"{self.organization.short_name} - {self.tariff.name}"
    
    @property
    def is_active(self):
        """Проверка активности подписки"""
        if self.status not in ['active', 'trial']:
            return False
        if self.end_date and timezone.now() > self.end_date:
            return False
        return True
    
    @property
    def days_left(self):
        """Дней до окончания подписки"""
        if not self.end_date:
            return None
        delta = self.end_date - timezone.now()
        return max(0, delta.days)


class Payment(models.Model):
    """Модель платежа"""
    PAYMENT_STATUS = [
        ('pending', 'Ожидает'),
        ('success', 'Успешен'),
        ('failed', 'Ошибка'),
        ('cancelled', 'Отменен'),
    ]
    
    PAYMENT_METHODS = [
        ('card', 'Банковская карта'),
        ('sberbank', 'Сбербанк Онлайн'),
        ('tinkoff', 'Тинькофф'),
        ('yookassa', 'ЮKassa'),
        ('manual', 'Ручное зачисление'),
    ]
    
    subscription = models.ForeignKey(
        Subscription,
        on_delete=models.CASCADE,
        related_name='payments',
        verbose_name='Подписка'
    )
    amount = models.DecimalField('Сумма', max_digits=10, decimal_places=2)
    status = models.CharField('Статус', max_length=20, choices=PAYMENT_STATUS, default='pending')
    payment_method = models.CharField('Способ оплаты', max_length=50, choices=PAYMENT_METHODS)
    transaction_id = models.CharField('ID транзакции', max_length=200, blank=True, unique=True, null=True)
    
    # Данные плательщика
    payer_name = models.CharField('ФИО плательщика', max_length=200, blank=True)
    payer_email = models.EmailField('Email плательщика', blank=True)
    payer_phone = models.CharField('Телефон', max_length=20, blank=True)
    
    # Детали
    description = models.TextField('Описание', blank=True)
    receipt_url = models.URLField('URL чека', blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField('Дата оплаты', null=True, blank=True)
    
    class Meta:
        verbose_name = 'Платёж'
        verbose_name_plural = 'Платежи'
        ordering = ['-created_at']
    
    def __str__(self):
        return f"Платёж {self.amount} ₽ - {self.get_status_display()}"


class Invoice(models.Model):
    """Счет на оплату"""
    INVOICE_STATUS = [
        ('draft', 'Черновик'),
        ('sent', 'Отправлен'),
        ('paid', 'Оплачен'),
        ('overdue', 'Просрочен'),
        ('cancelled', 'Отменен'),
    ]
    
    number = models.CharField('Номер счета', max_length=50, unique=True)
    organization = models.ForeignKey(
        'organizations.Organization',
        on_delete=models.CASCADE,
        related_name='invoices',
        verbose_name='СНТ'
    )
    subscription = models.ForeignKey(
        Subscription,
        on_delete=models.CASCADE,
        related_name='invoices',
        verbose_name='Подписка',
        null=True,
        blank=True
    )
    amount = models.DecimalField('Сумма', max_digits=10, decimal_places=2)
    status = models.CharField('Статус', max_length=20, choices=INVOICE_STATUS, default='draft')
    due_date = models.DateField('Срок оплаты')
    
    # Детали
    description = models.TextField('Описание', blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField('Дата оплаты', null=True, blank=True)
    
    class Meta:
        verbose_name = 'Счёт'
        verbose_name_plural = 'Счета'
        ordering = ['-created_at']
    
    def __str__(self):
        return f"Счёт №{self.number} - {self.amount} ₽"


class SubscriptionFeature(models.Model):
    """Логирование использования функций (для аналитики)"""
    subscription = models.ForeignKey(
        Subscription,
        on_delete=models.CASCADE,
        related_name='features_usage'
    )
    feature_name = models.CharField('Название функции', max_length=100, choices=[
        ('map', 'Карта СНТ'),
        ('payments', 'Управление платежами'),
        ('bank_import', 'Импорт из банка'),
        ('export', 'Экспорт данных'),
        ('assessments', 'Управление начислениями'),
    ])
    used_at = models.DateTimeField('Время использования', auto_now_add=True)
    ip_address = models.GenericIPAddressField('IP-адрес', null=True, blank=True)
    user_agent = models.TextField('User Agent', blank=True)
    
    class Meta:
        verbose_name = 'Использование функции'
        verbose_name_plural = 'Использование функций'
        ordering = ['-used_at']
    
    def __str__(self):
        return f"{self.subscription.organization.short_name} - {self.get_feature_name_display()} ({self.used_at})"