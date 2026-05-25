from django.core.management.base import BaseCommand
from django.core.management import call_command
from django.conf import settings
import os


class Command(BaseCommand):
    help = 'Загрузка начальных тарифов'
    
    def handle(self, *args, **options):
        fixture_path = os.path.join(
            settings.BASE_DIR, 
            'subscriptions', 
            'fixtures', 
            'initial_tariffs.json'
        )
        
        try:
            call_command('loaddata', fixture_path)
            self.stdout.write(
                self.style.SUCCESS('Тарифы успешно загружены')
            )
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'Ошибка загрузки тарифов: {e}')
            )