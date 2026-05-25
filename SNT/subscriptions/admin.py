# subscriptions/admin.py
from django.contrib import admin
from .models import Tariff, Subscription, Payment, Invoice


@admin.register(Tariff)
class TariffAdmin(admin.ModelAdmin):
    list_display = ['name', 'price', 'price_period', 'is_active', 'order']
    list_filter = ['is_active', 'price_period']
    search_fields = ['name', 'slug']
    list_editable = ['order', 'is_active']


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ['organization', 'tariff', 'status', 'start_date', 'end_date', 'is_active']
    list_filter = ['status', 'tariff']
    search_fields = ['organization__name', 'organization__short_name']
    readonly_fields = ['created_at', 'updated_at']


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ['id', 'subscription', 'amount', 'status', 'payment_method', 'created_at']
    list_filter = ['status', 'payment_method']
    readonly_fields = ['created_at', 'paid_at']


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ['number', 'organization', 'amount', 'status', 'due_date', 'created_at']
    list_filter = ['status']
    search_fields = ['number', 'organization__name']