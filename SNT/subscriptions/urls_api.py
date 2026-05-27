# subscriptions/urls_api.py
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import TariffViewSet, SubscriptionViewSet

router = DefaultRouter()
router.register(r'tariffs', TariffViewSet, basename='tariff')
router.register(r'subscription', SubscriptionViewSet, basename='subscription')

urlpatterns = [
    path('', include(router.urls)),
    path('subscription/current/', SubscriptionViewSet.as_view({'get': 'current_subscription'}), name='subscription-current'),
    path('subscription/features/', SubscriptionViewSet.as_view({'get': 'features'}), name='subscription-features'),
    path('subscription/upgrade/', SubscriptionViewSet.as_view({'post': 'upgrade'}), name='subscription-upgrade'),
    path('subscription/check-access/', SubscriptionViewSet.as_view({'get': 'check_access'}), name='subscription-check-access'),
]