from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter, OrderingFilter
from django.shortcuts import render
from django.views import View

from .models import LandPlot
from .serializers import (
    LandPlotListSerializer,
    LandPlotDetailSerializer,
    LandPlotGeoSerializer,
)


class LandPlotViewSet(viewsets.ModelViewSet):
    """
    ViewSet для управления земельными участками.
    
    list      — список всех участков (краткий)
    retrieve  — детальная информация об участке
    create    — создание нового
    update    — полное обновление
    partial_update — частичное обновление
    destroy   — удаление
    
    Дополнительные action'ы:
    geo/      — получение координат всех активных участков для карты
    search/   — поиск по номеру или кадастровому номеру
    """
    queryset = LandPlot.objects.all()
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ['status']
    search_fields = ['plot_number', 'cadastral_number', 'address']
    ordering_fields = ['plot_number', 'area_sqm', 'cadastral_number']
    ordering = ['plot_number']

    def get_serializer_class(self):
        """
        Для списка — краткий сериализатор, для детального просмотра — полный,
        для geo — географический.
        """
        if self.action == 'list':
            return LandPlotListSerializer
        elif self.action == 'geo':
            return LandPlotGeoSerializer
        return LandPlotDetailSerializer

    @action(detail=False, methods=['get'], url_path='geo')
    def geo(self, request):
        """
        GET /api/plots/geo/
        
        Возвращает координаты участков, у которых заданы latitude и longitude.
        Идеально для построения карты СНТ.
        Дополнительные фильтры:
        - ?status=active — только активные участки (по умолчанию все)
        """
        status_filter = request.query_params.get('status', None)
        queryset = LandPlot.objects.filter(
            latitude__isnull=False,
            longitude__isnull=False,
        )
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        serializer = LandPlotGeoSerializer(queryset, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['post'], url_path='set-coordinates')
    def set_coordinates(self, request, pk=None):
        """
        POST /api/plots/{id}/set-coordinates/
        
        Быстрое обновление координат одного участка без полного PATCH-запроса.
        Тело запроса: {"latitude": 55.123456, "longitude": 37.654321}
        """
        land_plot = self.get_object()
        lat = request.data.get('latitude')
        lon = request.data.get('longitude')
        if lat is None or lon is None:
            return Response(
                {'detail': 'Необходимо передать latitude и longitude'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            land_plot.latitude = float(lat)
            land_plot.longitude = float(lon)
            land_plot.full_clean()
            land_plot.save(update_fields=['latitude', 'longitude', 'updated_at'])
        except Exception as e:
            return Response(
                {'detail': str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        serializer = LandPlotDetailSerializer(land_plot)
        return Response(serializer.data)

    @action(detail=False, methods=['get'], url_path='stats')
    def stats(self, request):
        """
        GET /api/plots/stats/
        
        Простейшая статистика по участкам: общее количество и по статусам.
        """
        total = LandPlot.objects.count()
        active = LandPlot.objects.filter(status='active').count()
        abandoned = LandPlot.objects.filter(status='abandoned').count()
        disputed = LandPlot.objects.filter(status='disputed').count()
        return Response({
            'total': total,
            'active': active,
            'abandoned': abandoned,
            'disputed': disputed,
        })
        

class LandPlotListView(View):
    """Страница со списком участков."""
    def get(self, request):
        return render(request, 'land/list.html', {'active_page': 'plots'})


class LandPlotDetailView(View):
    """Страница карточки участка."""
    def get(self, request, pk):
        return render(request, 'land/detail.html', {
            'active_page': 'plots',
            'plot_id': pk,
        })


class LandPlotMapView(View):
    """Страница с картой СНТ."""
    def get(self, request):
        return render(request, 'land/map.html', {'active_page': 'map'})