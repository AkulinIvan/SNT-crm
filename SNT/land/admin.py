from django.contrib import admin
from django.contrib import messages
from .models import LandPlot
from .services import rosreestr_service

@admin.action(description='Исправить границы участков (замкнуть полигоны)')
def fix_boundaries(modeladmin, request, queryset):
    fixed = 0
    for plot in queryset:
        if plot.boundaries and len(plot.boundaries) >= 3:
            normalized = rosreestr_service.normalize_boundaries(plot.boundaries)
            if normalized != plot.boundaries:
                plot.boundaries = normalized
                plot.save()
                fixed += 1
    messages.success(request, f'Исправлены границы {fixed} участков')


@admin.register(LandPlot)
class LandPlotAdmin(admin.ModelAdmin):
    list_display = [
        'plot_number',
        'cadastral_number',
        'area_sqm',
        'status',
        'has_coordinates',
        'updated_at',
    ]
    list_filter = ['status']
    search_fields = ['plot_number', 'cadastral_number', 'address']
    readonly_fields = ['created_at', 'updated_at']
    fieldsets = (
        ('Основная информация', {
            'fields': (
                'plot_number',
                'cadastral_number',
                'area_sqm',
                'address',
                'status',
            )
        }),
        ('Координаты на карте', {
            'fields': ('latitude', 'longitude'),
            'description': 'Необязательно. Если задаёте — задавайте обе координаты.',
        }),
        ('Служебное', {
            'fields': ('notes', 'created_at', 'updated_at'),
        }),
    )

    @admin.display(boolean=True, description='Координаты заданы')
    def has_coordinates(self, obj):
        return obj.has_coordinates