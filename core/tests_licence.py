"""Licence verification: the Rwanda FDA list check, the admin checklist and expiry handling."""
import zlib
from datetime import date, timedelta
from unittest import mock

from django.contrib.admin.sites import site as admin_site
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, override_settings
from django.urls import reverse
from django.utils import timezone

from .admin import PharmacyAdminForm
from .billing import visible_q
from .licence_check import FAIL, OK, automatic_checks, extract_text, find_on_list
from .models import FdaLicenceList, Pharmacy, SmsLog
from .storage import CloudinaryStorage
from .tests import BaseTestCase
from .tests_features import MEDIA

FUTURE = (timezone.localdate() + timedelta(days=900)).strftime('%d/%m/%Y')
PAST = (timezone.localdate() - timedelta(days=10)).strftime('%d/%m/%Y')
LIST_TEXT = f"""Rwanda Food and Drugs Authority
LIST OF LICENSED HUMAN RETAIL PHARMACIES
No Name of pharmacy License number District Expiry date
1 KEMI PHARMACY Ltd RW/FDA/PHR/41234 Gasabo {FUTURE}
2 NEW HOPE PHARMACY RW/FDA/PHR/555 Huye {FUTURE}
3 OLD TOWN PHARMACIE RW/FDA/PHR/777 Musanze {PAST}
"""


def tiny_pdf(text):
    """A minimal valid one-page PDF with a text layer (no extra library needed)."""
    lines = text.replace('(', '').replace(')', '').splitlines()
    ops = ['BT /F1 9 Tf 20 800 Td 11 TL']
    for line in lines:
        ops.append(f'({line}) Tj T*')
    ops.append('ET')
    stream = zlib.compress('\n'.join(ops).encode('latin-1'))
    objs = [b'<< /Type /Catalog /Pages 2 0 R >>',
            b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
            b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R '
            b'/Resources << /Font << /F1 5 0 R >> >> >>',
            b'<< /Length %d /Filter /FlateDecode >>\nstream\n' % len(stream) + stream + b'\nendstream',
            b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>']
    out, offsets = b'%PDF-1.4\n', []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += b'%d 0 obj\n' % i + body + b'\nendobj\n'
    xref = len(out)
    out += b'xref\n0 %d\n0000000000 65535 f \n' % (len(objs) + 1)
    out += b''.join(b'%010d 00000 n \n' % o for o in offsets)
    out += b'trailer << /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n' % (len(objs) + 1, xref)
    return out


@override_settings(MEDIA_ROOT=MEDIA, AT_API_KEY='')
class LicenceCheckTests(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.admin_user = User.objects.create_superuser('boss', '', 'pw-12345-xyz')
        self.fda = FdaLicenceList.objects.create(list_date=timezone.localdate(), text=LIST_TEXT, line_count=6)
        self.new = self.make_pharmacy('kemi', 'RW-FDA-PHR-41234', verified=False, name='Kemi Pharmacy',
                                      district='Gasabo', license_document='licenses/x/kemi.pdf')

    # --- matching against the official list ---------------------------------------------------
    def test_finds_licence_whatever_the_separators(self):
        m = find_on_list(self.new, self.fda)
        self.assertTrue(m['found'])
        self.assertTrue(m['name_ok'])
        self.assertTrue(m['district_ok'])
        self.assertEqual(m['expiry'].strftime('%d/%m/%Y'), FUTURE)

    def test_part_of_a_number_does_not_match(self):
        self.new.license_no = 'PHR/1234'  # inside 41234, must not count
        self.assertFalse(find_on_list(self.new, self.fda)['found'])

    def test_wrong_number_but_name_on_list_is_reported(self):
        self.new.license_no = 'RW/FDA/PHR/99999'
        m = find_on_list(self.new, self.fda)
        self.assertFalse(m['found'])
        self.assertIn('41234', m['name_hits'][0])

    def test_automatic_checks_summary(self):
        states = {label.split(' (')[0]: st for label, st, _ in automatic_checks(self.new)}
        self.assertEqual(states['On the official Rwanda FDA list'], OK)
        self.assertEqual(states['Licence valid'], OK)
        expired = self.make_pharmacy('old', 'RW/FDA/PHR/777', verified=False, name='Old Town', district='Musanze')
        states = {label.split(' (')[0]: st for label, st, _ in automatic_checks(expired)}
        self.assertEqual(states['Licence valid'], FAIL)

    def test_pdf_list_is_read(self):
        text = extract_text('list.pdf', tiny_pdf(LIST_TEXT))
        self.assertIn('KEMI PHARMACY', text)
        self.assertIn('41234', text)

    def test_admin_uploads_a_list(self):
        self.client.force_login(self.admin_user)
        response = self.client.post(reverse('admin:core_fdalicencelist_add'), {
            'list_date': '2025-07-01', 'upload': SimpleUploadedFile('list.pdf', tiny_pdf(LIST_TEXT))})
        self.assertEqual(response.status_code, 302)
        uploaded = FdaLicenceList.objects.get(list_date=date(2025, 7, 1))
        self.assertIn('NEW HOPE PHARMACY', uploaded.text)
        self.assertEqual(uploaded.uploaded_by, self.admin_user)

    # --- the admin checklist ----------------------------------------------------------------
    def form(self, pharmacy, **changes):
        data = {f.name: getattr(pharmacy, f.name) for f in Pharmacy._meta.fields
                if f.editable and f.name not in ('id', 'license_document')}
        data['owner'] = pharmacy.owner_id
        data.update(changes)
        return PharmacyAdminForm(data=data, instance=pharmacy)

    def all_checks(self):
        return {f: True for f in Pharmacy.CHECK_FIELDS}

    def test_cannot_verify_without_the_checklist(self):
        form = self.form(self.new, verified=True)
        self.assertFalse(form.is_valid())
        self.assertIn('This pharmacy cannot be verified yet:', str(form.errors))

    def test_verify_with_checklist_fills_expiry_from_list_and_records_who(self):
        form = self.form(self.new, verified=True, **self.all_checks())
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['license_expiry'].strftime('%d/%m/%Y'), FUTURE)
        request = RequestFactory().post('/')
        request.user = self.admin_user
        obj = form.save(commit=False)
        admin_site._registry[Pharmacy].save_model(request, obj, form, change=True)
        obj.refresh_from_db()
        self.assertTrue(obj.verified)
        self.assertEqual(obj.verified_by, self.admin_user)
        self.assertTrue(SmsLog.objects.filter(message__contains='is now verified').exists())  # pharmacy told by SMS

    def test_expired_licence_cannot_be_verified(self):
        form = self.form(self.new, verified=True, license_expiry=timezone.localdate() - timedelta(days=1),
                         **self.all_checks())
        self.assertFalse(form.is_valid())
        self.assertIn('expired', str(form.errors))

    def test_bulk_action_skips_incomplete_checklists(self):
        ready = self.make_pharmacy('ready', 'READY-1', verified=False, license_document='licenses/x/r.pdf',
                                   license_expiry=timezone.localdate() + timedelta(days=200), **self.all_checks())
        self.client.force_login(self.admin_user)
        self.client.post(reverse('admin:core_pharmacy_changelist'),
                         {'action': 'verify_pharmacies', '_selected_action': [ready.pk, self.new.pk]})
        ready.refresh_from_db()
        self.new.refresh_from_db()
        self.assertTrue(ready.verified)
        self.assertEqual(ready.verified_by, self.admin_user)
        self.assertFalse(self.new.verified)

    def test_admin_page_opens_with_a_private_cloudinary_licence(self):
        self.client.force_login(self.admin_user)
        from django.core.files.storage import default_storage
        default_storage._setup()
        private_url = lambda name: CloudinaryStorage.url(None, name)  # noqa: E731 - like on the live site
        with mock.patch.object(default_storage._wrapped, 'url', private_url):
            response = self.client.get(reverse('admin:core_pharmacy_change', args=[self.new.pk]))
        self.assertContains(response, 'Automatic checks')
        self.assertContains(response, 'On the official Rwanda FDA list')
        self.assertContains(response, 'Open the uploaded license')

    # --- expiry hides the pharmacy -----------------------------------------------------------
    def test_expired_licence_is_hidden_from_patients(self):
        self.pharmacy.license_expiry = timezone.localdate() - timedelta(days=1)
        self.pharmacy.save()
        self.assertFalse(Pharmacy.objects.filter(visible_q(), pk=self.pharmacy.pk).exists())
        self.assertNotContains(self.client.get(reverse('search'), {'q': 'Panadol'}), 'Test Pharmacy')
        self.pharmacy.license_expiry = timezone.localdate() + timedelta(days=30)
        self.pharmacy.save()
        self.assertContains(self.client.get(reverse('search'), {'q': 'Panadol'}), 'Test Pharmacy')
