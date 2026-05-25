# subscriptions/decorators.py
from functools import wraps
from django.shortcuts import redirect
from django.contrib import messages
from django.urls import reverse
from django.core.exceptions import PermissionDenied


def subscription_required(feature=None, check_limits=False, redirect_url='subscription_plans'):
    """
    Декоратор для проверки наличия подписки на функцию и лимитов.
    
    Args:
        feature: название функции ('map', 'payments', 'bank_import', 'export', 'assessments')
        check_limits: проверить лимиты (владельцы, участки)
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
            
            org = request.current_organization
            
            # Получаем подписку организации
            subscription = getattr(org, 'subscription', None)
            
            # Если нет подписки или она неактивна
            if not subscription or not subscription.is_active:
                messages.warning(request, 'Для доступа к этому разделу необходимо оформить подписку')
                return redirect(redirect_url)
            
            tariff = subscription.tariff
            
            # Проверка лимитов
            if check_limits:
                # Проверка лимита владельцев
                if org.owners_count >= tariff.max_owners:
                    messages.warning(
                        request,
                        f'Достигнут лимит владельцев ({org.owners_count}/{tariff.max_owners}). '
                        f'Перейдите на более высокий тариф для добавления новых владельцев.'
                    )
                    return redirect(redirect_url)
                
                # Проверка лимита участков
                if org.plots_count >= tariff.max_plots:
                    messages.warning(
                        request,
                        f'Достигнут лимит участков ({org.plots_count}/{tariff.max_plots}). '
                        f'Перейдите на более высокий тариф для добавления новых участков.'
                    )
                    return redirect(redirect_url)
            
            # Проверяем конкретную функцию
            if feature:
                has_access = False
                
                feature_map = {
                    'map': tariff.can_view_map,
                    'payments': tariff.can_manage_payments,
                    'bank_import': tariff.can_import_bank,
                    'export': tariff.can_export_data,
                    'assessments': tariff.can_manage_assessments,
                }
                
                has_access = feature_map.get(feature, False)
                
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