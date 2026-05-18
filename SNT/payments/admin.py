# payments/admin.py

from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html
from django.db.models import Sum, Count, Q
from django.utils import timezone
from .models import (
    PaymentCategory, PaymentPeriod, Assessment,
    Payment, BankStatement, BankTransaction,
    ReceiptTemplate, ReceiptTemplateLine,
    ConsolidatedAssessment, ConsolidatedAssessmentLine
)


# ============================================================
# INLINE МОДЕЛИ
# ============================================================

class PaymentInline(admin.TabularInline):
    model = Payment
    extra = 0
    fields = ['amount', 'payment_date', 'payment_method', 'status', 'matched_uid']
    readonly_fields = ['matched_uid']
    show_change_link = True
    can_delete = True


class ReceiptTemplateLineInline(admin.TabularInline):
    model = ReceiptTemplateLine
    extra = 1
    fields = ['category', 'calc_type', 'amount', 'auto_quantity', 'manual_quantity', 'order']
    ordering = ['order']


class ConsolidatedAssessmentLineInline(admin.TabularInline):
    model = ConsolidatedAssessmentLine
    extra = 0
    fields = ['category', 'description', 'quantity', 'unit', 'rate', 'amount', 'order']
    readonly_fields = ['amount']
    ordering = ['order']
    can_delete = False


class BankTransactionInline(admin.TabularInline):
    model = BankTransaction
    extra = 0
    fields = ['transaction_date', 'payer_name', 'amount', 'matched_uid', 'is_matched']
    readonly_fields = ['matched_uid', 'is_matched']
    show_change_link = True
    can_delete = False


# ============================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================================

def format_money(amount):
    """Форматирование денежной суммы"""
    return f"{float(amount):,.2f} ₽"


def status_colors():
    """Цвета статусов"""
    return {
        'pending': '#f57c00',
        'paid': '#2c7a47',
        'partial': '#1976d2',
        'overdue': '#d32f2f',
        'cancelled': '#9e9e9e',
        'processed': '#2c7a47',
        'rejected': '#d32f2f',
        'imported': '#f57c00',
        'error': '#d32f2f',
    }


# ============================================================
# КАТЕГОРИИ ВЗНОСОВ
# ============================================================

@admin.register(PaymentCategory)
class PaymentCategoryAdmin(admin.ModelAdmin):
    list_display = ['name', 'code', 'default_amount', 'unit', 'is_active', 'created_at']
    list_filter = ['is_active', 'unit']
    search_fields = ['name', 'code']
    readonly_fields = ['created_at']


# ============================================================
# ПЕРИОДЫ ОПЛАТЫ
# ============================================================

@admin.register(PaymentPeriod)
class PaymentPeriodAdmin(admin.ModelAdmin):
    list_display = ['__str__', 'start_date', 'end_date', 'due_date', 'is_active', 'created_at']
    list_filter = ['is_active', 'year']
    search_fields = ['description', 'year']
    readonly_fields = ['created_at']
    ordering = ['-year', '-quarter']


# ============================================================
# НАЧИСЛЕНИЯ
# ============================================================

@admin.register(Assessment)
class AssessmentAdmin(admin.ModelAdmin):
    list_display = [
        'payment_uid', 'owner_link', 'plot_number',
        'category', 'amount_display', 'paid_display',
        'debt_display', 'status_badge', 'created_at'
    ]
    list_filter = ['status', 'category', 'period']
    search_fields = ['payment_uid', 'owner__full_name', 'land_plot__plot_number', 'notes']
    readonly_fields = ['payment_uid', 'paid_amount', 'created_at', 'updated_at']
    inlines = [PaymentInline]
    actions = ['mark_as_paid', 'mark_as_cancelled']

    # ---------- list_display методы ----------

    @admin.display(description='Владелец', ordering='owner__full_name')
    def owner_link(self, obj):
        url = reverse('admin:users_owner_change', args=[obj.owner_id])
        return format_html('<a href="{}">{}</a>', url, obj.owner.full_name)

    @admin.display(description='Участок', ordering='land_plot__plot_number')
    def plot_number(self, obj):
        url = reverse('admin:land_landplot_change', args=[obj.land_plot_id])
        return format_html('<a href="{}">Уч. №{}</a>', url, obj.land_plot.plot_number)

    @admin.display(description='Начислено', ordering='amount')
    def amount_display(self, obj):
        return f"{float(obj.amount):,.2f} ₽"

    @admin.display(description='Оплачено', ordering='paid_amount')
    def paid_display(self, obj):
        return f"{float(obj.paid_amount):,.2f} ₽"

    @admin.display(description='Долг')
    def debt_display(self, obj):
        debt = float(obj.debt)
        color = 'red' if debt > 0 else 'green'
        formatted = f"{debt:,.2f} ₽"  # Форматируем ДО format_html
        return format_html(
            '<span style="color:{}; font-weight:bold;">{}</span>',
            color, formatted
        )

    @admin.display(description='Статус', ordering='status')
    def status_badge(self, obj):
        colors = status_colors()
        color = colors.get(obj.status, '#9e9e9e')
        return format_html(
            '<span style="background:{}; color:white; padding:3px 10px; border-radius:12px; font-size:0.8rem; white-space:nowrap;">{}</span>',
            color, obj.get_status_display()
        )

    # ---------- Действия ----------

    @admin.action(description='✅ Отметить как оплаченные')
    def mark_as_paid(self, request, queryset):
        updated = 0
        for assessment in queryset.filter(status__in=['pending', 'partial', 'overdue']):
            Payment.objects.create(
                assessment=assessment,
                amount=assessment.debt,
                payment_method='cash',
                notes='Массовое погашение через админку',
            )
            updated += 1
        self.message_user(request, f'Отмечено как оплаченные: {updated} начислений.')

    @admin.action(description='❌ Отменить начисления')
    def mark_as_cancelled(self, request, queryset):
        updated = queryset.filter(status__in=['pending', 'partial']).update(
            status='cancelled',
            updated_at=timezone.now()
        )
        self.message_user(request, f'Отменено: {updated} начислений.')


# ============================================================
# ПЛАТЕЖИ
# ============================================================

@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = [
        'id', 'assessment_link', 'amount_display', 'payment_date',
        'payment_method', 'status_badge', 'matched_uid'
    ]
    list_filter = ['payment_method', 'status', 'payment_date']
    search_fields = ['matched_uid', 'transaction_id', 'assessment__owner__full_name', 'payment_purpose']
    readonly_fields = ['created_at', 'updated_at']
    raw_id_fields = ['assessment']

    @admin.display(description='Начисление')
    def assessment_link(self, obj):
        url = reverse('admin:payments_assessment_change', args=[obj.assessment_id])
        return format_html(
            '<a href="{}">{} — {}</a>',
            url, obj.assessment.owner.full_name, obj.assessment.payment_uid
        )

    @admin.display(description='Сумма', ordering='amount')
    def amount_display(self, obj):
        return format_money(obj.amount)

    @admin.display(description='Статус', ordering='status')
    def status_badge(self, obj):
        colors = status_colors()
        color = colors.get(obj.status, '#9e9e9e')
        return format_html(
            '<span style="color:{}; font-weight:bold;">{}</span>',
            color, obj.get_status_display()
        )


# ============================================================
# БАНКОВСКИЕ ВЫПИСКИ
# ============================================================

@admin.register(BankStatement)
class BankStatementAdmin(admin.ModelAdmin):
    list_display = [
        'id', 'bank_name', 'statement_date',
        'total_transactions', 'matched_transactions', 'status_badge'
    ]
    list_filter = ['status', 'bank_name', 'statement_date']
    readonly_fields = ['total_transactions', 'matched_transactions', 'created_at']
    inlines = [BankTransactionInline]
    actions = ['reprocess_statement']

    @admin.display(description='Статус')
    def status_badge(self, obj):
        colors = status_colors()
        color = colors.get(obj.status, '#9e9e9e')
        return format_html(
            '<span style="color:{}; font-weight:bold;">{}</span>',
            color, obj.get_status_display()
        )

    @admin.action(description='🔄 Переобработать выписку')
    def reprocess_statement(self, request, queryset):
        from .bank_parser import BankStatementParser
        import re

        reprocessed = 0
        for statement in queryset:
            parser = BankStatementParser(statement.bank_name)
            try:
                transactions_data = parser.parse_file(statement.file_original.path)
                statement.transactions.all().delete()

                matched = 0
                for trans_data in transactions_data:
                    uid_match = re.search(
                        r'(?:UID|ID):?\s*(SNT-\d{6})',
                        trans_data.get('payment_purpose', ''),
                        re.IGNORECASE
                    )
                    uid = uid_match.group(1).upper() if uid_match else ''

                    BankTransaction.objects.create(
                        statement=statement,
                        transaction_date=trans_data['transaction_date'],
                        amount=trans_data['amount'],
                        payer_name=trans_data.get('payer_name', ''),
                        payer_account=trans_data.get('payer_account', ''),
                        payer_inn=trans_data.get('payer_inn', ''),
                        payment_purpose=trans_data.get('payment_purpose', ''),
                        matched_uid=uid,
                    )

                    if uid and Assessment.objects.filter(payment_uid=uid).exists():
                        matched += 1

                statement.total_transactions = len(transactions_data)
                statement.matched_transactions = matched
                statement.status = 'processed'
                statement.save()
                reprocessed += 1
            except Exception as e:
                statement.status = 'error'
                statement.notes = str(e)
                statement.save()

        self.message_user(request, f'Переобработано: {reprocessed} выписок.')


@admin.register(BankTransaction)
class BankTransactionAdmin(admin.ModelAdmin):
    list_display = [
        'id', 'transaction_date', 'payer_name', 'amount_display',
        'matched_uid', 'is_matched_badge', 'match_confidence'
    ]
    list_filter = ['is_matched', 'transaction_date']
    search_fields = ['payer_name', 'payment_purpose', 'matched_uid']
    readonly_fields = ['matched_uid', 'is_matched', 'match_confidence', 'created_at']

    @admin.display(description='Сумма')
    def amount_display(self, obj):
        return format_money(obj.amount)

    @admin.display(description='Сопоставлено')
    def is_matched_badge(self, obj):
        if obj.is_matched:
            return format_html('<span style="color:green;">✅ Да</span>')
        return format_html('<span style="color:red;">❌ Нет</span>')


# ============================================================
# ШАБЛОНЫ КВИТАНЦИЙ
# ============================================================

@admin.register(ReceiptTemplate)
class ReceiptTemplateAdmin(admin.ModelAdmin):
    list_display = ['name', 'is_active', 'created_at']
    list_filter = ['is_active']
    search_fields = ['name', 'description']
    readonly_fields = ['created_at']
    inlines = [ReceiptTemplateLineInline]


@admin.register(ReceiptTemplateLine)
class ReceiptTemplateLineAdmin(admin.ModelAdmin):
    list_display = ['template', 'category', 'calc_type', 'amount', 'auto_quantity', 'order']
    list_filter = ['template', 'calc_type', 'category']


# ============================================================
# СОСТАВНЫЕ НАЧИСЛЕНИЯ
# ============================================================

@admin.register(ConsolidatedAssessment)
class ConsolidatedAssessmentAdmin(admin.ModelAdmin):
    list_display = [
        'payment_uid', 'owner_link', 'plot_number',
        'total_amount_display', 'paid_display', 'debt_display',
        'status_badge', 'created_at'
    ]
    list_filter = ['status', 'period']
    search_fields = ['payment_uid', 'owner__full_name', 'land_plot__plot_number']
    readonly_fields = ['payment_uid', 'total_amount', 'paid_amount', 'created_at', 'updated_at']
    inlines = [ConsolidatedAssessmentLineInline]

    @admin.display(description='Владелец')
    def owner_link(self, obj):
        url = reverse('admin:users_owner_change', args=[obj.owner_id])
        return format_html('<a href="{}">{}</a>', url, obj.owner.full_name)

    @admin.display(description='Участок')
    def plot_number(self, obj):
        url = reverse('admin:land_landplot_change', args=[obj.land_plot_id])
        return format_html('<a href="{}">Уч. №{}</a>', url, obj.land_plot.plot_number)

    @admin.display(description='Начислено')
    def total_amount_display(self, obj):
        return format_money(obj.total_amount)

    @admin.display(description='Оплачено')
    def paid_display(self, obj):
        return format_money(obj.paid_amount)

    @admin.display(description='Долг')
    def debt_display(self, obj):
        debt = float(obj.debt)
        color = 'red' if debt > 0 else 'green'
        formatted = f"{debt:,.2f} ₽"  # Форматируем ДО format_html
        return format_html(
            '<span style="color:{}; font-weight:bold;">{}</span>',
            color, formatted
        )

    @admin.display(description='Статус', ordering='status')
    def status_badge(self, obj):
        colors = status_colors()
        color = colors.get(obj.status, '#9e9e9e')
        return format_html(
            '<span style="background:{}; color:white; padding:3px 10px; border-radius:12px; font-size:0.8rem; white-space:nowrap;">{}</span>',
            color, obj.get_status_display()
        )


@admin.register(ConsolidatedAssessmentLine)
class ConsolidatedAssessmentLineAdmin(admin.ModelAdmin):
    list_display = ['consolidated', 'category', 'description', 'quantity', 'rate', 'amount', 'order']
    list_filter = ['category']
    search_fields = ['description', 'consolidated__payment_uid']