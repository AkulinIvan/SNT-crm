from functools import wraps
import logging
import traceback
from typing import Optional, Callable
from django.shortcuts import redirect
from django.contrib import messages
from django.urls import reverse
from django.core.exceptions import PermissionDenied
from django.utils import timezone

logger = logging.getLogger(__name__)


def subscription_required(feature: Optional[str] = None, 
                         check_limits: bool = False, 
                         redirect_url: str = 'subscription_plans'):
    """
    Декоратор для проверки наличия подписки на функцию и лимитов.
    
    Args:
        feature: название функции ('map', 'payments', 'bank_import', 'export', 'assessments')
        check_limits: проверить лимиты (владельцы, участки)
        redirect_url: URL для редиректа при отсутствии подписки
        
    Returns:
        decorated function
    """
    def decorator(view_func: Callable) -> Callable:
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            # Логируем вызов
            logger.info(f"Checking subscription access for user {request.user.id}, feature={feature}, check_limits={check_limits}")
            logger.debug(f"View function: {view_func.__name__}, URL: {request.path}")
            
            try:
                # Суперпользователи и админы имеют полный доступ
                if request.user.is_superuser or request.user.is_admin:
                    logger.info(f"Admin access granted for user {request.user.id}")
                    return view_func(request, *args, **kwargs)
                
                # Проверяем аутентификацию
                if not request.user.is_authenticated:
                    logger.warning("User not authenticated, redirecting to login")
                    messages.warning(request, 'Необходимо авторизоваться')
                    return redirect('login')
                
                # Проверяем наличие организации у пользователя
                if not hasattr(request, 'current_organization') or not request.current_organization:
                    logger.warning(f"User {request.user.id} has no organization")
                    messages.error(request, 'Организация не найдена. Пожалуйста, обратитесь к администратору.')
                    return redirect(redirect_url)
                
                org = request.current_organization
                logger.debug(f"Organization: id={org.id}, name={org.name}")
                
                # Получаем подписку организации
                try:
                    subscription = getattr(org, 'subscription', None)
                    
                    # Если нет подписки или она неактивна
                    if not subscription:
                        logger.warning(f"No subscription found for organization {org.id}")
                        messages.warning(request, 'Для доступа к этому разделу необходимо оформить подписку')
                        return redirect(redirect_url)
                    
                    if not subscription.is_active:
                        logger.warning(f"Subscription {subscription.id} is not active for organization {org.id}")
                        
                        # Проверяем, не истекла ли подписка
                        
                        if subscription.end_date and subscription.end_date < timezone.now():
                            messages.warning(
                                request, 
                                f'Срок действия подписки "{subscription.tariff.name}" истек {subscription.end_date.strftime("%d.%m.%Y")}. '
                                f'Пожалуйста, продлите подписку для доступа к функциям.'
                            )
                        else:
                            messages.warning(request, 'Подписка неактивна. Пожалуйста, активируйте подписку.')
                        
                        return redirect(redirect_url)
                    
                    tariff = subscription.tariff
                    logger.debug(f"Tariff: {tariff.name}, slug: {tariff.slug}")
                    
                except Exception as e:
                    logger.error(f"Error getting subscription/tariff: {e}\n{traceback.format_exc()}")
                    messages.error(request, 'Ошибка при проверке подписки. Пожалуйста, попробуйте позже.')
                    return redirect(redirect_url)
                
                # Проверка лимитов
                if check_limits:
                    try:
                        # Проверка лимита владельцев
                        owners_count = org.owners_count
                        if owners_count >= tariff.max_owners:
                            logger.warning(
                                f"Owner limit exceeded: {owners_count}/{tariff.max_owners} "
                                f"for organization {org.id}"
                            )
                            messages.warning(
                                request,
                                f'Достигнут лимит владельцев ({owners_count}/{tariff.max_owners}). '
                                f'Перейдите на более высокий тариф для добавления новых владельцев.'
                            )
                            return redirect(redirect_url)
                        
                        # Проверка лимита участков
                        plots_count = org.plots_count
                        if plots_count >= tariff.max_plots:
                            logger.warning(
                                f"Plot limit exceeded: {plots_count}/{tariff.max_plots} "
                                f"for organization {org.id}"
                            )
                            messages.warning(
                                request,
                                f'Достигнут лимит участков ({plots_count}/{tariff.max_plots}). '
                                f'Перейдите на более высокий тариф для добавления новых участков.'
                            )
                            return redirect(redirect_url)
                        
                        logger.debug(f"Limits OK: owners={owners_count}/{tariff.max_owners}, plots={plots_count}/{tariff.max_plots}")
                        
                    except Exception as e:
                        logger.error(f"Error checking limits: {e}\n{traceback.format_exc()}")
                        # Продолжаем, даже если не удалось проверить лимиты
                
                # Проверяем конкретную функцию
                if feature:
                    try:
                        has_access = False
                        
                        # Карта соответствия функций и полей тарифа
                        feature_map = {
                            'map': tariff.can_view_map,
                            'payments': tariff.can_manage_payments,
                            'bank_import': tariff.can_import_bank,
                            'export': tariff.can_export_data,
                            'assessments': tariff.can_manage_assessments,
                        }
                        
                        has_access = feature_map.get(feature, False)
                        
                        if not has_access:
                            logger.warning(
                                f"Feature '{feature}' not available for tariff '{tariff.name}' "
                                f"(organization {org.id})"
                            )
                            messages.warning(
                                request, 
                                f'Функция "{feature}" недоступна в вашем тарифном плане "{tariff.name}". '
                                f'Перейдите на более высокий тариф для доступа.'
                            )
                            return redirect(redirect_url)
                        
                        logger.info(f"Feature '{feature}' access granted for organization {org.id}")
                        
                    except Exception as e:
                        logger.error(f"Error checking feature access: {e}\n{traceback.format_exc()}")
                        messages.error(request, 'Ошибка при проверке доступа к функции')
                        return redirect(redirect_url)
                
                # Логируем использование функции
                if subscription and feature:
                    try:
                        from .models import SubscriptionFeature
                        SubscriptionFeature.objects.create(
                            subscription=subscription,
                            feature_name=feature,
                            ip_address=request.META.get('REMOTE_ADDR', ''),
                            user_agent=request.META.get('HTTP_USER_AGENT', '')[:255],
                            created_at=timezone.now()
                        )
                        logger.debug(f"Feature usage logged for subscription {subscription.id}")
                    except Exception as e:
                        logger.error(f"Error logging feature usage: {e}")
                        # Не блокируем доступ из-за ошибки логирования
                
                return view_func(request, *args, **kwargs)
                
            except PermissionDenied:
                logger.warning(f"Permission denied for user {request.user.id}")
                raise
                
            except Exception as e:
                logger.error(f"Unexpected error in subscription decorator: {e}\n{traceback.format_exc()}")
                messages.error(request, 'Произошла ошибка при проверке подписки. Пожалуйста, попробуйте позже.')
                return redirect(redirect_url)
                
        return wrapper
    return decorator


def check_subscription_access(tariff, feature: str) -> bool:
    """
    Проверка доступа к функции для конкретного тарифа.
    
    Args:
        tariff: Объект тарифа
        feature: Название функции
        
    Returns:
        bool: Доступна ли функция
    """
    logger.debug(f"Checking subscription access for tariff {tariff.name}, feature {feature}")
    
    try:
        if not tariff:
            logger.error("Tariff is None")
            return False
        
        if not feature:
            logger.warning("Feature name is empty")
            return False
        
        access_map = {
            'map': tariff.can_view_map,
            'payments': tariff.can_manage_payments,
            'bank_import': tariff.can_import_bank,
            'export': tariff.can_export_data,
            'assessments': tariff.can_manage_assessments,
        }
        
        has_access = access_map.get(feature, False)
        
        logger.debug(f"Feature '{feature}' access: {has_access}")
        return has_access
        
    except AttributeError as e:
        logger.error(f"Tariff missing attribute for feature {feature}: {e}")
        return False
    except Exception as e:
        logger.error(f"Error checking subscription access: {e}\n{traceback.format_exc()}")
        return False


def get_subscription_info(request):
    """
    Вспомогательная функция для получения информации о подписке.
    
    Args:
        request: HTTP request object
        
    Returns:
        dict: Информация о подписке
    """
    logger.debug(f"Getting subscription info for user {request.user.id}")
    
    try:
        # Проверяем суперпользователя
        if request.user.is_superuser or request.user.is_admin:
            logger.debug("Admin user, returning full access")
            return {
                'has_subscription': True,
                'is_admin': True,
                'tariff_name': 'Administrator',
                'max_owners': 999999,
                'max_plots': 999999,
                'features': {
                    'map': True,
                    'payments': True,
                    'bank_import': True,
                    'export': True,
                    'assessments': True,
                }
            }
        
        # Получаем организацию
        if not hasattr(request, 'current_organization') or not request.current_organization:
            logger.warning(f"No organization for user {request.user.id}")
            return {
                'has_subscription': False,
                'error': 'Организация не найдена'
            }
        
        org = request.current_organization
        subscription = getattr(org, 'subscription', None)
        
        if not subscription or not subscription.is_active:
            logger.warning(f"No active subscription for organization {org.id}")
            return {
                'has_subscription': False,
                'organization_id': org.id,
                'organization_name': org.name,
            }
        
        tariff = subscription.tariff
        
        info = {
            'has_subscription': True,
            'subscription_id': subscription.id,
            'subscription_status': subscription.status,
            'organization_id': org.id,
            'organization_name': org.name,
            'tariff_id': tariff.id,
            'tariff_name': tariff.name,
            'tariff_slug': tariff.slug,
            'start_date': subscription.start_date,
            'end_date': subscription.end_date,
            'days_left': (subscription.end_date - timezone.now()).days if subscription.end_date else None,
            'max_owners': tariff.max_owners,
            'max_plots': tariff.max_plots,
            'max_users': tariff.max_users,
            'features': {
                'map': tariff.can_view_map,
                'payments': tariff.can_manage_payments,
                'bank_import': tariff.can_import_bank,
                'export': tariff.can_export_data,
                'assessments': tariff.can_manage_assessments,
            }
        }
        
        logger.debug(f"Subscription info retrieved for organization {org.id}: {tariff.name}")
        return info
        
    except Exception as e:
        logger.error(f"Error getting subscription info: {e}\n{traceback.format_exc()}")
        return {
            'has_subscription': False,
            'error': str(e)
        }


