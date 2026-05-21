from django.utils import timezone

from django.core.management.base import BaseCommand
from land.models import LandPlot
from land.services import rosreestr_service
import logging

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Исправляет границы участков (замыкает полигоны и загружает отсутствующие)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--plot-ids',
            type=str,
            help='ID участков через запятую (например: 56,75,90)'
        )
        parser.add_argument(
            '--fix-all',
            action='store_true',
            help='Исправить все участки'
        )
        parser.add_argument(
            '--load-missing',
            action='store_true',
            help='Загрузить границы для участков без границ'
        )

    def handle(self, *args, **options):
        # Определяем список участков для обработки
        if options['plot_ids']:
            plot_ids = [int(id.strip()) for id in options['plot_ids'].split(',')]
            plots = LandPlot.objects.filter(id__in=plot_ids)
        elif options['fix_all']:
            plots = LandPlot.objects.all()
        else:
            plots = LandPlot.objects.filter(id__in=[56, 75, 90])  # По умолчанию эти три
        
        self.stdout.write(f"Обрабатывается {plots.count()} участков...")
        
        fixed_closed = 0
        loaded_missing = 0
        
        for plot in plots:
            self.stdout.write(f"\n📍 Участок {plot.id} (№{plot.plot_number})")
            
            # 1. Исправляем незамкнутые полигоны
            if plot.boundaries and len(plot.boundaries) >= 3:
                boundaries = plot.boundaries
                first = boundaries[0]
                last = boundaries[-1]
                
                if first != last:
                    plot.boundaries.append(first)
                    plot.save(update_fields=['boundaries', 'updated_at'])
                    fixed_closed += 1
                    self.stdout.write(self.style.SUCCESS(
                        f"  ✅ Исправлен незамкнутый полигон (было {len(boundaries)}, стало {len(plot.boundaries)} точек)"
                    ))
                else:
                    self.stdout.write(f"  ✓ Полигон уже замкнут ({len(boundaries)} точек)")
            
            # 2. Загружаем границы для участков без них
            elif options['load_missing'] and not plot.boundaries and plot.cadastral_number:
                self.stdout.write(f"  🔄 Загружаем границы для {plot.cadastral_number}...")
                
                try:
                    boundaries = rosreestr_service.get_parcel_boundaries(plot.cadastral_number)
                    if boundaries:
                        plot.boundaries = boundaries
                        plot.rosreestr_updated = timezone.now()
                        plot.save(update_fields=['boundaries', 'rosreestr_updated', 'updated_at'])
                        loaded_missing += 1
                        self.stdout.write(self.style.SUCCESS(
                            f"  ✅ Загружено {len(boundaries)} точек границ"
                        ))
                    else:
                        self.stdout.write(self.style.WARNING(
                            f"  ⚠️ Границы не найдены в Росреестре"
                        ))
                except Exception as e:
                    self.stdout.write(self.style.ERROR(
                        f"  ❌ Ошибка: {str(e)}"
                    ))
            else:
                self.stdout.write(f"  ⚠️ Нет границ для загрузки (нет кадастрового номера или загрузка отключена)")
        
        # Итоги
        self.stdout.write("\n" + "="*50)
        self.stdout.write(self.style.SUCCESS(
            f"📊 ИТОГИ:\n"
            f"  • Исправлено незамкнутых полигонов: {fixed_closed}\n"
            f"  • Загружено новых границ: {loaded_missing}"
        ))