"""Pharmacy subscriptions: trial, paid periods, reminders and visibility.

A pharmacy is covered until max(trial_ends_at, paid_until). After that it has
SUBSCRIPTION_GRACE_DAYS during which it is still visible; then, if
HIDE_UNPAID_PHARMACIES is on, it disappears from search until it pays.
"""
import logging
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.crypto import get_random_string

from . import payments
from .models import Pharmacy, SubscriptionPayment
from .sms import send_sms

logger = logging.getLogger(__name__)

PLAN_MONTHS = (1, 3, 6, 12)


def price_for(months):
    return Decimal(settings.SUBSCRIPTION_PRICE_RWF) * months


def visible_q(prefix=''):
    """Q filter for pharmacies patients may see (verified, licence not expired, not hidden for non-payment)."""
    q = Q(**{f'{prefix}verified': True})
    q &= Q(**{f'{prefix}license_expiry__isnull': True}) | Q(**{f'{prefix}license_expiry__gte': timezone.localdate()})
    if settings.HIDE_UNPAID_PHARMACIES:
        cutoff = timezone.localdate() - timedelta(days=settings.SUBSCRIPTION_GRACE_DAYS)
        q &= (Q(**{f'{prefix}trial_ends_at__isnull': True, f'{prefix}paid_until__isnull': True})
              | Q(**{f'{prefix}trial_ends_at__gte': cutoff})
              | Q(**{f'{prefix}paid_until__gte': cutoff}))
    return q


def extend_coverage(pharmacy, months):
    """Add paid months after whatever the pharmacy is already covered for. Returns (start, end)."""
    today = timezone.localdate()
    current_end = pharmacy.coverage_end
    start = max(today, current_end + timedelta(days=1)) if current_end else today
    end = start + timedelta(days=settings.SUBSCRIPTION_MONTH_DAYS * months - 1)
    pharmacy.paid_until = end
    pharmacy.last_reminder = ''
    pharmacy.save(update_fields=['paid_until', 'last_reminder'])
    return start, end


def _confirm(payment):
    """Must run inside transaction.atomic(). The SMS goes out only after the commit,
    so a slow SMS gateway never holds the database lock."""
    start, end = extend_coverage(payment.pharmacy, payment.months)
    payment.status = 'successful'
    payment.period_start, payment.period_end = start, end
    payment.save(update_fields=['status', 'period_start', 'period_end', 'updated_at'])
    phone = payment.pharmacy.phone
    message = (f'MedFinder: payment of {payment.amount:.0f} RWF received. Your listing is active until '
               f'{end:%d %b %Y}. Thank you!')
    transaction.on_commit(lambda: send_sms(phone, message))


def record_manual_payment(pharmacy, months, note='admin'):
    """Admin records a cash or bank payment."""
    with transaction.atomic():
        payment = SubscriptionPayment.objects.create(
            pharmacy=pharmacy, months=months, amount=price_for(months), method='cash',
            provider_reference=f'cash-{get_random_string(16)}', failure_reason=note[:200])
        _confirm(payment)
    return payment


def start_mobile_payment(pharmacy, months, phone):
    """Send a mobile money request (MTN or Airtel, by number). Raises payments.PaymentError."""
    provider = payments.provider_for(phone)
    if provider is None:
        raise payments.PaymentError('Use an MTN (078/079) or Airtel (072/073) number.')
    amount = price_for(months)
    reference = payments.request_to_pay(provider, amount, phone, external_id=f'sub-{pharmacy.pk}',
                                        note=f'MedFinder listing {months} month(s)')
    return SubscriptionPayment.objects.create(
        pharmacy=pharmacy, months=months, amount=amount, method='momo' if provider == 'mtn' else 'airtel',
        payer_phone=phone, provider_reference=reference, simulated=payments.is_simulation(provider))


start_momo_payment = start_mobile_payment  # backwards-compatible name


def provider_of(payment):
    return 'airtel' if payment.method == 'airtel' else 'mtn'


def apply_status(payment, status, reason=''):
    """Apply a MoMo result exactly once. Returns True if the payment changed."""
    with transaction.atomic():
        payment = SubscriptionPayment.objects.select_for_update().get(pk=payment.pk)
        if payment.status != 'pending' or status == 'pending':
            return False
        if status == 'successful':
            _confirm(payment)
        else:
            payment.status = 'failed'
            payment.failure_reason = reason[:200]
            payment.save(update_fields=['status', 'failure_reason', 'updated_at'])
    return True


def send_reminders():
    """SMS pharmacies before and after their coverage ends. Each message is sent once. Returns count."""
    sent = 0
    grace = settings.SUBSCRIPTION_GRACE_DAYS
    price = settings.SUBSCRIPTION_PRICE_RWF
    for p in Pharmacy.objects.filter(verified=True):
        end, left = p.coverage_end, p.days_left
        if end is None:
            continue
        kind = 'trial' if not (p.paid_until and p.paid_until >= end) else 'subscription'
        message = None
        if left in settings.SUBSCRIPTION_REMINDER_DAYS:
            step = f'before{left}'
            message = (f'MedFinder: your {kind} ends on {end:%d %b %Y} ({left} day{"s" if left != 1 else ""}). '
                       f'Pay {price} RWF/month by MoMo in your dashboard (Billing) or call {settings.SUPPORT_PHONE}.')
        elif -grace <= left < 0 and not p.last_reminder.endswith('overdue'):
            step = 'overdue'
            message = (f'MedFinder: your {kind} ended on {end:%d %b %Y}. Pay within {grace} days after that '
                       f'date to stay visible to patients.')
        elif left < -grace and settings.HIDE_UNPAID_PHARMACIES:
            step = 'hidden'
            message = ('MedFinder: your pharmacy is now hidden from patient search because the subscription '
                       'is unpaid. Pay in your dashboard (Billing) to appear again.')
        else:
            continue
        key = f'{end:%Y%m%d}:{step}'
        if p.last_reminder == key:
            continue
        send_sms(p.phone, message)
        p.last_reminder = key
        p.save(update_fields=['last_reminder'])
        sent += 1
    return sent


def subscription_summary():
    today = timezone.localdate()
    counts = {'trial': 0, 'active': 0, 'grace': 0, 'overdue': 0}
    for p in Pharmacy.objects.filter(verified=True):
        counts[p.subscription_status] = counts.get(p.subscription_status, 0) + 1
    since = timezone.now() - timedelta(days=30)
    paid = SubscriptionPayment.objects.filter(status='successful', updated_at__gte=since)
    revenue = sum((x.amount for x in paid), Decimal('0'))
    return {
        'counts': counts,
        'mrr': counts['active'] * settings.SUBSCRIPTION_PRICE_RWF,
        'revenue_30d': revenue,
        'today': today,
    }
