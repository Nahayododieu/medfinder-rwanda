import csv
import io
import json
import logging
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth import login as auth_login
from django.contrib.auth import views as auth_views
from django.contrib.auth.hashers import check_password, make_password
from django.utils.crypto import get_random_string
from django.contrib.auth.models import User
from django.db import transaction
from django.http import Http404, HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from decimal import InvalidOperation

from django.db import connection
from django.db.models import Avg, Count, Q
from django.http import JsonResponse

from . import billing as billing_svc
from . import payments
from .access import OWNER, get_membership, pharmacy_required
from .analytics import pharmacy_insights, platform_summary
from .channels import _match_medicine, handle_incoming_sms, ussd_response
from .forms import (LoginCodeForm, MedicineRequestForm, PaymentForm, PharmacyProfileForm,
                    PharmacyRegisterForm, ReservationForm, ReviewForm, StockForm, StockImportForm,
                    SubscriptionPayForm, TrackForm)
from .i18n import STRINGS
from .models import (INSURER_CHOICES, Inventory, Medicine, Payment, Pharmacy, Reservation, Review,
                     SearchLog, SmsLog, SubscriptionPayment, Testimonial)
from .ratelimit import ratelimit
from .services import (ReservationError, cancel_reservation, create_reservation, expire_old_reservations,
                       find_alternatives, find_stock, log_search, log_stock_change, patient_cancel,
                       suggest_spelling)
from .sms import send_sms
from .views_browse import categories_with_counts, site_stats
from .utils import DISTRICT_CHOICES, distance_km

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Patient pages
# ---------------------------------------------------------------------------
def _parse_coords(lat, lng):
    try:
        lat, lng = float(lat), float(lng)
    except (TypeError, ValueError):
        return None
    if -90 <= lat <= 90 and -180 <= lng <= 180:
        return lat, lng
    return None


def _apply_filters(items, max_price, open_now, insurer):
    out = []
    for i in items:
        if max_price is not None and i.price > max_price:
            continue
        if open_now and i.pharmacy.is_open_now is not True:
            continue
        if insurer and insurer not in i.pharmacy.insurer_codes:
            continue
        out.append(i)
    return out


def _ratings_for(pharmacy_ids):
    rows = (Review.objects.filter(status='approved', pharmacy_id__in=pharmacy_ids)
            .values('pharmacy_id').annotate(avg=Avg('rating'), n=Count('id')))
    return {r['pharmacy_id']: (round(r['avg'], 1), r['n']) for r in rows}


def search(request):
    expire_old_reservations()  # release unclaimed stock before showing results

    query = request.GET.get('q', '').strip()[:100]
    district = request.GET.get('district', '')
    if district not in dict(DISTRICT_CHOICES):
        district = ''
    coords = _parse_coords(request.GET.get('lat'), request.GET.get('lng'))
    sort = request.GET.get('sort', '')
    try:
        max_price = Decimal(request.GET['max_price']) if request.GET.get('max_price') else None
        if max_price is not None and max_price < 0:
            max_price = None
    except (InvalidOperation, ValueError):
        max_price = None
    open_now = request.GET.get('open_now') == '1'
    insurer = request.GET.get('insurer', '')
    if insurer not in dict(INSURER_CHOICES):
        insurer = ''
    results, corrected, alt_for, alternatives = [], None, None, []
    filtered_out = 0

    if query:
        found = list(find_stock(query, district))
        if not found:
            spelled = suggest_spelling(query)
            if spelled and spelled != query.lower():
                found = list(find_stock(spelled, district))
                if found:
                    corrected = spelled
        results = _apply_filters(found, max_price, open_now, insurer)
        filtered_out = len(found) - len(results)
        if not found:
            alt_for, alternatives = find_alternatives(corrected or query, district)
            alternatives = _apply_filters(alternatives, max_price, open_now, insurer)
        ratings = _ratings_for({i.pharmacy_id for i in results + alternatives})
        for item in results + alternatives:
            item.rating = ratings.get(item.pharmacy_id)
        for item in results:
            item.distance = None
            if coords:
                item.distance = round(distance_km(coords[0], coords[1],
                                                  item.pharmacy.latitude, item.pharmacy.longitude), 1)
        if sort != 'price' and coords:
            sort = 'distance'
            results.sort(key=lambda i: (i.distance, i.price))
        else:
            sort = 'price'
            results.sort(key=lambda i: (i.price, i.pharmacy.name))

        # District for analytics: chosen filter, else the nearest pharmacy's district.
        log_district = district
        if not log_district and coords:
            nearest = min(Pharmacy.objects.filter(verified=True),
                          key=lambda p: distance_km(coords[0], coords[1], p.latitude, p.longitude),
                          default=None)
            log_district = nearest.district if nearest else ''
        log_search(query, results, log_district, channel='web')

    map_points = [{
        'lat': i.pharmacy.latitude, 'lng': i.pharmacy.longitude,
        'name': i.pharmacy.name, 'medicine': i.medicine.name,
        'price': f'{i.price:.0f}', 'phone': i.pharmacy.phone, 'id': i.pk,
        'url': reverse('pharmacy_detail', args=[i.pharmacy_id]),
    } for i in results]

    return render(request, 'core/search.html', {
        'query': query,
        'results': results,
        'district': district,
        'districts': DISTRICT_CHOICES,
        'sort': sort,
        'has_coords': bool(coords),
        'map_points': map_points,
        'corrected': corrected,
        'alt_for': alt_for,
        'alternatives': alternatives,
        'filtered_out': filtered_out,
        'max_price': max_price,
        'open_now': open_now,
        'insurer': insurer,
        'insurers': INSURER_CHOICES,
        'filters_active': bool(max_price is not None or open_now or insurer),
        'testimonials': Testimonial.objects.filter(published=True)[:3] if not query else [],
        'price_summary': _price_summary(results),
        'alert_medicine': _alert_medicine(query, corrected, alt_for) if query and not results else None,
        'stats': site_stats() if not query else None,
        'categories': categories_with_counts() if not query else [],
    })


def _price_summary(results):
    if not results:
        return None
    prices = [i.price for i in results]
    return {'low': min(prices), 'high': max(prices),
            'pharmacies': len({i.pharmacy_id for i in results})}


def _alert_medicine(query, corrected, alt_for):
    """The catalogue medicine a patient can ask to be alerted about when nothing is in stock."""
    if alt_for:
        return alt_for
    term = corrected or query
    return (Medicine.objects.filter(Q(name__iexact=term) | Q(generic_name__iexact=term)).first()
            or Medicine.objects.filter(Q(name__icontains=term) | Q(generic_name__icontains=term)).first())


def _back_to_search(request):
    target = request.POST.get('next', '')
    if target and url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}):
        return redirect(target)
    return redirect('search')


@require_POST
@ratelimit('reserve', settings.RATE_LIMIT_RESERVE)
def reserve(request, pk):
    form = ReservationForm(request.POST, request.FILES)
    if not form.is_valid():
        for errors in form.errors.values():
            for e in errors:
                messages.error(request, e)
        return _back_to_search(request)

    data = form.cleaned_data
    try:
        reservation = create_reservation(pk, data['phone'], data['quantity'], delivery=data['delivery'],
                                         address=data['address'], prescription=data.get('prescription'))
    except ReservationError as e:
        messages.error(request, str(e))
        return _back_to_search(request)

    messages.success(request, 'Reservation created. Your pickup code was also sent by SMS.')
    return redirect('reservation_detail', pk=reservation.pk, code=reservation.pickup_code)


def _get_reservation(pk, code):
    return get_object_or_404(
        Reservation.objects.select_related('inventory__pharmacy', 'inventory__medicine'),
        pk=pk, pickup_code=code)


def reservation_detail(request, pk, code):
    expire_old_reservations()
    reservation = _get_reservation(pk, code)
    last_payment = reservation.payments.first()
    can_pay = reservation.is_open and not reservation.is_paid
    review = Review.objects.filter(reservation=reservation).first()
    return render(request, 'core/reservation.html', {
        'res': reservation,
        'payment': last_payment,
        'can_pay': can_pay,
        'pay_form': PaymentForm(initial={'phone': reservation.patient_phone}),
        'momo_simulated': payments.any_simulation(),
        'review': review,
        'review_form': ReviewForm() if reservation.status == 'done' and not review else None,
        'rx_upload': (reservation.is_open and reservation.needs_prescription
                      and reservation.prescription_status in ('missing', 'rejected')),
    })


@require_POST
@ratelimit('review', settings.RATE_LIMIT_RESERVE)
def reservation_review(request, pk, code):
    """A patient rates the pharmacy after picking up. Reviews wait for moderation."""
    reservation = _get_reservation(pk, code)
    if reservation.status != 'done' or Review.objects.filter(reservation=reservation).exists():
        messages.error(request, 'You can review a pharmacy once, after picking up your medicine.')
        return redirect('reservation_detail', pk=pk, code=code)
    form = ReviewForm(request.POST)
    if form.is_valid():
        Review.objects.create(reservation=reservation, pharmacy=reservation.inventory.pharmacy,
                              rating=form.cleaned_data['rating'], comment=form.cleaned_data['comment'].strip())
        messages.success(request, 'Thank you! Your review will appear after a quick check.')
    else:
        messages.error(request, 'Please choose a rating from 1 to 5 stars.')
    return redirect('reservation_detail', pk=pk, code=code)


@require_POST
def reservation_cancel(request, pk, code):
    """The patient releases their own reservation (unpaid only)."""
    if patient_cancel(pk, code):
        messages.success(request, 'Your reservation was cancelled. Thank you for letting the pharmacy know.')
    else:
        messages.error(request, 'This reservation can no longer be cancelled online. Please call the pharmacy.')
    return redirect('reservation_detail', pk=pk, code=code)


def medicine_suggest(request):
    """Autocomplete for the search box: medicine names matching what the patient types."""
    q = request.GET.get('q', '').strip()[:60]
    names = []
    if len(q) >= 2:
        meds = (Medicine.objects.filter(Q(name__icontains=q) | Q(generic_name__icontains=q))
                .order_by('name')[:8])
        names = [{'name': m.name, 'generic': m.generic_name} for m in meds]
    return JsonResponse({'results': names})


def track(request):
    form = TrackForm(request.GET or None)
    not_found = False
    if request.GET and form.is_valid():
        res = Reservation.objects.filter(pk=form.cleaned_data['reservation'],
                                         pickup_code=form.cleaned_data['code']).first()
        if res:
            return redirect('reservation_detail', pk=res.pk, code=res.pickup_code)
        not_found = True
    return render(request, 'core/track.html', {'form': form, 'not_found': not_found})


def privacy(request):
    return render(request, 'core/privacy.html')


def terms(request):
    return render(request, 'core/terms.html', {'grace_days': settings.SUBSCRIPTION_GRACE_DAYS,
                                               'hide_unpaid': settings.HIDE_UNPAID_PHARMACIES})


def for_pharmacies(request):
    return render(request, 'core/pharmacies.html')


def set_language(request, lang):
    if lang in STRINGS:
        request.session['lang'] = lang
    target = request.GET.get('next', '')
    if not url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}):
        target = reverse('search')
    return redirect(target)


# ---------------------------------------------------------------------------
# Mobile money
# ---------------------------------------------------------------------------
def _commission(amount):
    rate = Decimal(str(settings.PLATFORM_COMMISSION_RATE))
    return (amount * rate).quantize(Decimal('1'), rounding=ROUND_HALF_UP)


def _apply_payment_status(payment, status, reason=''):
    """Update a payment once; notify the patient on success. Returns True if changed.

    The conditional UPDATE makes this safe when the status check and the MoMo
    callback arrive at the same time: only one of them changes the row.
    """
    if payment.status != 'pending' or status == 'pending':
        return False
    # An order is paid with ONE mobile money request: every line's Payment row shares the reference.
    group = list(Payment.objects.filter(provider_reference=payment.provider_reference, status='pending'))
    ids = [p.pk for p in group] or [payment.pk]
    changed = Payment.objects.filter(pk__in=ids, status='pending').update(
        status=status, failure_reason=reason[:200], updated_at=timezone.now())
    if not changed:
        return False
    payment.status, payment.failure_reason = status, reason[:200]
    if status == 'successful':
        res = payment.reservation
        amount = sum((p.amount for p in group), Decimal('0')) or payment.amount
        what = f'order #{res.order_id}' if res.order_id and len(group) > 1 else f'reservation #{res.pk}'
        send_sms(res.patient_phone,
                 f"MedFinder: payment of {amount:.0f} RWF received for {what} "
                 f"at {res.inventory.pharmacy.name}. Code: {res.pickup_code}.")
    return True


@require_POST
@ratelimit('pay', settings.RATE_LIMIT_RESERVE)
def pay(request, pk, code):
    reservation = _get_reservation(pk, code)
    if not reservation.is_open or reservation.is_paid:
        messages.error(request, 'This reservation cannot be paid.')
        return redirect('reservation_detail', pk=pk, code=code)
    if reservation.payments.filter(status='pending').exists():
        messages.info(request, 'A payment is already waiting for your approval.')
        return redirect('reservation_detail', pk=pk, code=code)

    form = PaymentForm(request.POST)
    if not form.is_valid():
        for e in form.errors.get('phone', []):
            messages.error(request, e)
        return redirect('reservation_detail', pk=pk, code=code)
    provider = form.provider
    if provider is None:
        messages.error(request, 'Use an MTN (078/079) or Airtel (072/073) number.')
        return redirect('reservation_detail', pk=pk, code=code)

    amount = reservation.total
    try:
        reference = payments.request_to_pay(
            provider, amount, form.cleaned_data['phone'], external_id=reservation.pk,
            note=f'MedFinder reservation {reservation.pk}')
    except payments.PaymentError as e:
        messages.error(request, str(e))
        return redirect('reservation_detail', pk=pk, code=code)

    Payment.objects.create(
        reservation=reservation,
        payer_phone=form.cleaned_data['phone'],
        amount=amount,
        commission=_commission(amount),
        currency='RWF',
        provider=provider,
        provider_reference=reference,
        simulated=payments.is_simulation(provider),
    )
    messages.info(request, f'Payment request sent. Approve it on your phone with your '
                           f'{payments.PROVIDER_NAMES[provider]} PIN, then press "check payment".')
    return redirect('reservation_detail', pk=pk, code=code)


@require_POST
def payment_status(request, pk, code):
    reservation = _get_reservation(pk, code)
    payment = reservation.payments.filter(status='pending').first()
    if payment:
        try:
            status, reason = payments.get_status(payment.provider, payment.provider_reference)
            _apply_payment_status(payment, status, reason)
        except payments.PaymentError as e:
            messages.error(request, str(e))
    return redirect('reservation_detail', pk=pk, code=code)


@csrf_exempt
@require_POST
def momo_callback(request):
    """MTN calls this URL (X-Callback-Url) when a payment completes."""
    try:
        data = json.loads(request.body or b'{}')
    except ValueError:
        return HttpResponse(status=400)
    reference = data.get('referenceId') or request.headers.get('X-Reference-Id', '')
    payment = Payment.objects.filter(provider_reference=reference).first()
    sub_payment = None if payment else SubscriptionPayment.objects.filter(provider_reference=reference).first()
    if not (payment or sub_payment):
        return HttpResponse(status=404)
    # Never trust the callback body alone: confirm with the API.
    try:
        status, reason = payments.get_status('mtn', reference)
    except payments.PaymentError:
        return HttpResponse(status=502)
    if payment:
        _apply_payment_status(payment, status, reason)
    else:
        billing_svc.apply_status(sub_payment, status, reason)
    return HttpResponse('OK')


@csrf_exempt
@require_POST
def airtel_callback(request):
    """Airtel calls this URL when a payment completes; the result is confirmed with the API."""
    try:
        data = json.loads(request.body or b'{}')
    except ValueError:
        return HttpResponse(status=400)
    reference = str(((data.get('transaction') or {}).get('id')) or '')
    payment = Payment.objects.filter(provider_reference=reference, provider='airtel').first()
    sub_payment = None if payment else SubscriptionPayment.objects.filter(
        provider_reference=reference, method='airtel').first()
    if not (payment or sub_payment):
        return HttpResponse(status=404)
    try:
        status, reason = payments.get_status('airtel', reference)
    except payments.PaymentError:
        return HttpResponse(status=502)
    if payment:
        _apply_payment_status(payment, status, reason)
    else:
        billing_svc.apply_status(sub_payment, status, reason)
    return HttpResponse('OK')


# ---------------------------------------------------------------------------
# Pharmacy pages
# ---------------------------------------------------------------------------
@method_decorator(ratelimit('login', settings.RATE_LIMIT_LOGIN), name='dispatch')
class RateLimitedLoginView(auth_views.LoginView):
    """Password login; with PHARMACY_LOGIN_2FA on, pharmacy accounts also confirm an SMS code."""
    redirect_authenticated_user = True

    def form_valid(self, form):
        user = form.get_user()
        pharmacy, _role = get_membership(user)
        if not (settings.PHARMACY_LOGIN_2FA and pharmacy):
            return super().form_valid(form)
        code = get_random_string(6, allowed_chars='0123456789')
        self.request.session[LOGIN_2FA_KEY] = {
            'uid': user.pk, 'hash': make_password(code), 'tries': 0,
            'exp': (timezone.now() + timedelta(minutes=5)).timestamp(),
            'next': self.get_success_url(),
        }
        send_sms(pharmacy.phone, f'MedFinder login code: {code}. It expires in 5 minutes. '
                                 f'Never share this code.')
        return redirect('login_verify')


LOGIN_2FA_KEY = 'medfinder_2fa'


@ratelimit('login2fa', settings.RATE_LIMIT_LOGIN)
def login_verify(request):
    """Second step of pharmacy login: the 6-digit SMS code."""
    pending = request.session.get(LOGIN_2FA_KEY)
    if not pending or timezone.now().timestamp() > pending['exp']:
        request.session.pop(LOGIN_2FA_KEY, None)
        messages.error(request, 'Your login code expired. Please log in again.')
        return redirect('login')
    form = LoginCodeForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        if check_password(form.cleaned_data['code'], pending['hash']):
            user = User.objects.get(pk=pending['uid'])
            request.session.pop(LOGIN_2FA_KEY, None)
            auth_login(request, user, backend='django.contrib.auth.backends.ModelBackend')
            target = pending.get('next') or reverse('dashboard')
            if not url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}):
                target = reverse('dashboard')
            return redirect(target)
        pending['tries'] += 1
        if pending['tries'] >= 5:
            request.session.pop(LOGIN_2FA_KEY, None)
            messages.error(request, 'Too many wrong codes. Please log in again.')
            return redirect('login')
        request.session[LOGIN_2FA_KEY] = pending
        form.add_error('code', 'That code is not correct.')
    return render(request, 'registration/login_verify.html', {'form': form})


@pharmacy_required()
def dashboard(request):
    pharmacy = request.pharmacy
    expire_old_reservations()

    if request.method == 'POST':
        form = StockForm(request.POST)
        if form.is_valid():
            item = Inventory.objects.filter(pharmacy=pharmacy, medicine=form.cleaned_data['medicine']).first()
            old_q, old_p = (item.quantity, item.price) if item else (None, None)
            if item is None:
                item = Inventory(pharmacy=pharmacy, medicine=form.cleaned_data['medicine'])
            item.quantity = form.cleaned_data['quantity']
            item.price = form.cleaned_data['price']
            if form.cleaned_data.get('expiry_date') and form.cleaned_data['expiry_date'] != item.expiry_date:
                item.expiry_date = form.cleaned_data['expiry_date']
                item.expiry_alert_sent = False
            item.save()
            log_stock_change(item, 'web', request.user, old_q, old_p)
            messages.success(request, f"{form.cleaned_data['medicine'].name} saved.")
            return redirect('dashboard')
    else:
        form = StockForm()

    items = Inventory.objects.filter(pharmacy=pharmacy).select_related('medicine').order_by('medicine__name')
    reservations = (Reservation.objects
                    .filter(inventory__pharmacy=pharmacy, status__in=['pending', 'ready'])
                    .select_related('inventory__medicine', 'order')
                    .prefetch_related('payments')
                    .order_by('created_at'))
    history = (Reservation.objects
               .filter(inventory__pharmacy=pharmacy, status__in=['done', 'cancelled'])
               .select_related('inventory__medicine')
               .order_by('-created_at')[:10])
    low_stock = [i for i in items if i.is_low]
    expiring = sorted((i for i in items if i.expiry_state), key=lambda i: i.expiry_date)
    stock_q = request.GET.get('stock', '').strip()
    shown_items = [i for i in items if stock_q.lower() in i.medicine.name.lower()] if stock_q else items
    latest = max((r.pk for r in reservations), default=0)
    return render(request, 'core/dashboard.html', {
        'pharmacy': pharmacy, 'form': form, 'items': shown_items, 'all_count': len(items),
        'stock_q': stock_q, 'is_owner': request.pharmacy_role == OWNER, 'expiring': expiring,
        'expiry_days': settings.EXPIRY_ALERT_DAYS,
        'reservations': reservations, 'history': history,
        'low_stock': low_stock, 'insights': pharmacy_insights(pharmacy),
        'hold_hours': settings.RESERVATION_HOLD_HOURS,
        'request_form': MedicineRequestForm(), 'import_form': StockImportForm(),
        'my_requests': pharmacy.medicine_requests.all()[:5],
        'latest_reservation': latest,
    })


@pharmacy_required(owner_only=True)
def billing(request):
    """Subscription status, payment history and mobile money payment for the pharmacy owner."""
    pharmacy = request.pharmacy

    if request.method == 'POST':
        if not pharmacy.verified:
            messages.error(request, 'You can pay once your pharmacy has been verified.')
            return redirect('billing')
        form = SubscriptionPayForm(request.POST)
        if pharmacy.subscription_payments.filter(status='pending').exists():
            messages.info(request, 'A payment is already waiting for your approval on your phone.')
        elif form.is_valid():
            try:
                billing_svc.start_mobile_payment(pharmacy, form.cleaned_data['months'], form.cleaned_data['phone'])
                messages.info(request, 'Payment request sent. Approve it on your phone with your mobile money PIN, '
                                       'then press "Check payment".')
            except payments.PaymentError as e:
                messages.error(request, str(e))
        else:
            for errors in form.errors.values():
                for e in errors:
                    messages.error(request, e)
        return redirect('billing')

    form = SubscriptionPayForm(initial={'phone': pharmacy.phone, 'months': 1})
    plans = [{'months': m, 'amount': billing_svc.price_for(m)} for m in billing_svc.PLAN_MONTHS]
    return render(request, 'core/billing.html', {
        'pharmacy': pharmacy, 'form': form, 'plans': plans,
        'payments': pharmacy.subscription_payments.all()[:12],
        'pending': pharmacy.subscription_payments.filter(status='pending').first(),
        'grace_days': settings.SUBSCRIPTION_GRACE_DAYS,
        'hide_unpaid': settings.HIDE_UNPAID_PHARMACIES,
        'momo_simulated': payments.any_simulation(),
    })


@pharmacy_required(owner_only=True)
@require_POST
def billing_check(request):
    pharmacy = request.pharmacy
    payment = pharmacy.subscription_payments.filter(status='pending').first()
    if payment:
        try:
            status, reason = payments.get_status(billing_svc.provider_of(payment), payment.provider_reference)
            if billing_svc.apply_status(payment, status, reason):
                payment.refresh_from_db()
                if payment.status == 'successful':
                    messages.success(request, f'Payment received. Your listing is active until '
                                              f'{payment.period_end:%d %b %Y}.')
                else:
                    messages.error(request, 'The payment was not completed. You can try again.')
            else:
                messages.info(request, 'Still waiting for you to approve the payment on your phone.')
        except payments.PaymentError as e:
            messages.error(request, str(e))
    return redirect('billing')


@pharmacy_required()
def dashboard_poll(request):
    """Small JSON endpoint the dashboard checks every 30 s to announce new reservations."""
    pharmacy = request.pharmacy
    open_qs = Reservation.objects.filter(inventory__pharmacy=pharmacy, status__in=['pending', 'ready'])
    latest = open_qs.order_by('-pk').values_list('pk', flat=True).first() or 0
    return JsonResponse({'open': open_qs.count(), 'latest': latest})


@pharmacy_required()
@require_POST
def medicine_request(request):
    pharmacy = request.pharmacy
    form = MedicineRequestForm(request.POST)
    if form.is_valid():
        req = form.save(commit=False)
        req.pharmacy = pharmacy
        req.save()
        messages.success(request, f'Request for "{req.name}" sent to the administrator.')
    else:
        messages.error(request, 'Please enter the medicine name.')
    return redirect('dashboard')


@pharmacy_required()
@require_POST
def stock_import(request):
    """Bulk add/update stock from a CSV file: medicine, quantity, price, optional expiry (YYYY-MM-DD)."""
    pharmacy = request.pharmacy
    form = StockImportForm(request.POST, request.FILES)
    if not form.is_valid():
        for e in form.errors.get('file', []):
            messages.error(request, e)
        return redirect('dashboard')

    try:
        text = form.cleaned_data['file'].read().decode('utf-8-sig')
    except UnicodeDecodeError:
        messages.error(request, 'The file must be saved as UTF-8 CSV.')
        return redirect('dashboard')
    sample = text[:2048]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=',;\t')
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    fields = {(f or '').strip().lower(): f for f in (reader.fieldnames or [])}
    if not {'medicine', 'quantity'} <= fields.keys():
        messages.error(request, 'The first row must contain the column names: medicine, quantity, price.')
        return redirect('dashboard')

    saved, problems = 0, []
    with transaction.atomic():
        for line_no, row in enumerate(reader, start=2):
            name = (row.get(fields['medicine']) or '').strip()
            if not name:
                continue
            medicine, error = _match_medicine(name)
            if error:
                problems.append(f'Row {line_no}: {error}')
                continue
            try:
                qty = int(Decimal((row.get(fields['quantity']) or '').strip()))
                raw_price = (row.get(fields.get('price', ''), '') or '').strip() if 'price' in fields else ''
                price = Decimal(raw_price) if raw_price else None
                if qty < 0 or (price is not None and price < 0):
                    raise ValueError
            except (InvalidOperation, ValueError):
                problems.append(f'Row {line_no}: quantity and price must be positive numbers.')
                continue
            expiry = None
            raw_expiry = (row.get(fields['expiry']) or '').strip() if 'expiry' in fields else ''
            if raw_expiry:
                try:
                    expiry = date.fromisoformat(raw_expiry)
                except ValueError:
                    problems.append(f'Row {line_no}: expiry must be a date like 2027-06-30.')
                    continue
            item = Inventory.objects.filter(pharmacy=pharmacy, medicine=medicine).first()
            if item is None and price is None:
                problems.append(f'Row {line_no}: {medicine.name} is new, so a price is required.')
                continue
            old_q, old_p = (item.quantity, item.price) if item else (None, None)
            if item is None:
                item = Inventory(pharmacy=pharmacy, medicine=medicine, price=price)
            item.quantity = qty
            if price is not None:
                item.price = price
            if expiry and expiry != item.expiry_date:
                item.expiry_date, item.expiry_alert_sent = expiry, False
            item.save()
            log_stock_change(item, 'csv', request.user, old_q, old_p)
            saved += 1

    if saved:
        messages.success(request, f'{saved} medicine{"s" if saved != 1 else ""} imported.')
    for p in problems[:6]:
        messages.error(request, p)
    if len(problems) > 6:
        messages.error(request, f'…and {len(problems) - 6} more rows with problems.')
    return redirect('dashboard')


@pharmacy_required()
def stock_export(request):
    pharmacy = request.pharmacy
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="medfinder_stock.csv"'
    response.write('﻿')  # lets Excel open accents correctly
    writer = csv.writer(response)
    writer.writerow(['medicine', 'quantity', 'price', 'expiry'])
    for item in Inventory.objects.filter(pharmacy=pharmacy).select_related('medicine').order_by('medicine__name'):
        writer.writerow([item.medicine.name, item.quantity, f'{item.price:.0f}',
                         item.expiry_date.isoformat() if item.expiry_date else ''])
    return response


@pharmacy_required()
@require_POST
def update_stock(request, pk):
    item = get_object_or_404(Inventory, pk=pk, pharmacy=request.pharmacy)  # can only touch own stock
    old_q, old_p = item.quantity, item.price
    try:
        quantity = int(request.POST.get('quantity', item.quantity))
        price = Decimal(request.POST.get('price', item.price))
        if quantity < 0 or price < 0:
            raise ValueError
        raw_expiry = request.POST.get('expiry_date', '').strip()
        expiry = date.fromisoformat(raw_expiry) if raw_expiry else None
    except (ValueError, ArithmeticError):
        messages.error(request, 'Please enter valid, positive numbers and a valid date.')
        return redirect('dashboard')
    item.quantity = quantity
    item.price = price
    if 'expiry_date' in request.POST and expiry != item.expiry_date:
        item.expiry_date, item.expiry_alert_sent = expiry, False
    item.save()
    log_stock_change(item, 'web', request.user, old_q, old_p)
    messages.success(request, f'{item.medicine.name} updated.')
    return redirect('dashboard')


@pharmacy_required()
@require_POST
def delete_stock(request, pk):
    item = get_object_or_404(Inventory, pk=pk, pharmacy=request.pharmacy)
    if Reservation.objects.filter(inventory=item, status__in=['pending', 'ready']).exists():
        messages.error(request, 'This medicine has open reservations. Complete or cancel them first.')
        return redirect('dashboard')
    log_stock_change(item, 'web', request.user, item.quantity, item.price, deleted=True)
    item.delete()
    messages.success(request, 'Medicine removed.')
    return redirect('dashboard')


@pharmacy_required()
@require_POST
def reservation_action(request, pk, action):
    pharmacy = request.pharmacy

    with transaction.atomic():
        reservation = get_object_or_404(
            Reservation.objects.select_for_update(),
            pk=pk,
            inventory__pharmacy=pharmacy,
        )
        medicine = reservation.inventory.medicine.name
        phone = reservation.patient_phone

        if action == 'ready' and reservation.status == 'pending':
            reservation.status = 'ready'
            reservation.save(update_fields=['status'])
            message = (f"MedFinder: your {medicine} is ready at {pharmacy.name}. "
                       f"Code: {reservation.pickup_code}.")
            transaction.on_commit(lambda: send_sms(phone, message))
            messages.success(request, 'Reservation marked ready. The patient was notified by SMS.')
        elif action == 'done' and reservation.status in ('pending', 'ready'):
            if request.POST.get('code', '').strip() != reservation.pickup_code:
                messages.error(request, 'The pickup code is incorrect.')
            else:
                reservation.status = 'done'
                reservation.completed_at = timezone.now()
                reservation.save(update_fields=['status', 'completed_at'])
                messages.success(request, 'Pickup confirmed.')
        elif action == 'rx_ok' and reservation.is_open and reservation.prescription_status == 'uploaded':
            reservation.prescription_status = 'approved'
            reservation.save(update_fields=['prescription_status'])
            messages.success(request, f'Prescription approved for {medicine}.')
        elif action == 'rx_reject' and reservation.is_open and reservation.prescription_status == 'uploaded':
            reservation.prescription_status = 'rejected'
            reservation.save(update_fields=['prescription_status'])
            message = (f"MedFinder: {pharmacy.name} could not accept the prescription for {medicine}. "
                       f"Please upload a clear photo of a valid prescription, or bring it at pickup. "
                       f"Code: {reservation.pickup_code}.")
            transaction.on_commit(lambda: send_sms(phone, message))
            messages.success(request, 'Prescription rejected. The patient was asked by SMS for a valid one.')
        elif action == 'cancel' and reservation.status in ('pending', 'ready'):
            was_paid = reservation.is_paid
            cancel_reservation(reservation, reason='pharmacy')
            message = f"MedFinder: your reservation for {medicine} at {pharmacy.name} was cancelled."
            if was_paid:
                message += ' Your MoMo payment will be refunded.'
            transaction.on_commit(lambda: send_sms(phone, message))
            messages.success(request, 'Reservation cancelled and stock restored.'
                             + (' A refund is due to the patient.' if was_paid else ''))
        else:
            messages.error(request, 'That action is not available for this reservation.')

    return redirect('dashboard')


@pharmacy_required(owner_only=True)
def pharmacy_profile(request):
    pharmacy = request.pharmacy
    form = PharmacyProfileForm(request.POST or None, request.FILES or None, instance=pharmacy)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Pharmacy details updated.')
        return redirect('dashboard')
    return render(request, 'core/profile.html', {'form': form, 'pharmacy': pharmacy})


@ratelimit('register', settings.RATE_LIMIT_REGISTER)
def register(request):
    if request.method == 'POST':
        form = PharmacyRegisterForm(request.POST, request.FILES)
        if form.is_valid():
            data = form.cleaned_data
            with transaction.atomic():
                user = User.objects.create_user(username=data['username'], password=data['password'],
                                                email=data['email'])
                Pharmacy.objects.create(
                    owner=user,
                    name=data['name'],
                    license_no=data['license_no'],
                    license_document=data['license_document'],
                    district=data['district'],
                    address=data['address'],
                    phone=data['phone'],
                    latitude=data['latitude'],
                    longitude=data['longitude'],
                    verified=False,
                )
            messages.success(request, 'Registration received. Log in to add your stock. '
                                      'Your pharmacy appears in search once an administrator verifies your license.')
            return redirect('login')
    else:
        form = PharmacyRegisterForm()
    return render(request, 'core/register.html', {'form': form})


# ---------------------------------------------------------------------------
# Error pages, health check, robots.txt
# ---------------------------------------------------------------------------
def _error(request, status, title, text):
    return render(request, 'core/error.html', {'status': status, 'title': title, 'text': text}, status=status)


def error_400(request, exception=None):
    return _error(request, 400, 'Bad request', 'The request could not be understood. Please go back and try again.')


def error_403(request, exception=None):
    return _error(request, 403, 'Access denied', 'You do not have permission to open this page.')


def error_404(request, exception=None):
    return _error(request, 404, 'Page not found',
                  'The page you are looking for does not exist or has moved. Check the link or search for a medicine.')


def error_500(request):
    return _error(request, 500, 'Something went wrong',
                  'An unexpected error happened on our side. Please try again in a moment.')


def csrf_failure(request, reason=''):
    return _error(request, 403, 'Your session expired',
                  'For your security, the form expired. Go back, refresh the page and submit it again.')


def healthz(request):
    """Used by the hosting platform / uptime monitor."""
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
        return JsonResponse({'status': 'ok'})
    except Exception:
        return JsonResponse({'status': 'error'}, status=503)


def robots_txt(request):
    lines = ['User-agent: *', 'Disallow: /dashboard/', 'Disallow: /admin/', 'Disallow: /analytics/',
             'Disallow: /tools/', 'Disallow: /reservation/', 'Allow: /',
             'Sitemap: ' + request.build_absolute_uri(reverse('sitemap'))]
    return HttpResponse('\n'.join(lines) + '\n', content_type='text/plain')


# ---------------------------------------------------------------------------
# Administrator analytics
# ---------------------------------------------------------------------------
@staff_member_required
def analytics(request):
    try:
        days = int(request.GET.get('days', 30))
    except ValueError:
        days = 30
    days = days if days in (7, 30, 90) else 30
    return render(request, 'core/analytics.html', {'s': platform_summary(days), 'days': days,
                                                   'subs': billing_svc.subscription_summary()})


@staff_member_required
def analytics_export(request):
    """Anonymous search log export for supplier/NGO reports."""
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="medfinder_searches.csv"'
    writer = csv.writer(response)
    writer.writerow(['date', 'query', 'medicine', 'district', 'found', 'results', 'channel'])
    for log in SearchLog.objects.select_related('medicine').order_by('created_at').iterator():
        writer.writerow([timezone.localtime(log.created_at).strftime('%Y-%m-%d %H:%M'), log.query,
                         log.medicine.name if log.medicine else '', log.district,
                         'yes' if log.found else 'no', log.result_count, log.channel])
    return response


# ---------------------------------------------------------------------------
# Africa's Talking callbacks (USSD + incoming SMS)
# ---------------------------------------------------------------------------
def _callback_allowed(request):
    token = settings.AT_CALLBACK_TOKEN
    return not token or request.GET.get('token') == token


@csrf_exempt
@require_POST
def ussd(request):
    if not _callback_allowed(request):
        return HttpResponseForbidden('Forbidden')
    phone = request.POST.get('phoneNumber', '')
    text = request.POST.get('text', '')
    try:
        body = ussd_response(phone, text)
    except Exception:
        logger.exception('USSD error')
        body = 'END Sorry, something went wrong. Please try again.'
    return HttpResponse(body, content_type='text/plain')


@csrf_exempt
@require_POST
def sms_incoming(request):
    if not _callback_allowed(request):
        return HttpResponseForbidden('Forbidden')
    phone = request.POST.get('from', '')
    text = request.POST.get('text', '')
    SmsLog.objects.create(direction='in', phone=phone[:20], message=text[:1000], status='received')
    reply = handle_incoming_sms(phone, text)
    send_sms(phone, reply)
    return HttpResponse('OK')


# ---------------------------------------------------------------------------
# Developer / demo tools (staff only): USSD phone simulator and SMS outbox
# ---------------------------------------------------------------------------
@staff_member_required
def tool_ussd(request):
    return render(request, 'core/tools_ussd.html')


@staff_member_required
def tool_ussd_step(request):
    if request.method != 'POST':
        raise Http404
    body = ussd_response(request.POST.get('phoneNumber', ''), request.POST.get('text', ''))
    return HttpResponse(body, content_type='text/plain')


@staff_member_required
def tool_sms(request):
    reply = None
    if request.method == 'POST':
        phone = request.POST.get('from', '')
        text = request.POST.get('text', '')
        SmsLog.objects.create(direction='in', phone=phone[:20], message=text[:1000], status='received')
        reply = handle_incoming_sms(phone, text)
        send_sms(phone, reply)
        messages.success(request, 'Incoming SMS processed.')
        return redirect('tool_sms')
    phone = request.GET.get('phone', '').strip()
    logs = SmsLog.objects.all()
    if phone:
        logs = logs.filter(phone__icontains=phone.lstrip('0'))
    return render(request, 'core/tools_sms.html', {'logs': logs[:60], 'phone': phone,
                                                   'live': bool(settings.AT_API_KEY)})
