# SNT/voting/models.py
from django.db import models
from django.conf import settings
from django.core.validators import MinValueValidator, MaxValueValidator
from django.utils import timezone
from datetime import timedelta
import secrets
import logging

logger = logging.getLogger(__name__)


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
        permissions = [
            ("can_vote", "Может голосовать"),
            ("can_manage_voting", "Может управлять голосованиями"),
        ]
    
    def __str__(self):
        return self.title
    
    def save(self, *args, **kwargs):
        """При сохранении обновляем статус если нужно"""
        # Если дата окончания прошла, а статус активный - автоматически закрываем
        if self.status == 'active' and self.end_date < timezone.now():
            self.status = 'closed'
            logger.info(f"Voting session {self.id} auto-closed because end_date passed")
        
        super().save(*args, **kwargs)
    
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
    def quorum_required(self):
        """Количество голосов, необходимых для кворума"""
        if self.total_eligible == 0:
            return 0
        return (self.total_eligible * self.quorum_percent) / 100
    
    @property
    def quorum_remaining(self):
        """Сколько ещё голосов нужно для кворума"""
        if self.quorum_reached:
            return 0
        return max(0, self.quorum_required - self.total_voted)
    
    @property
    def days_remaining(self):
        """Дней до окончания"""
        if self.is_closed or self.end_date < timezone.now():
            return 0
        delta = self.end_date - timezone.now()
        return delta.days
    
    @property
    def hours_remaining(self):
        """Часов до окончания"""
        if self.is_closed or self.end_date < timezone.now():
            return 0
        delta = self.end_date - timezone.now()
        return delta.seconds // 3600
    
    @property
    def participation_rate(self):
        """Процент участия"""
        if self.total_eligible == 0:
            return 0
        return (self.total_voted / self.total_eligible) * 100
    
    def activate(self):
        """Активировать голосование"""
        if self.status != 'draft':
            raise ValueError(f"Cannot activate voting session with status {self.status}")
        
        if self.questions.count() == 0:
            raise ValueError("Cannot activate voting session without questions")
        
        self.status = 'active'
        self.save()
        logger.info(f"Voting session {self.id} activated")
    
    def close_voting(self):
        """Закрыть голосование"""
        if self.status not in ['active', 'draft']:
            raise ValueError(f"Cannot close voting session with status {self.status}")
        
        self.status = 'closed'
        self.save()
        logger.info(f"Voting session {self.id} closed")
    
    def cancel(self):
        """Отменить голосование"""
        self.status = 'cancelled'
        self.save()
        logger.info(f"Voting session {self.id} cancelled")
    
    def calculate_results(self):
        """Рассчитать результаты по всем вопросам"""
        for question in self.questions.all():
            question.calculate_results()
        logger.info(f"Results calculated for voting session {self.id}")
    
    def update_eligible_count(self):
        """Обновить количество имеющих право голоса"""
        from users.models import Owner
        
        count = Owner.objects.filter(
            memberships__organization=self.organization,
            memberships__status='active'
        ).count()
        
        self.total_eligible = count
        self.save(update_fields=['total_eligible'])
        logger.debug(f"Updated eligible count for session {self.id}: {count}")
    
    def get_ballot_for_owner(self, owner):
        """Получить бюллетень владельца"""
        return self.ballots.filter(owner=owner).first()
    
    def has_voted(self, owner):
        """Проверить, голосовал ли владелец"""
        ballot = self.get_ballot_for_owner(owner)
        return ballot and ballot.status == 'submitted'


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
            option.update_votes_count()
        logger.debug(f"Results calculated for question {self.id}")
    
    @property
    def total_ballots(self):
        """Количество бюллетеней, ответивших на вопрос"""
        return self.votes.filter(ballot__status='submitted').count()


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
    
    def update_votes_count(self):
        """Обновить количество голосов"""
        self.votes_count = self.votes.filter(ballot__status='submitted').count()
        
        question_total = self.question.total_votes
        if question_total > 0:
            self.percentage = (self.votes_count / question_total) * 100
        else:
            self.percentage = 0
        
        self.save(update_fields=['votes_count', 'percentage'])
        logger.debug(f"Updated option {self.id}: votes={self.votes_count}, percentage={self.percentage:.1f}%")


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
    
    # Время голосования
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
    
    def save(self, *args, **kwargs):
        """При сохранении обновляем статистику сессии"""
        is_new = self.pk is None
        old_status = None
        
        if not is_new:
            old_ballot = Ballot.objects.get(pk=self.pk)
            old_status = old_ballot.status
        
        super().save(*args, **kwargs)
        
        # Обновляем статистику сессии
        if self.status == 'submitted' and old_status != 'submitted':
            # Добавляем голос
            self.voting_session.total_voted += 1
            self.voting_session.save(update_fields=['total_voted'])
            logger.debug(f"Ballot {self.id} added to session stats")
        elif old_status == 'submitted' and self.status != 'submitted':
            # Убираем голос
            self.voting_session.total_voted = max(0, self.voting_session.total_voted - 1)
            self.voting_session.save(update_fields=['total_voted'])
            logger.debug(f"Ballot {self.id} removed from session stats")
    
    @property
    def is_valid(self):
        return self.status == 'submitted'
    
    def mark_valid(self):
        """Отметить бюллетень как действительный"""
        self.status = 'submitted'
        self.save()
        logger.info(f"Ballot {self.id} marked as valid")
    
    def mark_invalid(self, reason=''):
        """Отметить бюллетень как недействительный"""
        self.status = 'invalid'
        self.save()
        logger.info(f"Ballot {self.id} marked as invalid: {reason}")
    
    def get_votes_summary(self):
        """Получить сводку по голосам"""
        summary = {}
        for vote in self.votes.select_related('question', 'option'):
            if vote.option:
                summary[vote.question.title] = vote.option.text
            elif vote.rating_value:
                summary[vote.question.title] = f"Оценка: {vote.rating_value}/10"
            elif vote.text_answer:
                summary[vote.question.title] = vote.text_answer[:100]
        return summary


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
        elif self.rating_value:
            return f"{self.ballot.owner.full_name}: {self.question.title} -> {self.rating_value}/10"
        elif self.text_answer:
            return f"{self.ballot.owner.full_name}: {self.question.title} -> {self.text_answer[:50]}"
        return f"{self.ballot.owner.full_name}: {self.question.title}"
    
    def save(self, *args, **kwargs):
        """При сохранении голоса обновляем статистику вопроса"""
        is_new = self.pk is None
        
        super().save(*args, **kwargs)
        
        if is_new and self.ballot.status == 'submitted':
            # Обновляем количество голосов в вопросе
            self.question.total_votes = Vote.objects.filter(
                question=self.question,
                ballot__status='submitted'
            ).count()
            self.question.save(update_fields=['total_votes'])
            
            # Обновляем статистику опции
            if self.option:
                self.option.update_votes_count()


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
        """Генерация уникального токена"""
        import secrets
        self.unique_token = secrets.token_urlsafe(32)
        return self.unique_token
    
    def mark_sent(self):
        """Отметить как отправленное"""
        self.sent_at = timezone.now()
        self.save(update_fields=['sent_at'])
        logger.debug(f"Invitation {self.id} marked as sent")
    
    def mark_opened(self):
        """Отметить как открытое"""
        if not self.opened_at:
            self.opened_at = timezone.now()
            self.save(update_fields=['opened_at'])
            logger.debug(f"Invitation {self.id} marked as opened")
    
    def get_voting_url(self):
        """Получить URL для голосования"""
        from django.urls import reverse
        return reverse('voting:public_vote', kwargs={'token': self.unique_token})
    
    def save(self, *args, **kwargs):
        if not self.unique_token:
            self.generate_token()
        super().save(*args, **kwargs)