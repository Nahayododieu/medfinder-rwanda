"""Tests for the patient features: basket orders, delivery, prescriptions, My reservations, sharing, dark mode."""
import re
import shutil
import tempfile
from datetime import timedelta
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Inventory, Medicine, Order, Payment, Reservation, SmsLog
from .services import send_hold_reminders
from .tests import PHONE, PHONE_E164, BaseTestCase

PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 64
MEDIA = tempfile.mkdtemp(prefix='medfinder-test-media-')


def png(name='rx.png'):
    return SimpleUploadedFile(name, PNG, content_type='image/png')


@override_settings(MEDIA_ROOT=MEDIA, AT_API_KEY='', MOMO_SUBSCRIPTION_KEY='', AIRTEL_CLIENT_ID='')
class FeatureBase(BaseTestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def setUp(self):
        super().setUp()
        self.amox = Medicine.objects.create(name='Amoxil 500', generic_name='Amoxicillin', requires_prescription=True)
        self.amox_inv = Inventory.objects.create(pharmacy=self.pharmacy, medicine=self.amox, quantity=10,
                                                 price='300.00')

    def add(self, inv, qty=1, **extra):
        return self.client.post(reverse('basket_add', args=[inv.pk]), {'quantity': qty, **extra})


class BasketTests(FeatureBase):
    def test_add_items_and_show_count(self):
        self.add(self.inventory, 2)
        self.add(self.amox_inv, 1)
        response = self.client.get(reverse('basket'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['lines']), 2)
        self.assertEqual(response.context['subtotal'], Decimal('500'))
        self.assertEqual(response.context['basket_count'], 3)
        self.assertTrue(response.context['needs_rx'])

    def test_quantity_is_capped_at_stock(self):
        self.add(self.inventory, 50)
        self.assertEqual(self.client.session['medfinder_basket']['items'][str(self.inventory.pk)], 5)

    def test_other_pharmacy_asks_before_replacing(self):
        self.add(self.inventory)
        other = self.make_pharmacy('other', 'TEST-777')
        other_inv = Inventory.objects.create(pharmacy=other, medicine=self.medicine, quantity=3, price='90')
        self.add(other_inv)
        response = self.client.get(reverse('basket'))
        self.assertEqual(response.context['conflict'].pk, other_inv.pk)
        self.assertEqual(len(response.context['lines']), 1)  # nothing replaced yet
        self.add(other_inv, 1, replace='1')
        lines = self.client.get(reverse('basket')).context['lines']
        self.assertEqual([line['inv'].pk for line in lines], [other_inv.pk])

    def test_update_and_remove(self):
        self.add(self.inventory)
        self.add(self.amox_inv)
        self.client.post(reverse('basket'), {'action': 'update', f'qty_{self.inventory.pk}': '3',
                                             f'qty_{self.amox_inv.pk}': '0'})
        items = self.client.session['medfinder_basket']['items']
        self.assertEqual(items, {str(self.inventory.pk): 3})
        self.client.post(reverse('basket'), {'action': f'remove:{self.inventory.pk}'})
        self.assertNotIn('medfinder_basket', self.client.session)

    def test_checkout_creates_one_order_with_shared_code(self):
        self.add(self.inventory, 2)
        self.add(self.amox_inv, 1)
        response = self.client.post(reverse('basket_checkout'), {'phone': PHONE, 'consent': '1',
                                                                  'prescription': png()})
        order = Order.objects.get()
        self.assertRedirects(response, reverse('order_detail', args=[order.pk, order.pickup_code]))
        lines = list(order.items.all())
        self.assertEqual(len(lines), 2)
        self.assertEqual({r.pickup_code for r in lines}, {order.pickup_code})
        self.inventory.refresh_from_db()
        self.amox_inv.refresh_from_db()
        self.assertEqual((self.inventory.quantity, self.amox_inv.quantity), (3, 9))
        self.assertEqual(order.total, Decimal('500'))
        rx = Reservation.objects.get(inventory=self.amox_inv)
        self.assertEqual(rx.prescription_status, 'uploaded')
        self.assertTrue(rx.prescription.name.startswith('prescriptions/'))
        self.assertEqual(Reservation.objects.get(inventory=self.inventory).prescription_status, '')
        self.assertNotIn('medfinder_basket', self.client.session)
        page = self.client.get(reverse('order_detail', args=[order.pk, order.pickup_code]))
        self.assertContains(page, 'Amoxil 500')
        self.assertContains(page, '500 RWF')

    def test_checkout_is_all_or_nothing(self):
        self.add(self.inventory, 2)
        self.add(self.amox_inv, 5)
        self.amox_inv.quantity = 1
        self.amox_inv.save()
        self.client.post(reverse('basket_checkout'), {'phone': PHONE, 'consent': '1'})
        self.assertFalse(Order.objects.exists())
        self.inventory.refresh_from_db()
        self.assertEqual(self.inventory.quantity, 5)  # nothing was held

    def test_checkout_needs_consent_and_valid_phone(self):
        self.add(self.inventory)
        self.client.post(reverse('basket_checkout'), {'phone': '123'})
        self.assertFalse(Order.objects.exists())


class OrderPaymentTests(FeatureBase):
    def make_order(self):
        self.add(self.inventory, 2)
        self.add(self.amox_inv, 1)
        self.client.post(reverse('basket_checkout'), {'phone': PHONE, 'consent': '1'})
        return Order.objects.get()

    def test_one_payment_request_pays_every_line(self):
        order = self.make_order()
        self.client.post(reverse('order_pay', args=[order.pk, order.pickup_code]), {'phone': PHONE})
        payments = list(Payment.objects.all())
        self.assertEqual(len(payments), 2)
        self.assertEqual(len({p.provider_reference for p in payments}), 1)
        self.assertEqual(sum(p.amount for p in payments), Decimal('500'))
        sms_before = SmsLog.objects.count()
        self.client.post(reverse('order_payment_status', args=[order.pk, order.pickup_code]))
        self.assertEqual(set(Payment.objects.values_list('status', flat=True)), {'successful'})
        self.assertTrue(all(r.is_paid for r in order.items.all()))
        self.assertEqual(SmsLog.objects.count() - sms_before, 1)  # one confirmation, not one per medicine
        self.assertIn('500 RWF', SmsLog.objects.first().message)

    def test_paid_lines_survive_expiry(self):
        order = self.make_order()
        self.client.post(reverse('order_pay', args=[order.pk, order.pickup_code]), {'phone': PHONE})
        self.client.post(reverse('order_payment_status', args=[order.pk, order.pickup_code]))
        Reservation.objects.update(created_at=timezone.now() - timedelta(hours=48))
        self.client.get(reverse('order_detail', args=[order.pk, order.pickup_code]))
        self.assertFalse(Reservation.objects.filter(status='cancelled').exists())

    def test_patient_cancels_order_and_stock_returns(self):
        order = self.make_order()
        self.client.post(reverse('order_cancel', args=[order.pk, order.pickup_code]))
        self.assertEqual(set(order.items.values_list('status', flat=True)), {'cancelled'})
        self.inventory.refresh_from_db()
        self.assertEqual(self.inventory.quantity, 5)

    def test_wrong_code_is_404(self):
        order = self.make_order()
        bad = '000000' if order.pickup_code != '000000' else '111111'
        self.assertEqual(self.client.get(reverse('order_detail', args=[order.pk, bad])).status_code, 404)

    def test_one_hold_reminder_per_order(self):
        order = self.make_order()
        Reservation.objects.update(created_at=timezone.now() - timedelta(hours=2, minutes=-10))
        with override_settings(RESERVATION_HOLD_HOURS=2, RESERVATION_REMINDER_MINUTES=30):
            self.assertEqual(send_hold_reminders(), 1)
        self.assertIn(f'order #{order.pk}', SmsLog.objects.first().message)


class DeliveryTests(FeatureBase):
    def test_delivery_refused_when_pharmacy_does_not_deliver(self):
        self.client.post(reverse('reserve', args=[self.inventory.pk]),
                         {'phone': PHONE, 'quantity': 1, 'consent': '1', 'delivery': '1', 'address': 'KG 11 Ave, Kimironko'})
        self.assertFalse(Reservation.objects.exists())

    def test_delivery_adds_fee_and_address(self):
        self.pharmacy.offers_delivery = True
        self.pharmacy.delivery_fee = Decimal('1000')
        self.pharmacy.save()
        self.client.post(reverse('reserve', args=[self.inventory.pk]),
                         {'phone': PHONE, 'quantity': 2, 'consent': '1', 'delivery': '1', 'address': 'KG 11 Ave, Kimironko'})
        res = Reservation.objects.get()
        self.assertTrue(res.delivery)
        self.assertEqual(res.total, Decimal('1200'))
        self.assertIn('DELIVERY', SmsLog.objects.filter(phone='+250788000000').first().message)

    def test_delivery_needs_an_address(self):
        self.pharmacy.offers_delivery = True
        self.pharmacy.save()
        self.client.post(reverse('reserve', args=[self.inventory.pk]),
                         {'phone': PHONE, 'quantity': 1, 'consent': '1', 'delivery': '1', 'address': ''})
        self.assertFalse(Reservation.objects.exists())

    def test_order_fee_charged_once(self):
        self.pharmacy.offers_delivery = True
        self.pharmacy.delivery_fee = Decimal('800')
        self.pharmacy.save()
        self.add(self.inventory, 1)
        self.add(self.amox_inv, 1)
        self.client.post(reverse('basket_checkout'), {'phone': PHONE, 'consent': '1', 'delivery': '1',
                                                      'address': 'Remera, near Amahoro stadium'})
        order = Order.objects.get()
        self.assertEqual(order.total, Decimal('1200'))  # 100 + 300 + one 800 fee

    def test_pharmacy_list_delivery_filter(self):
        self.make_pharmacy('nodeliver', 'TEST-555')
        self.pharmacy.offers_delivery = True
        self.pharmacy.save()
        response = self.client.get(reverse('pharmacy_list'), {'delivery': '1'})
        self.assertContains(response, 'Test Pharmacy')
        self.assertNotContains(response, 'nodeliver pharmacy')

    def test_profile_saves_delivery_settings(self):
        self.client.force_login(self.owner)
        self.client.post(reverse('pharmacy_profile'), {
            'name': 'Test Pharmacy', 'district': 'Gasabo', 'phone': '0788000000', 'email': 'a@b.rw',
            'latitude': '-1.95', 'longitude': '30.12', 'offers_delivery': 'on', 'delivery_fee': '1500',
            'delivery_area': 'Kimironko and Remera'})
        self.pharmacy.refresh_from_db()
        self.assertTrue(self.pharmacy.offers_delivery)
        self.assertEqual(self.pharmacy.delivery_fee, Decimal('1500'))


class PrescriptionTests(FeatureBase):
    def reserve_rx(self, **files):
        return self.client.post(reverse('reserve', args=[self.amox_inv.pk]),
                                {'phone': PHONE, 'quantity': 1, 'consent': '1', **files})

    def test_upload_with_reservation(self):
        self.reserve_rx(prescription=png())
        res = Reservation.objects.get()
        self.assertEqual(res.prescription_status, 'uploaded')
        self.assertIn('prescription uploaded', SmsLog.objects.filter(phone='+250788000000').first().message)

    def test_without_upload_patient_brings_it(self):
        self.reserve_rx()
        self.assertEqual(Reservation.objects.get().prescription_status, 'missing')

    def test_fake_image_is_rejected(self):
        self.reserve_rx(prescription=SimpleUploadedFile('rx.png', b'<html>not an image</html>'))
        self.assertFalse(Reservation.objects.exists())

    def test_upload_later_from_reservation_page(self):
        self.reserve_rx()
        res = Reservation.objects.get()
        self.client.post(reverse('reservation_prescription', args=[res.pk, res.pickup_code]), {'prescription': png()})
        res.refresh_from_db()
        self.assertEqual(res.prescription_status, 'uploaded')

    def test_only_the_right_pharmacy_can_open_it(self):
        self.reserve_rx(prescription=png())
        res = Reservation.objects.get()
        url = reverse('prescription_file', args=[res.pk])
        self.assertEqual(self.client.get(url).status_code, 302)  # anonymous -> login
        other = self.make_pharmacy('spy', 'TEST-999')
        self.client.force_login(other.owner)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.client.force_login(self.owner)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Cache-Control'], 'private, no-store')

    def test_pharmacy_approves_or_rejects(self):
        self.reserve_rx(prescription=png())
        res = Reservation.objects.get()
        self.client.force_login(self.owner)
        with self.captureOnCommitCallbacks(execute=True):  # the SMS is sent after the database commit
            self.client.post(reverse('reservation_action', args=[res.pk, 'rx_reject']))
        res.refresh_from_db()
        self.assertEqual(res.prescription_status, 'rejected')
        self.assertIn('could not accept', SmsLog.objects.filter(phone=PHONE_E164).first().message)
        # patient sends a new one, pharmacy approves it
        self.client.logout()
        self.client.post(reverse('reservation_prescription', args=[res.pk, res.pickup_code]), {'prescription': png()})
        self.client.force_login(self.owner)
        self.client.post(reverse('reservation_action', args=[res.pk, 'rx_ok']))
        res.refresh_from_db()
        self.assertEqual(res.prescription_status, 'approved')
        dashboard = self.client.get(reverse('dashboard'))
        self.assertContains(dashboard, 'Prescription OK')


class MyReservationsTests(FeatureBase):
    def last_code(self):
        return re.search(r'\b(\d{6})\b', SmsLog.objects.filter(phone=PHONE_E164).first().message).group(1)

    def test_sign_in_with_sms_code_and_see_reservations(self):
        self.client.post(reverse('reserve', args=[self.inventory.pk]), {'phone': PHONE, 'quantity': 1, 'consent': '1'})
        self.assertTemplateUsed(self.client.get(reverse('my_reservations')), 'core/my_login.html')
        response = self.client.post(reverse('my_reservations'), {'phone': PHONE})
        self.assertRedirects(response, reverse('my_verify'))
        self.client.post(reverse('my_verify'), {'code': self.last_code()})
        page = self.client.get(reverse('my_reservations'))
        self.assertTemplateUsed(page, 'core/my_reservations.html')
        self.assertEqual(len(page.context['active']), 1)
        self.assertContains(page, 'Panadol')

    def test_other_phone_numbers_are_not_shown(self):
        self.client.post(reverse('reserve', args=[self.inventory.pk]),
                         {'phone': '0781111111', 'quantity': 1, 'consent': '1'})
        self.client.post(reverse('my_reservations'), {'phone': PHONE})
        self.client.post(reverse('my_verify'), {'code': self.last_code()})
        page = self.client.get(reverse('my_reservations'))
        self.assertEqual(page.context['active'] + page.context['past'], [])

    def test_wrong_codes_lock_out(self):
        self.client.post(reverse('my_reservations'), {'phone': PHONE})
        code = self.last_code()
        wrong = '000000' if code != '000000' else '111111'
        for _ in range(5):
            self.client.post(reverse('my_verify'), {'code': wrong})
        self.client.post(reverse('my_verify'), {'code': code})
        self.assertTemplateUsed(self.client.get(reverse('my_reservations')), 'core/my_login.html')

    def test_order_shows_as_one_entry(self):
        self.add(self.inventory)
        self.add(self.amox_inv)
        self.client.post(reverse('basket_checkout'), {'phone': PHONE, 'consent': '1'})
        self.client.post(reverse('my_reservations'), {'phone': PHONE})
        self.client.post(reverse('my_verify'), {'code': self.last_code()})
        page = self.client.get(reverse('my_reservations'))
        self.assertEqual(len(page.context['active']), 1)
        self.assertEqual(len(page.context['active'][0]['items']), 2)


class SharingAndThemeTests(FeatureBase):
    def test_share_buttons_on_medicine_and_pharmacy_pages(self):
        for url in (reverse('medicine_detail', args=[self.medicine.pk]),
                    reverse('pharmacy_detail', args=[self.pharmacy.pk])):
            response = self.client.get(url)
            self.assertContains(response, 'https://wa.me/?text=')
            self.assertContains(response, 'data-copy-url')

    def test_cheaper_alternative_shows_saving(self):
        generic = Medicine.objects.create(name='Paracetamol 500', generic_name='Paracetamol')
        Inventory.objects.create(pharmacy=self.pharmacy, medicine=generic, quantity=9, price='40')
        response = self.client.get(reverse('medicine_detail', args=[self.medicine.pk]))
        alt = response.context['alternatives'][0]
        self.assertEqual(alt.saving, Decimal('60'))
        self.assertContains(response, 'Save 60 RWF')

    def test_header_has_basket_and_theme_toggle(self):
        response = self.client.get(reverse('search'))
        self.assertContains(response, 'id="themeToggle"')
        self.assertContains(response, reverse('basket'))
        self.assertContains(response, reverse('my_reservations'))

    def test_result_card_offers_basket_and_prescription_upload(self):
        response = self.client.get(reverse('search'), {'q': 'Amoxil'})
        self.assertContains(response, reverse('basket_add', args=[self.amox_inv.pk]))
        self.assertContains(response, 'name="prescription"')
        self.assertContains(response, 'enctype="multipart/form-data"')


class MedicineReferencePriceTests(BaseTestCase):
    """Official reference prices are only shown with a source and date, never invented."""

    def test_no_reference_price_shown_when_empty(self):
        med = Medicine.objects.create(name='Testamol 10mg', generic_name='Testamol')
        response = self.client.get(reverse('medicine_detail', args=[med.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Official reference price')

    def test_reference_price_shown_with_source_and_date(self):
        med = Medicine.objects.create(name='Testamol 20mg', reference_price=Decimal('45'),
                                      reference_unit='per tablet', reference_source='RSSB medicines tariff list',
                                      reference_date=timezone.localdate())
        response = self.client.get(reverse('medicine_detail', args=[med.pk]))
        self.assertContains(response, 'RSSB medicines tariff list')
        self.assertContains(response, '45 RWF per tablet')

    def test_reference_price_requires_source_and_date(self):
        from django.core.exceptions import ValidationError
        med = Medicine(name='Testamol 30mg', reference_price=Decimal('45'))
        with self.assertRaises(ValidationError):
            med.full_clean()

    def test_header_starts_in_dark_mode(self):
        response = self.client.get('/')
        self.assertContains(response, 'data-theme="dark"')


class MedicineCatalogueCommandTests(BaseTestCase):
    def test_catalogue_loads_without_prices_and_is_repeatable(self):
        from django.core.management import call_command
        call_command('load_medicine_catalogue', stdout=open(__import__('os').devnull, 'w'))
        n = Medicine.objects.count()
        call_command('load_medicine_catalogue', stdout=open(__import__('os').devnull, 'w'))
        self.assertEqual(Medicine.objects.count(), n)
        self.assertFalse(Medicine.objects.filter(reference_price__isnull=False).exists())
        self.assertTrue(Medicine.objects.filter(name='Coartem 20/120', requires_prescription=True).exists())
