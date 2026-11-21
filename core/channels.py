"""USSD menu and incoming-SMS commands (Africa's Talking).

USSD is stateless: Africa's Talking sends the whole input path in `text`,
e.g. "1*paracetamol*2*3*1". Each request rebuilds the screen from that path.
Responses start with "CON " (continue) or "END " (close session).
"""
import re
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db.models import Q

from .models import Inventory, Medicine, Pharmacy, Reservation
from .services import ReservationError, create_reservation, find_stock, log_search, log_stock_change
from .utils import normalize_phone

MAX_OPTIONS = 5

USSD_TEXT = {
    'en': {
        'menu': 'Welcome to MedFinder\n1. Search medicine\n2. Check my reservation\n3. Help\n4. Ikinyarwanda',
        'enter_med': 'Enter medicine name:',
        'none': 'No verified pharmacy has "{q}" in stock now. Try another name later.',
        'choose': '{q}: choose pharmacy',
        'enter_qty': '{med} at {ph}\n{price} RWF each, {stock} available.\nEnter quantity:',
        'bad_choice': 'Invalid choice. Please dial again.',
        'bad_qty': 'Invalid quantity. Please dial again.',
        'confirm': 'Reserve {qty} x {med} at {ph}?\nTotal {total} RWF\n1. Confirm\n2. Cancel',
        'reserved': 'Reserved! Code {code}. Reservation no. {pk}. Collect at {ph} within {h}h. SMS sent.',
        'aborted': 'Reservation cancelled. Thank you for using MedFinder.',
        'enter_code': 'Enter your 6-digit pickup code:',
        'res_status': '{med} x{qty} at {ph}: {status}. Code {code}.',
        'no_res': 'No reservation found for this phone and code.',
        'help': 'MedFinder: search medicine at verified pharmacies, reserve and pay at pickup. Bring a prescription when needed.',
        'invalid': 'Invalid option. Please dial again.',
        'status': {'pending': 'being prepared', 'ready': 'READY for pickup', 'done': 'picked up', 'cancelled': 'cancelled'},
        'ph_menu': '5. Pharmacy: update stock',
        'ph_enter_med': 'Update stock. Enter medicine name:',
        'ph_no_med': '"{q}" is not in the catalogue. Ask the admin to add it.',
        'ph_choose': 'Choose medicine:',
        'ph_not_listed': 'not listed yet',
        'ph_enter_qty': '{med}\nNow: {current}\nEnter new quantity:',
        'ph_enter_price': 'New item. Enter price in RWF:',
        'ph_done': 'Updated: {med} = {qty} units at {price} RWF.',
    },
    'rw': {
        'menu': 'Murakaza neza kuri MedFinder\n1. Shakisha umuti\n2. Reba ibyo nabikishije\n3. Ubufasha',
        'enter_med': 'Andika izina ry\'umuti:',
        'none': 'Nta farumasi yemewe ifite "{q}" ubu. Uzongere ugerageze.',
        'choose': '{q}: hitamo farumasi',
        'enter_qty': '{med} kuri {ph}\n{price} RWF kimwe, {stock} birahari.\nAndika umubare:',
        'bad_choice': 'Ibyo wahisemo si byo. Ongera ukande.',
        'bad_qty': 'Umubare si wo. Ongera ukande.',
        'confirm': 'Bikisha {qty} x {med} kuri {ph}?\nIgiteranyo {total} RWF\n1. Emeza\n2. Reka',
        'reserved': 'Byabikishijwe! Kode {code}. Nimero {pk}. Fata kuri {ph} mu masaha {h}. SMS yoherejwe.',
        'aborted': 'Byahagaritswe. Murakoze gukoresha MedFinder.',
        'enter_code': 'Andika kode y\'imibare 6:',
        'res_status': '{med} x{qty} kuri {ph}: {status}. Kode {code}.',
        'no_res': 'Nta byabikishijwe bibonetse kuri iyi telefoni n\'iyi kode.',
        'help': 'MedFinder: shakisha umuti muri farumasi zemewe, ubikishe wishyure ugezeyo. Zana urupapuro rwa muganga bibaye ngombwa.',
        'invalid': 'Ibyo wahisemo si byo. Ongera ukande.',
        'status': {'pending': 'birategurwa', 'ready': 'BYITEGUYE', 'done': 'byafashwe', 'cancelled': 'byahagaritswe'},
        'ph_menu': '5. Farumasi: vugurura imiti',
        'ph_enter_med': 'Vugurura imiti. Andika izina ry\'umuti:',
        'ph_no_med': '"{q}" ntiri ku rutonde. Saba umuyobozi kuwongeraho.',
        'ph_choose': 'Hitamo umuti:',
        'ph_not_listed': 'ntiwanditse',
        'ph_enter_qty': '{med}\nUbu: {current}\nAndika umubare mushya:',
        'ph_enter_price': 'Umuti mushya. Andika igiciro (RWF):',
        'ph_done': 'Byavuguruwe: {med} = {qty} ku {price} RWF.',
    },
}


def _options(query):
    items = list(find_stock(query).order_by('price', 'pk')[:MAX_OPTIONS])
    return items


def ussd_response(phone, text):
    """Return the USSD screen for an input path."""
    parts = [p.strip() for p in (text or '').split('*')] if text else []
    lang = 'en'
    if parts and parts[0] == '4':
        lang = 'rw'
        parts = parts[1:]
    t = USSD_TEXT[lang]

    if not parts:
        menu = t['menu']
        if find_pharmacy_by_phone(phone):
            menu += '\n' + t['ph_menu']
        return 'CON ' + menu

    choice = parts[0]

    # 1. Search and reserve
    if choice == '1':
        if len(parts) == 1:
            return 'CON ' + t['enter_med']
        query = parts[1][:60]
        if not query:
            return 'END ' + t['invalid']
        items = _options(query)
        if len(parts) == 2:
            log_search(query, items, channel='ussd')
        if not items:
            return 'END ' + t['none'].format(q=query)
        if len(parts) == 2:
            lines = [t['choose'].format(q=query)]
            for i, it in enumerate(items, 1):
                lines.append(f'{i}. {it.pharmacy.name[:18]} ({it.pharmacy.district}) {it.price:.0f}')
            return 'CON ' + '\n'.join(lines)

        try:
            item = items[int(parts[2]) - 1]
            if int(parts[2]) < 1:
                raise IndexError
        except (ValueError, IndexError):
            return 'END ' + t['bad_choice']
        if len(parts) == 3:
            return 'CON ' + t['enter_qty'].format(
                med=item.medicine.name, ph=item.pharmacy.name, price=f'{item.price:.0f}', stock=item.quantity)

        try:
            qty = int(parts[3])
        except ValueError:
            return 'END ' + t['bad_qty']
        if qty < 1 or qty > item.quantity:
            return 'END ' + t['bad_qty']
        if len(parts) == 4:
            return 'CON ' + t['confirm'].format(
                qty=qty, med=item.medicine.name, ph=item.pharmacy.name, total=f'{item.price * qty:.0f}')

        if parts[4] != '1':
            return 'END ' + t['aborted']
        try:
            res = create_reservation(item.pk, normalize_phone(phone), qty, source='ussd')
        except ReservationError as e:
            return 'END ' + str(e)
        return 'END ' + t['reserved'].format(
            code=res.pickup_code, pk=res.pk, ph=item.pharmacy.name, h=settings.RESERVATION_HOLD_HOURS)

    # 2. Check reservation
    if choice == '2':
        if len(parts) == 1:
            return 'CON ' + t['enter_code']
        code = parts[1]
        number = normalize_phone(phone)
        res = (Reservation.objects.filter(pickup_code=code)
               .filter(Q(patient_phone=number) | Q(patient_phone=phone))
               .select_related('inventory__medicine', 'inventory__pharmacy').first())
        if not res:
            return 'END ' + t['no_res']
        return 'END ' + t['res_status'].format(
            med=res.inventory.medicine.name, qty=res.quantity, ph=res.inventory.pharmacy.name,
            status=t['status'][res.status], code=res.pickup_code)

    if choice == '3':
        return 'END ' + t['help']

    # 5. Pharmacy stock update (only for phones registered to a verified pharmacy)
    if choice == '5':
        return _ussd_pharmacy(phone, parts[1:], t)

    return 'END ' + t['invalid']


def _ussd_pharmacy(phone, parts, t):
    pharmacy = find_pharmacy_by_phone(phone)
    if pharmacy is None:
        return 'END ' + t['invalid']
    if not parts:
        return 'CON ' + t['ph_enter_med']
    name = parts[0][:60]
    meds = list(Medicine.objects.filter(Q(name__icontains=name) | Q(generic_name__icontains=name))
                .order_by('name')[:MAX_OPTIONS])
    if not meds:
        return 'END ' + t['ph_no_med'].format(q=name)
    if len(parts) == 1:
        lines = [t['ph_choose']] + [f'{i}. {m.name[:28]}' for i, m in enumerate(meds, 1)]
        return 'CON ' + '\n'.join(lines)
    try:
        idx = int(parts[1])
        if idx < 1:
            raise IndexError
        med = meds[idx - 1]
    except (ValueError, IndexError):
        return 'END ' + t['bad_choice']
    item = Inventory.objects.filter(pharmacy=pharmacy, medicine=med).first()
    if len(parts) == 2:
        current = f'{item.quantity} @ {item.price:.0f} RWF' if item else t['ph_not_listed']
        return 'CON ' + t['ph_enter_qty'].format(med=med.name, current=current)
    try:
        qty = int(parts[2])
        if qty < 0:
            raise ValueError
    except ValueError:
        return 'END ' + t['bad_qty']
    if item is None and len(parts) == 3:
        return 'CON ' + t['ph_enter_price']
    if item is None:
        try:
            price = Decimal(parts[3])
            if price < 0:
                raise InvalidOperation
        except InvalidOperation:
            return 'END ' + t['bad_qty']
        item = Inventory.objects.create(pharmacy=pharmacy, medicine=med, quantity=qty, price=price)
        log_stock_change(item, 'ussd', pharmacy.owner, None, None)
    else:
        old_q, old_p = item.quantity, item.price
        item.quantity = qty
        item.save()
        log_stock_change(item, 'ussd', pharmacy.owner, old_q, old_p)
    return 'END ' + t['ph_done'].format(med=med.name, qty=item.quantity, price=f'{item.price:.0f}')


# ---------------------------------------------------------------------------
# Incoming SMS: pharmacies update stock without opening the website.
# ---------------------------------------------------------------------------
SMS_HELP = ('MedFinder SMS commands:\n'
            'STOCK <medicine> <qty> [price]  e.g. STOCK Paracetamol 40 200\n'
            'LIST  - your stock\n'
            'HELP  - this message')


def find_pharmacy_by_phone(phone):
    number = normalize_phone(phone)
    for p in Pharmacy.objects.filter(verified=True):
        if normalize_phone(p.phone) == number:
            return p
    return None


def _match_medicine(name):
    exact = Medicine.objects.filter(Q(name__iexact=name) | Q(generic_name__iexact=name))
    if exact.count() == 1:
        return exact.first(), None
    if exact.count() > 1:
        return None, 'Several medicines match "%s". Use the full name shown on the website.' % name
    partial = Medicine.objects.filter(Q(name__icontains=name) | Q(generic_name__icontains=name))
    if partial.count() == 1:
        return partial.first(), None
    if partial.count() > 1:
        options = ', '.join(m.name for m in partial[:4])
        return None, f'Several medicines match "{name}": {options}. Send the full name.'
    return None, f'Medicine "{name}" is not in the MedFinder catalogue. Ask the admin to add it.'


def handle_incoming_sms(phone, text):
    """Process a pharmacy SMS command and return the reply text."""
    pharmacy = find_pharmacy_by_phone(phone)
    if pharmacy is None:
        return 'MedFinder: this number is not linked to a verified pharmacy.'

    words = (text or '').strip().split()
    if not words:
        return SMS_HELP
    command = words[0].upper()

    if command == 'HELP':
        return SMS_HELP

    if command == 'LIST':
        items = (Inventory.objects.filter(pharmacy=pharmacy).select_related('medicine')
                 .order_by('medicine__name')[:12])
        if not items:
            return 'MedFinder: you have no stock listed yet.'
        return 'Your stock: ' + '; '.join(f'{i.medicine.name} {i.quantity} @ {i.price:.0f}' for i in items)

    if command == 'STOCK':
        args = words[1:]
        numbers = []
        while args and re.fullmatch(r'\d+(\.\d+)?', args[-1]) and len(numbers) < 2:
            numbers.insert(0, args.pop())
        name = ' '.join(args)
        if not name or not numbers:
            return 'Format: STOCK <medicine> <qty> [price]. Example: STOCK Paracetamol 40 200'
        medicine, error = _match_medicine(name)
        if error:
            return error
        try:
            qty = int(Decimal(numbers[0]))
            price = Decimal(numbers[1]) if len(numbers) == 2 else None
        except (InvalidOperation, ValueError):
            return 'Quantity and price must be numbers.'
        if qty < 0 or (price is not None and price < 0):
            return 'Quantity and price must be positive.'

        item = Inventory.objects.filter(pharmacy=pharmacy, medicine=medicine).first()
        if item is None:
            if price is None:
                return f'{medicine.name} is new in your stock. Send the price too: STOCK {medicine.name} {qty} <price>'
            item = Inventory.objects.create(pharmacy=pharmacy, medicine=medicine, quantity=qty, price=price)
            log_stock_change(item, 'sms', pharmacy.owner, None, None)
        else:
            old_q, old_p = item.quantity, item.price
            item.quantity = qty
            if price is not None:
                item.price = price
            item.save()
            log_stock_change(item, 'sms', pharmacy.owner, old_q, old_p)
            price = item.price
        return f'MedFinder: {medicine.name} updated to {qty} units at {price:.0f} RWF.'

    return 'Unknown command. ' + SMS_HELP
