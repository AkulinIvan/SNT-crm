from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import APISecurityViewSet, health_check

router = DefaultRouter()
router.register(r'security', APISecurityViewSet, basename='api-security')

urlpatterns = [
    path('', include(router.urls)),
    path('health/', health_check, name='health-check'),
]