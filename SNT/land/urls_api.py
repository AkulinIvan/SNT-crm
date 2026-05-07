from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import LandPlotViewSet

router = DefaultRouter()
router.register(r'plots', LandPlotViewSet, basename='plot')

urlpatterns = [
    path('', include(router.urls)),
]