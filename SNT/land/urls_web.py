from django.urls import path
from .views import LandPlotListView, LandPlotDetailView, LandPlotMapView

urlpatterns = [
    path('plots/', LandPlotListView.as_view(), name='plot-list'),
    path('plots/<int:pk>/', LandPlotDetailView.as_view(), name='plot-detail'),
    path('map/', LandPlotMapView.as_view(), name='plot-map'),
]