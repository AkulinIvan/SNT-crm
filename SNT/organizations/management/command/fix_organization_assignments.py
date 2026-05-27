from django.core.management.base import BaseCommand
from organizations.models import Organization, OrganizationStaffAssignment
from accounts.models import User

class Command(BaseCommand):
    help = 'Исправляет назначения сотрудников в организациях'
    
    def handle(self, *args, **options):
        # Для каждой организации создаем назначения
        for org in Organization.objects.all():
            # Председатель
            if org.chairman:
                assignment, created = OrganizationStaffAssignment.objects.get_or_create(
                    organization=org,
                    user=org.chairman,
                    role='chairman',
                    defaults={
                        'position_title': 'Председатель правления',
                        'is_active': True
                    }
                )
                if created:
                    self.stdout.write(f'Создано назначение: {org.chairman} -> {org.short_name} (председатель)')
                
                # Привязываем организацию пользователю
                if not org.chairman.organization:
                    org.chairman.organization = org
                    org.chairman.save(update_fields=['organization'])
                    self.stdout.write(f'Привязана организация к пользователю: {org.chairman}')
            
            # Бухгалтер
            if org.accountant:
                assignment, created = OrganizationStaffAssignment.objects.get_or_create(
                    organization=org,
                    user=org.accountant,
                    role='accountant',
                    defaults={
                        'position_title': 'Бухгалтер',
                        'is_active': True
                    }
                )
                if created:
                    self.stdout.write(f'Создано назначение: {org.accountant} -> {org.short_name} (бухгалтер)')
                
                if not org.accountant.organization:
                    org.accountant.organization = org
                    org.accountant.save(update_fields=['organization'])
        
        self.stdout.write(self.style.SUCCESS('Назначения исправлены'))