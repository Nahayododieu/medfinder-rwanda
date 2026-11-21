from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import Inventory, Pharmacy
from core.sms import send_sms


def send_expiry_alerts():
    """One SMS per pharmacy listing stock that expires within EXPIRY_ALERT_DAYS (each item once)."""
    limit = timezone.localdate() + timedelta(days=settings.EXPIRY_ALERT_DAYS)
    sent = 0
    for pharmacy in Pharmacy.objects.filter(verified=True):
        items = list(Inventory.objects.filter(pharmacy=pharmacy, quantity__gt=0, expiry_alert_sent=False,
                                              expiry_date__isnull=False, expiry_date__lte=limit)
                     .select_related('medicine').order_by('expiry_date'))
        if not items:
            continue
        listed = '; '.join(f'{i.medicine.name} {i.expiry_date:%d %b}' for i in items[:5])
        more = f' and {len(items) - 5} more' if len(items) > 5 else ''
        send_sms(pharmacy.phone, f'MedFinder: stock expiring soon: {listed}{more}. Check your dashboard.')
        Inventory.objects.filter(pk__in=[i.pk for i in items]).update(expiry_alert_sent=True)
        sent += 1
    return sent


class Command(BaseCommand):
    help = 'SMS pharmacies about stock that expires soon (run once a day).'

    def handle(self, *args, **options):
        n = send_expiry_alerts()
        self.stdout.write(self.style.SUCCESS(f'Sent expiry alerts to {n} pharmacy(ies).'))
