from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import User, UserActionLog


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ('username', 'full_name', 'email', 'role', 'position', 'is_active', 'last_activity')
    list_filter = ('role', 'is_active', 'is_staff', 'is_superuser')
    search_fields = ('username', 'first_name', 'last_name', 'email', 'phone')
    ordering = ('last_name', 'first_name')
    
    fieldsets = BaseUserAdmin.fieldsets + (
        ('Дополнительная информация', {
            'fields': ('middle_name', 'phone', 'position', 'role', 'avatar', 'notes')
        }),
    )
    
    add_fieldsets = BaseUserAdmin.add_fieldsets + (
        ('Дополнительная информация', {
            'fields': ('middle_name', 'phone', 'position', 'role')
        }),
    )


@admin.register(UserActionLog)
class UserActionLogAdmin(admin.ModelAdmin):
    list_display = ('user', 'action', 'model_name', 'object_id', 'ip_address', 'created_at')
    list_filter = ('action', 'model_name', 'created_at')
    search_fields = ('user__username', 'details', 'ip_address')
    ordering = ('-created_at',)
    readonly_fields = ('user', 'action', 'model_name', 'object_id', 'details', 'ip_address', 'user_agent', 'created_at')