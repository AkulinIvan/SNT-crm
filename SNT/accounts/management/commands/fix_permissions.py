# accounts/management/commands/fix_permissions.py
from django.core.management.base import BaseCommand
from django.contrib.auth.models import Permission
from accounts.models import User


class Command(BaseCommand):
    help = 'Исправляет права доступа для всех пользователей'

    def handle(self, *args, **options):
        self.stdout.write("Начинаем исправление прав пользователей...")
        
        for user in User.objects.all():
            self.stdout.write(f"\nОбработка пользователя: {user.username} (role={user.role}, is_superuser={user.is_superuser})")
            
            # Суперпользователи
            if user.is_superuser:
                user.role = 'admin'
                user.user_permissions.set(Permission.objects.all())
                user.save()
                self.stdout.write(self.style.SUCCESS(f'  -> Суперпользователь: роль изменена на admin, назначены все права'))
                continue
            
            # Назначение прав по роли
            permissions_map = {
                'viewer': [
                    'can_view_all_owners', 'can_view_all_plots', 'can_view_finances',
                ],
                'accountant': [
                    'can_view_all_owners', 'can_view_all_plots', 'can_view_finances',
                    'can_manage_finances', 'can_export_data',
                ],
                'manager': [
                    'can_view_all_owners', 'can_edit_owners', 'can_delete_owners',
                    'can_view_all_plots', 'can_edit_plots', 'can_delete_plots',
                    'can_view_finances', 'can_manage_finances', 'can_export_data',
                    'can_manage_users', 'can_view_audit_log',
                ],
                'admin': [],
            }
            
            if user.role == 'admin':
                user.user_permissions.set(Permission.objects.all())
                self.stdout.write(self.style.SUCCESS(f'  -> Администратор: назначены все права'))
            else:
                codenames = permissions_map.get(user.role, [])
                perms = Permission.objects.filter(codename__in=codenames)
                user.user_permissions.set(perms)
                self.stdout.write(self.style.SUCCESS(f'  -> {user.get_role_display()}: назначено {perms.count()} прав'))
            
            user.save()
        
        self.stdout.write("\n" + "="*50)
        self.stdout.write(self.style.SUCCESS("Проверка итогов:"))
        for user in User.objects.all():
            self.stdout.write(f"{user.username} ({user.get_role_display()}): {user.user_permissions.count()} прав")
        
        self.stdout.write(self.style.SUCCESS("\nГотово!"))