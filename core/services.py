"""Business logic shared by the website, USSD and SMS channels.

Every stock change happens inside a transaction with row locks
(select_for_update) so two patients can never reserve the same last unit.
"""
import difflib
import re
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.crypto import get_random_string

from .billing import visible_q
from .models import Inventory, Medicine, Order, Reservation, SearchLog, StockChange
from .sms import send_sms

# Kept for backwards compatibility with earlier code/tests.
HOLD_HOURS = settings.RESERVATION_HOLD_HOURS


class ReservationError(Exception):
    pass


def find_stock(query, district=''):
    """Verified pharmacies holding a medicine (brand or generic name match)."""
    items = Inventory.objects.filter(
        Q(medicine__name__icontains=query) | Q(medicine__generic_name__icontains=query),
        visible_q('pharmacy__'),
        quantity__gt=0,
    ).select_related('pharmacy', 'medicine')
    if district:
        items = items.filter(pharmacy__district=district)
    return items


def _medicine_words():
    """Words from catalogue names, used to correct typos. Cached for 5 minutes."""
    words = cache.get('medfinder:medicine-words')
    if words is None:
        words = set()
        for name, generic in Medicine.objects.values_list('name', 'generic_name'):
            for w in re.split(r'[^a-zA-Z]+', f'{name} {generic}'.lower()):
                if len(w) >= 4:
                    words.add(w)
        words = sorted(words)
        cache.set('medfinder:medicine-words', words, 300)
    return words


def suggest_spelling(query):
    """'paracetmol' -> 'paracetamol'. Returns a corrected query or None."""
    tokens = [t for t in re.split(r'\s+', query.lower().strip()) if t]
    if not tokens:
        return None
    vocab = _medicine_words()
    fixed, changed = [], False
    for t in tokens:
        if len(t) < 4 or t in vocab:
            fixed.append(t)
            continue
        match = difflib.get_close_matches(t, vocab, n=1, cutoff=0.75)
        if match:
            fixed.append(match[0])
            changed = True
        else:
            fixed.append(t)
    return ' '.join(fixed) if changed else None


def find_alternatives(query, district=''):
    """Other medicines with the same active ingredient, when the searched one is unavailable."""
    med = (Medicine.objects.filter(Q(name__iexact=query) | Q(generic_name__iexact=query)).first()
           or Medicine.objects.filter(Q(name__icontains=query) | Q(generic_name__icontains=query)).first())
    if not med or not med.generic_name:
        return None, []
    ingredient = re.split(r'\s*[+/,]\s*', med.generic_name)[0].strip()
    if len(ingredient) < 3:
        return med, []
    items = list(find_stock(ingredient, district).exclude(medicine=med).order_by('price')[:6])
    return med, items


def log_stock_change(inventory, source, user=None, old_quantity=None, old_price=None, deleted=False):
    """Audit-log a manual stock or price change (skips no-op saves)."""
    new_q = None if deleted else inventory.quantity
    new_p = None if deleted else inventory.price
    if not deleted and old_quantity == new_q and old_price is not None and Decimal(old_price) == Decimal(new_p):
        return None
    if user is not None and not getattr(user, 'is_authenticated', False):
        user = None
    return StockChange.objects.create(
        pharmacy_id=inventory.pharmacy_id, medicine_id=inventory.medicine_id, user=user,
        source='delete' if deleted else source, old_quantity=old_quantity, new_quantity=new_q,
        old_price=old_price, new_price=new_p)


def log_search(query, results, district='', channel='web'):
    """Record a search for demand analytics. Stores no personal data."""
    query = query.strip()[:150]
    if not query:
        return None
    medicine = None
    if results:
        medicine = results[0].medicine
    else:
        medicine = (Medicine.objects.filter(Q(name__iexact=query) | Q(generic_name__iexact=query)).first()
                    or Medicine.objects.filter(Q(name__icontains=query) | Q(generic_name__icontains=query)).first())
    return SearchLog.objects.create(
        query=query,
        medicine=medicine,
        district=district or '',
        found=bool(results),
        result_count=len(results),
        channel=channel,
    )


def _delivery_fee(pharmacy, delivery, address):
    """Validate a delivery request and return the fee (0 for pickup)."""
    if not delivery:
        return Decimal('0')
    if not pharmacy.offers_delivery:
        raise ReservationError('This pharmacy does not deliver. Please choose pickup.')
    if len((address or '').strip()) < 5:
        raise ReservationError('Please enter a delivery address (area, street and a landmark).')
    return pharmacy.delivery_fee or Decimal('0')


def _rx_status(medicine, has_prescription):
    if not medicine.requires_prescription:
        return ''
    return 'uploaded' if has_prescription else 'missing'


def create_reservation(inventory_id, phone, quantity, source='web', delivery=False, address='',
                       prescription=None):
    """Hold stock and create a reservation. Raises ReservationError with a user-safe message."""
    if quantity < 1:
        raise ReservationError('Please enter a quantity of at least 1.')

    with transaction.atomic():
        try:
            inventory = (Inventory.objects.select_for_update()
                         .select_related('pharmacy', 'medicine')
                         .filter(visible_q('pharmacy__'))
                         .get(pk=inventory_id))
        except Inventory.DoesNotExist:
            raise ReservationError('This listing is no longer available.')

        if quantity > inventory.quantity:
            if inventory.quantity == 0:
                raise ReservationError('Sorry, this medicine is now out of stock at this pharmacy.')
            raise ReservationError(f'Only {inventory.quantity} left in stock. Please reduce the quantity.')

        fee = _delivery_fee(inventory.pharmacy, delivery, address)
        rx = prescription if inventory.medicine.requires_prescription else None
        inventory.quantity -= quantity
        inventory.save(update_fields=['quantity', 'updated_at'])
        reservation = Reservation(
            patient_phone=phone,
            inventory=inventory,
            quantity=quantity,
            source=source,
            pickup_code=get_random_string(6, allowed_chars='0123456789'),  # uses secrets module
            delivery=bool(delivery),
            delivery_address=(address or '').strip()[:255] if delivery else '',
            delivery_fee=fee,
            prescription_status=_rx_status(inventory.medicine, bool(rx)),
        )
        if rx:
            reservation.prescription = rx
        reservation.save()

    send_sms(phone, (
        f"MedFinder: {inventory.medicine.name} x{quantity} reserved at "
        f"{inventory.pharmacy.name}. Pickup code: {reservation.pickup_code}. "
        f"Total {reservation.total:.0f} RWF. Held for {settings.RESERVATION_HOLD_HOURS} hours."))
    # Tell the pharmacy so it can prepare the medicine without watching the dashboard.
    extra = ''
    if inventory.medicine.requires_prescription:
        extra += ' (prescription uploaded)' if reservation.prescription else ' (prescription required)'
    if reservation.delivery:
        extra += f'. DELIVERY to: {reservation.delivery_address}'
    send_sms(inventory.pharmacy.phone, (
        f"MedFinder: new reservation #{reservation.pk}: {inventory.medicine.name} x{quantity}{extra}. "
        f"Open your dashboard to mark it ready."))
    return reservation


def create_order(lines, phone, delivery=False, address='', prescription=None):
    """Reserve several medicines at ONE pharmacy together, sharing one pickup code.

    `lines` is a list of (inventory_id, quantity). Every line is checked and held inside one
    transaction, so either the whole order is reserved or nothing is.
    """
    merged = {}
    for inv_id, qty in lines:
        if int(qty) > 0:
            merged[int(inv_id)] = merged.get(int(inv_id), 0) + int(qty)
    if not merged:
        raise ReservationError('Your basket is empty.')
    if len(merged) > 15:
        raise ReservationError('An order can contain at most 15 different medicines.')
    code = get_random_string(6, allowed_chars='0123456789')
    with transaction.atomic():
        stock = {i.pk: i for i in (Inventory.objects.select_for_update()
                                   .select_related('pharmacy', 'medicine')
                                   .filter(visible_q('pharmacy__'), pk__in=list(merged)))}
        if len(stock) != len(merged):
            raise ReservationError('A medicine in your basket is no longer available. Please review your basket.')
        if len({inv.pharmacy_id for inv in stock.values()}) != 1:
            raise ReservationError('All medicines in one order must come from the same pharmacy.')
        pharmacy = next(iter(stock.values())).pharmacy
        fee = _delivery_fee(pharmacy, delivery, address)
        for inv_id, qty in merged.items():
            inv = stock[inv_id]
            if qty > inv.quantity:
                if inv.quantity == 0:
                    raise ReservationError(f'Sorry, {inv.medicine.name} is now out of stock.')
                raise ReservationError(f'Only {inv.quantity} {inv.medicine.name} left. Please reduce the quantity.')

        order = Order.objects.create(patient_phone=phone, pharmacy=pharmacy, pickup_code=code)
        created, saved_rx = [], None
        for n, (inv_id, qty) in enumerate(merged.items()):
            inv = stock[inv_id]
            inv.quantity -= qty
            inv.save(update_fields=['quantity', 'updated_at'])
            needs_rx = inv.medicine.requires_prescription
            res = Reservation(
                patient_phone=phone, inventory=inv, quantity=qty, source='web', pickup_code=code, order=order,
                delivery=bool(delivery),
                delivery_address=(address or '').strip()[:255] if delivery else '',
                delivery_fee=fee if n == 0 else Decimal('0'),  # the fee is charged once per order
                prescription_status=_rx_status(inv.medicine, bool(prescription)),
            )
            if prescription and needs_rx:
                res.prescription = saved_rx or prescription  # one uploaded file shared by all Rx lines
            res.save()
            if res.prescription and not saved_rx:
                saved_rx = res.prescription.name
            created.append(res)

        total = sum((r.total for r in created), Decimal('0'))
        names = ', '.join(f'{r.inventory.medicine.name} x{r.quantity}' for r in created)
        rx_note = ''
        if any(r.needs_prescription for r in created):
            rx_note = ' (prescription uploaded)' if saved_rx else ' (prescription required)'
        delivery_note = f'. DELIVERY to: {created[0].delivery_address}' if delivery else ''
        patient_msg = (f"MedFinder: order #{order.pk} reserved at {pharmacy.name}: {names}. "
                       f"Pickup code: {code}. Total {total:.0f} RWF. "
                       f"Held for {settings.RESERVATION_HOLD_HOURS} hours.")
        pharmacy_msg = (f"MedFinder: new order #{order.pk} ({len(created)} medicines): {names}"
                        f"{rx_note}{delivery_note}. Open your dashboard to prepare it.")
        pharmacy_phone = pharmacy.phone
        transaction.on_commit(lambda: send_sms(phone, patient_msg))
        transaction.on_commit(lambda: send_sms(pharmacy_phone, pharmacy_msg))
    return order


def attach_prescription(reservations, upload):
    """Add a prescription the patient uploads after reserving. Returns how many lines were updated."""
    saved, count = None, 0
    for res in reservations:
        if not (res.is_open and res.needs_prescription and res.prescription_status in ('missing', 'rejected')):
            continue
        res.prescription = saved or upload
        res.prescription_status = 'uploaded'
        res.save(update_fields=['prescription', 'prescription_status'])
        saved = res.prescription.name
        count += 1
    if count:
        first = reservations[0]
        label = f'order #{first.order_id}' if first.order_id else f'reservation #{first.pk}'
        send_sms(first.inventory.pharmacy.phone,
                 f'MedFinder: the patient uploaded a prescription for {label}. Please check it on your dashboard.')
    return count


def patient_cancel_order(order_id, code):
    """Cancel every open, unpaid line of an order. Returns the number of lines cancelled."""
    count = 0
    with transaction.atomic():
        order = Order.objects.select_for_update().select_related('pharmacy').filter(
            pk=order_id, pickup_code=code).first()
        if order is None:
            return 0
        for res in Reservation.objects.select_for_update().filter(order=order):
            if res.is_open and not res.is_paid:
                cancel_reservation(res, reason='patient')
                count += 1
        if count:
            message = (f"MedFinder: order #{order.pk} was cancelled by the patient "
                       f"({count} medicine(s)). Stock has been restored.")
            pharmacy_phone = order.pharmacy.phone
            transaction.on_commit(lambda: send_sms(pharmacy_phone, message))
    return count


def patient_cancel(reservation_id, code):
    """Let a patient release their own unpaid reservation. Returns True if cancelled."""
    with transaction.atomic():
        res = (Reservation.objects.select_for_update()
               .select_related('inventory__pharmacy', 'inventory__medicine')
               .filter(pk=reservation_id, pickup_code=code).first())
        if res is None or not res.is_open or res.is_paid:
            return False
        cancel_reservation(res, reason='patient')
        message = (f"MedFinder: reservation #{res.pk} ({res.inventory.medicine.name} x{res.quantity}) "
                   f"was cancelled by the patient. Stock has been restored.")
        pharmacy_phone = res.inventory.pharmacy.phone
        transaction.on_commit(lambda: send_sms(pharmacy_phone, message))
    return True


def cancel_reservation(reservation, reason='pharmacy'):
    """Restore stock and cancel. Must be called inside transaction.atomic() with the reservation locked."""
    inventory = Inventory.objects.select_for_update().get(pk=reservation.inventory_id)
    inventory.quantity += reservation.quantity
    inventory.save(update_fields=['quantity', 'updated_at'])
    reservation.status = 'cancelled'
    reservation.cancel_reason = reason
    reservation.completed_at = timezone.now()
    reservation.save(update_fields=['status', 'cancel_reason', 'completed_at'])
    # A paid reservation that is cancelled must be refunded by the pharmacy/platform.
    reservation.payments.filter(status='successful').update(status='refund_due')


def send_hold_reminders():
    """SMS patients shortly before an unpaid reservation is released. Returns count."""
    hold = timedelta(hours=settings.RESERVATION_HOLD_HOURS)
    lead = timedelta(minutes=settings.RESERVATION_REMINDER_MINUTES)
    now = timezone.now()
    due = (Reservation.objects
           .filter(status__in=['pending', 'ready'], reminder_sent=False,
                   created_at__lt=now - hold + lead, created_at__gte=now - hold)
           .exclude(payments__status='successful')
           .select_related('inventory__medicine', 'inventory__pharmacy'))
    count = 0
    reminded_orders = set()
    for res in due:
        # Mark first so two simultaneous requests never send the reminder twice.
        if not Reservation.objects.filter(pk=res.pk, reminder_sent=False).update(reminder_sent=True):
            continue
        what = res.inventory.medicine.name
        if res.order_id:
            if res.order_id in reminded_orders:
                continue  # one reminder per order, not one per medicine
            reminded_orders.add(res.order_id)
            what = f'order #{res.order_id}'
        until = timezone.localtime(res.expires_at)
        send_sms(res.patient_phone,
                 f'MedFinder: reminder, your {what} at {res.inventory.pharmacy.name} '
                 f'is held until {until:%H:%M}. Code {res.pickup_code}. Not needed? Please cancel so others can buy it.')
        count += 1
    return count


def expire_old_reservations():
    """Send hold reminders, then cancel unpaid reservations older than the hold time and restore stock.

    Paid reservations are kept: the patient has already paid and the pharmacy
    resolves them manually (pickup or refund).
    """
    send_hold_reminders()
    cutoff = timezone.now() - timedelta(hours=settings.RESERVATION_HOLD_HOURS)
    ids = list(Reservation.objects
               .filter(status__in=['pending', 'ready'], created_at__lt=cutoff)
               .exclude(payments__status='successful')
               .values_list('pk', flat=True))
    count = 0
    for pk in ids:
        with transaction.atomic():
            res = Reservation.objects.select_for_update().select_related(
                'inventory__medicine', 'inventory__pharmacy').get(pk=pk)
            if res.status not in ('pending', 'ready'):
                continue
            cancel_reservation(res, reason='expired')
            message = (f"MedFinder: your reservation for {res.inventory.medicine.name} at "
                       f"{res.inventory.pharmacy.name} expired after "
                       f"{settings.RESERVATION_HOLD_HOURS} hours and was released.")
            phone = res.patient_phone
            transaction.on_commit(lambda p=phone, m=message: send_sms(p, m))
            count += 1
    return count
