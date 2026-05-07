from django.db import models
from django.core.validators import MinValueValidator
from django.utils import timezone
from land.models import LandPlot
from users.models import Owner


class PaymentCategory(models.Model):
    """Категории взносов: членские, целевые, электроэнергия и т.д."""
    name = models.CharField('Название', max_length=100)
    code = models.CharField('Код', max_length=20, unique=True, help_text='membership, target, electricity')
    description = models.TextField('Описание', blank=True)
    is_regular = models.BooleanField('Регулярный', default=True, help_text='Начисляется ежегодно')
    default_amount = models.DecimalField(
        'Сумма по умолчанию',
        max_digits=10,
        decimal_places=2,
        default=0,
        help_text='Базовая сумма для начисления'
    )
    unit = models.CharField(
        'Единица измерения',
        max_length=20,
        default='участок',
        help_text='участок, сотка, кВт·ч'
    )
    rate_per_unit = models.DecimalField(
        'Тариф за единицу',
        max_digits=10,
        decimal_places=2,
        default=0,
        help_text='Если зависит от площади или потребления'
    )
    is_active = models.BooleanField('Активна', default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Категория взноса'
        verbose_name_plural = 'Категории взносов'
        ordering = ['name']

    def __str__(self):
        return self.name


class PaymentPeriod(models.Model):
    """Периоды начисления (годы, кварталы)"""
    year = models.PositiveIntegerField('Год')
    quarter = models.PositiveIntegerField('Квартал', null=True, blank=True)
    start_date = models.DateField('Начало периода')
    end_date = models.DateField('Конец периода')
    due_date = models.DateField('Оплатить до')
    is_active = models.BooleanField('Активный', default=True)
    description = models.CharField('Описание', max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Период оплаты'
        verbose_name_plural = 'Периоды оплаты'
        ordering = ['-year', '-quarter']
        unique_together = ['year', 'quarter']

    def __str__(self):
        if self.quarter:
            return f"{self.year} год, {self.quarter} квартал"
        return f"{self.year} год"


class Assessment(models.Model):
    """Начисление — конкретная сумма, которую должен заплатить владелец"""
    STATUS_PENDING = 'pending'
    STATUS_PAID = 'paid'
    STATUS_PARTIAL = 'partial'
    STATUS_OVERDUE = 'overdue'
    STATUS_CANCELLED = 'cancelled'
    
    STATUS_CHOICES = [
        (STATUS_PENDING, 'Ожидает оплаты'),
        (STATUS_PAID, 'Оплачено'),
        (STATUS_PARTIAL, 'Частично оплачено'),
        (STATUS_OVERDUE, 'Просрочено'),
        (STATUS_CANCELLED, 'Отменено'),
    ]

    owner = models.ForeignKey(
        Owner,
        on_delete=models.CASCADE,
        related_name='assessments',
        verbose_name='Владелец'
    )
    land_plot = models.ForeignKey(
        LandPlot,
        on_delete=models.CASCADE,
        related_name='assessments',
        verbose_name='Участок'
    )
    category = models.ForeignKey(
        PaymentCategory,
        on_delete=models.PROTECT,
        related_name='assessments',
        verbose_name='Категория'
    )
    period = models.ForeignKey(
        PaymentPeriod,
        on_delete=models.PROTECT,
        related_name='assessments',
        verbose_name='Период'
    )
    amount = models.DecimalField('Сумма начисления', max_digits=10, decimal_places=2)
    paid_amount = models.DecimalField('Оплачено', max_digits=10, decimal_places=2, default=0)
    status = models.CharField(
        'Статус',
        max_length=15,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        db_index=True
    )
    # Для расчёта пени
    penalty_rate = models.DecimalField(
        'Ставка пени (% в день)',
        max_digits=5,
        decimal_places=3,
        default=0,
        help_text='Например, 0.1% в день от суммы долга'
    )
    penalty_amount = models.DecimalField('Начислено пени', max_digits=10, decimal_places=2, default=0)
    notes = models.TextField('Примечания', blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Начисление'
        verbose_name_plural = 'Начисления'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['owner', 'status']),
            models.Index(fields=['land_plot', 'status']),
            models.Index(fields=['period', 'status']),
            models.Index(fields=['category', 'status']),
        ]

    def __str__(self):
        return f"{self.owner.full_name} — {self.category.name} ({self.period})"

    @property
    def debt(self):
        """Остаток долга с учётом пени"""
        return max(0, self.amount + self.penalty_amount - self.paid_amount)

    def calculate_penalty(self):
        """Рассчитать пеню на текущую дату"""
        if self.status == self.STATUS_PAID or self.penalty_rate == 0:
            return 0
        if self.status == self.STATUS_PENDING and self.period.due_date:
            days_overdue = (timezone.now().date() - self.period.due_date).days
            if days_overdue > 0:
                return round(float(self.debt) * float(self.penalty_rate) * days_overdue / 100, 2)
        return 0


class Payment(models.Model):
    """Платёж — поступление денег от владельца"""
    PAYMENT_METHODS = [
        ('bank', 'Банковский перевод'),
        ('cash', 'Наличные'),
        ('terminal', 'Терминал'),
        ('online', 'Онлайн-платёж'),
    ]
    
    STATUS_PROCESSED = 'processed'
    STATUS_PENDING = 'pending'
    STATUS_REJECTED = 'rejected'
    
    PAYMENT_STATUS = [
        (STATUS_PROCESSED, 'Проведён'),
        (STATUS_PENDING, 'В обработке'),
        (STATUS_REJECTED, 'Отклонён'),
    ]

    assessment = models.ForeignKey(
        Assessment,
        on_delete=models.CASCADE,
        related_name='payments',
        verbose_name='Начисление'
    )
    amount = models.DecimalField('Сумма платежа', max_digits=10, decimal_places=2)
    payment_date = models.DateField('Дата платежа', default=timezone.now)
    payment_method = models.CharField(
        'Способ оплаты',
        max_length=10,
        choices=PAYMENT_METHODS,
        default='bank'
    )
    status = models.CharField(
        'Статус',
        max_length=15,
        choices=PAYMENT_STATUS,
        default=STATUS_PROCESSED
    )
    
    # Банковские реквизиты
    bank_name = models.CharField('Банк', max_length=100, blank=True)
    bank_account = models.CharField('Счёт плательщика', max_length=30, blank=True)
    transaction_id = models.CharField('ID транзакции', max_length=100, blank=True, unique=True)
    payment_purpose = models.TextField('Назначение платежа', blank=True)
    
    # Служебные поля
    receipt_file = models.FileField('Квитанция', upload_to='receipts/%Y/%m/', blank=True, null=True)
    notes = models.TextField('Примечания', blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Платёж'
        verbose_name_plural = 'Платежи'
        ordering = ['-payment_date']
        indexes = [
            models.Index(fields=['transaction_id']),
            models.Index(fields=['payment_date']),
        ]

    def __str__(self):
        return f"Платёж {self.amount} руб. от {self.payment_date}"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        # Обновляем сумму оплаты в начислении
        self.update_assessment()

    def update_assessment(self):
        """Пересчитать оплаченную сумму в начислении"""
        total_paid = self.assessment.payments.filter(
            status=self.STATUS_PROCESSED
        ).aggregate(
            total=models.Sum('amount')
        )['total'] or 0
        
        self.assessment.paid_amount = total_paid
        
        # Обновляем статус начисления
        if total_paid >= self.assessment.amount:
            self.assessment.status = Assessment.STATUS_PAID
        elif total_paid > 0:
            self.assessment.status = Assessment.STATUS_PARTIAL
        
        self.assessment.save(update_fields=['paid_amount', 'status', 'updated_at'])


class BankStatement(models.Model):
    """Банковская выписка — импортированные данные из банка"""
    bank_name = models.CharField('Банк', max_length=100)
    account_number = models.CharField('Номер счёта', max_length=30)
    statement_date = models.DateField('Дата выписки')
    file_original = models.FileField('Файл выписки', upload_to='bank_statements/')
    
    # Статус обработки
    STATUS_IMPORTED = 'imported'
    STATUS_PROCESSED = 'processed'
    STATUS_ERROR = 'error'
    
    STATUS_CHOICES = [
        (STATUS_IMPORTED, 'Импортирована'),
        (STATUS_PROCESSED, 'Обработана'),
        (STATUS_ERROR, 'Ошибка'),
    ]
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default=STATUS_IMPORTED)
    total_transactions = models.PositiveIntegerField('Всего транзакций', default=0)
    matched_transactions = models.PositiveIntegerField('Сопоставлено', default=0)
    notes = models.TextField('Заметки', blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Банковская выписка'
        verbose_name_plural = 'Банковские выписки'

    def __str__(self):
        return f"Выписка {self.bank_name} от {self.statement_date}"


class BankTransaction(models.Model):
    """Отдельная транзакция из банковской выписки"""
    statement = models.ForeignKey(
        BankStatement,
        on_delete=models.CASCADE,
        related_name='transactions'
    )
    transaction_date = models.DateField('Дата операции')
    amount = models.DecimalField('Сумма', max_digits=10, decimal_places=2)
    payer_name = models.CharField('Плательщик', max_length=200)
    payer_account = models.CharField('Счёт плательщика', max_length=30, blank=True)
    payer_inn = models.CharField('ИНН плательщика', max_length=12, blank=True)
    payment_purpose = models.TextField('Назначение платежа')
    
    # Сопоставление с CRM
    matched_owner = models.ForeignKey(
        Owner,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='bank_transactions'
    )
    matched_payment = models.OneToOneField(
        Payment,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='bank_transaction'
    )
    is_matched = models.BooleanField('Сопоставлено', default=False)
    match_confidence = models.FloatField('Точность сопоставления (%)', default=0)
    
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Банковская транзакция'
        verbose_name_plural = 'Банковские транзакции'

    def __str__(self):
        return f"{self.payer_name} — {self.amount} руб. ({self.transaction_date})"