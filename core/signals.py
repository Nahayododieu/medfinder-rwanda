"""Back-in-stock SMS alerts.

When any channel (dashboard, CSV import, SMS, USSD, cancelled reservation) saves an
inventory row with stock, patients waiting for that medicine get one SMS.
"""
from django.db import transaction
from django.db.models import Q
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

from .billing import visible_q
from .models import Inventory, Pharmacy, StockAlert
from .sms import send_sms


def notify_waiting_patients(inventory_id):
    inv = Inventory.objects.select_related('pharmacy', 'medicine').filter(pk=inventory_id, quantity__gt=0).first()
    if not inv or not Pharmacy.objects.filter(visible_q(), pk=inv.pharmacy_id).exists():
        return 0
    p = inv.pharmacy
    alerts = StockAlert.objects.filter(medicine_id=inv.medicine_id, notified_at__isnull=True).filter(
        Q(district='') | Q(district=p.district))
    sent = 0
    for alert in alerts:
        # Mark first so a second save at the same moment cannot send twice.
        if StockAlert.objects.filter(pk=alert.pk, notified_at__isnull=True).update(notified_at=timezone.now()):
            send_sms(alert.phone, f'MedFinder: {inv.medicine.name} is now available at {p.name} '
                                  f'({p.district}), {inv.price:.0f} RWF. Call {p.phone} or reserve on MedFinder.')
            sent += 1
    return sent


@receiver(post_save, sender=Inventory)
def inventory_saved(sender, instance, **kwargs):
    if instance.quantity > 0 and StockAlert.objects.filter(
            medicine_id=instance.medicine_id, notified_at__isnull=True).exists():
        transaction.on_commit(lambda: notify_waiting_patients(instance.pk))
