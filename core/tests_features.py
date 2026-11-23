"""Tests for the patient features: basket orders, delivery, prescriptions, My reservations, sharing, yellow mode."""
import re
import shutil
import tempfile
from datetime import timedelta
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings
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

    def test_header_starts_in_yellow_mode(self):
        response = self.client.get('/')
        self.assertContains(response, 'data-theme="yellow"')


class MedicineCatalogueCommandTests(BaseTestCase):
    def test_catalogue_loads_without_prices_and_is_repeatable(self):
        from django.core.management import call_command
        call_command('load_medicine_catalogue', stdout=open(__import__('os').devnull, 'w'))
        n = Medicine.objects.count()
        call_command('load_medicine_catalogue', stdout=open(__import__('os').devnull, 'w'))
        self.assertEqual(Medicine.objects.count(), n)
        self.assertFalse(Medicine.objects.filter(reference_price__isnull=False).exists())
        self.assertTrue(Medicine.objects.filter(name='Coartem 20/120', requires_prescription=True).exists())


class PharmacyLocationBoundsTests(SimpleTestCase):
    """A typo (even in the admin panel) must not put a pharmacy outside Rwanda on the map."""

    def test_coordinates_outside_rwanda_are_rejected(self):
        from django.core.exceptions import ValidationError
        from .models import Pharmacy
        lat, lng = Pharmacy._meta.get_field('latitude'), Pharmacy._meta.get_field('longitude')
        with self.assertRaises(ValidationError):
            lat.run_validators(18.0)
        with self.assertRaises(ValidationError):
            lng.run_validators(3.0)
        lat.run_validators(-2.5967)   # Huye
        lng.run_validators(29.7394)


class MedicinePhotoTests(FeatureBase):
    """Pharmacies upload a photo of the real box; patients see it on search, pharmacy and medicine pages."""

    def upload(self, file, pk=None):
        self.client.force_login(self.owner)
        return self.client.post(reverse('stock_photo', args=[pk or self.inventory.pk]), {'photo': file})

    def test_pharmacy_uploads_photo_and_patients_see_it(self):
        self.upload(png('box.png'))
        self.inventory.refresh_from_db()
        self.assertTrue(self.inventory.photo.name.startswith('medicine-photos/'))
        url = self.inventory.photo_url
        self.client.logout()
        for page in (reverse('search') + '?q=Panadol', reverse('pharmacy_detail', args=[self.pharmacy.pk]),
                     reverse('medicine_detail', args=[self.medicine.pk])):
            self.assertContains(self.client.get(page), url, msg_prefix=page)
        served = self.client.get(url)
        self.assertEqual(served.status_code, 200)
        self.assertEqual(b''.join(served.streaming_content), PNG)

    def test_webp_and_jpeg_accepted(self):
        webp = SimpleUploadedFile('box.webp', b'RIFF\x00\x00\x00\x00WEBPVP8 ' + b'\x00' * 32, content_type='image/webp')
        self.upload(webp)
        self.inventory.refresh_from_db()
        self.assertTrue(self.inventory.photo.name.endswith('.webp'))
        self.upload(SimpleUploadedFile('box.jpg', b'\xff\xd8\xff\xe0' + b'\x00' * 32, content_type='image/jpeg'))
        self.inventory.refresh_from_db()
        self.assertTrue(self.inventory.photo.name.endswith('.jpg'))

    def test_windows_jfif_and_gif_are_accepted_by_content(self):
        self.upload(SimpleUploadedFile('box.jfif', b'\xff\xd8\xff\xe0' + b'\x00' * 32, content_type='image/jpeg'))
        self.inventory.refresh_from_db()
        self.assertTrue(self.inventory.photo.name.endswith('.jpg'))
        self.upload(SimpleUploadedFile('box.gif', b'GIF89a' + b'\x00' * 32, content_type='image/gif'))
        self.inventory.refresh_from_db()
        self.assertTrue(self.inventory.photo.name.endswith('.gif'))

    def test_result_is_shown_next_to_the_medicine(self):
        response = self.upload(SimpleUploadedFile('notes.txt', b'hello there, not a picture'))
        self.assertTrue(response['Location'].endswith(f'#stock-{self.inventory.pk}'))
        page = self.client.get(reverse('dashboard'))
        self.assertContains(page, 'photo-status is-error')
        self.assertContains(page, 'not a picture we can show')
        self.upload(png('box.png'))
        page = self.client.get(reverse('dashboard'))
        self.assertContains(page, 'photo-status is-ok')
        self.assertNotContains(self.client.get(reverse('dashboard')), 'photo-status')  # shown only once

    def test_fake_or_wrong_files_are_rejected(self):
        self.upload(SimpleUploadedFile('box.png', b'<html>not an image</html>', content_type='image/png'))
        self.upload(SimpleUploadedFile('box.pdf', b'%PDF-1.4 ' + b'\x00' * 32, content_type='application/pdf'))
        self.inventory.refresh_from_db()
        self.assertFalse(self.inventory.photo)

    def test_cannot_change_another_pharmacys_photo(self):
        other = self.make_pharmacy('other', 'OTHER-1')
        self.client.force_login(other.owner)
        response = self.client.post(reverse('stock_photo', args=[self.inventory.pk]), {'photo': png('x.png')})
        self.assertEqual(response.status_code, 404)
        self.inventory.refresh_from_db()
        self.assertFalse(self.inventory.photo)

    def test_replace_and_remove_delete_old_files(self):
        self.upload(png('one.png'))
        self.inventory.refresh_from_db()
        first = self.inventory.photo.name
        self.upload(png('two.png'))
        self.inventory.refresh_from_db()
        storage = self.inventory.photo.storage
        self.assertNotEqual(self.inventory.photo.name, first)
        self.assertFalse(storage.exists(first))
        second = self.inventory.photo.name
        self.client.post(reverse('stock_photo', args=[self.inventory.pk]), {'remove': '1'})
        self.inventory.refresh_from_db()
        self.assertFalse(self.inventory.photo)
        self.assertFalse(storage.exists(second))

    def test_private_files_are_not_reachable_through_photo_address(self):
        response = self.client.get('/media/prescriptions/2026/01/rx-secret.png')
        self.assertEqual(response.status_code, 404)
        response = self.client.get('/media/medicine-photos/../prescriptions/rx.png')
        self.assertEqual(response.status_code, 404)

    def test_dashboard_shows_photo_controls(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse('dashboard'))
        self.assertContains(response, '+ Photo')
        self.assertContains(response, reverse('stock_photo', args=[self.inventory.pk]))


class CloudinaryStorageTests(SimpleTestCase):
    """The Cloudinary backend keeps licences/prescriptions private and makes medicine photos public."""

    def test_targets_and_urls(self):
        import os
        from unittest import mock
        from .storage import CloudinaryStorage
        with mock.patch.dict(os.environ, {'CLOUDINARY_URL': 'cloudinary://111:secret@demo-cloud'}):
            import cloudinary
            cloudinary.reset_config()
            s = CloudinaryStorage()
            self.assertEqual(s._target('prescriptions/2026/01/rx-a.png'),
                             {'public_id': 'prescriptions/2026/01/rx-a.png', 'resource_type': 'raw', 'type': 'authenticated'})
            self.assertEqual(s._target('medicine-photos/2026/01/med-a.jpg')['type'], 'upload')
            url = s.url('medicine-photos/2026/01/med-a.jpg')
            self.assertIn('res.cloudinary.com/demo-cloud/image/upload/', url)
            self.assertIn('f_auto', url)
            self.assertTrue(url.endswith('medicine-photos/2026/01/med-a'))
            with self.assertRaises(ValueError):
                s.url('licenses/2026/01/x.pdf')
            with mock.patch('cloudinary.uploader.upload') as up:
                from django.core.files.base import ContentFile
                name = s.save('licenses/2026/01/lic.pdf', ContentFile(b'%PDF-1.4'))
                self.assertEqual(name, 'licenses/2026/01/lic.pdf')
                self.assertEqual(up.call_args.kwargs['type'], 'authenticated')
                self.assertEqual(up.call_args.kwargs['resource_type'], 'raw')
        cloudinary.reset_config()


BROWSER = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36'


class WelcomePageTests(BaseTestCase):
    """First visit: a welcome page explains MedFinder and lets the visitor choose a language."""

    def visit(self, path, agent=BROWSER, **extra):
        return self.client.get(path, HTTP_USER_AGENT=agent, HTTP_ACCEPT='text/html,application/xhtml+xml', **extra)

    def test_first_visit_goes_to_welcome_and_back(self):
        page = reverse('medicine_detail', args=[self.medicine.pk])
        response = self.visit(page)
        self.assertRedirects(response, f"{reverse('welcome')}?next={page}", fetch_redirect_response=False)
        welcome = self.visit(response['Location'])
        self.assertContains(welcome, 'Welcome to')
        self.assertContains(welcome, 'Choose your language')
        self.assertContains(welcome, 'Ikinyarwanda')
        done = self.client.post(reverse('welcome'), {'lang': 'rw', 'next': page})
        self.assertRedirects(done, page, fetch_redirect_response=False)
        self.assertEqual(self.client.session['lang'], 'rw')
        again = self.visit(reverse('search'))
        self.assertEqual(again.status_code, 200)  # not sent to the welcome page again
        self.assertContains(again, '<html lang="rw"')

    def test_never_for_bots_previews_monitors_or_scripts(self):
        for agent in ('Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)',
                      'WhatsApp/2.23.20 A', 'facebookexternalhit/1.1', 'Mozilla/5.0+(compatible; UptimeRobot/2.0)',
                      'curl/8.4.0', ''):
            self.assertEqual(self.visit(reverse('search'), agent=agent).status_code, 200, agent)

    def test_never_for_pharmacy_pages_sms_links_or_logged_in_users(self):
        for path in (reverse('login'), reverse('track'), reverse('healthz'), reverse('register')):
            self.assertNotEqual(self.visit(path).get('Location', '').split('?')[0], reverse('welcome'), path)
        self.client.force_login(self.owner)
        self.assertEqual(self.visit(reverse('search')).status_code, 200)

    def test_choosing_language_from_menu_skips_welcome(self):
        self.client.get(reverse('set_language', args=['fr']))
        self.assertEqual(self.visit(reverse('search')).status_code, 200)

    def test_browser_language_is_preselected(self):
        response = self.visit(reverse('welcome'), HTTP_ACCEPT_LANGUAGE='fr-FR,fr;q=0.9,en;q=0.8')
        self.assertContains(response, 'Bienvenue sur')
        self.assertContains(response, 'value="fr" checked')

    def test_next_cannot_leave_the_site(self):
        for bad in ('https://evil.example/', '//evil.example/x', '/welcome/?next=/'):
            response = self.client.post(reverse('welcome'), {'lang': 'en', 'next': bad})
            self.assertEqual(response['Location'], reverse('search'), bad)

    def test_all_languages_have_all_welcome_texts(self):
        from .i18n import WELCOME_STRINGS
        keys = set(WELCOME_STRINGS['en'])
        for code, texts in WELCOME_STRINGS.items():
            self.assertEqual(set(texts), keys, code)
            self.assertTrue(all(v.strip() for v in texts.values()), code)
