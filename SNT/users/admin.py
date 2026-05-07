from django.contrib import admin
from .models import Owner, Ownership, ContactInfo


class OwnershipInline(admin.TabularInline):
    """Встроенное редактирование участков прямо в карточке владельца."""
    model = Ownership
    extra = 0
    raw_id_fields = ('land_plot',)
    fields = ('land_plot', 'share', 'ownership_since', 'document_basis')


class ContactInfoInline(admin.TabularInline):
    """Встроенные контакты в карточке владельца."""
    model = ContactInfo
    extra = 0
    fields = ('type', 'value', 'is_active', 'is_verified', 'note')


@admin.register(Owner)
class OwnerAdmin(admin.ModelAdmin):
    list_display = [
        'full_name',
        'primary_phone',
        'primary_email',
        'plots_count',
        'created_at',
    ]
    search_fields = ['full_name', 'contacts__value']
    inlines = [ContactInfoInline, OwnershipInline]

    def plots_count(self, obj):
        return obj.land_plots.count()
    plots_count.short_description = 'Кол-во участков'

    def primary_phone(self, obj):
        return obj.primary_phone
    primary_phone.short_description = 'Телефон'

    def primary_email(self, obj):
        return obj.primary_email
    primary_email.short_description = 'Email'


@admin.register(ContactInfo)
class ContactInfoAdmin(admin.ModelAdmin):
    list_display = ['owner', 'type', 'value', 'is_active', 'is_verified', 'created_at']
    list_filter = ['type', 'is_active', 'is_verified']
    search_fields = ['value', 'owner__full_name']
    raw_id_fields = ('owner',)


@admin.register(Ownership)
class OwnershipAdmin(admin.ModelAdmin):
    list_display = ['owner', 'land_plot', 'share', 'ownership_since']
    list_filter = ['ownership_since']
    search_fields = ['owner__full_name', 'land_plot__plot_number', 'land_plot__cadastral_number']
    raw_id_fields = ('owner', 'land_plot')