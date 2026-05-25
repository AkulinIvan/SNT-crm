from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import TariffViewSet, SubscriptionViewSet

router = DefaultRouter()
router.register(r'tariffs', TariffViewSet, basename='tariff')
router.register(r'subscription', SubscriptionViewSet, basename='subscription')

urlpatterns = [
    path('', include(router.urls)),
    path('tariff-info/', SubscriptionViewSet.as_view({'get': 'tariff_info'}), name='tariff-info'),
]