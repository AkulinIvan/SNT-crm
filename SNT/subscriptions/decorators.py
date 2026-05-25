# subscriptions/decorators.py
from functools import wraps
from django.shortcuts import redirect
from django.contrib import messages
from django.urls import reverse
from django.core.exceptions import PermissionDenied


def subscription_required(feature=None, redirect_url='subscription_plans'):
    """
    Декоратор для проверки наличия подписки на функцию.
    
    Args:
        feature: название функции ('map', 'payments', 'bank_import', 'export', 'assessments')
        redirect_url: URL для редиректа при отсутствии подписки
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            # Суперпользователи и админы имеют полный доступ
            if request.user.is_superuser or request.user.is_admin:
                return view_func(request, *args, **kwargs)
            
            # Проверяем наличие организации у пользователя
            if not hasattr(request, 'current_organization') or not request.current_organization:
                messages.error(request, 'Организация не найдена')
                return redirect(redirect_url)
            
            # Получаем подписку организации
            subscription = getattr(request.current_organization, 'subscription', None)
            
            # Если нет подписки или она неактивна
            if not subscription or not subscription.is_active:
                messages.warning(request, 'Для доступа к этому разделу необходимо оформить подписку')
                return redirect(redirect_url)
            
            # Проверяем конкретную функцию
            if feature:
                tariff = subscription.tariff
                has_access = False
                
                # Карта СНТ
                if feature == 'map' and tariff.can_view_map:
                    has_access = True
                # Управление платежами
                elif feature == 'payments' and tariff.can_manage_payments:
                    has_access = True
                # Импорт из банка
                elif feature == 'bank_import' and tariff.can_import_bank:
                    has_access = True
                # Экспорт данных
                elif feature == 'export' and tariff.can_export_data:
                    has_access = True
                # Управление начислениями
                elif feature == 'assessments' and tariff.can_manage_assessments:
                    has_access = True
                
                if not has_access:
                    messages.warning(
                        request, 
                        f'Функция "{feature}" недоступна в вашем тарифном плане "{tariff.name}". '
                        f'Перейдите на более высокий тариф для доступа.'
                    )
                    return redirect(redirect_url)
            
            # Логируем использование функции
            if subscription and feature:
                from .models import SubscriptionFeature
                SubscriptionFeature.objects.create(
                    subscription=subscription,
                    feature_name=feature,
                    ip_address=request.META.get('REMOTE_ADDR')
                )
            
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator


def check_subscription_access(tariff, feature):
    """
    Проверка доступа к функции для конкретного тарифа
    """
    access_map = {
        'map': tariff.can_view_map,
        'payments': tariff.can_manage_payments,
        'bank_import': tariff.can_import_bank,
        'export': tariff.can_export_data,
        'assessments': tariff.can_manage_assessments,
    }
    return access_map.get(feature, False)