from django.db import models
from django.conf import settings
from django.core.validators import MinValueValidator, MaxValueValidator
from django.utils import timezone
from datetime import timedelta

class VotingSession(models.Model):
    """
    Сессия голосования (общее собрание, заочное голосование и т.д.)
    """
    STATUS_CHOICES = [
        ('draft', 'Черновик'),
        ('active', 'Активно'),
        ('closed', 'Завершено'),
        ('cancelled', 'Отменено'),
    ]
    
    TYPE_CHOICES = [
        ('in_person', 'Очное собрание'),
        ('absentee', 'Заочное голосование'),
        ('mixed', 'Смешанное'),
    ]
    
    organization = models.ForeignKey(
        'organizations.Organization',
        on_delete=models.CASCADE,
        related_name='voting_sessions',
        verbose_name='СНТ'
    )
    
    title = models.CharField('Название голосования', max_length=200)
    description = models.TextField('Описание', blank=True)
    
    session_type = models.CharField(
        'Тип голосования',
        max_length=20,
        choices=TYPE_CHOICES,
        default='absentee'
    )
    
    status = models.CharField(
        'Статус',
        max_length=20,
        choices=STATUS_CHOICES,
        default='draft',
        db_index=True
    )
    
    start_date = models.DateTimeField('Дата начала', db_index=True)
    end_date = models.DateTimeField('Дата окончания', db_index=True)
    
    # Кворум и результаты
    quorum_percent = models.DecimalField(
        'Кворум (%)',
        max_digits=5,
        decimal_places=2,
        default=50.00,
        validators=[MinValueValidator(0), MaxValueValidator(100)]
    )
    
    total_eligible = models.PositiveIntegerField(
        'Всего имеющих право голоса',
        default=0
    )
    total_voted = models.PositiveIntegerField(
        'Проголосовало',
        default=0
    )
    
    # Дополнительные поля
    meeting_place = models.CharField('Место проведения', max_length=200, blank=True)
    protocol_number = models.CharField('Номер протокола', max_length=50, blank=True)
    protocol_date = models.DateField('Дата протокола', null=True, blank=True)
    
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='created_voting_sessions',
        verbose_name='Создал'
    )
    
    created_at = models.DateTimeField('Создано', auto_now_add=True)
    updated_at = models.DateTimeField('Обновлено', auto_now=True)
    
    class Meta:
        verbose_name = 'Сессия голосования'
        verbose_name_plural = 'Сессии голосования'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['organization', 'status']),
            models.Index(fields=['start_date', 'end_date']),
        ]
    
    def __str__(self):
        return self.title
    
    @property
    def is_active(self):
        """Активно ли голосование в данный момент"""
        now = timezone.now()
        return (self.status == 'active' and 
                self.start_date <= now <= self.end_date)
    
    @property
    def is_closed(self):
        return self.status in ['closed', 'cancelled']
    
    @property
    def quorum_reached(self):
        """Достигнут ли кворум"""
        if self.total_eligible == 0:
            return False
        required = (self.total_eligible * self.quorum_percent) / 100
        return self.total_voted >= required
    
    @property
    def days_remaining(self):
        """Дней до окончания"""
        if self.is_closed or self.end_date < timezone.now():
            return 0
        delta = self.end_date - timezone.now()
        return delta.days
    
    def close_voting(self):
        """Закрыть голосование"""
        self.status = 'closed'
        self.save()
    
    def calculate_results(self):
        """Рассчитать результаты по всем вопросам"""
        for question in self.questions.all():
            question.calculate_results()


class Question(models.Model):
    """
    Вопрос для голосования
    """
    QUESTION_TYPE_CHOICES = [
        ('single', 'Единичный выбор (да/нет/воздержался)'),
        ('multiple', 'Множественный выбор'),
        ('rating', 'Рейтинговое голосование'),
    ]
    
    voting_session = models.ForeignKey(
        VotingSession,
        on_delete=models.CASCADE,
        related_name='questions',
        verbose_name='Сессия голосования'
    )
    
    title = models.CharField('Название вопроса', max_length=300)
    description = models.TextField('Описание вопроса', blank=True)
    question_type = models.CharField(
        'Тип вопроса',
        max_length=20,
        choices=QUESTION_TYPE_CHOICES,
        default='single'
    )
    
    order = models.PositiveIntegerField('Порядок', default=0)
    
    # Результаты
    total_votes = models.PositiveIntegerField('Всего голосов', default=0)
    
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        verbose_name = 'Вопрос'
        verbose_name_plural = 'Вопросы'
        ordering = ['order', 'id']
    
    def __str__(self):
        return self.title
    
    def calculate_results(self):
        """Рассчитать результаты по вариантам ответов"""
        for option in self.options.all():
            option.votes_count = option.votes.count()
            option.percentage = (option.votes_count / self.total_votes * 100) if self.total_votes > 0 else 0
            option.save(update_fields=['votes_count', 'percentage'])


class AnswerOption(models.Model):
    """
    Вариант ответа на вопрос
    """
    question = models.ForeignKey(
        Question,
        on_delete=models.CASCADE,
        related_name='options',
        verbose_name='Вопрос'
    )
    
    text = models.CharField('Текст варианта', max_length=200)
    order = models.PositiveIntegerField('Порядок', default=0)
    
    # Результаты
    votes_count = models.PositiveIntegerField('Количество голосов', default=0)
    percentage = models.DecimalField(
        'Процент',
        max_digits=5,
        decimal_places=2,
        default=0
    )
    
    class Meta:
        verbose_name = 'Вариант ответа'
        verbose_name_plural = 'Варианты ответов'
        ordering = ['order', 'id']
    
    def __str__(self):
        return self.text


class Ballot(models.Model):
    """
    Бюллетень голосования (голос одного участника)
    """
    STATUS_CHOICES = [
        ('pending', 'Ожидает'),
        ('submitted', 'Подан'),
        ('invalid', 'Недействителен'),
    ]
    
    voting_session = models.ForeignKey(
        VotingSession,
        on_delete=models.CASCADE,
        related_name='ballots',
        verbose_name='Сессия голосования'
    )
    
    owner = models.ForeignKey(
        'users.Owner',
        on_delete=models.CASCADE,
        related_name='ballots',
        verbose_name='Владелец'
    )
    
    # Кто заполнил бюллетень (сам владелец или представитель)
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='submitted_ballots',
        verbose_name='Кто заполнил'
    )
    
    status = models.CharField(
        'Статус',
        max_length=20,
        choices=STATUS_CHOICES,
        default='pending',
        db_index=True
    )
    
    # Данные о голосующем
    representative_name = models.CharField('ФИО представителя', max_length=200, blank=True)
    representative_document = models.CharField('Документ представителя', max_length=100, blank=True)
    
    # Файл с подписью (опционально)
    signature_file = models.FileField(
        'Файл с подписью',
        upload_to='voting_signatures/%Y/%m/',
        blank=True,
        null=True
    )
    
    # IP-адрес и User-Agent для верификации
    ip_address = models.GenericIPAddressField('IP-адрес', null=True, blank=True)
    user_agent = models.TextField('User Agent', blank=True)
    
    submitted_at = models.DateTimeField('Дата подачи', auto_now_add=True)
    updated_at = models.DateTimeField('Обновлено', auto_now=True)
    
    class Meta:
        verbose_name = 'Бюллетень'
        verbose_name_plural = 'Бюллетени'
        ordering = ['-submitted_at']
        unique_together = ['voting_session', 'owner']
        indexes = [
            models.Index(fields=['voting_session', 'status']),
            models.Index(fields=['owner', 'submitted_at']),
        ]
    
    def __str__(self):
        return f"Бюллетень {self.owner.full_name} - {self.voting_session.title}"
    
    @property
    def is_valid(self):
        return self.status == 'submitted'
    
    def mark_valid(self):
        self.status = 'submitted'
        self.save()
    
    def mark_invalid(self, reason=''):
        self.status = 'invalid'
        if reason:
            # Можно добавить поле reason
            pass
        self.save()


class Vote(models.Model):
    """
    Отдельный голос (связь бюллетеня с вариантом ответа)
    """
    ballot = models.ForeignKey(
        Ballot,
        on_delete=models.CASCADE,
        related_name='votes',
        verbose_name='Бюллетень'
    )
    
    question = models.ForeignKey(
        Question,
        on_delete=models.CASCADE,
        related_name='votes',
        verbose_name='Вопрос'
    )
    
    option = models.ForeignKey(
        AnswerOption,
        on_delete=models.CASCADE,
        related_name='votes',
        verbose_name='Выбранный вариант',
        null=True,
        blank=True
    )
    
    # Для рейтингового голосования (оценка от 1 до 10)
    rating_value = models.PositiveSmallIntegerField(
        'Оценка',
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(10)]
    )
    
    # Текстовый ответ (для открытых вопросов)
    text_answer = models.TextField('Текстовый ответ', blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        verbose_name = 'Голос'
        verbose_name_plural = 'Голоса'
        unique_together = ['ballot', 'question']
    
    def __str__(self):
        if self.option:
            return f"{self.ballot.owner.full_name}: {self.question.title} -> {self.option.text}"
        return f"{self.ballot.owner.full_name}: {self.question.title}"


class VotingInvitation(models.Model):
    """
    Приглашения на голосование (для отправки)
    """
    INVITATION_TYPE_CHOICES = [
        ('email', 'Email'),
        ('sms', 'SMS'),
        ('letter', 'Письмо'),
    ]
    
    voting_session = models.ForeignKey(
        VotingSession,
        on_delete=models.CASCADE,
        related_name='invitations',
        verbose_name='Сессия голосования'
    )
    
    owner = models.ForeignKey(
        'users.Owner',
        on_delete=models.CASCADE,
        related_name='voting_invitations',
        verbose_name='Владелец'
    )
    
    invitation_type = models.CharField(
        'Тип приглашения',
        max_length=20,
        choices=INVITATION_TYPE_CHOICES
    )
    
    contact_value = models.CharField('Контакт (email/телефон)', max_length=200)
    unique_token = models.CharField('Уникальный токен', max_length=100, unique=True, blank=True)
    
    sent_at = models.DateTimeField('Дата отправки', null=True, blank=True)
    opened_at = models.DateTimeField('Дата открытия', null=True, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        verbose_name = 'Приглашение'
        verbose_name_plural = 'Приглашения'
        indexes = [
            models.Index(fields=['unique_token']),
            models.Index(fields=['voting_session', 'owner']),
        ]
    
    def __str__(self):
        return f"Приглашение {self.owner.full_name} ({self.get_invitation_type_display()})"
    
    def generate_token(self):
        import secrets
        self.unique_token = secrets.token_urlsafe(32)
        return self.unique_token
    
    def save(self, *args, **kwargs):
        if not self.unique_token:
            self.generate_token()
        super().save(*args, **kwargs)