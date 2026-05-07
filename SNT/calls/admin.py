import os
from django.contrib import admin
from django.utils.html import format_html
from django.conf import settings
from .models import CallRecord


@admin.register(CallRecord)
class CallRecordAdmin(admin.ModelAdmin):
    list_display = [
        'id',
        'caller_display_admin',
        'direction_badge',
        'duration_display',
        'status_badge',
        'started_at',
        'has_recording_badge',
        'is_important',
    ]
    list_filter = ['direction', 'status', 'is_important', 'started_at']
    search_fields = [
        'caller_number', 'called_number', 'operator_note', 'tags',
        'owner__full_name', 'asterisk_uniqueid',
    ]
    readonly_fields = [
        'caller_display', 'duration_display', 'tags_list_display',
        'audio_player', 'created_at', 'updated_at',
    ]
    date_hierarchy = 'started_at'
    list_per_page = 50
    fieldsets = (
        ('Информация о звонке', {
            'fields': (
                ('caller_number', 'called_number'),
                ('direction', 'status'),
                ('started_at', 'answered_at', 'ended_at'),
                ('duration_seconds', 'duration_display'),
            )
        }),
        ('Связь с CRM', {
            'fields': ('owner', 'land_plot', 'caller_display'),
        }),
        ('Аудиозапись', {
            'fields': ('audio_file', 'audio_player'),
        }),
        ('Технические поля Asterisk', {
            'fields': ('asterisk_uniqueid', 'asterisk_channel', 'asterisk_recording_file'),
            'classes': ('collapse',),
        }),
        ('Обработка оператором', {
            'fields': ('operator_note', 'tags', 'tags_list_display', 'is_important'),
        }),
        ('Служебное', {
            'fields': ('created_at', 'updated_at'),
        }),
    )

    # --- Кастомные поля для list_display ---

    def caller_display_admin(self, obj):
        return obj.caller_display
    caller_display_admin.short_description = 'Звонящий'
    caller_display_admin.admin_order_field = 'caller_number'

    def direction_badge(self, obj):
        colors = {
            'in': 'green',
            'out': 'blue',
            'missed': 'red',
        }
        return format_html(
            '<span style="color:{}; font-weight:bold;">{}</span>',
            colors.get(obj.direction, 'black'),
            obj.get_direction_display(),
        )
    direction_badge.short_description = 'Тип'

    def status_badge(self, obj):
        colors = {
            'new': 'orange',
            'processed': 'green',
            'archived': 'gray',
        }
        return format_html(
            '<span style="color:{}; font-weight:bold;">{}</span>',
            colors.get(obj.status, 'black'),
            obj.get_status_display(),
        )
    status_badge.short_description = 'Статус'

    def has_recording_badge(self, obj):
        if obj.has_recording:
            return format_html('✅')
        return format_html('❌')
    has_recording_badge.short_description = 'Запись'

    # --- Кастомные поля для детального просмотра ---

    def audio_player(self, obj):
        if obj.audio_file:
            return format_html(
                '<audio controls>'
                '<source src="{}" type="audio/wav">'
                'Ваш браузер не поддерживает аудиоплеер.'
                '</audio>',
                obj.audio_file.url,
            )
        return 'Нет записи'
    audio_player.short_description = 'Плеер'

    def tags_list_display(self, obj):
        if obj.tags_list:
            return format_html(
                ' '.join(
                    f'<span style="background:#e0e0e0;padding:2px 8px;border-radius:10px;margin:2px;">{t}</span>'
                    for t in obj.tags_list
                )
            )
        return '—'
    tags_list_display.short_description = 'Теги'