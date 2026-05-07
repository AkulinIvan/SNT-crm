from datetime import timedelta
from django.test import TestCase
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from rest_framework.test import APITestCase
from rest_framework import status
from land.models import LandPlot
from users.models import Owner, ContactInfo
from .models import CallRecord


class CallRecordModelTest(TestCase):
    """Тесты модели CallRecord."""

    def setUp(self):
        self.owner = Owner.objects.create(full_name='Сидоров Сидор Сидорович')
        self.plot = LandPlot.objects.create(
            cadastral_number='50:20:0010101:005',
            plot_number='5',
            area_sqm=550.0,
        )
        self.call = CallRecord.objects.create(
            owner=self.owner,
            land_plot=self.plot,
            caller_number='+79161234567',
            called_number='102',
            direction='in',
            started_at=timezone.now(),
            duration_seconds=120,
        )

    def test_create_call(self):
        self.assertEqual(self.call.caller_number, '+79161234567')
        self.assertEqual(self.call.status, 'new')

    def test_caller_display(self):
        display = self.call.caller_display
        self.assertIn('Сидоров', display)
        self.assertIn('+79161234567', display)

    def test_duration_display(self):
        self.assertEqual(self.call.duration_display, '2м 0с')
        self.call.duration_seconds = 3661
        self.assertEqual(self.call.duration_display, '1ч 1м 1с')

    def test_tags(self):
        self.call.add_tag('жалоба')
        self.call.add_tag('должник')
        self.call.save()
        self.call.refresh_from_db()
        self.assertIn('должник', self.call.tags_list)
        self.assertIn('жалоба', self.call.tags_list)

    def test_remove_tag(self):
        self.call.add_tag('спам')
        self.call.save()
        self.call.remove_tag('спам')
        self.assertEqual(len(self.call.tags_list), 0)

    def test_str_method(self):
        self.assertIn('Входящий', str(self.call))
        self.assertIn('+79161234567', str(self.call))


class CallRecordAPITest(APITestCase):
    """Тесты API звонков."""

    def setUp(self):
        self.owner = Owner.objects.create(full_name='Николаев Николай')
        self.plot = LandPlot.objects.create(
            cadastral_number='50:20:0010101:006',
            plot_number='6',
            area_sqm=700.0,
        )
        self.call = CallRecord.objects.create(
            owner=self.owner,
            caller_number='+79031112233',
            direction='in',
            started_at=timezone.now(),
            duration_seconds=45,
        )
        self.list_url = '/api/calls/'
        self.detail_url = f'/api/calls/{self.call.pk}/'

    def test_list_calls(self):
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(len(response.data), 1)

    def test_filter_by_direction(self):
        response = self.client.get(f'{self.list_url}?direction=in')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(all(item['direction'] == 'in' for item in response.data))

    def test_search_by_number(self):
        response = self.client.get(f'{self.list_url}?search=9031112233')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)

    def test_add_tag(self):
        url = f'{self.detail_url}add-tag/'
        response = self.client.post(url, {'tag': 'важно'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.call.refresh_from_db()
        self.assertIn('важно', self.call.tags_list)

    def test_remove_tag(self):
        self.call.add_tag('спам')
        self.call.save()
        url = f'{self.detail_url}remove-tag/'
        response = self.client.post(url, {'tag': 'спам'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_update_operator_note(self):
        response = self.client.patch(
            self.detail_url,
            {'operator_note': 'Просил перезвонить'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.call.refresh_from_db()
        self.assertEqual(self.call.operator_note, 'Просил перезвонить')

    def test_webhook_creates_call_and_finds_owner(self):
        ContactInfo.objects.create(
            owner=self.owner,
            type='ph',
            value='+79169876543',
        )
        url = '/api/calls/webhook/hangup/'
        data = {
            'caller_number': '+79169876543',
            'called_number': '100',
            'direction': 'in',
            'started_at': timezone.now().isoformat(),
            'duration_seconds': 10,
            'asterisk_uniqueid': 'test.123',
        }
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(CallRecord.objects.filter(caller_number='+79169876543').count(), 1)
        # Проверяем, что владелец нашёлся
        new_call = CallRecord.objects.get(caller_number='+79169876543')
        self.assertEqual(new_call.owner, self.owner)

    def test_stats(self):
        response = self.client.get('/api/calls/stats/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('total', response.data)
        self.assertIn('by_direction', response.data)