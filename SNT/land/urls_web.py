from django.urls import path
from .views import ExcelImportView, LandPlotListView, LandPlotDetailView, LandPlotMapView

urlpatterns = [
    path('plots/', LandPlotListView.as_view(), name='plot-list'),
    path('plots/<int:pk>/', LandPlotDetailView.as_view(), name='plot-detail'),
    path('map/', LandPlotMapView.as_view(), name='plot-map'),
    path('import/', ExcelImportView.as_view(), name='excel-import'),
]