from django.db import models
from django.contrib.auth.models import AbstractUser, Group, Permission
from django.core.validators import RegexValidator
from django.utils import timezone


class User(AbstractUser):
    """
    Расширенная модель пользователя системы.
    """
    ROLE_CHOICES = [
        ('admin', 'Администратор'),
        ('manager', 'Председатель'),
        ('accountant', 'Бухгалтер'),
        ('viewer', 'Наблюдатель'),
    ]
    
    # Переопределяем связи с уникальными related_name
    groups = models.ManyToManyField(
        Group,
        verbose_name='Группы',
        blank=True,
        help_text='Группы, к которым принадлежит пользователь.',
        related_name='accounts_users',  # ← Уникальное имя
        related_query_name='accounts_user',
    )
    user_permissions = models.ManyToManyField(
        Permission,
        verbose_name='Разрешения',
        blank=True,
        help_text='Специфические разрешения для пользователя.',
        related_name='accounts_users_permissions',  # ← Уникальное имя
        related_query_name='accounts_user_permission',
    )
    
    role = models.CharField(
        'Роль',
        max_length=20,
        choices=ROLE_CHOICES,
        default='viewer',
        db_index=True
    )
    middle_name = models.CharField(
        'Отчество',
        max_length=50,
        blank=True
    )
    phone = models.CharField(
        'Телефон',
        max_length=20,
        blank=True,
        validators=[
            RegexValidator(
                regex=r'^\+?[\d\s\-\(\)]{10,20}$',
                message='Введите корректный номер телефона'
            )
        ]
    )
    email = models.EmailField(unique=True, null=True, blank=True)
    position = models.CharField(
        'Должность',
        max_length=100,
        blank=True,
        help_text='Например: Председатель, Бухгалтер, Охранник'
    )
    is_active = models.BooleanField(
        'Активен',
        default=True,
        db_index=True
    )
    last_activity = models.DateTimeField(
        'Последняя активность',
        null=True,
        blank=True
    )
    avatar = models.ImageField(
        'Фото',
        upload_to='avatars/',
        null=True,
        blank=True
    )
    notes = models.TextField(
        'Примечания',
        blank=True
    )
    created_at = models.DateTimeField(
        'Дата создания',
        auto_now_add=True
    )
    updated_at = models.DateTimeField(
        'Дата обновления',
        auto_now=True
    )
    # Связь с СНТ (для сотрудников)
    organization = models.ForeignKey(
        'organizations.Organization',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='staff_members',
        verbose_name='СНТ',
        help_text='СНТ, в котором работает сотрудник'
    )
    class Meta:
        verbose_name = 'Пользователь'
        verbose_name_plural = 'Пользователи'
        ordering = ['last_name', 'first_name']
        indexes = [
            models.Index(fields=['role']),
            models.Index(fields=['is_active']),
            models.Index(fields=['last_activity']),
        ]
        permissions = [
            ("can_view_all_owners", "Может просматривать всех владельцев"),
            ("can_edit_owners", "Может редактировать владельцев"),
            ("can_delete_owners", "Может удалять владельцев"),
            ("can_view_all_plots", "Может просматривать все участки"),
            ("can_edit_plots", "Может редактировать участки"),
            ("can_delete_plots", "Может удалять участки"),
            ("can_view_finances", "Может просматривать финансы"),
            ("can_manage_finances", "Может управлять финансами"),
            ("can_export_data", "Может экспортировать данные"),
            ("can_manage_users", "Может управлять пользователями"),
            ("can_view_audit_log", "Может просматривать логи"),
        ]

    def __str__(self):
        full = f"{self.last_name} {self.first_name}"
        if self.middle_name:
            full += f" {self.middle_name}"
        return full.strip() or self.username

    @property
    def full_name(self):
        return str(self)

    @property
    def is_admin(self):
        return self.role == 'admin' or self.is_superuser

    @property
    def is_manager(self):
        return self.role in ['admin', 'manager'] or self.is_superuser

    @property
    def is_accountant(self):
        return self.role in ['admin', 'accountant'] or self.is_superuser

    def update_activity(self):
        """Обновить время последней активности"""
        self.last_activity = timezone.now()
        self.save(update_fields=['last_activity'])

    def get_permissions_list(self):
        """Получить список всех разрешений пользователя"""
        if self.is_superuser:
            return list(Permission.objects.all().values_list('codename', flat=True))
        
        perms = set()
        # Групповые разрешения
        for group in self.groups.all():
            perms.update(group.permissions.values_list('codename', flat=True))
        # Персональные разрешения
        perms.update(self.user_permissions.values_list('codename', flat=True))
        
        return list(perms)
    
    def save(self, *args, **kwargs):
        """При сохранении суперпользователя автоматически назначаем роль admin"""
        if self.is_superuser and self.role != 'admin':
            self.role = 'admin'
        super().save(*args, **kwargs)

    @property
    def organization_name(self):
        """Название СНТ пользователя"""
        if self.organization:
            return self.organization.short_name
        return None

class UserActionLog(models.Model):
    """
    Лог действий пользователей в системе.
    """
    ACTION_CHOICES = [
        ('create', 'Создание'),
        ('update', 'Обновление'),
        ('delete', 'Удаление'),
        ('view', 'Просмотр'),
        ('login', 'Вход'),
        ('logout', 'Выход'),
        ('export', 'Экспорт'),
        ('other', 'Другое'),
    ]
    
    user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='actions'
    )
    action = models.CharField(
        'Действие',
        max_length=20,
        choices=ACTION_CHOICES
    )
    model_name = models.CharField(
        'Модель',
        max_length=50,
        blank=True
    )
    object_id = models.IntegerField(
        'ID объекта',
        null=True,
        blank=True
    )
    details = models.TextField(
        'Детали',
        blank=True
    )
    ip_address = models.GenericIPAddressField(
        'IP-адрес',
        null=True,
        blank=True
    )
    user_agent = models.TextField(
        'User Agent',
        blank=True
    )
    created_at = models.DateTimeField(
        'Время',
        auto_now_add=True,
        db_index=True
    )

    class Meta:
        verbose_name = 'Лог действий'
        verbose_name_plural = 'Логи действий'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', '-created_at']),
            models.Index(fields=['action']),
            models.Index(fields=['model_name']),
        ]

    def __str__(self):
        return f"{self.user} — {self.get_action_display()} ({self.created_at:%d.%m.%Y %H:%M})"