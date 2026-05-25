# subscriptions/serializers.py
from rest_framework import serializers
from .models import Tariff, Subscription


class TariffSerializer(serializers.ModelSerializer):
    price_display = serializers.SerializerMethodField()
    
    class Meta:
        model = Tariff
        fields = [
            'id', 'name', 'slug', 'description', 'price', 'price_period',
            'price_display', 'can_view_map', 'can_manage_payments',
            'can_import_bank', 'can_export_data', 'can_manage_assessments',
            'max_users', 'max_owners', 'max_plots', 'order'
        ]
    
    def get_price_display(self, obj):
        period_map = {'month': 'месяц', 'year': 'год', 'once': 'разово'}
        return f"{obj.price} ₽/{period_map.get(obj.price_period, '')}"


class SubscriptionSerializer(serializers.ModelSerializer):
    tariff_name = serializers.CharField(source='tariff.name', read_only=True)
    tariff_details = TariffSerializer(source='tariff', read_only=True)
    is_active = serializers.BooleanField(read_only=True)
    days_left = serializers.IntegerField(read_only=True)
    
    class Meta:
        model = Subscription
        fields = [
            'id', 'tariff', 'tariff_name', 'tariff_details', 'status',
            'start_date', 'end_date', 'is_active', 'days_left',
            'auto_renew', 'created_at'
        ]