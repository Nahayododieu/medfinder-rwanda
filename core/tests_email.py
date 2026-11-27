"""Email notifications: password reset (HTML), registration, verification and password-change notices."""
from unittest import mock

from django.contrib.auth.models import User
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse

from .models import Pharmacy
from .tests import BaseTestCase
from .tests_features import MEDIA


@override_settings(MEDIA_ROOT=MEDIA, AT_API_KEY='')
class EmailNotificationTests(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.boss = User.objects.create_superuser('boss', 'boss@example.com', 'pw-12345-xyz')
        self.owner.email = 'owner@example.com'
        self.owner.save()

    def register(self):
        data = {'username': 'newpharma', 'email': 'new@example.com', 'password': 'Strong-pass-2026',
                'password_confirm': 'Strong-pass-2026', 'name': 'New Pharmacy', 'license_no': 'NEW-001',
                'district': 'Gasabo', 'phone': '0788123456', 'address': '', 'latitude': '-1.95',
                'longitude': '30.06', 'agree': 'on',
                'license_document': SimpleUploadedFile('lic.pdf', b'%PDF-1.4 test', content_type='application/pdf')}
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(reverse('register'), data)

    def test_registration_sends_welcome_and_admin_alert(self):
        self.assertEqual(self.register().status_code, 302)
        by_to = {m.to[0]: m for m in mail.outbox}
        welcome, alert = by_to['new@example.com'], by_to['boss@example.com']
        self.assertIn('registration', welcome.subject.lower())
        self.assertIn('NEW-001', welcome.body)
        self.assertIn('New Pharmacy', alert.subject)
        html = alert.alternatives[0][0]
        self.assertIn('Check the licence', html)
        self.assertIn(reverse('admin:core_pharmacy_change', args=[Pharmacy.objects.get(license_no='NEW-001').pk]), html)

    def test_email_failure_does_not_break_registration(self):
        with mock.patch('django.core.mail.EmailMultiAlternatives.send', side_effect=OSError('smtp down')):
            response = self.register()
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Pharmacy.objects.filter(license_no='NEW-001').exists())

    def test_password_reset_email_is_branded_html(self):
        self.client.post(reverse('password_reset'), {'email': 'owner@example.com'})
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertIn('/password/reset/', message.body)
        html = message.alternatives[0][0]
        self.assertIn('Choose a new password', html)
        self.assertIn('/password/reset/', html)

    def test_password_change_sends_security_notice(self):
        self.client.force_login(self.owner)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('password_change'), {
                'old_password': 'test-password', 'new_password1': 'Another-pass-2026',
                'new_password2': 'Another-pass-2026'})
        self.assertEqual(mail.outbox[-1].to, ['owner@example.com'])
        self.assertIn('password was changed', mail.outbox[-1].subject)

    def test_verification_emails_the_pharmacy(self):
        p = self.make_pharmacy('ready', 'READY-1', verified=False, license_document='licenses/x/r.pdf',
                               **{f: True for f in Pharmacy.CHECK_FIELDS})
        p.owner.email = 'ready@example.com'
        p.owner.save()
        p.license_expiry = p.created_at.date().replace(year=p.created_at.year + 1)
        p.save()
        self.client.force_login(self.boss)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('admin:core_pharmacy_changelist'),
                             {'action': 'verify_pharmacies', '_selected_action': [p.pk]})
        sent = [m for m in mail.outbox if m.to == ['ready@example.com']]
        self.assertEqual(len(sent), 1)
        self.assertIn('is now live', sent[0].subject)
