import logging
import json
import time
from typing import Optional, Dict, Any, List
from django.utils import timezone
from django.conf import settings

import requests

from rosreestr2coord.parser import Area

logger = logging.getLogger(__name__)

# Отключаем предупреждения SSL
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class RosreestrService:
    """
    Сервис для работы с данными Росреестра через библиотеку rosreestr2coord
    """
    
    def __init__(self):
        self.session = requests.Session()
        self.session.verify = False
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'application/json',
        })
        logger.info("Сервис Росреестра инициализирован (rosreestr2coord)")
    
    def get_boundaries_from_rosreestr(self, cadastral_number: str) -> Optional[List[List[float]]]:
        """
        Получение границ участка через библиотеку rosreestr2coord
        
        Args:
            cadastral_number: Кадастровый номер участка
            
        Returns:
            Список координат [[lat, lon], ...] или None
        """
        try:
            cadastral_number = cadastral_number.strip()
            logger.info(f"Запрос границ через rosreestr2coord для: {cadastral_number}")
            
            # Создаем объект Area с правильными параметрами
            area = Area(
                code=cadastral_number,
                area_type=1,  # 1 - Объекты недвижимости (Земельные участки)
                with_log=False,  # Отключаем логирование библиотеки
                use_cache=True,  # Используем кэш
                coord_out='EPSG:4326',  # Координаты в WGS84
                timeout=10
            )
            
            # Получаем GeoJSON
            geojson_data = area.to_geojson(dumps=False)  # dumps=False возвращает dict
            
            if not geojson_data:
                logger.warning(f"Не удалось получить данные для {cadastral_number}")
                return None
            
            # Извлекаем координаты из GeoJSON
            boundaries = self._extract_coordinates_from_geojson(geojson_data)
            
            if boundaries:
                logger.info(f"Границы получены через rosreestr2coord: {len(boundaries)} точек")
                return boundaries
            else:
                logger.warning(f"Координаты не найдены в ответе для {cadastral_number}")
                return None
                
        except Exception as e:
            logger.error(f"Ошибка при запросе к rosreestr2coord: {str(e)}")
            return None
    
    def _extract_coordinates_from_geojson(self, geojson_data: Dict) -> Optional[List[List[float]]]:
        """
        Извлечение координат из GeoJSON
        
        Args:
            geojson_data: GeoJSON объект (словарь)
            
        Returns:
            Список координат [[lat, lon], ...] или None
        """
        try:
            geometry = geojson_data.get('geometry', {})
            
            coordinates = None
            
            if geometry.get('type') == 'Polygon':
                coordinates = geometry.get('coordinates', [[]])[0]
            elif geometry.get('type') == 'MultiPolygon':
                coordinates = geometry.get('coordinates', [[[[]]]])[0][0]
            elif geometry.get('type') == 'Point':
                logger.info("Получена точка, а не полигон")
                return None
            
            if not coordinates or len(coordinates) < 3:
                return None
            
            # Конвертируем [lon, lat] в [lat, lon]
            boundaries = []
            for point in coordinates:
                if len(point) >= 2:
                    lon, lat = float(point[0]), float(point[1])
                    boundaries.append([lat, lon])
            
            # Нормализуем границы
            boundaries = self.normalize_boundaries(boundaries)
            
            return boundaries
            
        except Exception as e:
            logger.error(f"Ошибка извлечения координат из GeoJSON: {str(e)}")
            return None
    
    def get_boundaries_from_nspd_direct(self, cadastral_number: str) -> Optional[List[List[float]]]:
        """
        Прямой запрос к API НСПД (резервный вариант)
        """
        cadastral_number = cadastral_number.strip()
        logger.info(f"Прямой запрос к НСПД для: {cadastral_number}")
        
        url = "https://nspd.gov.ru/api/geoportal/search"
        
        params = {
            'text': cadastral_number,
            'limit': 1,
            'tolerance': 2,
        }
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': 'https://nspd.gov.ru/',
            'Accept': 'application/json',
        }
        
        try:
            response = self.session.get(url, params=params, headers=headers, timeout=15)
            response.raise_for_status()
            
            data = response.json()
            features = data.get('features', [])
            
            if not features:
                logger.warning(f"Объект не найден в НСПД: {cadastral_number}")
                return None
            
            feature = features[0]
            geometry = feature.get('geometry', {})
            
            if not geometry:
                return None
            
            return self._extract_nspd_coordinates(geometry)
            
        except Exception as e:
            logger.error(f"Ошибка прямого запроса к НСПД: {str(e)}")
            return None
    
    def _extract_nspd_coordinates(self, geometry: Dict) -> Optional[List[List[float]]]:
        """Извлечение координат из геометрии НСПД"""
        try:
            coords = None
            
            if geometry.get('type') == 'Polygon':
                coords = geometry.get('coordinates', [[]])[0]
            elif geometry.get('type') == 'MultiPolygon':
                coords = geometry.get('coordinates', [[[[]]]])[0][0]
            else:
                return None
            
            if not coords or len(coords) < 3:
                return None
            
            boundaries = []
            for point in coords:
                if len(point) >= 2:
                    lon, lat = float(point[0]), float(point[1])
                    boundaries.append([lat, lon])
            
            boundaries = self.normalize_boundaries(boundaries)
            return boundaries
            
        except Exception as e:
            logger.error(f"Ошибка извлечения координат: {str(e)}")
            return None
    
    def normalize_boundaries(self, boundaries: List[List[float]]) -> List[List[float]]:
        """
        Нормализация границ участка:
        1. Замыкание полигона (первая точка = последняя)
        2. Удаление дубликатов подряд
        3. Проверка минимального количества точек
        """
        if not boundaries or len(boundaries) < 3:
            return boundaries
        
        # Удаляем дублирующиеся подряд точки
        unique = []
        for i, point in enumerate(boundaries):
            if i > 0 and point == boundaries[i-1]:
                continue
            unique.append(point)
        
        if len(unique) < 3:
            return boundaries
        
        # Замыкаем полигон
        if unique[0] != unique[-1]:
            unique.append(unique[0])
        
        return unique
    
    def get_parcel_boundaries(self, cadastral_number: str) -> Optional[List[List[float]]]:
        """
        Получение границ участка.
        Пробует rosreestr2coord, затем прямой запрос к НСПД.
        """
        cadastral_number = cadastral_number.strip()
        logger.info(f"Получение границ для: {cadastral_number}")
        
        # Пробуем rosreestr2coord
        boundaries = self.get_boundaries_from_rosreestr(cadastral_number)
        if boundaries:
            logger.info(f"Границы получены через rosreestr2coord: {len(boundaries)} точек")
            return boundaries
        
        # Пробуем прямой запрос к НСПД
        boundaries = self.get_boundaries_from_nspd_direct(cadastral_number)
        if boundaries:
            logger.info(f"Границы получены через прямой запрос: {len(boundaries)} точек")
            return boundaries
        
        logger.warning(f"Границы не найдены для {cadastral_number}")
        return None
    
    def get_parcel_by_coordinates(self, lat: float, lon: float) -> Optional[Dict]:
        """Поиск участка по координатам"""
        return None
    
    def enrich_plot_info(self, plot) -> Dict[str, Any]:
        """Обогащение информации об участке"""
        result = {
            'cadastral_number': plot.cadastral_number,
            'has_boundaries': False,
            'boundaries': None,
        }
        
        if not plot.cadastral_number:
            return result
        
        boundaries = self.get_parcel_boundaries(plot.cadastral_number)
        if boundaries:
            result['has_boundaries'] = True
            result['boundaries'] = boundaries
        
        return result
    
    def batch_load_boundaries(self, plots, delay: float = 1.0) -> Dict:
        """Пакетная загрузка границ"""
        plots_list = list(plots)
        total = len(plots_list)
        
        results = {
            'total': total,
            'success': 0,
            'failed': 0,
            'details': []
        }
        
        for i, plot in enumerate(plots_list):
            logger.info(f"[{i+1}/{total}] {plot.plot_number} ({plot.cadastral_number})")
            
            try:
                enriched = self.enrich_plot_info(plot)
                
                if enriched['has_boundaries']:
                    plot.boundaries = enriched['boundaries']
                    plot.rosreestr_updated = timezone.now()
                    plot.save(update_fields=['boundaries', 'rosreestr_updated', 'updated_at'])
                    
                    results['success'] += 1
                    results['details'].append({
                        'id': plot.id,
                        'plot_number': plot.plot_number,
                        'status': 'success',
                        'points': len(enriched['boundaries'])
                    })
                    logger.info(f"  ✓ {len(enriched['boundaries'])} точек")
                else:
                    results['failed'] += 1
                    results['details'].append({
                        'id': plot.id,
                        'plot_number': plot.plot_number,
                        'status': 'not_found'
                    })
                    logger.info(f"  ✗ Не найдено")
                
                if i < total - 1:
                    time.sleep(delay)
                    
            except Exception as e:
                results['failed'] += 1
                results['details'].append({
                    'id': plot.id,
                    'plot_number': plot.plot_number,
                    'status': 'error',
                    'message': str(e)[:200]
                })
                logger.error(f"  ✗ {str(e)[:100]}")
        
        logger.info(f"Готово: {results['success']}/{results['total']}")
        return results


# Экземпляр сервиса
rosreestr_service = RosreestrService()