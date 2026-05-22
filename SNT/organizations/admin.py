from django.contrib import admin
from .models import Organization, OrganizationMembership


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ['short_name', 'inn', 'kpp', 'chairman', 'is_active']
    list_filter = ['is_active']
    search_fields = ['name', 'short_name', 'inn']
    readonly_fields = ['created_at', 'updated_at']
    fieldsets = (
        ('Основные реквизиты', {
            'fields': ('name', 'short_name', 'inn', 'kpp', 'ogrn')
        }),
        ('Адреса', {
            'fields': ('legal_address', 'actual_address')
        }),
        ('Банковские реквизиты', {
            'fields': ('bank_name', 'bank_bik', 'bank_account', 'bank_corr_account')
        }),
        ('Контакты', {
            'fields': ('phone', 'email', 'website')
        }),
        ('Руководство', {
            'fields': ('chairman', 'accountant')
        }),
        ('Статус', {
            'fields': ('is_active',)
        }),
        ('Служебная информация', {
            'fields': ('created_at', 'updated_at')
        }),
    )


@admin.register(OrganizationMembership)
class OrganizationMembershipAdmin(admin.ModelAdmin):
    list_display = ['owner', 'organization', 'status', 'member_since']
    list_filter = ['organization', 'status']
    search_fields = ['owner__full_name', 'member_card_number']
    raw_id_fields = ['owner', 'organization']