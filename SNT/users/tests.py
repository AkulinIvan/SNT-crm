from django.test import TestCase
from django.core.exceptions import ValidationError
from rest_framework.test import APITestCase
from rest_framework import status
from land.models import LandPlot
from .models import Owner, Ownership, ContactInfo


class OwnerModelTest(TestCase):
    """Тесты моделей владельцев и контактов."""

    def setUp(self):
        self.plot1 = LandPlot.objects.create(
            cadastral_number='50:20:0010101:001',
            plot_number='1',
            area_sqm=500.0,
        )
        self.plot2 = LandPlot.objects.create(
            cadastral_number='50:20:0010101:002',
            plot_number='2',
            area_sqm=600.0,
        )
        self.owner = Owner.objects.create(full_name='Иванов Иван Иванович')

    def test_create_owner(self):
        self.assertEqual(self.owner.full_name, 'Иванов Иван Иванович')

    def test_add_plot_via_ownership(self):
        Ownership.objects.create(
            owner=self.owner,
            land_plot=self.plot1,
            share='1/2',
        )
        self.assertEqual(self.owner.land_plots.count(), 1)

    def test_multiple_plots(self):
        Ownership.objects.create(owner=self.owner, land_plot=self.plot1)
        Ownership.objects.create(owner=self.owner, land_plot=self.plot2)
        self.assertEqual(self.owner.land_plots.count(), 2)

    def test_primary_phone(self):
        ContactInfo.objects.create(
            owner=self.owner,
            type=ContactInfo.PHONE,
            value='+79161234567',
        )
        self.assertEqual(self.owner.primary_phone, '+79161234567')

    def test_primary_phone_returns_active_only(self):
        ContactInfo.objects.create(
            owner=self.owner,
            type=ContactInfo.PHONE,
            value='+79161111111',
            is_active=False,
        )
        ContactInfo.objects.create(
            owner=self.owner,
            type=ContactInfo.PHONE,
            value='+79162222222',
            is_active=True,
        )
        self.assertEqual(self.owner.primary_phone, '+79162222222')

    def test_contact_invalid_email(self):
        contact = ContactInfo(
            owner=self.owner,
            type=ContactInfo.EMAIL,
            value='not-an-email',
        )
        with self.assertRaises(ValidationError):
            contact.full_clean()

    def test_contact_short_phone(self):
        contact = ContactInfo(
            owner=self.owner,
            type=ContactInfo.PHONE,
            value='123',
        )
        with self.assertRaises(ValidationError):
            contact.full_clean()


class OwnerAPITest(APITestCase):
    """Тесты API владельцев."""

    def setUp(self):
        self.plot = LandPlot.objects.create(
            cadastral_number='50:20:0010101:001',
            plot_number='1',
            area_sqm=500.0,
        )
        self.owner = Owner.objects.create(full_name='Петров Пётр Петрович')
        self.list_url = '/api/owners/'
        self.detail_url = f'/api/owners/{self.owner.pk}/'

    def test_list_owners(self):
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)

    def test_retrieve_owner(self):
        response = self.client.get(self.detail_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('contacts', response.data)
        self.assertIn('ownerships', response.data)

    def test_search_owner(self):
        response = self.client.get(f'{self.list_url}?search=Петров')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)

    def test_add_plot_to_owner(self):
        url = f'{self.detail_url}add-plot/'
        data = {'land_plot_id': self.plot.pk, 'share': '1/1'}
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(self.owner.land_plots.count(), 1)

    def test_add_duplicate_plot(self):
        Ownership.objects.create(owner=self.owner, land_plot=self.plot)
        url = f'{self.detail_url}add-plot/'
        data = {'land_plot_id': self.plot.pk}
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)

    def test_remove_plot_from_owner(self):
        Ownership.objects.create(owner=self.owner, land_plot=self.plot)
        url = f'{self.detail_url}remove-plot/'
        data = {'land_plot_id': self.plot.pk}
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(self.owner.land_plots.count(), 0)

    def test_add_contact(self):
        url = f'{self.detail_url}add-contact/'
        data = {'type': 'ph', 'value': '+79161234567'}
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(self.owner.contacts.count(), 1)

    def test_deactivate_contact(self):
        contact = ContactInfo.objects.create(
            owner=self.owner,
            type='ph',
            value='+79161234567',
        )
        url = f'{self.detail_url}deactivate-contact/'
        data = {'contact_id': contact.pk}
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        contact.refresh_from_db()
        self.assertFalse(contact.is_active)

    def test_list_contacts(self):
        ContactInfo.objects.create(owner=self.owner, type='ph', value='+79161234567')
        url = f'{self.detail_url}contacts/'
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)