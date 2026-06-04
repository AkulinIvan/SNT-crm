from django.contrib import admin
from django.utils.safestring import mark_safe
from .models import VotingSession, Question, AnswerOption, Ballot, Vote, VotingInvitation


class AnswerOptionInline(admin.TabularInline):
    model = AnswerOption
    extra = 2
    fields = ['text', 'order']


class QuestionInline(admin.TabularInline):
    model = Question
    extra = 1
    fields = ['title', 'description', 'question_type', 'order']
    inlines = [AnswerOptionInline]


@admin.register(VotingSession)
class VotingSessionAdmin(admin.ModelAdmin):
    list_display = ['title', 'organization', 'status', 'start_date', 'end_date', 
                    'total_eligible', 'total_voted', 'quorum_reached_display']
    list_filter = ['status', 'session_type', 'organization', 'created_at']
    search_fields = ['title', 'description']
    readonly_fields = ['total_voted', 'created_at', 'updated_at']
    fieldsets = (
        ('Основная информация', {
            'fields': ('organization', 'title', 'description', 'session_type', 'status')
        }),
        ('Даты и кворум', {
            'fields': ('start_date', 'end_date', 'quorum_percent', 'total_eligible', 'total_voted')
        }),
        ('Место и протокол', {
            'fields': ('meeting_place', 'protocol_number', 'protocol_date')
        }),
        ('Служебная информация', {
            'fields': ('created_by', 'created_at', 'updated_at')
        }),
    )
    inlines = [QuestionInline]
    
    def quorum_reached_display(self, obj):
        """Отображение достижения кворума"""
        if obj.quorum_reached:
            return mark_safe('<span style="color: green;">✓ Достигнут</span>')
        return mark_safe('<span style="color: red;">✗ Не достигнут</span>')
    
    quorum_reached_display.short_description = 'Кворум'
    
    actions = ['activate_votings', 'close_votings']
    
    def activate_votings(self, request, queryset):
        for voting in queryset.filter(status='draft'):
            voting.status = 'active'
            voting.save()
        self.message_user(request, f'Активировано {queryset.count()} голосований')
    activate_votings.short_description = 'Активировать выбранные голосования'
    
    def close_votings(self, request, queryset):
        for voting in queryset.filter(status='active'):
            voting.close_voting()
        self.message_user(request, f'Закрыто {queryset.count()} голосований')
    close_votings.short_description = 'Закрыть выбранные голосования'


@admin.register(Ballot)
class BallotAdmin(admin.ModelAdmin):
    list_display = ['id', 'voting_session', 'owner', 'status', 'submitted_at']
    list_filter = ['status', 'voting_session', 'submitted_at']
    search_fields = ['owner__full_name', 'representative_name']
    readonly_fields = ['submitted_at', 'ip_address', 'user_agent']


@admin.register(VotingInvitation)
class VotingInvitationAdmin(admin.ModelAdmin):
    list_display = ['voting_session', 'owner', 'invitation_type', 'contact_value', 'sent_at', 'opened_at']
    list_filter = ['invitation_type', 'voting_session']
    search_fields = ['owner__full_name', 'contact_value']