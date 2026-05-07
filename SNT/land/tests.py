from django.test import TestCase
from django.core.exceptions import ValidationError
from rest_framework.test import APITestCase
from rest_framework import status
from .models import LandPlot


class LandPlotModelTest(TestCase):
    """Тесты модели LandPlot."""
    
    def setUp(self):
        self.plot = LandPlot.objects.create(
            cadastral_number='50:20:0010101:123',
            plot_number='42A',
            area_sqm=600.0,
            address='ул. Садовая, линия 3',
            latitude=None,
            longitude=None,
        )

    def test_create_land_plot(self):
        """Проверка создания участка."""
        self.assertEqual(self.plot.plot_number, '42A')
        self.assertEqual(self.plot.status, 'active')

    def test_str_method(self):
        """Проверка строкового представления."""
        expected = 'Уч. №42A (50:20:0010101:123)'
        self.assertEqual(str(self.plot), expected)

    def test_has_coordinates_false(self):
        """Если координаты не заданы — свойство возвращает False."""
        self.assertFalse(self.plot.has_coordinates)

    def test_has_coordinates_true(self):
        """Если координаты заданы — свойство возвращает True."""
        self.plot.latitude = 55.7558
        self.plot.longitude = 37.6173
        self.plot.save()
        self.assertTrue(self.plot.has_coordinates)

    def test_invalid_latitude(self):
        """Широта вне диапазона [-90, 90] — ошибка валидации."""
        self.plot.latitude = 100.0
        self.plot.longitude = 37.0
        with self.assertRaises(ValidationError):
            self.plot.full_clean()

    def test_invalid_longitude(self):
        """Долгота вне диапазона [-180, 180] — ошибка валидации."""
        self.plot.latitude = 55.0
        self.plot.longitude = 200.0
        with self.assertRaises(ValidationError):
            self.plot.full_clean()

    def test_constraint_both_or_none(self):
        """Нельзя задать только одну координату."""
        self.plot.latitude = 55.0
        self.plot.longitude = None
        with self.assertRaises(ValidationError):
            self.plot.full_clean()


class LandPlotAPITest(APITestCase):
    """Тесты API для участков."""
    
    def setUp(self):
        self.plot = LandPlot.objects.create(
            cadastral_number='50:20:0010101:001',
            plot_number='1',
            area_sqm=500.0,
            latitude=55.7558,
            longitude=37.6173,
        )
        self.list_url = '/api/plots/'
        self.detail_url = f'/api/plots/{self.plot.pk}/'

    def test_list_plots(self):
        """GET /api/plots/ — список участков."""
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertIn('plot_number', response.data[0])

    def test_retrieve_plot(self):
        """GET /api/plots/{id}/ — детальная информация."""
        response = self.client.get(self.detail_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['cadastral_number'], '50:20:0010101:001')

    def test_create_plot(self):
        """POST /api/plots/ — создание нового участка."""
        data = {
            'cadastral_number': '50:20:0010101:002',
            'plot_number': '2',
            'area_sqm': 650.0,
        }
        response = self.client.post(self.list_url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(LandPlot.objects.count(), 2)

    def test_unique_cadastral(self):
        """Нельзя создать два участка с одинаковым кадастровым номером."""
        data = {
            'cadastral_number': '50:20:0010101:001',
            'plot_number': '3',
            'area_sqm': 700.0,
        }
        response = self.client.post(self.list_url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_geo_list(self):
        """GET /api/plots/geo/ — только участки с координатами."""
        LandPlot.objects.create(
            cadastral_number='50:20:0010101:003',
            plot_number='3',
            area_sqm=400.0,
            latitude=None,
            longitude=None,
        )
        response = self.client.get('/api/plots/geo/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)  # только первый участок

    def test_set_coordinates(self):
        """POST /api/plots/{id}/set-coordinates/ — обновление координат."""
        url = f'{self.detail_url}set-coordinates/'
        data = {'latitude': 59.9343, 'longitude': 30.3351}
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.plot.refresh_from_db()
        self.assertAlmostEqual(self.plot.latitude, 59.9343)
        self.assertAlmostEqual(self.plot.longitude, 30.3351)

    def test_stats(self):
        """GET /api/plots/stats/ — статистика."""
        response = self.client.get('/api/plots/stats/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['total'], 1)