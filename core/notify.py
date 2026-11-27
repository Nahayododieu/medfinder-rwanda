"""Branded email notifications (plain text + HTML).

Every email is sent after the database change is saved, and a failed email never breaks the page:
the problem is logged (visible in Render's Logs) and the user's action still succeeds.
"""
import logging

from django.conf import settings
from django.contrib.auth.models import User
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.template.loader import render_to_string
from django.urls import reverse

logger = logging.getLogger(__name__)


def site_url(path=''):
    host = getattr(settings, 'PUBLIC_HOST', '') or 'medfinder-rwanda.onrender.com'
    return f'https://{host}{path}'


def send(subject, to, template, context=None, reply_to=None):
    """Send `core/email/<template>.txt` (+ `.html`) to a list of addresses. Returns True if sent."""
    to = [a for a in (to or []) if a]
    if not to:
        return False
    ctx = {'site_url': site_url(), 'support_email': settings.SUPPORT_EMAIL, 'support_phone': settings.SUPPORT_PHONE,
           'business_name': settings.BUSINESS_NAME, **(context or {})}
    ctx['subject'] = subject
    try:
        text = render_to_string(f'core/email/{template}.txt', ctx)
        html = render_to_string(f'core/email/{template}.html', ctx)
        message = EmailMultiAlternatives(subject, text, settings.DEFAULT_FROM_EMAIL, to,
                                         reply_to=reply_to or [settings.SUPPORT_EMAIL])
        message.attach_alternative(html, 'text/html')
        message.send()
        logger.info('Email "%s" sent to %d recipient(s)', subject, len(to))
        return True
    except Exception:
        logger.exception('Email "%s" could not be sent', subject)
        return False


def send_later(*args, **kwargs):
    """Send once the current database transaction is saved (nothing is emailed for a rolled-back action)."""
    transaction.on_commit(lambda: send(*args, **kwargs))


def admin_emails():
    return list(User.objects.filter(is_superuser=True, is_active=True).exclude(email='')
                .values_list('email', flat=True))


# --- The notifications ---------------------------------------------------------------------------
def pharmacy_registered(pharmacy):
    """Welcome email to the new pharmacy, and an alert to the administrators."""
    send_later('We received your MedFinder registration', [pharmacy.owner.email], 'registration_received',
               {'pharmacy': pharmacy, 'login_url': site_url(reverse('login'))})
    send_later(f'New pharmacy to verify: {pharmacy.name}', admin_emails(), 'admin_new_pharmacy',
               {'pharmacy': pharmacy,
                'admin_url': site_url(reverse('admin:core_pharmacy_change', args=[pharmacy.pk]))})


def pharmacy_verified(pharmacy):
    send_later(f'{pharmacy.name} is now live on MedFinder', [pharmacy.owner.email], 'pharmacy_verified',
               {'pharmacy': pharmacy, 'dashboard_url': site_url(reverse('dashboard'))})


def password_changed(user):
    """Security notice: tells the owner of the account if someone else changed the password."""
    send_later('Your MedFinder password was changed', [user.email], 'password_changed',
               {'user': user, 'reset_url': site_url(reverse('password_reset'))})
