from django.db import models
from django.utils import timezone
from users.models import Owner
from land.models import LandPlot


class CallRecord(models.Model):
    """
    Модель записи телефонного разговора.
    
    Хранит метаданные звонка, связь с владельцем / участком,
    аудиофайл, заметки оператора и технические поля интеграции с Asterisk.
    """

    INCOMING = 'in'
    OUTGOING = 'out'
    MISSED = 'missed'
    DIRECTION_CHOICES = [
        (INCOMING, 'Входящий'),
        (OUTGOING, 'Исходящий'),
        (MISSED, 'Пропущенный'),
    ]

    # Статусы обработки звонка
    STATUS_NEW = 'new'
    STATUS_PROCESSED = 'processed'
    STATUS_ARCHIVED = 'archived'
    STATUS_CHOICES = [
        (STATUS_NEW, 'Новый'),
        (STATUS_PROCESSED, 'Обработан'),
        (STATUS_ARCHIVED, 'Архивирован'),
    ]

    # Связь с владельцем (может быть не определён для неизвестных номеров)
    owner = models.ForeignKey(
        Owner,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='calls',
        verbose_name='Владелец',
    )
    # Связь с участком (если разговор касается конкретного участка)
    land_plot = models.ForeignKey(
        LandPlot,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='calls',
        verbose_name='Участок',
    )

    # Номера телефонов
    caller_number = models.CharField(
        'Номер звонящего',
        max_length=20,
        db_index=True,
    )
    called_number = models.CharField(
        'Номер назначения',
        max_length=20,
        blank=True,
        help_text='Внутренний номер СНТ, на который поступил звонок',
    )

    # Параметры звонка
    direction = models.CharField(
        'Направление',
        max_length=10,
        choices=DIRECTION_CHOICES,
        default=INCOMING,
    )
    status = models.CharField(
        'Статус обработки',
        max_length=15,
        choices=STATUS_CHOICES,
        default=STATUS_NEW,
        db_index=True,
    )
    started_at = models.DateTimeField(
        'Начало звонка',
        db_index=True,
    )
    answered_at = models.DateTimeField(
        'Время ответа',
        null=True,
        blank=True,
    )
    ended_at = models.DateTimeField(
        'Окончание звонка',
        null=True,
        blank=True,
    )
    duration_seconds = models.PositiveIntegerField(
        'Длительность (сек)',
        default=0,
    )

    # Аудиофайл
    audio_file = models.FileField(
        'Файл записи',
        upload_to='call_recordings/%Y/%m/',
        blank=True,
        null=True,
    )

    # Технические поля Asterisk
    asterisk_uniqueid = models.CharField(
        'Asterisk UniqueID',
        max_length=50,
        unique=True,
        blank=True,
        null=True,
        help_text='Уникальный идентификатор канала в Asterisk',
    )
    asterisk_channel = models.CharField(
        'Канал',
        max_length=100,
        blank=True,
        help_text='Название канала Asterisk (SIP/dialer/...)',
    )
    asterisk_recording_file = models.CharField(
        'Путь к файлу в Asterisk',
        max_length=500,
        blank=True,
        help_text='Полный путь к wav-файлу на сервере Asterisk',
    )

    # Пользовательские поля
    operator_note = models.TextField(
        'Заметка оператора',
        blank=True,
        help_text='Краткое описание сути разговора',
    )
    tags = models.CharField(
        'Теги',
        max_length=200,
        blank=True,
        help_text='Теги через запятую: жалоба, должник, электричество',
    )
    is_important = models.BooleanField(
        'Важный',
        default=False,
        help_text='Пометить как важный звонок',
    )

    # Служебное
    created_at = models.DateTimeField(
        'Дата создания',
        auto_now_add=True,
    )
    updated_at = models.DateTimeField(
        'Дата обновления',
        auto_now=True,
    )

    class Meta:
        verbose_name = 'Запись звонка'
        verbose_name_plural = 'Записи звонков'
        ordering = ['-started_at']
        indexes = [
            models.Index(fields=['owner', 'started_at']),
            models.Index(fields=['caller_number', 'started_at']),
            models.Index(fields=['status', 'started_at']),
            models.Index(fields=['asterisk_uniqueid']),
        ]

    def __str__(self):
        direction_label = self.get_direction_display()
        time_str = timezone.localtime(self.started_at).strftime('%d.%m.%Y %H:%M')
        return f"[{direction_label}] {self.caller_number} ({time_str})"

    @property
    def caller_display(self):
        """Отображение номера с именем владельца, если он известен."""
        if self.owner:
            return f"{self.owner.full_name} ({self.caller_number})"
        return self.caller_number

    @property
    def duration_display(self):
        """Человекочитаемая длительность."""
        m, s = divmod(self.duration_seconds, 60)
        h, m = divmod(m, 60)
        if h:
            return f"{h}ч {m}м {s}с"
        elif m:
            return f"{m}м {s}с"
        return f"{s}с"

    @property
    def has_recording(self):
        """Есть ли файл записи."""
        return bool(self.audio_file)

    @property
    def tags_list(self):
        """Список тегов."""
        return [t.strip() for t in self.tags.split(',') if t.strip()] if self.tags else []

    def add_tag(self, tag):
        """Добавить тег."""
        tags = set(self.tags_list)
        tags.add(tag.strip())
        self.tags = ', '.join(sorted(tags))

    def remove_tag(self, tag):
        """Удалить тег."""
        tags = set(self.tags_list)
        tags.discard(tag.strip())
        self.tags = ', '.join(sorted(tags))