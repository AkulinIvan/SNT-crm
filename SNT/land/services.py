import logging
import json
import time
from typing import Optional, Dict, Any, List
from django.utils import timezone
from django.conf import settings
from django.core.cache import cache

import requests
from requests.exceptions import (
    RequestException, Timeout, ConnectionError, 
    HTTPError, TooManyRedirects
)

from rosreestr2coord.parser import Area

logger = logging.getLogger(__name__)

# Отключаем предупреждения SSL
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class RosreestrServiceError(Exception):
    """Базовое исключение сервиса Росреестра"""
    pass


class RosreestrTimeoutError(RosreestrServiceError):
    """Таймаут запроса к Росреестру"""
    pass


class RosreestrNotFoundError(RosreestrServiceError):
    """Данные не найдены в Росреестре"""
    pass


class RosreestrService:
    """
    Сервис для работы с данными Росреестра через библиотеку rosreestr2coord.
    
    Использует несколько источников:
    1. rosreestr2coord (основной)
    2. Прямой запрос к API НСПД (резервный)
    
    Результаты кэшируются на 24 часа.
    """
    
    def __init__(self):
        self.session = requests.Session()
        self.session.verify = False
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'application/json',
        })
        # Настройка таймаутов
        self.default_timeout = 15
        self.cache_timeout = 60 * 60 * 24  # 24 часа
        logger.info("Rosreestr service initialized")
    
    def get_parcel_boundaries(self, cadastral_number: str) -> Optional[List[List[float]]]:
        """
        Получение границ участка.
        
        Args:
            cadastral_number: Кадастровый номер участка
            
        Returns:
            Список координат [[lat, lon], ...] или None
            
        Raises:
            RosreestrTimeoutError: при таймауте
            RosreestrNotFoundError: если данные не найдены
        """
        cadastral_number = cadastral_number.strip()
        logger.info(f"Getting boundaries for: {cadastral_number}")
        
        # Проверяем кэш
        cache_key = f"rosreestr_boundaries:{cadastral_number}"
        cached = cache.get(cache_key)
        if cached:
            logger.debug(f"Cache hit for {cadastral_number}")
            return cached
        
        start_time = time.time()
        
        try:
            # Способ 1: rosreestr2coord
            boundaries = self.get_boundaries_from_rosreestr(cadastral_number)
            if boundaries:
                elapsed = time.time() - start_time
                logger.info(
                    f"Boundaries from rosreestr2coord: {len(boundaries)} points "
                    f"({elapsed:.2f}s)"
                )
                cache.set(cache_key, boundaries, self.cache_timeout)
                return boundaries
            
            # Способ 2: Прямой запрос к НСПД
            boundaries = self.get_boundaries_from_nspd_direct(cadastral_number)
            if boundaries:
                elapsed = time.time() - start_time
                logger.info(
                    f"Boundaries from NSPD: {len(boundaries)} points "
                    f"({elapsed:.2f}s)"
                )
                cache.set(cache_key, boundaries, self.cache_timeout)
                return boundaries
            
            elapsed = time.time() - start_time
            logger.warning(f"Boundaries not found for {cadastral_number} ({elapsed:.2f}s)")
            return None
            
        except Timeout:
            logger.error(f"Timeout for {cadastral_number}")
            raise RosreestrTimeoutError(f"Таймаут запроса для {cadastral_number}")
        except ConnectionError as e:
            logger.error(f"Connection error for {cadastral_number}: {e}")
            raise RosreestrServiceError(f"Ошибка соединения: {e}")
        except Exception as e:
            logger.error(f"Unexpected error for {cadastral_number}: {e}", exc_info=True)
            raise RosreestrServiceError(f"Ошибка: {e}")
    
    def get_boundaries_from_rosreestr(self, cadastral_number: str) -> Optional[List[List[float]]]:
        """
        Получение границ через библиотеку rosreestr2coord.
        """
        try:
            logger.debug(f"Requesting rosreestr2coord for: {cadastral_number}")
            
            area = Area(
                code=cadastral_number,
                area_type=1,
                with_log=False,
                use_cache=True,
                coord_out='EPSG:4326',
                timeout=10
            )
            
            geojson_data = area.to_geojson(dumps=False)
            
            if not geojson_data:
                logger.warning(f"No data from rosreestr2coord for {cadastral_number}")
                return None
            
            boundaries = self._extract_coordinates_from_geojson(geojson_data)
            
            if boundaries:
                logger.debug(
                    f"Extracted {len(boundaries)} points from GeoJSON "
                    f"for {cadastral_number}"
                )
                return boundaries
            
            logger.warning(f"No coordinates in GeoJSON for {cadastral_number}")
            return None
            
        except Exception as e:
            logger.error(f"rosreestr2coord error for {cadastral_number}: {e}")
            return None
    
    def _extract_coordinates_from_geojson(self, geojson_data: Dict) -> Optional[List[List[float]]]:
        """Извлечение координат из GeoJSON"""
        try:
            geometry = geojson_data.get('geometry', {})
            
            if geometry.get('type') == 'Polygon':
                coordinates = geometry.get('coordinates', [[]])[0]
            elif geometry.get('type') == 'MultiPolygon':
                coordinates = geometry.get('coordinates', [[[[]]]])[0][0]
            else:
                logger.debug(f"Unsupported geometry type: {geometry.get('type')}")
                return None
            
            if not coordinates or len(coordinates) < 3:
                logger.debug(f"Not enough coordinates: {len(coordinates) if coordinates else 0}")
                return None
            
            # Конвертируем [lon, lat] -> [lat, lon]
            boundaries = []
            for point in coordinates:
                if len(point) >= 2:
                    try:
                        lon, lat = float(point[0]), float(point[1])
                        # Проверяем валидность координат
                        if -90 <= lat <= 90 and -180 <= lon <= 180:
                            boundaries.append([lat, lon])
                    except (ValueError, TypeError):
                        continue
            
            if len(boundaries) < 3:
                return None
            
            return self.normalize_boundaries(boundaries)
            
        except Exception as e:
            logger.error(f"Error extracting coordinates: {e}", exc_info=True)
            return None
    
    def get_boundaries_from_nspd_direct(self, cadastral_number: str) -> Optional[List[List[float]]]:
        """Прямой запрос к API НСПД"""
        logger.debug(f"Direct NSPD request for: {cadastral_number}")
        
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
            response = self.session.get(
                url, 
                params=params, 
                headers=headers, 
                timeout=15
            )
            response.raise_for_status()
            
            data = response.json()
            features = data.get('features', [])
            
            if not features:
                logger.warning(f"Object not found in NSPD: {cadastral_number}")
                return None
            
            feature = features[0]
            geometry = feature.get('geometry', {})
            
            if not geometry:
                return None
            
            return self._extract_nspd_coordinates(geometry)
            
        except Timeout:
            logger.error(f"NSPD timeout for {cadastral_number}")
            return None
        except HTTPError as e:
            logger.error(f"NSPD HTTP error {e.response.status_code}: {e}")
            return None
        except Exception as e:
            logger.error(f"NSPD error: {e}")
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
                    try:
                        lon, lat = float(point[0]), float(point[1])
                        if -90 <= lat <= 90 and -180 <= lon <= 180:
                            boundaries.append([lat, lon])
                    except (ValueError, TypeError):
                        continue
            
            if len(boundaries) < 3:
                return None
            
            return self.normalize_boundaries(boundaries)
            
        except Exception as e:
            logger.error(f"Error extracting NSPD coordinates: {e}", exc_info=True)
            return None
    
    def normalize_boundaries(self, boundaries: List[List[float]]) -> List[List[float]]:
        """
        Нормализация границ:
        - Замыкание полигона
        - Удаление дубликатов
        - Округление координат до 7 знаков
        """
        if not boundaries or len(boundaries) < 3:
            return boundaries
        
        # Удаляем дублирующиеся подряд точки
        unique = []
        for point in boundaries:
            if not unique or point != unique[-1]:
                unique.append(point)
        
        if len(unique) < 3:
            return boundaries
        
        # Округляем координаты
        rounded = [[round(p[0], 7), round(p[1], 7)] for p in unique]
        
        # Замыкаем полигон
        if rounded[0] != rounded[-1]:
            rounded.append(rounded[0])
        
        return rounded
    
    def batch_load_boundaries(self, plots, delay: float = 1.0) -> Dict:
        """
        Пакетная загрузка границ.
        
        Args:
            plots: QuerySet или список участков
            delay: Задержка между запросами в секундах
            
        Returns:
            Dict с результатами
        """
        plots_list = list(plots)
        total = len(plots_list)
        
        logger.info(f"Batch loading boundaries for {total} plots (delay={delay}s)")
        
        results = {
            'total': total,
            'success': 0,
            'failed': 0,
            'skipped': 0,
            'details': []
        }
        
        for i, plot in enumerate(plots_list):
            logger.info(f"[{i+1}/{total}] Plot {plot.plot_number} (cad: {plot.cadastral_number})")
            
            # Пропускаем участки без кадастрового номера
            if not plot.cadastral_number:
                logger.debug(f"Skipping plot {plot.plot_number}: no cadastral number")
                results['skipped'] += 1
                results['details'].append({
                    'id': plot.id,
                    'plot_number': plot.plot_number,
                    'status': 'skipped',
                    'message': 'No cadastral number'
                })
                continue
            
            try:
                boundaries = self.get_parcel_boundaries(plot.cadastral_number)
                
                if boundaries:
                    plot.boundaries = boundaries
                    plot.rosreestr_updated = timezone.now()
                    plot.save(update_fields=['boundaries', 'rosreestr_updated', 'updated_at'])
                    
                    results['success'] += 1
                    results['details'].append({
                        'id': plot.id,
                        'plot_number': plot.plot_number,
                        'status': 'success',
                        'points': len(boundaries)
                    })
                    logger.info(f"  OK: {len(boundaries)} points")
                else:
                    results['failed'] += 1
                    results['details'].append({
                        'id': plot.id,
                        'plot_number': plot.plot_number,
                        'status': 'not_found'
                    })
                    logger.info(f"  Not found")
                
                # Задержка между запросами (кроме последнего)
                if i < total - 1:
                    time.sleep(delay)
                    
            except RosreestrTimeoutError:
                results['failed'] += 1
                results['details'].append({
                    'id': plot.id,
                    'plot_number': plot.plot_number,
                    'status': 'timeout'
                })
                logger.warning(f"  Timeout")
                time.sleep(delay * 2)  # Увеличенная задержка после таймаута
                
            except Exception as e:
                results['failed'] += 1
                results['details'].append({
                    'id': plot.id,
                    'plot_number': plot.plot_number,
                    'status': 'error',
                    'message': str(e)[:200]
                })
                logger.error(f"  Error: {str(e)[:100]}")
        
        logger.info(
            f"Batch complete: {results['success']} success, "
            f"{results['failed']} failed, {results['skipped']} skipped"
        )
        return results


# Глобальный экземпляр сервиса
rosreestr_service = RosreestrService()