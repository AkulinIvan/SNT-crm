from typing import List

from django.db import models
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter, OrderingFilter
from django.shortcuts import render
from django.views import View
from django.db.models import Count, Q, Avg, Sum
from django.db import transaction
from django.utils import timezone
import logging
from rest_framework import permissions
from .services import rosreestr_service
from common.mixins import OrganizationMixin

from accounts.permissions import IsManagerOrAbove

from .models import LandPlot
from .serializers import (
    LandPlotListSerializer,
    LandPlotDetailSerializer,
    LandPlotGeoSerializer,
)


logger = logging.getLogger(__name__)


class LandPlotViewSet(OrganizationMixin, viewsets.ModelViewSet):
    """
    ViewSet для управления земельными участками.
    """
    queryset = LandPlot.objects.prefetch_related('ownerships__owner')
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ['status']
    search_fields = ['plot_number', 'cadastral_number', 'address']
    ordering_fields = ['plot_number', 'area_sqm', 'cadastral_number', 'created_at', 'status']
    ordering = ['plot_number']

    def get_permissions(self):
        if self.action in ('create', 'update', 'partial_update', 'destroy', 'deactivate', 'activate'):
            return [permissions.IsAuthenticated(), IsManagerOrAbove()]
        return [permissions.IsAuthenticated()]

    def get_serializer_class(self):
        if self.action == 'list':
            return LandPlotListSerializer
        elif self.action == 'geo':
            return LandPlotGeoSerializer
        return LandPlotDetailSerializer

    def get_queryset(self):
        """Расширенная фильтрация с учетом организации"""
        # Базовый queryset с фильтрацией по организации
        queryset = super().get_queryset()
        
        # Дополнительная фильтрация по наличию координат
        has_coordinates = self.request.query_params.get('has_coordinates')
        if has_coordinates is not None:
            if has_coordinates.lower() == 'true':
                queryset = queryset.filter(
                    latitude__isnull=False, 
                    longitude__isnull=False
                )
            else:
                queryset = queryset.filter(
                    Q(latitude__isnull=True) | Q(longitude__isnull=True)
                )
        
        # Фильтрация по диапазону площади
        area_min = self.request.query_params.get('area_min')
        area_max = self.request.query_params.get('area_max')
        if area_min:
            queryset = queryset.filter(area_sqm__gte=float(area_min))
        if area_max:
            queryset = queryset.filter(area_sqm__lte=float(area_max))
        
        # Поиск по владельцам
        owner_search = self.request.query_params.get('owner_search')
        if owner_search:
            queryset = queryset.filter(
                ownerships__owner__full_name__icontains=owner_search
            ).distinct()
        
        return queryset

    def perform_create(self, serializer):
        """При создании автоматически подставляем организацию"""
        if hasattr(self.request, 'current_organization') and self.request.current_organization:
            serializer.save(organization=self.request.current_organization)
        else:
            super().perform_create(serializer)
            
    def list(self, request, *args, **kwargs):
        """Расширенный list с дополнительной статистикой и фильтрацией по СНТ"""
        queryset = self.filter_queryset(self.get_queryset())

        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            response = self.get_paginated_response(serializer.data)

            # Статистика по отфильтрованным участкам
            total_area = queryset.aggregate(total=Sum('area_sqm'))['total'] or 0

            response.data['stats'] = {
                'total': queryset.count(),
                'total_area': round(total_area, 2),
                'by_status': {
                    'active': queryset.filter(status='active').count(),
                    'abandoned': queryset.filter(status='abandoned').count(),
                    'disputed': queryset.filter(status='disputed').count(),
                }
            }
            return response

        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)


    def destroy(self, request, *args, **kwargs):
        """Безопасное удаление с проверками"""
        land_plot = self.get_object()
        
        # Проверяем наличие активных начислений на участок
        try:
            from payments.models import Assessment
            has_active_assessments = Assessment.objects.filter(
                land_plot=land_plot,
                status__in=['pending', 'partial', 'overdue']
            ).exists()
        except ImportError:
            has_active_assessments = False
        
        if has_active_assessments:
            return Response(
                {
                    'detail': 'Невозможно удалить участок с неоплаченными начислениями.',
                    'code': 'has_active_assessments'
                },
                status=status.HTTP_409_CONFLICT
            )
        
        # Проверяем наличие владельцев (предупреждение, но не блокировка)
        owners_count = land_plot.ownerships.count()
        
        # Логируем удаление
        logger.warning(
            f'Удаление участка #{land_plot.plot_number} '
            f'(ID: {land_plot.id}) с {owners_count} владельцами'
        )
        
        return super().destroy(request, *args, **kwargs)

    @action(detail=False, methods=['get'], url_path='geo')
    def geo(self, request):
        """
        GET /api/plots/geo/

        Возвращает координаты участков для карты с учетом СНТ пользователя.
        """
        status_filter = request.query_params.get('status')
        has_owners = request.query_params.get('has_owners')

        # Базовый queryset с фильтрацией по организации
        queryset = LandPlot.objects.filter(
            latitude__isnull=False,
            longitude__isnull=False,
        )

        # Фильтруем по организации пользователя (если не админ)
        if not request.user.is_superuser and not request.user.is_admin:
            if hasattr(request, 'current_organization') and request.current_organization:
                queryset = queryset.filter(organization=request.current_organization)
            else:
                # Если у пользователя нет организации, возвращаем пустой результат
                return Response({
                    'count': 0,
                    'results': []
                })

        if status_filter:
            queryset = queryset.filter(status=status_filter)

        if has_owners is not None:
            queryset = queryset.annotate(
                owners_count=Count('ownerships')
            )
            if has_owners.lower() == 'true':
                queryset = queryset.filter(owners_count__gt=0)
            else:
                queryset = queryset.filter(owners_count=0)

        # Добавляем информацию о владельцах для всплывающих подсказок
        queryset = queryset.prefetch_related('ownerships__owner')

        serializer = LandPlotGeoSerializer(queryset, many=True)
        return Response({
            'count': queryset.count(),
            'results': serializer.data
        })

    @action(detail=True, methods=['post'], url_path='set-coordinates')
    def set_coordinates(self, request, pk=None):
        """
        POST /api/plots/{id}/set-coordinates/
        
        Быстрое обновление координат одного участка.
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
            lat = float(lat)
            lon = float(lon)
            
            # Валидация диапазонов
            if not (-90 <= lat <= 90):
                return Response(
                    {'detail': 'Широта должна быть от -90 до 90'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if not (-180 <= lon <= 180):
                return Response(
                    {'detail': 'Долгота должна быть от -180 до 180'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            
            land_plot.latitude = lat
            land_plot.longitude = lon
            land_plot.full_clean()
            land_plot.save(update_fields=['latitude', 'longitude', 'updated_at'])
            
            logger.info(
                f'Обновлены координаты участка #{land_plot.plot_number}: '
                f'{lat:.6f}, {lon:.6f}'
            )
            
        except Exception as e:
            logger.error(f'Ошибка обновления координат: {str(e)}')
            return Response(
                {'detail': str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        serializer = LandPlotDetailSerializer(land_plot)
        return Response(serializer.data)

    @action(detail=True, methods=['post'], url_path='load-boundaries')
    def load_boundaries(self, request, pk=None):
        """
        POST /api/plots/{id}/load-boundaries/
        
        Загрузка границ участка из Росреестра через бесплатное API ПКК
        """
        land_plot = self.get_object()
        
        if not land_plot.cadastral_number:
            return Response(
                {'detail': 'Отсутствует кадастровый номер'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            if land_plot.has_coordinates:
                boundaries = rosreestr_service.get_boundaries_from_nspd_by_coords(
                    land_plot.latitude, 
                    land_plot.longitude
                )
                if boundaries:
                    land_plot.boundaries = boundaries
                    land_plot.rosreestr_updated = timezone.now()
                    land_plot.save(update_fields=['boundaries', 'rosreestr_updated'])
                
                logger.info(
                    f'Границы загружены для участка #{land_plot.plot_number}: '
                    f'{len(enriched_data["boundaries"])} точек'
                )
                
                return Response({
                    'detail': f'Границы загружены ({len(enriched_data["boundaries"])} точек)',
                    'boundaries': enriched_data['boundaries'],
                    'updated_at': land_plot.rosreestr_updated,
                })
            else:
                # Пробуем найти по координатам
                if land_plot.has_coordinates:
                    parcel_info = rosreestr_service.get_parcel_by_coordinates(
                        land_plot.latitude, 
                        land_plot.longitude
                    )
                    if parcel_info and parcel_info.get('cadastral_number'):
                        return Response({
                            'detail': 'Границы не найдены, но найден участок поблизости',
                            'suggested_cadastral': parcel_info['cadastral_number'],
                            'parcel_info': parcel_info
                        })
                
                return Response(
                    {'detail': 'Границы не найдены в ПКК'},
                    status=status.HTTP_404_NOT_FOUND
                )
                
        except Exception as e:
            logger.error(f'Ошибка загрузки границ: {str(e)}')
            return Response(
                {'detail': f'Ошибка загрузки: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=False, methods=['post'], url_path='bulk-load-boundaries')
    def bulk_load_boundaries(self, request):
        """
        POST /api/plots/bulk-load-boundaries/
        
        Массовая загрузка границ через бесплатное API ПКК
        """
        plot_ids = request.data.get('plot_ids')
        load_all = request.data.get('load_all', False)
        delay = float(request.data.get('delay', 0.5))
        
        if load_all:
            plots = LandPlot.objects.filter(
                cadastral_number__isnull=False
            ).exclude(cadastral_number='')
        elif plot_ids:
            plots = LandPlot.objects.filter(id__in=plot_ids)
        else:
            return Response(
                {'detail': 'Укажите plot_ids или load_all=true'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Используем пакетную загрузку
        results = rosreestr_service.batch_load_boundaries(plots, delay=delay)
        
        return Response(results)
    
    @action(detail=True, methods=['get'], url_path='check-rosreestr')
    def check_rosreestr(self, request, pk=None):
        """
        GET /api/plots/{id}/check-rosreestr/
        
        Проверка наличия данных в Росреестре без сохранения
        """
        land_plot = self.get_object()
        
        result = {
            'plot_id': land_plot.id,
            'plot_number': land_plot.plot_number,
            'cadastral_number': land_plot.cadastral_number,
            'has_boundaries_in_db': land_plot.has_boundaries,
            'checks': {}
        }
        
        # Проверка по кадастровому номеру
        if land_plot.cadastral_number:
            boundaries = rosreestr_service.get_parcel_boundaries(land_plot.cadastral_number)
            result['checks']['by_cadastral'] = {
                'found': boundaries is not None,
                'points_count': len(boundaries) if boundaries else 0
            }
        
        # Проверка по координатам
        if land_plot.has_coordinates:
            parcel_info = rosreestr_service.get_parcel_by_coordinates(
                land_plot.latitude, 
                land_plot.longitude
            )
            result['checks']['by_coordinates'] = {
                'found': parcel_info is not None,
                'cadastral_number': parcel_info.get('cadastral_number') if parcel_info else None,
                'address': parcel_info.get('address') if parcel_info else None
            }
        
        return Response(result)
    
    @action(detail=True, methods=['get'], url_path='boundaries')
    def get_boundaries(self, request, pk=None):
        """
        GET /api/plots/{id}/boundaries/
        
        Получение сохраненных границ участка
        """
        land_plot = self.get_object()
        
        if not land_plot.has_boundaries:
            return Response(
                {'detail': 'Границы не загружены'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        return Response({
            'id': land_plot.id,
            'plot_number': land_plot.plot_number,
            'cadastral_number': land_plot.cadastral_number,
            'boundaries': land_plot.boundaries,
            'rosreestr_updated': land_plot.rosreestr_updated,
        })
    
    @action(detail=False, methods=['get'], url_path='all-boundaries')
    def all_boundaries(self, request):
        """
        GET /api/plots/all-boundaries/
        
        Получение всех загруженных границ для карты
        """
        plots = LandPlot.objects.filter(
            boundaries__isnull=False
        ).exclude(boundaries=[])
        
        data = []
        for plot in plots:
            data.append({
                'id': plot.id,
                'plot_number': plot.plot_number,
                'cadastral_number': plot.cadastral_number,
                'status': plot.status,
                'boundaries': plot.boundaries,
                'area_sqm': plot.area_sqm,
                'owners_count': plot.owners_count,
            })
        
        return Response({
            'count': len(data),
            'results': data,
        })
    
    @action(detail=True, methods=['post'], url_path='save-boundaries')
    def save_boundaries(self, request, pk=None):
        """
        POST /api/plots/{id}/save-boundaries/
        Ручное сохранение границ участка с автоматической нормализацией.
        """
        land_plot = self.get_object()
        boundaries = request.data.get('boundaries', [])
        source = request.data.get('source', 'manual')   

        if not boundaries or len(boundaries) < 3:
            return Response(
                {'detail': 'Минимум 3 точки для построения полигона'},
                status=status.HTTP_400_BAD_REQUEST
            )   

        # Валидация и нормализация координат
        validated = []
        for point in boundaries:
            if len(point) < 2:
                continue
            try:
                lat, lon = float(point[0]), float(point[1])
                if -90 <= lat <= 90 and -180 <= lon <= 180:
                    validated.append([lat, lon])
            except (ValueError, TypeError):
                continue    

        if len(validated) < 3:
            return Response(
                {'detail': 'Недостаточно валидных координат (минимум 3 точки)'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # НОРМАЛИЗАЦИЯ: удаляем дубликаты и замыкаем полигон
        normalized = self._normalize_boundaries(validated)

        land_plot.boundaries = normalized
        land_plot.rosreestr_updated = timezone.now()
        land_plot.save(update_fields=['boundaries', 'rosreestr_updated', 'updated_at']) 

        logger.info(
            f'Границы сохранены для участка #{land_plot.plot_number}: '
            f'{len(normalized)} точек (источник: {source})'
        )   

        return Response({
            'detail': f'Границы сохранены ({len(normalized)} точек)',
            'boundaries': normalized,
            'source': source,
            'normalized': len(normalized) != len(validated)
        })

    def _normalize_boundaries(self, boundaries: List) -> List:
        """Нормализация границ: замыкание и удаление дубликатов"""
        if not boundaries or len(boundaries) < 3:
            return boundaries
        
        # Удаляем дублирующиеся подряд точки
        unique = []
        for point in boundaries:
            if not unique or point != unique[-1]:
                unique.append(point)
        
        # Замыкаем полигон если нужно
        if len(unique) >= 3 and unique[0] != unique[-1]:
            unique.append(unique[0])
        
        return unique
    
    @action(detail=False, methods=['get'], url_path='stats')
    def stats(self, request):
        """
        GET /api/plots/stats/

        Расширенная статистика по участкам с учетом СНТ пользователя.
        """
        # Базовый queryset с фильтрацией по организации
        queryset = LandPlot.objects.all()

        # Фильтруем по организации пользователя (если не админ)
        if not request.user.is_superuser and not request.user.is_admin:
            if hasattr(request, 'current_organization') and request.current_organization:
                queryset = queryset.filter(organization=request.current_organization)
            else:
                # Если у пользователя нет организации, возвращаем нулевую статистику
                return Response({
                    'total': 0,
                    'active': 0,
                    'abandoned': 0,
                    'disputed': 0,
                    'total_area': 0,
                    'with_coordinates': 0,
                    'without_coordinates': 0,
                    'with_owners': 0,
                    'without_owners': 0,
                    'average_area': 0,
                    'average_owners_per_plot': 0,
                    'last_added': None,
                })

        total = queryset.count()

        stats = {
            'total': total,
            'active': queryset.filter(status='active').count(),
            'abandoned': queryset.filter(status='abandoned').count(),
            'disputed': queryset.filter(status='disputed').count(),
            'total_area': round(queryset.aggregate(total=Sum('area_sqm'))['total'] or 0, 2),
            'with_coordinates': queryset.filter(
                latitude__isnull=False, 
                longitude__isnull=False
            ).count(),
            'without_coordinates': queryset.filter(
                Q(latitude__isnull=True) | Q(longitude__isnull=True)
            ).count(),
            'with_owners': queryset.annotate(
                owners_count=Count('ownerships')
            ).filter(owners_count__gt=0).count(),
            'without_owners': queryset.annotate(
                owners_count=Count('ownerships')
            ).filter(owners_count=0).count(),
            'average_area': round(
                queryset.aggregate(avg=Avg('area_sqm'))['avg'] or 0, 2
            ),
            'average_owners_per_plot': round(
                queryset.annotate(
                    owners_count=Count('ownerships')
                ).aggregate(avg=Avg('owners_count'))['avg'] or 0, 1
            ),
            'last_added': queryset.order_by('-created_at').first().plot_number 
                if queryset.exists() else None,
        }

        return Response(stats)

    @action(detail=False, methods=['get'], url_path='near')
    def near_plots(self, request):
        """
        GET /api/plots/near/?lat=55.75&lon=37.62&radius=0.01
        
        Поиск участков рядом с указанными координатами.
        """
        lat = request.query_params.get('lat')
        lon = request.query_params.get('lon')
        radius = float(request.query_params.get('radius', 0.01))
        
        if not lat or not lon:
            return Response(
                {'detail': 'Укажите lat и lon'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        try:
            lat = float(lat)
            lon = float(lon)
        except ValueError:
            return Response(
                {'detail': 'Некорректные координаты'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        # Простой поиск по прямоугольной области
        queryset = LandPlot.objects.filter(
            latitude__isnull=False,
            longitude__isnull=False,
            latitude__gte=lat - radius,
            latitude__lte=lat + radius,
            longitude__gte=lon - radius,
            longitude__lte=lon + radius,
        )
        
        serializer = LandPlotGeoSerializer(queryset, many=True)
        return Response({
            'center': {'lat': lat, 'lon': lon},
            'radius': radius,
            'count': queryset.count(),
            'results': serializer.data
        })

    @action(detail=False, methods=['get'], url_path='export')
    def export_plots(self, request):
        """
        GET /api/plots/export/?format=csv
        
        Экспорт списка участков в CSV.
        """
        import csv
        from django.http import HttpResponse
        
        plots = self.filter_queryset(self.get_queryset())
        
        response = HttpResponse(content_type='text/csv')
        timestamp = timezone.now().strftime('%Y%m%d_%H%M%S')
        response['Content-Disposition'] = f'attachment; filename="land_plots_{timestamp}.csv"'
        
        writer = csv.writer(response)
        writer.writerow([
            'ID', 'Номер участка', 'Кадастровый номер', 'Площадь (м²)',
            'Адрес', 'Статус', 'Широта', 'Долгота', 
            'Кол-во владельцев', 'Примечания', 'Дата добавления'
        ])
        
        for plot in plots:
            writer.writerow([
                plot.id,
                plot.plot_number,
                plot.cadastral_number,
                plot.area_sqm,
                plot.address or '',
                plot.get_status_display(),
                plot.latitude or '',
                plot.longitude or '',
                plot.ownerships.count(),
                plot.notes or '',
                plot.created_at.strftime('%d.%m.%Y'),
            ])
        
        return response

    @action(detail=False, methods=['post'], url_path='bulk-update-status')
    def bulk_update_status(self, request):
        """
        POST /api/plots/bulk-update-status/
        
        Массовое обновление статусов.
        Тело: {"plot_ids": [1,2,3], "status": "active"}
        """
        plot_ids = request.data.get('plot_ids', [])
        new_status = request.data.get('status')
        
        if not plot_ids:
            return Response(
                {'detail': 'Укажите plot_ids'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        valid_statuses = [s[0] for s in LandPlot.STATUS_CHOICES]
        if new_status not in valid_statuses:
            return Response(
                {'detail': f'Недопустимый статус. Допустимые: {", ".join(valid_statuses)}'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        updated_count = LandPlot.objects.filter(
            id__in=plot_ids
        ).update(
            status=new_status,
            updated_at=timezone.now()
        )
        
        logger.info(
            f'Массовое обновление статуса на "{new_status}": '
            f'{updated_count} участков из {len(plot_ids)}'
        )
        
        return Response({
            'detail': f'Обновлено участков: {updated_count}',
            'updated_count': updated_count,
            'requested_count': len(plot_ids),
            'new_status': new_status,
        })

    @action(detail=False, methods=['post'], url_path='bulk-set-coordinates')
    def bulk_set_coordinates(self, request):
        """
        POST /api/plots/bulk-set-coordinates/
        
        Массовое обновление координат.
        Тело: [{"id": 1, "latitude": 55.75, "longitude": 37.62}, ...]
        """
        coordinates_data = request.data
        
        if not isinstance(coordinates_data, list):
            return Response(
                {'detail': 'Ожидается массив объектов с id, latitude, longitude'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        updated = []
        errors = []
        
        with transaction.atomic():
            for item in coordinates_data:
                try:
                    plot_id = item.get('id')
                    lat = float(item.get('latitude'))
                    lon = float(item.get('longitude'))
                    
                    if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
                        errors.append({
                            'id': plot_id,
                            'error': 'Координаты вне допустимого диапазона'
                        })
                        continue
                    
                    plot = LandPlot.objects.filter(id=plot_id).first()
                    if not plot:
                        errors.append({
                            'id': plot_id,
                            'error': 'Участок не найден'
                        })
                        continue
                    
                    plot.latitude = lat
                    plot.longitude = lon
                    plot.save(update_fields=['latitude', 'longitude', 'updated_at'])
                    updated.append(plot_id)
                    
                except Exception as e:
                    errors.append({
                        'id': item.get('id'),
                        'error': str(e)
                    })
        
        logger.info(f'Массовое обновление координат: {len(updated)} успешно')
        
        return Response({
            'updated': len(updated),
            'errors': len(errors),
            'error_details': errors[:10],  # Первые 10 ошибок
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
    

class DashboardView(View):
    """Главная страница дашборда (если ещё не создана)."""
    def get(self, request):
        return render(request, 'users/dashboard.html', {'active_page': 'dashboard'})