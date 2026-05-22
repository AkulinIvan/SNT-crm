from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import DashboardViewSet, OwnerViewSet, ContactInfoViewSet, OwnershipViewSet

router = DefaultRouter()
router.register(r'owners', OwnerViewSet, basename='owner')
router.register(r'contacts', ContactInfoViewSet, basename='contact')
router.register(r'ownerships', OwnershipViewSet, basename='ownership')
router.register(r'dashboard', DashboardViewSet, basename='dashboard')

urlpatterns = [
    path('', include(router.urls)),
]