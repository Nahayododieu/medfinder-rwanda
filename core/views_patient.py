"""Patient features: basket (several medicines in one order), prescriptions, and "My reservations".

The basket lives in the session and holds medicines from ONE pharmacy, because an order is
picked up (or delivered) from one place with one pickup code and paid with one mobile money request.
"""
import mimetypes
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.hashers import check_password, make_password
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import get_random_string
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from . import payments
from .access import get_membership
from .billing import visible_q
from .forms import CheckoutForm, LoginCodeForm, PatientPhoneForm, PaymentForm, PrescriptionForm
from .models import Inventory, Order, Payment, Reservation
from .ratelimit import ratelimit
from .services import (ReservationError, attach_prescription, create_order, expire_old_reservations,
                       patient_cancel_order)
from .sms import send_sms

BASKET_KEY = 'medfinder_basket'
PATIENT_KEY = 'medfinder_patient'          # verified phone + expiry
PATIENT_PENDING_KEY = 'medfinder_patient_code'
PATIENT_SESSION_DAYS = 30
MAX_LINES = 15


# ---------------------------------------------------------------------------
# Basket helpers
# ---------------------------------------------------------------------------
def _basket(request):
    b = request.session.get(BASKET_KEY) or {}
    return {'pharmacy': b.get('pharmacy'), 'items': {str(k): int(v) for k, v in (b.get('items') or {}).items()}}


def _save_basket(request, basket):
    if basket['items']:
        request.session[BASKET_KEY] = basket
    else:
        request.session.pop(BASKET_KEY, None)


def basket_count(request):
    return sum(_basket(request)['items'].values()) if hasattr(request, 'session') else 0


def basket_context(request):
    """Template context: number of items in the basket and the signed-in patient phone (if any)."""
    if not hasattr(request, 'session'):
        return {}
    return {'basket_count': basket_count(request), 'patient_phone': _patient_phone(request)}


def _basket_lines(basket):
    ids = [int(i) for i in basket['items']]
    stock = {i.pk: i for i in Inventory.objects.filter(visible_q('pharmacy__'), pk__in=ids)
             .select_related('pharmacy', 'medicine')}
    lines = []
    for inv_id, qty in basket['items'].items():
        inv = stock.get(int(inv_id))
        if inv is None:
            continue
        lines.append({'inv': inv, 'qty': qty, 'subtotal': inv.price * qty,
                      'short': qty > inv.quantity})
    return lines


def _next_url(request, default):
    target = request.POST.get('next', '')
    if target and url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}):
        return target
    return default


@require_POST
def basket_add(request, pk):
    inv = Inventory.objects.filter(visible_q('pharmacy__'), pk=pk, quantity__gt=0).select_related(
        'pharmacy', 'medicine').first()
    if inv is None:
        messages.error(request, 'Sorry, this medicine is no longer available at this pharmacy.')
        return redirect(_next_url(request, reverse('search')))
    try:
        qty = max(1, min(int(request.POST.get('quantity', 1)), 1000))
    except (TypeError, ValueError):
        qty = 1
    basket = _basket(request)
    if basket['pharmacy'] and basket['pharmacy'] != inv.pharmacy_id and basket['items']:
        if request.POST.get('replace') != '1':
            request.session['medfinder_basket_conflict'] = {'inv': inv.pk, 'qty': qty}
            return redirect('basket')
        basket = {'pharmacy': None, 'items': {}}
    if str(inv.pk) not in basket['items'] and len(basket['items']) >= MAX_LINES:
        messages.error(request, f'An order can contain at most {MAX_LINES} different medicines.')
        return redirect('basket')
    new_qty = min(basket['items'].get(str(inv.pk), 0) + qty, inv.quantity)
    basket['pharmacy'] = inv.pharmacy_id
    basket['items'][str(inv.pk)] = new_qty
    _save_basket(request, basket)
    request.session.pop('medfinder_basket_conflict', None)
    messages.success(request, f'{inv.medicine.name} added to your basket ({inv.pharmacy.name}).')
    return redirect(_next_url(request, reverse('basket')))


def basket(request):
    basket = _basket(request)
    if request.method == 'POST':
        action = request.POST.get('action', '')
        if action == 'clear':
            basket = {'pharmacy': None, 'items': {}}
            messages.info(request, 'Your basket is empty.')
        elif action.startswith('remove:'):
            basket['items'].pop(action.split(':', 1)[1], None)
        elif action == 'update':
            for key in list(basket['items']):
                try:
                    qty = int(request.POST.get(f'qty_{key}', basket['items'][key]))
                except (TypeError, ValueError):
                    continue
                if qty <= 0:
                    basket['items'].pop(key)
                else:
                    basket['items'][key] = min(qty, 1000)
            messages.success(request, 'Basket updated.')
        _save_basket(request, basket)
        return redirect('basket')

    lines = _basket_lines(basket)
    # Drop lines that disappeared (pharmacy hidden, medicine removed).
    if len(lines) != len(basket['items']):
        basket['items'] = {str(line['inv'].pk): line['qty'] for line in lines}
        _save_basket(request, basket)
    pharmacy = lines[0]['inv'].pharmacy if lines else None
    subtotal = sum((line['subtotal'] for line in lines), Decimal('0'))

    conflict = None
    pending = request.session.get('medfinder_basket_conflict')
    if pending:
        conflict = Inventory.objects.filter(pk=pending['inv']).select_related('pharmacy', 'medicine').first()
        if conflict:
            conflict.wanted = pending['qty']

    form = CheckoutForm(initial={'phone': _patient_phone(request) or ''})
    return render(request, 'core/basket.html', {
        'lines': lines, 'pharmacy': pharmacy, 'subtotal': subtotal,
        'needs_rx': any(line['inv'].medicine.requires_prescription for line in lines),
        'has_short': any(line['short'] for line in lines),
        'form': form, 'conflict': conflict,
    })


@require_POST
def basket_conflict_cancel(request):
    request.session.pop('medfinder_basket_conflict', None)
    return redirect('basket')


@require_POST
@ratelimit('reserve', settings.RATE_LIMIT_RESERVE)
def basket_checkout(request):
    basket = _basket(request)
    if not basket['items']:
        messages.error(request, 'Your basket is empty.')
        return redirect('basket')
    form = CheckoutForm(request.POST, request.FILES)
    if not form.is_valid():
        for errors in form.errors.values():
            for e in errors:
                messages.error(request, e)
        return redirect('basket')
    data = form.cleaned_data
    try:
        order = create_order(list(basket['items'].items()), data['phone'], delivery=data['delivery'],
                             address=data['address'], prescription=data.get('prescription'))
    except ReservationError as e:
        messages.error(request, str(e))
        return redirect('basket')
    request.session.pop(BASKET_KEY, None)
    messages.success(request, 'Order reserved. Your pickup code was also sent by SMS.')
    return redirect('order_detail', pk=order.pk, code=order.pickup_code)


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------
def _get_order(pk, code):
    return get_object_or_404(Order.objects.select_related('pharmacy'), pk=pk, pickup_code=code)


def order_detail(request, pk, code):
    expire_old_reservations()
    order = _get_order(pk, code)
    lines = order.lines
    pending = Payment.objects.filter(reservation__order=order, status='pending').first()
    paid = Payment.objects.filter(reservation__order=order, status='successful')
    refunds = Payment.objects.filter(reservation__order=order, status='refund_due')
    unpaid_total = sum((r.total for r in lines if r.is_open and not r.is_paid), Decimal('0'))
    last = Payment.objects.filter(reservation__order=order).order_by('-created_at').first()
    failed = bool(last and last.status == 'failed' and not pending)
    return render(request, 'core/order.html', {
        'order': order, 'lines': lines, 'status': order.status,
        'total': sum((r.total for r in lines if r.status != 'cancelled'), Decimal('0')),
        'unpaid_total': unpaid_total,
        'delivery': next((r for r in lines if r.delivery), None),
        'delivery_fee': sum((r.delivery_fee for r in lines if r.status != 'cancelled'), Decimal('0')),
        'pending_payment': pending,
        'paid_amount': sum((p.amount for p in paid), Decimal('0')),
        'refund_amount': sum((p.amount for p in refunds), Decimal('0')),
        'payment_failed': failed,
        'can_pay': unpaid_total > 0 and not pending,
        'can_cancel': any(r.is_open and not r.is_paid for r in lines),
        'rx_upload': any(r.is_open and r.needs_prescription and r.prescription_status in ('missing', 'rejected')
                         for r in lines),
        'expires_at': min((r.expires_at for r in lines if r.is_open), default=None),
        'pay_form': PaymentForm(initial={'phone': order.patient_phone}),
        'momo_simulated': payments.any_simulation(),
    })


@require_POST
@ratelimit('pay', settings.RATE_LIMIT_RESERVE)
def order_pay(request, pk, code):
    from .views import _commission  # shared commission rule
    order = _get_order(pk, code)
    back = redirect('order_detail', pk=pk, code=code)
    if Payment.objects.filter(reservation__order=order, status='pending').exists():
        messages.info(request, 'A payment is already waiting for your approval.')
        return back
    lines = [r for r in order.lines if r.is_open and not r.is_paid]
    if not lines:
        messages.error(request, 'There is nothing left to pay in this order.')
        return back
    form = PaymentForm(request.POST)
    if not form.is_valid():
        for e in form.errors.get('phone', []):
            messages.error(request, e)
        return back
    provider = form.provider
    if provider is None:
        messages.error(request, 'Use an MTN (078/079) or Airtel (072/073) number.')
        return back
    amount = sum((r.total for r in lines), Decimal('0'))
    try:
        reference = payments.request_to_pay(provider, amount, form.cleaned_data['phone'],
                                            external_id=f'order-{order.pk}', note=f'MedFinder order {order.pk}')
    except payments.PaymentError as e:
        messages.error(request, str(e))
        return back
    simulated = payments.is_simulation(provider)
    for r in lines:  # one row per line so refunds, reports and the dashboard stay per medicine
        Payment.objects.create(reservation=r, payer_phone=form.cleaned_data['phone'], amount=r.total,
                               commission=_commission(r.total), currency='RWF', provider=provider,
                               provider_reference=reference, simulated=simulated)
    messages.info(request, f'Payment request of {amount:.0f} RWF sent. Approve it on your phone with your '
                           f'{payments.PROVIDER_NAMES[provider]} PIN, then press "check payment".')
    return back


@require_POST
def order_payment_status(request, pk, code):
    from .views import _apply_payment_status
    order = _get_order(pk, code)
    payment = Payment.objects.filter(reservation__order=order, status='pending').first()
    if payment:
        try:
            status, reason = payments.get_status(payment.provider, payment.provider_reference)
            _apply_payment_status(payment, status, reason)
        except payments.PaymentError as e:
            messages.error(request, str(e))
    return redirect('order_detail', pk=pk, code=code)


@require_POST
def order_cancel(request, pk, code):
    _get_order(pk, code)
    if patient_cancel_order(pk, code):
        messages.success(request, 'Your order was cancelled. Thank you for letting the pharmacy know.')
    else:
        messages.error(request, 'This order can no longer be cancelled online. Please call the pharmacy.')
    return redirect('order_detail', pk=pk, code=code)


# ---------------------------------------------------------------------------
# Prescriptions
# ---------------------------------------------------------------------------
@require_POST
@ratelimit('rx', settings.RATE_LIMIT_RESERVE)
def order_prescription(request, pk, code):
    order = _get_order(pk, code)
    form = PrescriptionForm(request.POST, request.FILES)
    if form.is_valid():
        if attach_prescription(order.lines, form.cleaned_data['prescription']):
            messages.success(request, 'Prescription sent. The pharmacy will check it.')
        else:
            messages.info(request, 'No medicine in this order is waiting for a prescription.')
    else:
        for e in form.errors.get('prescription', []):
            messages.error(request, e)
    return redirect('order_detail', pk=pk, code=code)


@require_POST
@ratelimit('rx', settings.RATE_LIMIT_RESERVE)
def reservation_prescription(request, pk, code):
    res = get_object_or_404(Reservation.objects.select_related('inventory__medicine', 'inventory__pharmacy'),
                            pk=pk, pickup_code=code)
    form = PrescriptionForm(request.POST, request.FILES)
    if form.is_valid():
        if attach_prescription([res], form.cleaned_data['prescription']):
            messages.success(request, 'Prescription sent. The pharmacy will check it.')
        else:
            messages.info(request, 'This reservation is not waiting for a prescription.')
    else:
        for e in form.errors.get('prescription', []):
            messages.error(request, e)
    return redirect('reservation_detail', pk=pk, code=code)


def _serve_prescription(res):
    if not res.prescription:
        raise Http404
    try:
        handle = res.prescription.open('rb')
    except FileNotFoundError:
        raise Http404
    content_type = mimetypes.guess_type(res.prescription.name)[0] or 'application/octet-stream'
    response = FileResponse(handle, content_type=content_type)
    response['Cache-Control'] = 'private, no-store'
    response['X-Content-Type-Options'] = 'nosniff'
    return response


def prescription_file(request, pk):
    """Prescriptions are medical data: only the pharmacy that holds the reservation and admins may open them."""
    res = get_object_or_404(Reservation.objects.select_related('inventory'), pk=pk)
    user = request.user
    if not user.is_authenticated:
        return redirect(f"{reverse('login')}?next={request.path}")
    pharmacy, _role = get_membership(user)
    if not (user.is_staff or (pharmacy and pharmacy.pk == res.inventory.pharmacy_id)):
        raise Http404
    return _serve_prescription(res)


def patient_prescription_file(request, pk, code):
    """The patient can view the prescription they uploaded (they know the pickup code)."""
    res = get_object_or_404(Reservation, pk=pk, pickup_code=code)
    return _serve_prescription(res)


# ---------------------------------------------------------------------------
# "My reservations": a patient signs in with their phone number and an SMS code
# ---------------------------------------------------------------------------
def _patient_phone(request):
    data = request.session.get(PATIENT_KEY)
    if not data:
        return None
    if timezone.now().timestamp() > data.get('exp', 0):
        request.session.pop(PATIENT_KEY, None)
        return None
    return data.get('phone')


@ratelimit('patient_login', settings.RATE_LIMIT_LOGIN)
def my_reservations(request):
    phone = _patient_phone(request)
    if phone is None:
        form = PatientPhoneForm(request.POST or None)
        if request.method == 'POST' and form.is_valid():
            code = get_random_string(6, allowed_chars='0123456789')
            request.session[PATIENT_PENDING_KEY] = {
                'phone': form.cleaned_data['phone'], 'hash': make_password(code), 'tries': 0,
                'exp': (timezone.now() + timedelta(minutes=10)).timestamp(),
            }
            send_sms(form.cleaned_data['phone'],
                     f'MedFinder code: {code}. Use it to see your reservations. It expires in 10 minutes. '
                     f'Never share this code.')
            return redirect('my_verify')
        return render(request, 'core/my_login.html', {'form': form, 'step': 'phone'})

    expire_old_reservations()
    reservations = list(Reservation.objects.filter(patient_phone=phone)
                        .select_related('inventory__medicine', 'inventory__pharmacy', 'order')
                        .prefetch_related('payments')[:100])
    entries, seen_orders = [], set()
    for r in reservations:  # one row per order, one per single reservation
        if r.order_id:
            if r.order_id in seen_orders:
                continue
            seen_orders.add(r.order_id)
            items = [x for x in reservations if x.order_id == r.order_id]
            entries.append({'order': r.order, 'items': items, 'created_at': r.order.created_at,
                            'status': r.order.status, 'pharmacy': r.inventory.pharmacy,
                            'total': sum((x.total for x in items if x.status != 'cancelled'), Decimal('0')),
                            'paid': all(x.is_paid for x in items if x.status != 'cancelled')})
        else:
            entries.append({'res': r, 'items': [r], 'created_at': r.created_at, 'status': r.status,
                            'pharmacy': r.inventory.pharmacy, 'total': r.total, 'paid': r.is_paid})
    active = [e for e in entries if e['status'] in ('pending', 'ready')]
    past = [e for e in entries if e['status'] not in ('pending', 'ready')]
    return render(request, 'core/my_reservations.html', {'phone': phone, 'active': active, 'past': past})


@ratelimit('patient_code', settings.RATE_LIMIT_LOGIN)
def my_verify(request):
    pending = request.session.get(PATIENT_PENDING_KEY)
    if not pending or timezone.now().timestamp() > pending['exp']:
        request.session.pop(PATIENT_PENDING_KEY, None)
        messages.error(request, 'Your code expired. Please enter your phone number again.')
        return redirect('my_reservations')
    form = LoginCodeForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        if check_password(form.cleaned_data['code'], pending['hash']):
            request.session.pop(PATIENT_PENDING_KEY, None)
            request.session.cycle_key()  # new session id after signing in
            request.session[PATIENT_KEY] = {
                'phone': pending['phone'],
                'exp': (timezone.now() + timedelta(days=PATIENT_SESSION_DAYS)).timestamp(),
            }
            return redirect('my_reservations')
        pending['tries'] += 1
        if pending['tries'] >= 5:
            request.session.pop(PATIENT_PENDING_KEY, None)
            messages.error(request, 'Too many wrong codes. Please start again.')
            return redirect('my_reservations')
        request.session[PATIENT_PENDING_KEY] = pending
        form.add_error('code', 'That code is not correct.')
    return render(request, 'core/my_login.html', {'form': form, 'step': 'code', 'phone': pending['phone']})


@require_POST
def my_logout(request):
    request.session.pop(PATIENT_KEY, None)
    messages.info(request, 'You are signed out of My reservations on this device.')
    return redirect('search')
