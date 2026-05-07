from django.contrib import admin
from .models import LandPlot


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