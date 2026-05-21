# SNT/land/services.py
import logging
import json
import time
from typing import Optional, Dict, Any, List
from django.utils import timezone
import random
import math
from pyproj import Transformer
from django.conf import settings

import requests

logger = logging.getLogger(__name__)

# Отключаем предупреждения SSL
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class KadnetAPIClient:
    """
    Клиент для работы с API Каднет (https://api.kadnet.ru)
    Предоставляет доступ к данным ФГИС ЕГРН
    """
    
    BASE_URL = "https://api.kadnet.ru/v2"
    
    def __init__(self, api_key: str = None):
        self.api_key = api_key or getattr(settings, 'KADNET_API_KEY', '')
        self.session = requests.Session()
        self.session.headers.update({
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'User-Agent': 'SNT-CRM/1.0'
        })
        self.session.verify = False
        
        if not self.api_key:
            logger.warning("API ключ Каднет не настроен! Используйте заглушку или другой источник.")
    
    def get_cadastral_info(self, cadastral_number: str) -> Optional[Dict]:
        """
        Получение информации об объекте недвижимости по кадастровому номеру
        
        Args:
            cadastral_number: Кадастровый номер (например, 77:01:0001072:1002)
            
        Returns:
            Словарь с информацией об объекте или None
        """
        if not self.api_key:
            logger.error("API ключ Каднет не настроен")
            return None
        
        url = f"{self.BASE_URL}/egrn/cadastral"
        params = {'cadastral_number': cadastral_number}
        
        try:
            logger.info(f"Запрос к Каднет: {cadastral_number}")
            response = self.session.get(url, params=params, timeout=30)
            
            if response.status_code == 200:
                data = response.json()
                logger.info(f"Данные получены для {cadastral_number}")
                return data
            elif response.status_code == 404:
                logger.warning(f"Объект не найден: {cadastral_number}")
                return None
            elif response.status_code == 402:
                logger.error("Недостаточно средств на балансе Каднет")
                return None
            elif response.status_code == 429:
                logger.warning("Превышен лимит запросов к Каднет")
                time.sleep(5)
                return None
            else:
                logger.error(f"Ошибка API Каднет: {response.status_code} - {response.text[:200]}")
                return None
                
        except Exception as e:
            logger.error(f"Ошибка запроса к Каднет: {str(e)[:200]}")
            return None
    
    def get_boundaries(self, cadastral_number: str) -> Optional[List[List[float]]]:
        """
        Получение координат границ участка
        
        Args:
            cadastral_number: Кадастровый номер
            
        Returns:
            Список координат [[lat, lon], ...] или None
        """
        info = self.get_cadastral_info(cadastral_number)
        
        if not info:
            return None
        
        return self._extract_coordinates(info)
    
    def get_by_address(self, address: str) -> Optional[List[Dict]]:
        """
        Поиск объектов по адресу
        
        Args:
            address: Адрес для поиска
            
        Returns:
            Список найденных объектов
        """
        if not self.api_key:
            return None
        
        url = f"{self.BASE_URL}/egrn/search"
        params = {'address': address}
        
        try:
            response = self.session.get(url, params=params, timeout=30)
            if response.status_code == 200:
                return response.json().get('results', [])
        except Exception as e:
            logger.error(f"Ошибка поиска по адресу: {str(e)[:200]}")
        
        return None
    
    def _extract_coordinates(self, data: Dict) -> Optional[List[List[float]]]:
        """
        Извлечение координат из ответа API
        
        Args:
            data: Ответ от API
            
        Returns:
            Список координат [[lat, lon], ...] или None
        """
        try:
            # Пробуем разные пути к координатам
            coordinates = None
            
            # Путь 1: data -> object -> geometry -> coordinates
            obj = data.get('object', data)
            geometry = obj.get('geometry', {})
            
            if 'coordinates' in geometry:
                coords = geometry['coordinates']
                
                if geometry.get('type') == 'Polygon':
                    coordinates = coords[0]  # Внешний контур
                elif geometry.get('type') == 'MultiPolygon':
                    coordinates = coords[0][0]  # Первый полигон
                else:
                    coordinates = coords
            
            # Путь 2: data -> boundaries
            elif 'boundaries' in obj:
                boundaries = obj['boundaries']
                if isinstance(boundaries, list):
                    coordinates = boundaries
            
            # Путь 3: data -> coordinates
            elif 'coordinates' in obj:
                coordinates = obj['coordinates']
            
            if coordinates:
                # Форматируем координаты в [lat, lon]
                formatted = []
                for point in coordinates:
                    if len(point) >= 2:
                        # Проверяем порядок координат
                        # Если первое значение больше 90 - это долгота
                        if abs(float(point[0])) > 90:
                            formatted.append([float(point[1]), float(point[0])])
                        else:
                            formatted.append([float(point[0]), float(point[1])])
                
                if len(formatted) >= 3:
                    return formatted
            
            return None
            
        except Exception as e:
            logger.error(f"Ошибка извлечения координат из ответа Каднет: {str(e)[:200]}")
            return None


class PKKDirectClient:
    """Клиент для прямых запросов к ПКК (запасной вариант)"""
    
    PKK_URLS = [
        "https://pkk.rosreestr.ru/api/features",
        "https://pkk5.rosreestr.ru/api/features",
    ]
    
    def __init__(self):
        self.session = requests.Session()
        self.session.verify = False
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'application/json',
            'Referer': 'https://pkk.rosreestr.ru/',
        })
    
    def get_boundaries(self, cadastral_number: str) -> Optional[List[List[float]]]:
        """Получение границ через ПКК"""
        
        params = {
            'text': cadastral_number,
            'limit': 1,
            'tolerance': 2,
        }
        
        for base_url in self.PKK_URLS:
            try:
                response = self.session.get(
                    f"{base_url}/1",
                    params=params,
                    timeout=15
                )
                
                if response.status_code == 200:
                    data = response.json()
                    if data.get('features'):
                        return self._extract_pkk_coordinates(data['features'][0])
                        
            except Exception as e:
                logger.debug(f"ПКК {base_url} недоступен: {str(e)[:100]}")
                continue
        
        return None
    
    def _extract_pkk_coordinates(self, feature: Dict) -> Optional[List[List[float]]]:
        """извлечение координат с автоматическим замыканием полигона"""
        try:
            geometry = feature.get('geometry', {})

            # Получаем координаты
            if geometry.get('type') == 'Polygon':
                coords = geometry.get('coordinates', [[]])[0]
            elif geometry.get('type') == 'MultiPolygon':
                coords = geometry.get('coordinates', [[[[]]]])[0][0]
            else:
                logger.warning(f"Неизвестный тип геометрии: {geometry.get('type')}")
                return None

            if not coords or len(coords) < 3:
                return None

            # Конвертируем координаты
            formatted = []
            for point in coords:
                if len(point) >= 2:
                    x, y = point[0], point[1]

                    # ПКК возвращает [x, y] где x - долгота, y - широта
                    # Нам нужно [широта, долгота]
                    formatted.append([float(y), float(x)])

            if len(formatted) < 3:
                return None

            # ВАЖНО: Замыкаем полигон (добавляем первую точку в конец)
            if formatted[0] != formatted[-1]:
                formatted.append(formatted[0])
                logger.info(f"Полигон замкнут: добавлена точка {formatted[0]}")

            logger.info(f"Извлечено {len(formatted)} точек границ")
            return formatted

        except Exception as e:
            logger.error(f"Ошибка извлечения координат: {str(e)}")
            return None


class RosreestrService:
    """
    Основной сервис для работы с данными Росреестра.
    Использует API Каднет как основной источник,
    ПКК как резервный.
    """
    
    def __init__(self):
        self.kadnet = KadnetAPIClient()
        self.pkk = PKKDirectClient()
        
        # Определяем доступные источники
        self.primary_source = 'kadnet' if settings.KADNET_API_KEY else 'pkk'
        logger.info(f"Сервис Росреестра инициализирован. Основной источник: {self.primary_source}")
    
    def get_parcel_boundaries(self, cadastral_number: str) -> Optional[List[List[float]]]:
        """
        Получение границ участка.
        Пробует Каднет, затем ПКК.
        """
        cadastral_number = cadastral_number.strip()
        logger.info(f"Получение границ для: {cadastral_number}")
        
        # Пробуем Каднет
        boundaries = self.kadnet.get_boundaries(cadastral_number)
        if boundaries:
            logger.info(f"Границы получены через Каднет: {len(boundaries)} точек")
            return boundaries
        
        # Пробуем ПКК
        boundaries = self.pkk.get_boundaries(cadastral_number)
        if boundaries:
            logger.info(f"Границы получены через ПКК: {len(boundaries)} точек")
            return boundaries
        
        logger.warning(f"Границы не найдены для {cadastral_number}")
        return None
    
    def get_parcel_by_coordinates(self, lat: float, lon: float) -> Optional[Dict]:
        """Поиск участка по координатам"""
        # ПКК поддерживает поиск по координатам
        params = {
            'text': f'{lon},{lat}',
            'limit': 1,
            'tolerance': 5,
        }
        
        try:
            response = self.pkk.session.get(
                f"{self.pkk.PKK_URLS[0]}/1",
                params=params,
                timeout=15
            )
            
            if response.status_code == 200:
                data = response.json()
                if data.get('features'):
                    feature = data['features'][0]
                    attrs = feature.get('attrs', {})
                    
                    return {
                        'cadastral_number': attrs.get('cn'),
                        'area': attrs.get('area_value'),
                        'address': attrs.get('address'),
                        'category': attrs.get('category_type'),
                        'permitted_use': attrs.get('permitted_use'),
                    }
        except Exception as e:
            logger.error(f"Ошибка поиска по координатам: {str(e)[:100]}")
        
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

    def get_boundaries_from_nspd_by_coords(self, lat: float, lon: float, buffer_meters: float = 50) -> Optional[List[List[float]]]:
        """
        Получение границ участка через WMS API НСПД по известным координатам.

        Args:
            lat: Широта центра участка (в градусах).
            lon: Долгота центра участка (в градусах).
            buffer_meters: Буфер вокруг точки для поиска (в метрах).

        Returns:
            Список координат границ [[lat, lon], ...] или None.
        """
        # 1. Конвертируем WGS84 (lat, lon) в EPSG:3857 (метры)
        transformer = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
        x_center, y_center = transformer.transform(lon, lat)

        # 2. Создаем BBOX (квадрат) вокруг точки
        min_x = x_center - buffer_meters
        max_x = x_center + buffer_meters
        min_y = y_center - buffer_meters
        max_y = y_center + buffer_meters
        bbox = f"{min_x},{min_y},{max_x},{max_y}"

        # 3. Параметры запроса (фиксированные)
        width = 512
        height = 512
        # Пиксель в центре запрашиваемой области
        i = width // 2
        j = height // 2

        # 4. Формируем URL
        base_url = "https://nspd.gov.ru/api/aeggis/v4/36048/wms"
        params = {
            'REQUEST': 'GetFeatureInfo',
            'QUERY_LAYERS': '36048',
            'SERVICE': 'WMS',
            'VERSION': '1.3.0',
            'FORMAT': 'image/png',
            'STYLES': '',
            'TRANSPARENT': 'true',
            'LAYERS': '36048',
            'RANDOM': str(random.random()),
            'INFO_FORMAT': 'application/json',
            'FEATURE_COUNT': '10',
            'I': str(i),
            'J': str(j),
            'WIDTH': str(width),
            'HEIGHT': str(height),
            'CRS': 'EPSG:3857',
            'BBOX': bbox,
        }

        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': 'https://nspd.gov.ru/',
        }

        try:
            logger.info(f"Запрос к WMS НСПД для координат ({lat}, {lon})")
            response = self.session.get(base_url, params=params, headers=headers, timeout=15)
            response.raise_for_status()

            data = response.json()

            # Извлекаем координаты из ответа (путь может отличаться!)
            # Обычно в ответе GetFeatureInfo есть массив features
            features = data.get('features', [])
            if not features:
                logger.warning(f"Объекты не найдены для BBOX: {bbox}")
                return None

            # Берем первый найденный объект
            feature = features[0]
            geometry = feature.get('geometry', {})

            if geometry.get('type') == 'Polygon':
                # Координаты полигона: [ [ [x1,y1], [x2,y2], ... ] ]
                coords_3857 = geometry.get('coordinates', [[]])[0]
            elif geometry.get('type') == 'MultiPolygon':
                coords_3857 = geometry.get('coordinates', [[[[]]]])[0][0]
            else:
                logger.warning(f"Неизвестный тип геометрии: {geometry.get('type')}")
                return None

            if not coords_3857:
                logger.warning("Координаты полигона не найдены в ответе")
                return None

            # Конвертируем обратно из EPSG:3857 в WGS84 (lat, lon)
            transformer_back = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)
            boundaries_wgs84 = []
            for x, y in coords_3857:
                lon_point, lat_point = transformer_back.transform(x, y)
                boundaries_wgs84.append([lat_point, lon_point])

            # Проверяем, что получили полигон (минимум 3 точки)
            if len(boundaries_wgs84) >= 3:
                logger.info(f"Границы ({len(boundaries_wgs84)} точек) получены от НСПД для ({lat}, {lon})")
                return boundaries_wgs84
            else:
                logger.warning(f"Получено недостаточно точек: {len(boundaries_wgs84)}")
                return None

        except Exception as e:
            logger.error(f"Ошибка при запросе к WMS НСПД: {str(e)}")
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
        Получение границ участка с нормализацией.
        """
        cadastral_number = cadastral_number.strip()
        logger.info(f"Получение границ для: {cadastral_number}")
        
        # Пробуем Каднет
        boundaries = self.kadnet.get_boundaries(cadastral_number)
        if boundaries:
            boundaries = self.normalize_boundaries(boundaries)
            logger.info(f"Границы получены через Каднет: {len(boundaries)} точек")
            return boundaries
        
        # Пробуем ПКК
        boundaries = self.pkk.get_boundaries(cadastral_number)
        if boundaries:
            boundaries = self.normalize_boundaries(boundaries)
            logger.info(f"Границы получены через ПКК: {len(boundaries)} точек")
            return boundaries
        
        logger.warning(f"Границы не найдены для {cadastral_number}")
        return None
    
# Экземпляр сервиса
rosreestr_service = RosreestrService()