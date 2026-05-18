from datetime import date

from django.db import models
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
        if self.unit == 'сотка' and self.rate_per_unit:
            return f"{self.name} ({self.rate_per_unit} ₽/сотка)"
        return self.name
    
    def calculate_amount(self, land_plot=None, quantity=None):
        """
        Рассчитать сумму начисления в зависимости от категории.
        
        Args:
            land_plot: объект участка LandPlot
            quantity: ручное количество (для электричества)
        
        Returns:
            tuple: (amount, description)
        """
        from decimal import Decimal
        
        if self.unit == 'сотка' and self.rate_per_unit and land_plot:
            # Расчёт по площади в сотках
            area_sotka = Decimal(str(land_plot.area_sqm)) / 100
            amount = (area_sotka * self.rate_per_unit).quantize(Decimal('0.01'))
            description = f"{self.name}: {area_sotka:.2f} соток × {self.rate_per_unit} ₽/сотка = {amount} ₽"
            return amount, description
        
        elif self.unit == 'кВт·ч' and self.rate_per_unit and quantity:
            # Расчёт по потреблению электроэнергии
            amount = (Decimal(str(quantity)) * self.rate_per_unit).quantize(Decimal('0.01'))
            description = f"{self.name}: {quantity} кВт·ч × {self.rate_per_unit} ₽/кВт·ч = {amount} ₽"
            return amount, description
        
        elif self.default_amount:
            # Фиксированная сумма
            amount = self.default_amount
            description = f"{self.name}: {amount} ₽ (фиксированная сумма)"
            return amount, description
        
        return Decimal('0'), f"{self.name}: сумма не определена"


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

    # Уникальный идентификатор для квитанции
    payment_uid = models.CharField(
        'Уникальный ID квитанции',
        max_length=20,
        unique=True,
        blank=True,
        help_text='UID для идентификации платежа: SNT-000001'
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
            models.Index(fields=['payment_uid']),
        ]

    def save(self, *args, **kwargs):
        if not self.payment_uid:
            self.payment_uid = self.generate_uid()
        # Автоматический расчёт статуса при сохранении
        from datetime import date
        if self.paid_amount >= self.amount:
            self.status = self.STATUS_PAID
        elif self.paid_amount > 0:
            self.status = self.STATUS_PARTIAL
        elif self.period and self.period.due_date and date.today() > self.period.due_date:
            self.status = self.STATUS_OVERDUE
        else:
            self.status = self.STATUS_PENDING
        super().save(*args, **kwargs)
    
    @classmethod
    def generate_uid(cls):
        """
        Генерация уникального UID для квитанции.
        Использует максимальный существующий ID + 1 вместо последовательности.
        """
        # Получаем максимальный существующий ID
        max_id = cls.objects.aggregate(models.Max('id'))['id__max']
        
        if max_id is not None:
            next_id = max_id + 1
        else:
            next_id = 1
        
        # Проверяем, что UID не занят (на случай параллельных запросов)
        uid = f"SNT-{next_id:06d}"
        while cls.objects.filter(payment_uid=uid).exists():
            next_id += 1
            uid = f"SNT-{next_id:06d}"
        
        return uid


    def __str__(self):
        return f"{self.owner.full_name} — {self.category.name} ({self.period}) [{self.payment_uid}]"

    @property
    def debt(self):
        """Остаток долга с учётом пени"""
        return max(0, self.amount + self.penalty_amount - self.paid_amount)

    def calculate_penalty(self):
        """Рассчитать пеню на текущую дату"""
        if self.status == self.STATUS_PAID or self.penalty_rate == 0:
            return 0
        if self.status in [self.STATUS_PENDING, self.STATUS_PARTIAL, self.STATUS_OVERDUE] and self.period.due_date:
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
    transaction_id = models.CharField('ID транзакции', max_length=100, blank=True, unique=True, null=True)
    payment_purpose = models.TextField('Назначение платежа', blank=True)

    # Идентификатор из квитанции
    matched_uid = models.CharField('UID из назначения', max_length=20, blank=True, db_index=True)

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
            models.Index(fields=['matched_uid']),
            models.Index(fields=['payment_date']),
        ]

    def __str__(self):
        return f"Платёж {self.amount} руб. от {self.payment_date}"

    def save(self, *args, **kwargs):
        is_new = self.pk is None
        super().save(*args, **kwargs)
        if is_new and self.status == self.STATUS_PROCESSED:
            self.update_assessment()

    def update_assessment(self):
        """Пересчёт оплаченной суммы"""
        total_paid = self.assessment.payments.filter(
            status=self.STATUS_PROCESSED
        ).aggregate(total=models.Sum('amount'))['total'] or 0

        self.assessment.paid_amount = total_paid
        self.assessment.save()

class BankStatement(models.Model):
    """Банковская выписка — импортированные данные из банка"""
    bank_name = models.CharField('Банк', max_length=100)
    account_number = models.CharField('Номер счёта', max_length=30, blank=True)
    statement_date = models.DateField('Дата выписки')
    file_original = models.FileField('Файл выписки', upload_to='bank_statements/')

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
    matched_uid = models.CharField('Найденный UID', max_length=20, blank=True)
    is_matched = models.BooleanField('Сопоставлено', default=False)
    match_confidence = models.FloatField('Точность сопоставления (%)', default=0)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Банковская транзакция'
        verbose_name_plural = 'Банковские транзакции'

    def __str__(self):
        return f"{self.payer_name} — {self.amount} руб. ({self.transaction_date})"
    

class ReceiptTemplate(models.Model):
    """
    Шаблон квитанции — объединяет несколько строк расчёта.
    Например: Членские взносы (8 соток × 500 руб) + Целевые взносы (3000 руб) + Электричество (200 кВт × 5.5 руб)
    """
    name = models.CharField('Название шаблона', max_length=200)
    description = models.TextField('Описание', blank=True)
    is_active = models.BooleanField('Активен', default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Шаблон квитанции'
        verbose_name_plural = 'Шаблоны квитанций'

    def __str__(self):
        return self.name


class ReceiptTemplateLine(models.Model):
    """Строка в шаблоне квитанции"""
    template = models.ForeignKey(
        ReceiptTemplate,
        on_delete=models.CASCADE,
        related_name='lines'
    )
    category = models.ForeignKey(
        PaymentCategory,
        on_delete=models.PROTECT,
        verbose_name='Категория взноса'
    )
    # Тип расчёта
    CALC_FIXED = 'fixed'          # Фиксированная сумма
    CALC_PER_UNIT = 'per_unit'    # За единицу (сотку, кВт·ч)
    
    CALC_TYPE_CHOICES = [
        (CALC_FIXED, 'Фиксированная сумма'),
        (CALC_PER_UNIT, 'За единицу измерения'),
    ]
    calc_type = models.CharField(
        'Тип расчёта',
        max_length=10,
        choices=CALC_TYPE_CHOICES,
        default=CALC_FIXED
    )
    amount = models.DecimalField(
        'Сумма / Тариф',
        max_digits=10,
        decimal_places=2,
        default=0,
        help_text='Фиксированная сумма ИЛИ тариф за единицу'
    )
    # Для расчёта за единицу — сколько единиц (автоматически или вручную)
    auto_quantity = models.BooleanField(
        'Авто-количество',
        default=True,
        help_text='Брать площадь участка (для соток) или вводить вручную (для электричества)'
    )
    manual_quantity = models.DecimalField(
        'Количество вручную',
        max_digits=10,
        decimal_places=2,
        default=0,
        help_text='Если не авто — укажите количество единиц'
    )
    order = models.PositiveIntegerField('Порядок', default=0)

    class Meta:
        verbose_name = 'Строка шаблона'
        verbose_name_plural = 'Строки шаблона'
        ordering = ['order']

    def __str__(self):
        return f"{self.category.name} — {self.get_calc_type_display()}"


class ConsolidatedAssessment(models.Model):
    """
    Составное начисление — квитанция с несколькими строками расчёта.
    Создаётся на основе шаблона для конкретного владельца и участка.
    """
    owner = models.ForeignKey(Owner, on_delete=models.CASCADE, related_name='consolidated_assessments')
    land_plot = models.ForeignKey(LandPlot, on_delete=models.CASCADE, related_name='consolidated_assessments')
    period = models.ForeignKey(PaymentPeriod, on_delete=models.PROTECT)
    
    total_amount = models.DecimalField('Общая сумма', max_digits=10, decimal_places=2, default=0)
    paid_amount = models.DecimalField('Оплачено', max_digits=10, decimal_places=2, default=0)
    
    STATUS_CHOICES = Assessment.STATUS_CHOICES
    status = models.CharField('Статус', max_length=15, choices=STATUS_CHOICES, default='pending', db_index=True)
    
    payment_uid = models.CharField('UID квитанции', max_length=20, unique=True, blank=True)
    notes = models.TextField('Примечания', blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Составное начисление'
        verbose_name_plural = 'Составные начисления'
        ordering = ['-created_at']

    def save(self, *args, **kwargs):
        if not self.payment_uid:
            self.payment_uid = self.generate_uid()
        super().save(*args, **kwargs)
    
    @classmethod
    def generate_uid(cls):
        """Генерация уникального UID для составного начисления"""
        max_id = cls.objects.aggregate(models.Max('id'))['id__max']
        next_id = (max_id + 1) if max_id else 1
        return f"SNT-C-{next_id:06d}"

    @property
    def debt(self):
        return max(0, self.total_amount - self.paid_amount)


class ConsolidatedAssessmentLine(models.Model):
    """Строка в составном начислении"""
    consolidated = models.ForeignKey(
        ConsolidatedAssessment,
        on_delete=models.CASCADE,
        related_name='lines'
    )
    category = models.ForeignKey(PaymentCategory, on_delete=models.PROTECT)
    description = models.CharField('Описание', max_length=255, blank=True)
    quantity = models.DecimalField('Количество', max_digits=10, decimal_places=2, default=1)
    unit = models.CharField('Ед. изм.', max_length=20, default='участок')
    rate = models.DecimalField('Тариф', max_digits=10, decimal_places=2)
    amount = models.DecimalField('Сумма', max_digits=10, decimal_places=2)
    order = models.PositiveIntegerField('Порядок', default=0)

    class Meta:
        verbose_name = 'Строка составного начисления'
        verbose_name_plural = 'Строки составного начисления'
        ordering = ['order']

    def __str__(self):
        return f"{self.description}: {self.quantity} × {self.rate} = {self.amount} ₽"