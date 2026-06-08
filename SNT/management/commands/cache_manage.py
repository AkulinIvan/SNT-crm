# SNT/land/management/commands/cache_manage.py
from django.core.management.base import BaseCommand
from common.cache_utils import clear_all_cache, clear_api_cache, get_cache_stats, CacheWarmer


class Command(BaseCommand):
    help = 'Управление кэшем'
    
    def add_arguments(self, parser):
        parser.add_argument(
            'action',
            type=str,
            choices=['clear', 'clear-api', 'stats', 'warm'],
            help='Действие: clear (очистить всё), clear-api, stats (статистика), warm (прогрев)'
        )
        parser.add_argument(
            '--org-ids',
            type=str,
            help='ID организаций через запятую для прогрева'
        )
    
    def handle(self, *args, **options):
        action = options['action']
        
        if action == 'clear':
            clear_all_cache()
            self.stdout.write(self.style.SUCCESS('✓ Весь кэш очищен'))
        
        elif action == 'clear-api':
            clear_api_cache()
            self.stdout.write(self.style.SUCCESS('✓ API кэш очищен'))
        
        elif action == 'stats':
            stats = get_cache_stats()
            self.stdout.write("\n📊 Статистика кэша:")
            self.stdout.write(f"  Всего ключей: {stats['total_keys']}")
            self.stdout.write(f"  Попаданий: {stats['hits']}")
            self.stdout.write(f"  Промахов: {stats['misses']}")
            self.stdout.write(f"  Hit rate: {stats['hit_rate']:.1f}%")
            self.stdout.write(f"  Использовано памяти: {stats['used_memory']}")
        
        elif action == 'warm':
            org_ids = None
            if options['org_ids']:
                org_ids = [int(x.strip()) for x in options['org_ids'].split(',')]
            
            warmer = CacheWarmer()
            warmer.warm_land_plots_cache(org_ids)
            self.stdout.write(self.style.SUCCESS('✓ Кэш прогретый'))