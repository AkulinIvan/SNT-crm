from django.urls import path
from .views import ExcelImportView, ExcelTemplateView, LandPlotListView, LandPlotDetailView, LandPlotMapView
from django.views.decorators.cache import cache_page

urlpatterns = [
    path('plots/', cache_page(300)(LandPlotListView.as_view()), name='plot-list'),
    path('plots/<int:pk>/', cache_page(300)(LandPlotDetailView.as_view()), name='plot-detail'),
    path('map/', cache_page(600)(LandPlotMapView.as_view()), name='plot-map'),
    path('import/', ExcelImportView.as_view(), name='excel-import'),
    path('import/template/', ExcelTemplateView.as_view(), name='excel-template'),
]