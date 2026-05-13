# accounts/management/commands/createsuperuser.py (создайте этот файл)

from django.contrib.auth.management.commands import createsuperuser
from django.core.management import CommandError


class Command(createsuperuser.Command):
    def handle(self, *args, **options):
        # Вызываем стандартное создание суперпользователя
        super().handle(*args, **options)
        
        # После создания обновляем роль
        from accounts.models import User
        username = options.get('username')
        if username:
            try:
                user = User.objects.get(username=username)
                user.role = 'admin'
                user.save()
                self.stdout.write(self.style.SUCCESS(f'Роль установлена как "admin" для {username}'))
            except User.DoesNotExist:
                pass