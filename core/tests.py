"""Automated tests. T1-T15 match the test cases in the project report (section 7.1)."""
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .analytics import linear_forecast, platform_summary
from .channels import handle_incoming_sms, ussd_response
import io
import re

from .models import (Inventory, Medicine, Payment, Pharmacy, Reservation, Review, SearchLog, SmsLog, StockAlert,
                     StockChange, SubscriptionPayment)
from .services import expire_old_reservations
from .utils import is_valid_rw_mobile, normalize_phone

PHONE = '0788123456'
PHONE_E164 = '+250788123456'


@override_settings(AT_API_KEY='', MOMO_SUBSCRIPTION_KEY='')  # tests never touch real SMS / MoMo APIs
class BaseTestCase(TestCase):
    def setUp(self):
        cache.clear()  # reset rate limits between tests
        self.owner = User.objects.create_user(username='pharmacy', password='test-password')
        self.pharmacy = Pharmacy.objects.create(
            owner=self.owner, name='Test Pharmacy', license_no='TEST-001', district='Gasabo',
            phone='0788000000', latitude=-1.95, longitude=30.12, verified=True)
        self.medicine = Medicine.objects.create(name='Panadol', generic_name='Paracetamol')
        self.inventory = Inventory.objects.create(
            pharmacy=self.pharmacy, medicine=self.medicine, quantity=5, price='100.00')

    def make_pharmacy(self, username, license_no, verified=True, **kw):
        user = User.objects.create_user(username=username, password='test-password')
        defaults = dict(name=f'{username} pharmacy', district='Huye', phone='0788999999',
                        latitude=-2.59, longitude=29.74)
        defaults.update(kw)
        return Pharmacy.objects.create(owner=user, license_no=license_no, verified=verified, **defaults)

    def reserve(self, qty='1', phone=PHONE, consent='1', pk=None):
        data = {'phone': phone, 'quantity': qty}
        if consent:
            data['consent'] = consent
        return self.client.post(reverse('reserve', args=[pk or self.inventory.pk]), data)


class SearchTests(BaseTestCase):
    def test_homepage_renders(self):
        response = self.client.get(reverse('search'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Find what you need.')
        self.assertContains(response, 'Frequently asked questions')

    def test_T1_search_by_brand_name(self):
        response = self.client.get(reverse('search'), {'q': 'Panadol'})
        self.assertContains(response, 'Test Pharmacy')
        self.assertEqual(len(response.context['results']), 1)

    def test_T2_search_by_generic_name(self):
        response = self.client.get(reverse('search'), {'q': 'paracetamol'})
        self.assertContains(response, 'Test Pharmacy')

    def test_T3_unknown_medicine_shows_empty_message(self):
        response = self.client.get(reverse('search'), {'q': 'Nonexistium'})
        self.assertEqual(response.context['results'], [])
        self.assertContains(response, 'No pharmacy has this medicine right now')

    def test_T4_zero_quantity_hides_pharmacy(self):
        self.client.force_login(self.owner)
        self.client.post(reverse('update_stock', args=[self.inventory.pk]), {'quantity': '0', 'price': '100'})
        response = self.client.get(reverse('search'), {'q': 'Panadol'})
        self.assertEqual(response.context['results'], [])

    def test_T5_unverified_pharmacy_not_shown(self):
        hidden = self.make_pharmacy('hidden', 'TEST-002', verified=False)
        Inventory.objects.create(pharmacy=hidden, medicine=self.medicine, quantity=10, price='50')
        response = self.client.get(reverse('search'), {'q': 'Panadol'})
        names = [i.pharmacy.name for i in response.context['results']]
        self.assertEqual(names, ['Test Pharmacy'])

    def test_T6_location_sorts_by_distance(self):
        far = self.make_pharmacy('far', 'TEST-003')  # Huye, ~100 km away, but cheaper
        Inventory.objects.create(pharmacy=far, medicine=self.medicine, quantity=10, price='50')
        no_loc = self.client.get(reverse('search'), {'q': 'Panadol'})
        self.assertEqual(no_loc.context['results'][0].pharmacy, far)  # price order
        with_loc = self.client.get(reverse('search'), {'q': 'Panadol', 'lat': '-1.95', 'lng': '30.12'})
        self.assertEqual(with_loc.context['results'][0].pharmacy, self.pharmacy)
        self.assertEqual(with_loc.context['sort'], 'distance')

    def test_district_filter(self):
        far = self.make_pharmacy('far', 'TEST-003')
        Inventory.objects.create(pharmacy=far, medicine=self.medicine, quantity=10, price='50')
        response = self.client.get(reverse('search'), {'q': 'Panadol', 'district': 'Huye'})
        self.assertEqual([i.pharmacy for i in response.context['results']], [far])

    def test_search_is_logged_for_analytics(self):
        self.client.get(reverse('search'), {'q': 'Panadol', 'district': 'Gasabo'})
        self.client.get(reverse('search'), {'q': 'Nonexistium'})
        found, missing = SearchLog.objects.order_by('created_at')
        self.assertTrue(found.found)
        self.assertEqual(found.medicine, self.medicine)
        self.assertEqual(found.district, 'Gasabo')
        self.assertFalse(missing.found)


@patch('core.services.send_sms')
class ReservationTests(BaseTestCase):
    def test_T7_reserve_reduces_stock_and_sends_sms(self, send_sms):
        response = self.reserve(qty='2')
        reservation = Reservation.objects.get()
        self.inventory.refresh_from_db()
        self.assertEqual(self.inventory.quantity, 3)
        self.assertEqual(reservation.patient_phone, PHONE_E164)
        self.assertEqual(len(reservation.pickup_code), 6)
        self.assertRedirects(response, reverse('reservation_detail', args=[reservation.pk, reservation.pickup_code]))
        patient_call, pharmacy_call = send_sms.call_args_list
        self.assertEqual(patient_call.args, (
            PHONE_E164,
            f'MedFinder: Panadol x2 reserved at Test Pharmacy. '
            f'Pickup code: {reservation.pickup_code}. Total 200 RWF. Held for 2 hours.'))
        self.assertEqual(pharmacy_call.args[0], '0788000000')
        self.assertIn(f'new reservation #{reservation.pk}', pharmacy_call.args[1])

    def test_patient_can_cancel_own_reservation(self, send_sms):
        self.reserve(qty='2')
        r = Reservation.objects.get()
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('reservation_cancel', args=[r.pk, r.pickup_code]))
        r.refresh_from_db()
        self.inventory.refresh_from_db()
        self.assertEqual((r.status, r.cancel_reason), ('cancelled', 'patient'))
        self.assertEqual(self.inventory.quantity, 5)

    def test_patient_cannot_cancel_with_wrong_code(self, send_sms):
        self.reserve()
        r = Reservation.objects.get()
        self.client.post(reverse('reservation_cancel', args=[r.pk, '000000' if r.pickup_code != '000000' else '111111']))
        r.refresh_from_db()
        self.assertEqual(r.status, 'pending')

    def test_T8_reserve_more_than_available(self, send_sms):
        response = self.reserve(qty='9')
        self.assertFalse(Reservation.objects.exists())
        messages = [str(m) for m in response.wsgi_request._messages]
        self.assertIn('Only 5 left in stock. Please reduce the quantity.', messages)
        send_sms.assert_not_called()

    def test_T9_reserve_with_invalid_phone(self, send_sms):
        for bad in ['12345', 'abcdefghij', '0712345678', '07881234567']:
            self.reserve(phone=bad)
        self.assertFalse(Reservation.objects.exists())

    def test_reserve_requires_consent(self, send_sms):
        self.reserve(consent='')
        self.assertFalse(Reservation.objects.exists())

    def test_reservation_page_requires_correct_code(self, send_sms):
        self.reserve()
        r = Reservation.objects.get()
        self.assertEqual(self.client.get(reverse('reservation_detail', args=[r.pk, r.pickup_code])).status_code, 200)
        wrong = '000000' if r.pickup_code != '000000' else '111111'
        self.assertEqual(self.client.get(reverse('reservation_detail', args=[r.pk, wrong])).status_code, 404)

    def test_track_page_redirects_to_reservation(self, send_sms):
        self.reserve()
        r = Reservation.objects.get()
        response = self.client.get(reverse('track'), {'reservation': r.pk, 'code': r.pickup_code})
        self.assertRedirects(response, reverse('reservation_detail', args=[r.pk, r.pickup_code]))

    def test_reserve_is_rate_limited(self, send_sms):
        for _ in range(10):
            self.reserve(phone='abc')  # invalid but still counted
        self.assertEqual(self.reserve().status_code, 429)


@patch('core.views.send_sms')
class PharmacyActionTests(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.reservation = Reservation.objects.create(
            patient_phone=PHONE_E164, inventory=self.inventory, quantity=2, pickup_code='123456')
        self.inventory.quantity = 3
        self.inventory.save()
        self.client.force_login(self.owner)

    def action(self, name, **data):
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(reverse('reservation_action', args=[self.reservation.pk, name]), data)

    def test_ready_sends_sms_after_commit(self, send_sms):
        self.action('ready')
        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.status, 'ready')
        send_sms.assert_called_once_with(PHONE_E164, 'MedFinder: your Panadol is ready at Test Pharmacy. Code: 123456.')

    def test_pickup_with_correct_code(self, send_sms):
        self.action('ready')
        self.action('done', code='123456')
        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.status, 'done')
        self.assertIsNotNone(self.reservation.completed_at)

    def test_T10_pickup_with_wrong_code_rejected(self, send_sms):
        self.action('ready')
        self.action('done', code='999999')
        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.status, 'ready')

    def test_T11_cancel_restores_stock_and_notifies(self, send_sms):
        self.action('cancel')
        self.inventory.refresh_from_db()
        self.reservation.refresh_from_db()
        self.assertEqual(self.inventory.quantity, 5)
        self.assertEqual(self.reservation.status, 'cancelled')
        send_sms.assert_called_once_with(
            PHONE_E164, 'MedFinder: your reservation for Panadol at Test Pharmacy was cancelled.')

    def test_T13_other_pharmacy_cannot_touch_stock_or_reservations(self, send_sms):
        other = self.make_pharmacy('other', 'TEST-009')
        self.client.force_login(other.owner)
        r1 = self.client.post(reverse('update_stock', args=[self.inventory.pk]), {'quantity': '999', 'price': '1'})
        r2 = self.client.post(reverse('delete_stock', args=[self.inventory.pk]))
        r3 = self.client.post(reverse('reservation_action', args=[self.reservation.pk, 'cancel']))
        self.assertEqual((r1.status_code, r2.status_code, r3.status_code), (404, 404, 404))
        self.inventory.refresh_from_db()
        self.assertEqual(self.inventory.quantity, 3)

    def test_cannot_delete_stock_with_open_reservations(self, send_sms):
        self.client.post(reverse('delete_stock', args=[self.inventory.pk]))
        self.assertTrue(Inventory.objects.filter(pk=self.inventory.pk).exists())


@patch('core.services.send_sms')
class ExpiryTests(BaseTestCase):
    def _old_reservation(self, hours=3):
        r = Reservation.objects.create(patient_phone=PHONE_E164, inventory=self.inventory, quantity=2,
                                       pickup_code='123456')
        Reservation.objects.filter(pk=r.pk).update(created_at=timezone.now() - timedelta(hours=hours))
        self.inventory.quantity = 3
        self.inventory.save()
        return r

    def test_T12_old_reservation_expires_and_restores_stock(self, send_sms):
        r = self._old_reservation()
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(expire_old_reservations(), 1)
        r.refresh_from_db()
        self.inventory.refresh_from_db()
        self.assertEqual((r.status, r.cancel_reason), ('cancelled', 'expired'))
        self.assertEqual(self.inventory.quantity, 5)
        send_sms.assert_called_once()

    def test_recent_reservation_not_expired(self, send_sms):
        self._old_reservation(hours=1)
        self.assertEqual(expire_old_reservations(), 0)

    def test_paid_reservation_not_expired(self, send_sms):
        r = self._old_reservation()
        Payment.objects.create(reservation=r, payer_phone=PHONE_E164, amount=200, provider_reference='x',
                               status='successful')
        self.assertEqual(expire_old_reservations(), 0)

    def test_search_runs_expiry(self, send_sms):
        r = self._old_reservation()
        self.client.get(reverse('search'))
        r.refresh_from_db()
        self.assertEqual(r.status, 'cancelled')

    def test_management_command(self, send_sms):
        self._old_reservation()
        call_command('expire_reservations', stdout=open('NUL' if __import__('os').name == 'nt' else '/dev/null', 'w'))
        self.assertEqual(Reservation.objects.get().status, 'cancelled')


class AuthAndRegistrationTests(BaseTestCase):
    def test_T14_dashboard_requires_login(self):
        response = self.client.get(reverse('dashboard'))
        self.assertRedirects(response, f"{reverse('login')}?next={reverse('dashboard')}")

    def test_dashboard_renders_for_owner(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse('dashboard'))
        self.assertContains(response, 'Test Pharmacy')
        self.assertContains(response, 'Unmet demand')

    def _register(self, **overrides):
        from django.core.files.uploadedfile import SimpleUploadedFile
        data = {'username': 'newpharma', 'email': 'owner@example.rw', 'password': 'Str0ng-pass-2026', 'password_confirm': 'Str0ng-pass-2026',
                'name': 'New Pharmacy', 'license_no': 'NEW-1', 'district': 'Huye', 'address': '',
                'phone': '0788777666', 'latitude': '-2.6', 'longitude': '29.74', 'agree': 'on',
                'license_document': SimpleUploadedFile('license.pdf', b'%PDF-1.4 test license', 'application/pdf')}
        data.update(overrides)
        return self.client.post(reverse('register'), data)

    def test_registration_creates_unverified_pharmacy(self):
        import tempfile
        with tempfile.TemporaryDirectory() as media, self.settings(MEDIA_ROOT=media):
            response = self._register()
            self.assertRedirects(response, reverse('login'))
            p = Pharmacy.objects.get(license_no='NEW-1')
            self.assertFalse(p.verified)
            self.assertEqual(p.phone, '+250788777666')
            self.assertTrue(p.license_document.name.startswith('licenses/'))
            # Only administrators can open the license file.
            self.assertEqual(self.client.get(reverse('license_file', args=[p.pk])).status_code, 302)
            self.client.force_login(User.objects.create_superuser('lic-admin', '', 'pw-12345-xyz'))
            response = self.client.get(reverse('license_file', args=[p.pk]))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(b''.join(response.streaming_content), b'%PDF-1.4 test license')
            response.close()

    def test_registration_rejects_fake_license_file(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        response = self._register(license_document=SimpleUploadedFile('license.pdf', b'MZ not a pdf', 'application/pdf'))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Pharmacy.objects.filter(license_no='NEW-1').exists())

    def test_T15_duplicate_license_or_username_rejected(self):
        r1 = self._register(license_no='test-001')
        self.assertFormError(r1.context['form'], 'license_no', 'This license is already registered.')
        r2 = self._register(username='PHARMACY')
        self.assertFormError(r2.context['form'], 'username', 'This username is taken.')
        self.assertEqual(Pharmacy.objects.count(), 1)

    def test_login_is_rate_limited(self):
        for _ in range(5):
            self.client.post(reverse('login'), {'username': 'pharmacy', 'password': 'wrong'})
        response = self.client.post(reverse('login'), {'username': 'pharmacy', 'password': 'test-password'})
        self.assertEqual(response.status_code, 429)

    def test_analytics_is_staff_only(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse('analytics')).status_code, 302)
        admin = User.objects.create_superuser('boss', '', 'pw-12345-xyz')
        self.client.force_login(admin)
        self.assertEqual(self.client.get(reverse('analytics')).status_code, 200)
        self.assertEqual(self.client.get(reverse('analytics_export')).status_code, 200)


@patch('core.views.send_sms')
@patch('core.services.send_sms')
class PaymentTests(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.res = Reservation.objects.create(patient_phone=PHONE_E164, inventory=self.inventory, quantity=2,
                                              pickup_code='123456')

    def test_simulated_momo_payment_flow(self, svc_sms, view_sms):
        url = reverse('pay', args=[self.res.pk, '123456'])
        self.client.post(url, {'phone': PHONE})
        payment = Payment.objects.get()
        self.assertEqual(payment.status, 'pending')
        self.assertEqual(payment.amount, 200)
        self.assertEqual(payment.commission, 6)  # 3%
        self.client.post(reverse('payment_status', args=[self.res.pk, '123456']))
        payment.refresh_from_db()
        self.assertEqual(payment.status, 'successful')
        self.assertTrue(self.res.is_paid)
        view_sms.assert_called_once()

    @override_settings(AIRTEL_CLIENT_ID='')
    def test_airtel_number_uses_airtel_money(self, svc_sms, view_sms):
        self.client.post(reverse('pay', args=[self.res.pk, '123456']), {'phone': '0731234567'})
        payment = Payment.objects.get()
        self.assertEqual((payment.provider, payment.simulated), ('airtel', True))
        self.client.post(reverse('payment_status', args=[self.res.pk, '123456']))
        payment.refresh_from_db()
        self.assertEqual(payment.status, 'successful')

    def test_airtel_status_codes(self, svc_sms, view_sms):
        from .airtel import map_status
        self.assertEqual([map_status(c) for c in ('TS', 'TF', 'TIP', 'TA', None)],
                         ['successful', 'failed', 'pending', 'pending', 'pending'])

    def test_provider_routing(self, svc_sms, view_sms):
        from .payments import provider_for
        self.assertEqual([provider_for(p) for p in ('0788123456', '0791234567', '0721234567', '0731234567')],
                         ['mtn', 'mtn', 'airtel', 'airtel'])

    def test_cancel_paid_reservation_marks_refund(self, svc_sms, view_sms):
        Payment.objects.create(reservation=self.res, payer_phone=PHONE_E164, amount=200,
                               provider_reference='abc', status='successful')
        self.client.force_login(self.owner)
        self.client.post(reverse('reservation_action', args=[self.res.pk, 'cancel']))
        self.assertEqual(Payment.objects.get().status, 'refund_due')


@patch('core.services.send_sms')
class UssdTests(BaseTestCase):
    def test_main_menu(self, send_sms):
        self.assertTrue(ussd_response(PHONE_E164, '').startswith('CON Welcome to MedFinder'))

    def test_full_reservation_flow(self, send_sms):
        self.assertIn('Test Pharmacy', ussd_response(PHONE_E164, '1*panadol'))
        self.assertIn('Enter quantity', ussd_response(PHONE_E164, '1*panadol*1'))
        self.assertIn('Total 200 RWF', ussd_response(PHONE_E164, '1*panadol*1*2'))
        final = ussd_response(PHONE_E164, '1*panadol*1*2*1')
        self.assertTrue(final.startswith('END Reserved!'))
        r = Reservation.objects.get()
        self.assertEqual((r.source, r.quantity, r.patient_phone), ('ussd', 2, PHONE_E164))
        self.inventory.refresh_from_db()
        self.assertEqual(self.inventory.quantity, 3)
        self.assertEqual(SearchLog.objects.filter(channel='ussd').count(), 1)

    def test_check_reservation(self, send_sms):
        Reservation.objects.create(patient_phone=PHONE_E164, inventory=self.inventory, quantity=1,
                                   pickup_code='654321', status='ready')
        self.assertIn('READY', ussd_response(PHONE_E164, '2*654321'))
        self.assertIn('No reservation', ussd_response('+250788000111', '2*654321'))

    def test_kinyarwanda_menu(self, send_sms):
        self.assertIn('Shakisha umuti', ussd_response(PHONE_E164, '4'))

    def test_invalid_quantity(self, send_sms):
        self.assertIn('Invalid quantity', ussd_response(PHONE_E164, '1*panadol*1*99'))

    def test_ussd_endpoint(self, send_sms):
        response = self.client.post(reverse('ussd'), {'sessionId': 's', 'phoneNumber': PHONE_E164, 'text': ''})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.decode().startswith('CON'))

    @override_settings(AT_CALLBACK_TOKEN='secret')
    def test_ussd_callback_token(self, send_sms):
        self.assertEqual(self.client.post(reverse('ussd'), {'text': ''}).status_code, 403)
        self.assertEqual(self.client.post(reverse('ussd') + '?token=secret', {'text': ''}).status_code, 200)


class SmsCommandTests(BaseTestCase):
    def test_stock_update_by_sms(self):
        reply = handle_incoming_sms('+250788000000', 'STOCK Panadol 40 150')
        self.inventory.refresh_from_db()
        self.assertEqual((self.inventory.quantity, int(self.inventory.price)), (40, 150))
        self.assertIn('updated to 40', reply)

    def test_unknown_sender_rejected(self):
        reply = handle_incoming_sms('+250788555555', 'STOCK Panadol 40')
        self.assertIn('not linked', reply)
        self.inventory.refresh_from_db()
        self.assertEqual(self.inventory.quantity, 5)

    def test_new_medicine_needs_price(self):
        Medicine.objects.create(name='Ibuprofen 400mg', generic_name='Ibuprofen')
        self.assertIn('Send the price', handle_incoming_sms('0788000000', 'STOCK Ibuprofen 10'))
        handle_incoming_sms('0788000000', 'stock ibuprofen 10 200')
        self.assertTrue(Inventory.objects.filter(medicine__name='Ibuprofen 400mg', quantity=10).exists())

    def test_incoming_endpoint_logs_and_replies(self):
        self.client.post(reverse('sms_incoming'), {'from': '+250788000000', 'text': 'LIST'})
        self.assertTrue(SmsLog.objects.filter(direction='in').exists())
        self.assertTrue(SmsLog.objects.filter(direction='out', message__startswith='Your stock').exists())


class DashboardFeatureTests(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.owner)

    def _csv(self, text, name='stock.csv'):
        from django.core.files.uploadedfile import SimpleUploadedFile
        return SimpleUploadedFile(name, text.encode('utf-8'), content_type='text/csv')

    def test_stock_import_updates_and_creates(self):
        Medicine.objects.create(name='Ibuprofen 400mg', generic_name='Ibuprofen')
        csv_text = 'medicine,quantity,price\nPanadol,40,120\nIbuprofen 400mg,10,200\nUnknownium,5,10\n'
        self.client.post(reverse('stock_import'), {'file': self._csv(csv_text)})
        self.inventory.refresh_from_db()
        self.assertEqual((self.inventory.quantity, int(self.inventory.price)), (40, 120))
        self.assertTrue(Inventory.objects.filter(pharmacy=self.pharmacy, medicine__name='Ibuprofen 400mg').exists())

    def test_stock_import_semicolon_excel_csv(self):
        self.client.post(reverse('stock_import'), {'file': self._csv('﻿Medicine;Quantity;Price\nPanadol;7;90\n')})
        self.inventory.refresh_from_db()
        self.assertEqual(self.inventory.quantity, 7)

    def test_stock_import_rejects_non_csv(self):
        self.client.post(reverse('stock_import'), {'file': self._csv('x', name='stock.xlsx')})
        self.inventory.refresh_from_db()
        self.assertEqual(self.inventory.quantity, 5)

    def test_stock_export(self):
        response = self.client.get(reverse('stock_export'))
        self.assertIn('Panadol,5,100', response.content.decode('utf-8-sig'))

    def test_medicine_request(self):
        self.client.post(reverse('medicine_request'), {'name': 'Azithromycin 500mg'})
        self.assertEqual(self.pharmacy.medicine_requests.get().name, 'Azithromycin 500mg')

    def test_poll_reports_open_reservations(self):
        Reservation.objects.create(patient_phone=PHONE_E164, inventory=self.inventory, quantity=1, pickup_code='1')
        data = self.client.get(reverse('dashboard_poll')).json()
        self.assertEqual(data['open'], 1)

    def test_profile_saves_hours_and_email(self):
        self.client.post(reverse('pharmacy_profile'), {
            'name': 'Test Pharmacy', 'district': 'Gasabo', 'address': '', 'phone': '0788000000',
            'email': 'new@example.rw', 'opens_at': '08:00', 'closes_at': '20:00',
            'latitude': '-1.95', 'longitude': '30.12'})
        self.pharmacy.refresh_from_db()
        self.owner.refresh_from_db()
        self.assertEqual(str(self.pharmacy.opens_at), '08:00:00')
        self.assertEqual(self.owner.email, 'new@example.rw')

    def test_open_now(self):
        from datetime import time
        self.assertIsNone(self.pharmacy.is_open_now)
        self.pharmacy.open_24h = True
        self.assertTrue(self.pharmacy.is_open_now)
        self.pharmacy.open_24h, self.pharmacy.opens_at, self.pharmacy.closes_at = False, time(0, 0), time(0, 1)
        self.assertIn(self.pharmacy.is_open_now, (True, False))


@override_settings(AT_API_KEY='')
class SiteTests(TestCase):
    def test_medicine_suggestions(self):
        Medicine.objects.create(name='Amoxicillin 500mg', generic_name='Amoxicillin')
        data = self.client.get(reverse('medicine_suggest'), {'q': 'amox'}).json()
        self.assertEqual(data['results'][0]['name'], 'Amoxicillin 500mg')
        self.assertEqual(self.client.get(reverse('medicine_suggest'), {'q': 'a'}).json()['results'], [])

    def test_custom_404_page(self):
        response = self.client.get('/no-such-page/')
        self.assertEqual(response.status_code, 404)
        self.assertContains(response, 'Page not found', status_code=404)

    @override_settings(SUPPORT_PHONE='+250 789 000 000', SUBSCRIPTION_PRICE_RWF=7000, FREE_TRIAL_DAYS=14)
    def test_business_pages_show_contact_and_pricing(self):
        pharmacies = self.client.get(reverse('for_pharmacies'))
        self.assertContains(pharmacies, '7,000 RWF')
        self.assertContains(pharmacies, 'free 14-day trial')
        self.assertContains(self.client.get(reverse('terms')), 'Terms of Service')
        self.assertContains(self.client.get(reverse('privacy')), '+250 789 000 000')
        self.assertContains(self.client.get(reverse('search')), '+250 789 000 000')

    def test_healthz_and_robots(self):
        self.assertEqual(self.client.get(reverse('healthz')).json(), {'status': 'ok'})
        self.assertContains(self.client.get('/robots.txt'), 'Disallow: /dashboard/')

    def test_password_reset_sends_email(self):
        from django.core import mail
        cache.clear()
        User.objects.create_user('owner', 'owner@example.rw', 'pw-12345-xyz')
        self.client.post(reverse('password_reset'), {'email': 'owner@example.rw'})
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('/password/reset/', mail.outbox[0].body)


@override_settings(AT_API_KEY='', MOMO_SUBSCRIPTION_KEY='', FREE_TRIAL_DAYS=30, SUBSCRIPTION_GRACE_DAYS=7,
                   HIDE_UNPAID_PHARMACIES=True, SUBSCRIPTION_PRICE_RWF=5000)
class SubscriptionTests(BaseTestCase):
    def _set(self, trial_offset=None, paid_offset=None):
        today = timezone.localdate()
        self.pharmacy.trial_ends_at = today + timedelta(days=trial_offset) if trial_offset is not None else None
        self.pharmacy.paid_until = today + timedelta(days=paid_offset) if paid_offset is not None else None
        self.pharmacy.last_reminder = ''
        self.pharmacy.save()

    def test_trial_starts_when_verified(self):
        p = self.make_pharmacy('newp', 'NEW-9', verified=False)
        self.assertIsNone(p.trial_ends_at)
        self.assertEqual(p.subscription_status, 'unverified')
        p.verified = True
        p.save(update_fields=['verified'])
        p.refresh_from_db()
        self.assertEqual(p.trial_ends_at, timezone.localdate() + timedelta(days=30))
        self.assertEqual(p.subscription_status, 'trial')

    def test_status_progression(self):
        self._set(trial_offset=5)
        self.assertEqual(self.pharmacy.subscription_status, 'trial')
        self._set(trial_offset=-20, paid_offset=10)
        self.assertEqual(self.pharmacy.subscription_status, 'active')
        self._set(trial_offset=-3)
        self.assertEqual(self.pharmacy.subscription_status, 'grace')
        self._set(trial_offset=-8)
        self.assertEqual(self.pharmacy.subscription_status, 'overdue')

    def test_overdue_pharmacy_hidden_from_search_and_reservation(self):
        self._set(trial_offset=-3)  # grace: still visible
        self.assertEqual(len(self.client.get(reverse('search'), {'q': 'Panadol'}).context['results']), 1)
        self._set(trial_offset=-8)  # past grace: hidden
        self.assertEqual(self.client.get(reverse('search'), {'q': 'Panadol'}).context['results'], [])
        with patch('core.services.send_sms'):
            self.reserve()
        self.assertFalse(Reservation.objects.exists())

    @override_settings(HIDE_UNPAID_PHARMACIES=False)
    def test_hiding_can_be_switched_off(self):
        self._set(trial_offset=-30)
        self.assertEqual(len(self.client.get(reverse('search'), {'q': 'Panadol'}).context['results']), 1)

    def test_momo_payment_extends_after_trial(self):
        self._set(trial_offset=10)
        self.client.force_login(self.owner)
        self.client.post(reverse('billing'), {'months': '3', 'phone': '0788000000'})
        pay = SubscriptionPayment.objects.get()
        self.assertEqual((pay.status, int(pay.amount)), ('pending', 15000))
        self.client.post(reverse('billing_check'))
        pay.refresh_from_db()
        self.pharmacy.refresh_from_db()
        today = timezone.localdate()
        self.assertEqual(pay.status, 'successful')
        self.assertEqual(pay.period_start, today + timedelta(days=11))  # day after the trial
        self.assertEqual(self.pharmacy.paid_until, today + timedelta(days=10 + 90))
        self.assertEqual(self.pharmacy.subscription_status, 'active')  # paid ahead: shown as active
        self.assertEqual(self.pharmacy.coverage_end, today + timedelta(days=100))

    def test_overdue_payment_starts_today(self):
        from .billing import record_manual_payment
        self._set(trial_offset=-20)
        record_manual_payment(self.pharmacy, 1)
        self.pharmacy.refresh_from_db()
        self.assertEqual(self.pharmacy.paid_until, timezone.localdate() + timedelta(days=29))
        self.assertEqual(self.pharmacy.subscription_status, 'active')

    def test_payment_sms_sent_only_after_commit(self):
        """Regression: the SMS used to be sent inside the transaction, holding the SQLite lock."""
        from .billing import record_manual_payment
        with patch('core.billing.send_sms') as sms:
            with self.captureOnCommitCallbacks(execute=False) as callbacks:
                record_manual_payment(self.pharmacy, 1)
            sms.assert_not_called()  # nothing sent while the transaction is open
            for cb in callbacks:
                cb()
            sms.assert_called_once()

    def test_double_check_does_not_extend_twice(self):
        self._set(trial_offset=-20)
        self.client.force_login(self.owner)
        self.client.post(reverse('billing'), {'months': '1', 'phone': '0788000000'})
        self.client.post(reverse('billing_check'))
        self.client.post(reverse('billing_check'))  # second click
        self.pharmacy.refresh_from_db()
        self.assertEqual(self.pharmacy.paid_until, timezone.localdate() + timedelta(days=29))
        self.assertEqual(SubscriptionPayment.objects.filter(status='successful').count(), 1)

    def test_sqlite_uses_immediate_transactions(self):
        from django.conf import settings as s
        options = s.DATABASES['default'].get('OPTIONS', {})
        if s.DATABASES['default']['ENGINE'].endswith('sqlite3'):
            self.assertEqual(options.get('transaction_mode'), 'IMMEDIATE')

    def test_reminders_sent_once(self):
        from .billing import send_reminders
        self._set(trial_offset=3)
        with patch('core.billing.send_sms') as sms:
            self.assertEqual(send_reminders(), 1)
            self.assertEqual(send_reminders(), 0)  # same day again: nothing new
        self.assertIn('3 days', sms.call_args.args[1])
        self._set(trial_offset=-2)
        with patch('core.billing.send_sms') as sms:
            self.assertEqual(send_reminders(), 1)
        self.assertIn('ended', sms.call_args.args[1])
        self._set(trial_offset=10)
        with patch('core.billing.send_sms'):
            self.assertEqual(send_reminders(), 0)  # not a reminder day

    def test_admin_actions_and_filter(self):
        admin_user = User.objects.create_superuser('boss2', '', 'pw-12345-xyz')
        self.client.force_login(admin_user)
        self._set(trial_offset=-3)
        url = reverse('admin:core_pharmacy_changelist')
        self.assertContains(self.client.get(url, {'subscription': 'grace'}), 'Test Pharmacy')
        self.client.post(url, {'action': 'record_1_month', '_selected_action': [self.pharmacy.pk]})
        self.pharmacy.refresh_from_db()
        self.assertEqual(self.pharmacy.subscription_status, 'active')
        self.assertEqual(SubscriptionPayment.objects.get().method, 'cash')
        self.assertContains(self.client.get(reverse('analytics')), 'Pharmacy subscriptions')

    def test_billing_page_and_banner(self):
        self._set(trial_offset=4)
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(reverse('billing')), 'Pay with mobile money')
        self.assertContains(self.client.get(reverse('dashboard')), 'Free trial')


@override_settings(AT_API_KEY='', MOMO_SUBSCRIPTION_KEY='', AIRTEL_CLIENT_ID='')
class ProfessionalFeatureTests(BaseTestCase):
    """Features from the professional additions plan."""

    def test_typo_tolerant_search(self):
        from django.core.cache import cache as c
        c.clear()
        Medicine.objects.create(name='Paracetamol 500mg', generic_name='Paracetamol')
        response = self.client.get(reverse('search'), {'q': 'paracetmol'})
        self.assertEqual(response.context['corrected'], 'paracetamol')
        self.assertEqual(len(response.context['results']), 1)  # Panadol has generic Paracetamol
        self.assertContains(response, 'Showing results for')

    def test_alternatives_when_out_of_stock(self):
        brand = Medicine.objects.create(name='Doliprane', generic_name='Paracetamol')
        Inventory.objects.create(pharmacy=self.pharmacy, medicine=brand, quantity=0, price='300')
        response = self.client.get(reverse('search'), {'q': 'Doliprane'})
        self.assertEqual(response.context['results'], [])
        self.assertEqual([i.medicine.name for i in response.context['alternatives']], ['Panadol'])
        self.assertContains(response, 'Same active ingredient')

    def test_filters_price_open_insurance(self):
        cheap = self.make_pharmacy('cheap', 'TEST-C', district='Gasabo', insurers='mutuelle,rssb', open_24h=True)
        Inventory.objects.create(pharmacy=cheap, medicine=self.medicine, quantity=3, price='50')
        names = lambda r: [i.pharmacy.name for i in r.context['results']]
        self.assertEqual(names(self.client.get(reverse('search'), {'q': 'Panadol', 'max_price': '60'})), ['cheap pharmacy'])
        self.assertEqual(names(self.client.get(reverse('search'), {'q': 'Panadol', 'insurer': 'rssb'})), ['cheap pharmacy'])
        self.assertEqual(names(self.client.get(reverse('search'), {'q': 'Panadol', 'open_now': '1'})), ['cheap pharmacy'])
        r = self.client.get(reverse('search'), {'q': 'Panadol', 'insurer': 'rssb'})
        self.assertEqual(r.context['filtered_out'], 1)

    def test_result_card_has_call_directions_and_disclaimer(self):
        response = self.client.get(reverse('search'), {'q': 'Panadol'})
        self.assertContains(response, 'href="tel:0788000000"')
        self.assertContains(response, 'openstreetmap.org/directions')
        self.assertContains(response, 'Call the pharmacy to confirm')

    def test_french_language(self):
        self.client.get(reverse('set_language', args=['fr']))
        self.assertContains(self.client.get(reverse('search')), 'Trouvez ce qu’il vous faut.')

    @patch('core.services.send_sms')
    def test_hold_reminder_sent_once(self, sms):
        from .services import send_hold_reminders
        r = Reservation.objects.create(patient_phone=PHONE_E164, inventory=self.inventory, quantity=1, pickup_code='111222')
        Reservation.objects.filter(pk=r.pk).update(created_at=timezone.now() - timedelta(minutes=100))
        self.assertEqual(send_hold_reminders(), 1)
        self.assertEqual(send_hold_reminders(), 0)
        self.assertIn('held until', sms.call_args.args[1])

    def test_review_after_pickup_with_moderation(self):
        r = Reservation.objects.create(patient_phone=PHONE_E164, inventory=self.inventory, quantity=1,
                                       pickup_code='333444', status='done')
        url = reverse('reservation_review', args=[r.pk, '333444'])
        self.client.post(url, {'rating': '5', 'comment': 'Fast service'})
        self.client.post(url, {'rating': '1'})  # second review refused
        review = Review.objects.get()
        self.assertEqual((review.rating, review.status), (5, 'pending'))
        self.assertIsNone(self.client.get(reverse('search'), {'q': 'Panadol'}).context['results'][0].rating)
        review.status = 'approved'
        review.save()
        self.assertEqual(self.client.get(reverse('search'), {'q': 'Panadol'}).context['results'][0].rating, (5.0, 1))

    def test_review_not_allowed_before_pickup(self):
        r = Reservation.objects.create(patient_phone=PHONE_E164, inventory=self.inventory, quantity=1, pickup_code='555666')
        self.client.post(reverse('reservation_review', args=[r.pk, '555666']), {'rating': '4'})
        self.assertFalse(Review.objects.exists())

    def test_stock_audit_log_and_expiry(self):
        self.client.force_login(self.owner)
        self.client.post(reverse('update_stock', args=[self.inventory.pk]),
                         {'quantity': '9', 'price': '120', 'expiry_date': '2020-01-31'})
        change = StockChange.objects.get()
        self.assertEqual((change.old_quantity, change.new_quantity, change.source, change.user), (5, 9, 'web', self.owner))
        self.inventory.refresh_from_db()
        self.assertEqual(self.inventory.expiry_state, 'expired')
        self.assertContains(self.client.get(reverse('dashboard')), 'Expiry check')
        self.assertContains(self.client.get(reverse('reports')), 'Stock change history')

    @patch('core.management.commands.pharmacy_alerts.send_sms')
    def test_expiry_alert_sms_once(self, sms):
        from .management.commands.pharmacy_alerts import send_expiry_alerts
        self.inventory.expiry_date = timezone.localdate() + timedelta(days=10)
        self.inventory.save()
        self.assertEqual(send_expiry_alerts(), 1)
        self.assertEqual(send_expiry_alerts(), 0)
        self.assertIn('Panadol', sms.call_args.args[1])

    def test_staff_roles(self):
        self.client.force_login(self.owner)
        self.client.post(reverse('staff'), {'username': 'nurse1', 'password': 'Str0ng-pass-2026', 'email': ''})
        pharmacist = User.objects.get(username='nurse1')
        self.client.force_login(pharmacist)
        self.assertEqual(self.client.get(reverse('dashboard')).status_code, 200)
        self.client.post(reverse('update_stock', args=[self.inventory.pk]), {'quantity': '7', 'price': '100'})
        self.inventory.refresh_from_db()
        self.assertEqual(self.inventory.quantity, 7)
        for name in ('billing', 'staff', 'pharmacy_profile'):
            self.assertEqual(self.client.get(reverse(name)).status_code, 403)

    @patch('core.views.send_sms')
    @override_settings(PHARMACY_LOGIN_2FA=True)
    def test_sms_two_factor_login(self, sms):
        response = self.client.post(reverse('login'), {'username': 'pharmacy', 'password': 'test-password'})
        self.assertRedirects(response, reverse('login_verify'))
        self.assertNotIn('_auth_user_id', self.client.session)
        code = re.search(r'\d{6}', sms.call_args.args[1]).group()
        self.client.post(reverse('login_verify'), {'code': '000000' if code != '000000' else '111111'})
        self.assertNotIn('_auth_user_id', self.client.session)
        self.client.post(reverse('login_verify'), {'code': code})
        self.assertEqual(int(self.client.session['_auth_user_id']), self.owner.pk)

    @patch('core.services.send_sms')
    def test_pharmacy_ussd_stock_update(self, sms):
        owner_phone = '+250788000000'
        self.assertIn('5. Pharmacy', ussd_response(owner_phone, ''))
        self.assertNotIn('5. Pharmacy', ussd_response('+250788999111', ''))
        self.assertIn('Enter new quantity', ussd_response(owner_phone, '5*panadol*1'))
        self.assertIn('Updated', ussd_response(owner_phone, '5*panadol*1*33'))
        self.inventory.refresh_from_db()
        self.assertEqual(self.inventory.quantity, 33)
        self.assertEqual(StockChange.objects.get().source, 'ussd')

    def test_invoice_and_commission_report(self):
        from .billing import record_manual_payment
        payment = record_manual_payment(self.pharmacy, 1)
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(reverse('invoice', args=[payment.pk])), payment.invoice_number)
        res = Reservation.objects.create(patient_phone=PHONE_E164, inventory=self.inventory, quantity=2, pickup_code='1')
        Payment.objects.create(reservation=res, payer_phone=PHONE_E164, amount=200, commission=6,
                               provider_reference='r1', status='successful')
        self.client.force_login(User.objects.create_superuser('boss3', '', 'pw-12345-xyz'))
        response = self.client.get(reverse('commission_report'))
        self.assertEqual(response.context['totals']['commission'], 6)
        self.assertEqual(response.context['totals']['income'], 6 + 5000)
        self.assertIn('Test Pharmacy', self.client.get(reverse('commission_report'), {'export': 'csv'}).content.decode())

    def test_public_api(self):
        data = self.client.get(reverse('api_search'), {'q': 'panadol', 'lat': '-1.95', 'lng': '30.12'}).json()
        self.assertEqual(data['count'], 1)
        self.assertEqual(data['results'][0]['pharmacy']['name'], 'Test Pharmacy')
        self.assertIn('distance_km', data['results'][0])
        self.assertEqual(self.client.get(reverse('api_search'), {'q': 'x'}).status_code, 400)
        self.assertEqual(self.client.get(reverse('api_pharmacies')).json()['count'], 1)
        self.assertEqual(self.client.get(reverse('api_medicines'), {'q': 'pana'}).json()['count'], 1)
        self.assertContains(self.client.get(reverse('developers')), '/api/v1/search/')

    def test_pages_pwa_and_status(self):
        for name in ('about', 'contact', 'status', 'offline'):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)
        self.assertEqual(self.client.get(reverse('manifest')).json()['short_name'], 'MedFinder')
        self.assertContains(self.client.get(reverse('service_worker')), 'caches.open')
        self.assertContains(self.client.get(reverse('contact')), 'wa.me/')
        self.assertEqual(self.client.get('/').headers['Permissions-Policy'][:20], 'geolocation=(self), ')

    def test_testimonials_only_when_published(self):
        from .models import Testimonial
        Testimonial.objects.create(name='Alice', role='Pharmacist', quote='More customers every week.')
        self.assertNotContains(self.client.get(reverse('search')), 'More customers every week.')
        Testimonial.objects.update(published=True)
        self.assertContains(self.client.get(reverse('search')), 'More customers every week.')

    def test_backup_command(self):
        import gzip
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as folder, self.settings(BACKUP_DIR=Path(folder)):
            call_command('backup_db', stdout=io.StringIO())
            files = list(Path(folder).glob('medfinder-*.json.gz'))
            self.assertEqual(len(files), 1)
            with gzip.open(files[0], 'rt', encoding='utf-8') as f:
                self.assertIn('Test Pharmacy', f.read())


class SmsHelperTests(TestCase):
    @override_settings(AT_API_KEY='')
    def test_sms_without_key_is_logged_as_simulated(self):
        from .sms import send_sms
        self.assertFalse(send_sms('0788123456', 'hello'))
        self.assertEqual(SmsLog.objects.get().status, 'simulated')

    @override_settings(AT_API_KEY='key')
    def test_sms_failure_never_raises(self):
        from .sms import send_sms
        with patch('africastalking.SMS') as sms:
            sms.send.side_effect = Exception('network down')
            with patch('africastalking.initialize'):
                self.assertFalse(send_sms('0788123456', 'hello'))
        self.assertEqual(SmsLog.objects.get().status, 'failed')


@override_settings(AT_API_KEY='', MOMO_SUBSCRIPTION_KEY='')
class UtilityTests(TestCase):
    def test_phone_normalisation(self):
        for raw in ['0788123456', '788123456', '250788123456', '+250 788 123 456', '0788-123-456']:
            self.assertEqual(normalize_phone(raw), PHONE_E164)
        self.assertTrue(is_valid_rw_mobile('0721234567'))
        self.assertFalse(is_valid_rw_mobile('0712345678'))

    def test_linear_forecast(self):
        total, slope = linear_forecast([1, 2, 3, 4], horizon=2)
        self.assertAlmostEqual(slope, 1.0)
        self.assertAlmostEqual(total, 5 + 6)
        self.assertEqual(linear_forecast([5, 0, 0, 0], horizon=7)[0] >= 0, True)

    def test_seed_and_summary(self):
        call_command('seed_demo', stdout=open('NUL' if __import__('os').name == 'nt' else '/dev/null', 'w'))
        s = platform_summary(30)
        self.assertGreater(s['kpi']['searches'], 0)
        self.assertTrue(s['forecast'])

    def test_language_switch(self):
        self.client.get(reverse('set_language', args=['rw']))
        self.assertContains(self.client.get(reverse('search')), 'Bona umuti ukeneye.')


class BrowsingTests(BaseTestCase):
    """Pharmacy directory, medicine catalogue, help page, sitemap."""

    def test_pharmacy_directory_lists_only_verified(self):
        self.make_pharmacy('hidden', 'HID-1', verified=False, name='Hidden Pharmacy')
        r = self.client.get(reverse('pharmacy_list'))
        self.assertContains(r, 'Test Pharmacy')
        self.assertNotContains(r, 'Hidden Pharmacy')

    def test_pharmacy_directory_filters(self):
        self.make_pharmacy('huye', 'HUY-1', name='Huye Pharmacy', district='Huye', open_24h=True)
        r = self.client.get(reverse('pharmacy_list'), {'district': 'Huye'})
        self.assertContains(r, 'Huye Pharmacy')
        self.assertNotContains(r, 'Test Pharmacy')
        r = self.client.get(reverse('pharmacy_list'), {'h24': '1'})
        self.assertContains(r, 'Huye Pharmacy')
        self.assertNotContains(r, 'Test Pharmacy')

    def test_pharmacy_profile_shows_stock_and_hides_unverified(self):
        r = self.client.get(reverse('pharmacy_detail', args=[self.pharmacy.pk]))
        self.assertContains(r, 'Panadol')
        hidden = self.make_pharmacy('hidden', 'HID-2', verified=False)
        self.assertEqual(self.client.get(reverse('pharmacy_detail', args=[hidden.pk])).status_code, 404)

    def test_medicine_catalogue_and_detail(self):
        Medicine.objects.create(name='Amoxil', generic_name='Amoxicillin', category='Antibiotic')
        r = self.client.get(reverse('medicine_list'), {'category': 'Antibiotic'})
        self.assertContains(r, 'Amoxil')
        self.assertNotContains(r, 'Panadol')
        r = self.client.get(reverse('medicine_list'), {'available': '1'})
        self.assertContains(r, 'Panadol')
        self.assertNotContains(r, 'Amoxil')
        r = self.client.get(reverse('medicine_detail', args=[self.medicine.pk]))
        self.assertContains(r, 'Test Pharmacy')
        self.assertContains(r, '100 RWF')

    def test_help_sitemap_robots(self):
        self.assertContains(self.client.get(reverse('help')), 'How to use MedFinder')
        r = self.client.get(reverse('sitemap'))
        self.assertContains(r, reverse('pharmacy_detail', args=[self.pharmacy.pk]))
        self.assertContains(self.client.get(reverse('robots_txt')), 'Sitemap:')

    def test_search_shows_price_summary_and_links(self):
        r = self.client.get(reverse('search'), {'q': 'Panadol'})
        self.assertContains(r, 'pharmacy has it')
        self.assertContains(r, reverse('pharmacy_detail', args=[self.pharmacy.pk]))
        self.assertContains(r, reverse('medicine_detail', args=[self.medicine.pk]))


class StockAlertTests(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.inventory.quantity = 0
        self.inventory.save()

    def subscribe(self, **extra):
        data = {'medicine': self.medicine.pk, 'phone': PHONE, 'consent': '1', 'next': '/'}
        data.update(extra)
        return self.client.post(reverse('stock_alert'), data)

    def test_out_of_stock_search_offers_alert(self):
        r = self.client.get(reverse('search'), {'q': 'Panadol'})
        self.assertContains(r, 'Get an SMS when it is back in stock')

    def test_subscribe_validates(self):
        self.subscribe(phone='12345')
        self.subscribe(consent='')
        self.assertEqual(StockAlert.objects.count(), 0)
        self.subscribe()
        self.subscribe()  # duplicate is ignored
        self.assertEqual(StockAlert.objects.count(), 1)
        self.assertEqual(StockAlert.objects.get().phone, PHONE_E164)

    def test_restock_sends_one_sms(self):
        self.subscribe()
        with self.captureOnCommitCallbacks(execute=True):
            self.inventory.quantity = 10
            self.inventory.save()
        alert = StockAlert.objects.get()
        self.assertIsNotNone(alert.notified_at)
        self.assertEqual(SmsLog.objects.filter(phone=PHONE_E164, message__contains='now available').count(), 1)
        with self.captureOnCommitCallbacks(execute=True):
            self.inventory.save()  # second save: no second SMS
        self.assertEqual(SmsLog.objects.filter(message__contains='now available').count(), 1)

    def test_district_alert_ignores_other_districts(self):
        self.subscribe(district='Huye')  # the stock is in Gasabo
        with self.captureOnCommitCallbacks(execute=True):
            self.inventory.quantity = 10
            self.inventory.save()
        self.assertIsNone(StockAlert.objects.get().notified_at)


class NavigationTests(BaseTestCase):
    def test_quick_access_and_menus_on_homepage(self):
        r = self.client.get(reverse('search'))
        for name in ('medicine_list', 'pharmacy_list', 'track', 'help', 'for_pharmacies', 'about', 'contact'):
            self.assertContains(r, reverse(name))
        self.assertContains(r, 'class="quick-access"')
        self.assertContains(r, 'class="bottom-nav"')
        self.assertContains(r, 'href="tel:912"')

    def test_bottom_bar_hidden_for_logged_in_pharmacy(self):
        self.client.login(username='pharmacy', password='test-password')
        self.assertNotContains(self.client.get(reverse('help')), 'class="bottom-nav"')

    def test_language_menu_shows_full_names(self):
        r = self.client.get(reverse('help'))
        self.assertContains(r, 'class="lang-current">English<')
        for name in ('Ikinyarwanda', 'Français', 'Language · Ururimi · Langue'):
            self.assertContains(r, name)
        self.client.get(reverse('set_language', args=['rw']))
        self.assertContains(self.client.get(reverse('help')), 'class="lang-current">Ikinyarwanda<')
