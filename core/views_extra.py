"""Business pages, pharmacy staff and reports, invoices, admin reports, public API, PWA and status."""
import csv
import json
import mimetypes
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.models import User
from django.db import connection, transaction
from django.db.models import Count, Q, Sum
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import payments
from .access import pharmacy_required
from .billing import visible_q
from .forms import StaffForm
from .models import (Medicine, Payment, Pharmacy, PharmacyStaff, Reservation, SearchLog, SmsLog,
                     StockChange, SubscriptionPayment)
from .ratelimit import is_limited
from .services import find_stock, suggest_spelling
from .utils import DISTRICT_CHOICES, distance_km


# ---------------------------------------------------------------------------
# Public business pages
# ---------------------------------------------------------------------------
def about(request):
    stats = {
        'pharmacies': Pharmacy.objects.filter(visible_q()).count(),
        'medicines': Medicine.objects.count(),
        'districts': Pharmacy.objects.filter(visible_q()).values('district').distinct().count(),
    }
    return render(request, 'core/about.html', {'stats': stats, 'founder': settings.BUSINESS_FOUNDER})


def contact(request):
    whatsapp = ''.join(ch for ch in (settings.SUPPORT_WHATSAPP or settings.SUPPORT_PHONE) if ch.isdigit())
    return render(request, 'core/contact.html', {'whatsapp': whatsapp, 'social': settings.SOCIAL_LINKS})


# ---------------------------------------------------------------------------
# Pharmacy staff (owner only)
# ---------------------------------------------------------------------------
@pharmacy_required(owner_only=True)
def staff(request):
    pharmacy = request.pharmacy
    form = StaffForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            user = User.objects.create_user(username=form.cleaned_data['username'],
                                            email=form.cleaned_data['email'],
                                            password=form.cleaned_data['password'])
            PharmacyStaff.objects.create(pharmacy=pharmacy, user=user, role='pharmacist')
        messages.success(request, f'Pharmacist account "{user.username}" created. They can log in now.')
        return redirect('staff')
    return render(request, 'core/staff.html', {
        'pharmacy': pharmacy, 'form': form,
        'members': pharmacy.staff.select_related('user').order_by('user__username'),
    })


@pharmacy_required(owner_only=True)
@require_POST
def staff_remove(request, pk):
    member = get_object_or_404(PharmacyStaff, pk=pk, pharmacy=request.pharmacy)
    name = member.user.username
    member.user.delete()  # removes the login and the staff link
    messages.success(request, f'Account "{name}" removed.')
    return redirect('staff')


# ---------------------------------------------------------------------------
# Pharmacy reports
# ---------------------------------------------------------------------------
def _period(request):
    try:
        days = int(request.GET.get('days', 30))
    except ValueError:
        days = 30
    return days if days in (7, 30, 90, 365) else 30


@pharmacy_required()
def reports(request):
    pharmacy, days = request.pharmacy, _period(request)
    since = timezone.now() - timedelta(days=days)
    done = (Reservation.objects.filter(inventory__pharmacy=pharmacy, status='done', completed_at__gte=since)
            .select_related('inventory__medicine'))
    by_med = {}
    for r in done:
        row = by_med.setdefault(r.inventory.medicine.name, {'medicine': r.inventory.medicine.name, 'units': 0,
                                                            'revenue': Decimal('0'), 'orders': 0})
        row['units'] += r.quantity
        row['revenue'] += r.total
        row['orders'] += 1
    sales = sorted(by_med.values(), key=lambda x: -x['revenue'])
    all_res = Reservation.objects.filter(inventory__pharmacy=pharmacy, created_at__gte=since)
    totals = {
        'orders': sum(x['orders'] for x in sales),
        'units': sum(x['units'] for x in sales),
        'revenue': sum((x['revenue'] for x in sales), Decimal('0')),
        'reservations': all_res.count(),
        'cancelled': all_res.filter(status='cancelled').count(),
        'paid_online': Payment.objects.filter(reservation__inventory__pharmacy=pharmacy, status='successful',
                                              created_at__gte=since).aggregate(s=Sum('amount'))['s'] or 0,
    }
    missed = (SearchLog.objects.filter(created_at__gte=since, found=False, district=pharmacy.district)
              .values('query').annotate(total=Count('id')).order_by('-total')[:10])
    searched = (SearchLog.objects.filter(created_at__gte=since, district=pharmacy.district)
                .values('query').annotate(total=Count('id')).order_by('-total')[:10])
    changes = (StockChange.objects.filter(pharmacy=pharmacy).select_related('medicine', 'user')[:30])

    if request.GET.get('export') == 'csv':
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = f'attachment; filename="medfinder_sales_{days}d.csv"'
        response.write('﻿')
        w = csv.writer(response)
        w.writerow(['medicine', 'orders', 'units', 'revenue_rwf'])
        for x in sales:
            w.writerow([x['medicine'], x['orders'], x['units'], f"{x['revenue']:.0f}"])
        return response

    return render(request, 'core/reports.html', {
        'pharmacy': pharmacy, 'days': days, 'sales': sales, 'totals': totals,
        'missed': missed, 'searched': searched, 'changes': changes,
    })


@pharmacy_required(owner_only=True)
def invoice(request, pk):
    payment = get_object_or_404(SubscriptionPayment, pk=pk, pharmacy=request.pharmacy, status='successful')
    return render(request, 'core/invoice.html', {'p': payment, 'pharmacy': request.pharmacy})


# ---------------------------------------------------------------------------
# Administrator reports and files
# ---------------------------------------------------------------------------
@staff_member_required
def commission_report(request):
    """Online payments per pharmacy: gross, MedFinder commission and pharmacy share."""
    today = timezone.localdate()
    try:
        year, month = (int(x) for x in request.GET.get('month', f'{today:%Y-%m}').split('-'))
        start = today.replace(year=year, month=month, day=1)
    except (ValueError, TypeError):
        start = today.replace(day=1)
    end = (start + timedelta(days=32)).replace(day=1)
    rows = (Payment.objects.filter(status='successful', created_at__date__gte=start, created_at__date__lt=end)
            .values('reservation__inventory__pharmacy__name', 'provider')
            .annotate(n=Count('id'), gross=Sum('amount'), commission=Sum('commission'))
            .order_by('reservation__inventory__pharmacy__name'))
    rows = [dict(r, pharmacy=r['reservation__inventory__pharmacy__name'],
                 share=(r['gross'] or 0) - (r['commission'] or 0)) for r in rows]
    subs = SubscriptionPayment.objects.filter(status='successful', updated_at__date__gte=start,
                                              updated_at__date__lt=end).select_related('pharmacy')
    totals = {
        'gross': sum((r['gross'] or 0 for r in rows), Decimal('0')),
        'commission': sum((r['commission'] or 0 for r in rows), Decimal('0')),
        'share': sum((r['share'] for r in rows), Decimal('0')),
        'subscriptions': sum((s.amount for s in subs), Decimal('0')),
    }
    totals['income'] = totals['commission'] + totals['subscriptions']

    if request.GET.get('export') == 'csv':
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = f'attachment; filename="medfinder_commission_{start:%Y-%m}.csv"'
        response.write('﻿')
        w = csv.writer(response)
        w.writerow(['pharmacy', 'provider', 'payments', 'gross_rwf', 'commission_rwf', 'pharmacy_share_rwf'])
        for r in rows:
            w.writerow([r['pharmacy'], r['provider'], r['n'], f"{r['gross']:.0f}", f"{r['commission']:.0f}",
                        f"{r['share']:.0f}"])
        w.writerow([])
        w.writerow(['subscription invoices'])
        for s in subs:
            w.writerow([s.pharmacy.name, s.get_method_display(), s.invoice_number, f'{s.amount:.0f}'])
        return response

    months = [(today.replace(day=1) - timedelta(days=30 * i)).strftime('%Y-%m') for i in range(12)]
    return render(request, 'core/commission.html', {
        'rows': rows, 'subs': subs, 'totals': totals, 'start': start, 'months': sorted(set(months), reverse=True),
    })


@staff_member_required
def license_file(request, pk):
    """License documents are private: only administrators can open them."""
    pharmacy = get_object_or_404(Pharmacy, pk=pk)
    if not pharmacy.license_document:
        raise Http404
    try:
        handle = pharmacy.license_document.open('rb')
    except FileNotFoundError:
        raise Http404
    content_type = mimetypes.guess_type(pharmacy.license_document.name)[0] or 'application/octet-stream'
    return FileResponse(handle, content_type=content_type)


# ---------------------------------------------------------------------------
# Public API (read-only, JSON)
# ---------------------------------------------------------------------------
def _api_limited(request):
    limit, period = settings.RATE_LIMIT_API
    return is_limited(request, 'api', limit, period)


def _api(data, status=200):
    response = JsonResponse(data, status=status, json_dumps_params={'ensure_ascii': False})
    response['Access-Control-Allow-Origin'] = '*'
    response['Cache-Control'] = 'public, max-age=30'
    return response


def _pharmacy_json(p):
    return {
        'id': p.pk, 'name': p.name, 'district': p.district, 'address': p.address, 'phone': p.phone,
        'latitude': p.latitude, 'longitude': p.longitude,
        'open_now': p.is_open_now, 'hours': p.hours_display, 'insurance': p.insurer_codes,
    }


def api_search(request):
    if _api_limited(request):
        return _api({'error': 'rate_limited'}, 429)
    q = request.GET.get('q', '').strip()[:100]
    if len(q) < 2:
        return _api({'error': 'q must have at least 2 characters'}, 400)
    district = request.GET.get('district', '')
    district = district if district in dict(DISTRICT_CHOICES) else ''
    items = list(find_stock(q, district))
    corrected = None
    if not items:
        spelled = suggest_spelling(q)
        if spelled:
            items = list(find_stock(spelled, district))
            corrected = spelled if items else None
    try:
        lat, lng = float(request.GET['lat']), float(request.GET['lng'])
    except (KeyError, ValueError):
        lat = lng = None
    out = []
    for i in items:
        row = {'medicine': i.medicine.name, 'generic_name': i.medicine.generic_name,
               'requires_prescription': i.medicine.requires_prescription,
               'price_rwf': float(i.price), 'quantity': i.quantity,
               'updated_at': i.updated_at.isoformat(), 'pharmacy': _pharmacy_json(i.pharmacy)}
        if lat is not None:
            row['distance_km'] = round(distance_km(lat, lng, i.pharmacy.latitude, i.pharmacy.longitude), 2)
        out.append(row)
    out.sort(key=lambda r: (r.get('distance_km', 0), r['price_rwf']))
    return _api({'query': q, 'corrected_query': corrected, 'count': len(out), 'results': out})


def api_pharmacies(request):
    if _api_limited(request):
        return _api({'error': 'rate_limited'}, 429)
    qs = Pharmacy.objects.filter(visible_q()).order_by('name')
    district = request.GET.get('district', '')
    if district in dict(DISTRICT_CHOICES):
        qs = qs.filter(district=district)
    return _api({'count': qs.count(), 'results': [_pharmacy_json(p) for p in qs]})


def api_medicines(request):
    if _api_limited(request):
        return _api({'error': 'rate_limited'}, 429)
    q = request.GET.get('q', '').strip()[:60]
    qs = Medicine.objects.order_by('name')
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(generic_name__icontains=q))
    return _api({'count': qs.count(), 'results': [
        {'id': m.pk, 'name': m.name, 'generic_name': m.generic_name, 'category': m.category,
         'requires_prescription': m.requires_prescription} for m in qs[:200]]})


def developers(request):
    base = request.build_absolute_uri('/').rstrip('/')
    return render(request, 'core/developers.html', {'base': base, 'limit': settings.RATE_LIMIT_API[0]})


# ---------------------------------------------------------------------------
# Progressive web app
# ---------------------------------------------------------------------------
def manifest(request):
    data = {
        'name': settings.BUSINESS_NAME,
        'short_name': 'MedFinder',
        'description': 'Find medicine in stock at verified pharmacies in Rwanda and reserve it for pickup.',
        'start_url': '/?source=pwa',
        'scope': '/',
        'display': 'standalone',
        'background_color': '#a8a8a8',
        'theme_color': '#000000',
        'icons': [
            {'src': '/static/core/icons/icon-192.png', 'sizes': '192x192', 'type': 'image/png'},
            {'src': '/static/core/icons/icon-512.png', 'sizes': '512x512', 'type': 'image/png'},
            {'src': '/static/core/icons/icon-512.png', 'sizes': '512x512', 'type': 'image/png',
             'purpose': 'maskable'},
        ],
    }
    return HttpResponse(json.dumps(data), content_type='application/manifest+json')


SERVICE_WORKER = """
const CACHE = 'medfinder-v2';
const SHELL = ['/offline/', '/static/core/css/site.css', '/static/core/icons/icon-192.png'];
self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});
self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== location.origin) return;
  if (req.mode === 'navigate') {
    // Pages always come fresh from the network (stock changes); offline page as fallback.
    e.respondWith(fetch(req).catch(() => caches.match('/offline/')));
    return;
  }
  if (url.pathname.startsWith('/static/')) {
    // Network first so design updates show up at once; the saved copy is only used offline.
    e.respondWith(fetch(req).then(res => {
      if (res.ok) {
        const copy = res.clone();
        caches.open(CACHE).then(c => c.put(req, copy));
      }
      return res;
    }).catch(() => caches.match(req)));
  }
});
"""


def service_worker(request):
    response = HttpResponse(SERVICE_WORKER, content_type='application/javascript')
    response['Service-Worker-Allowed'] = '/'
    response['Cache-Control'] = 'no-cache'
    return response


def offline(request):
    return render(request, 'core/offline.html')


# ---------------------------------------------------------------------------
# Status page
# ---------------------------------------------------------------------------
def status(request):
    checks = []
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
        checks.append(('Website and database', 'ok', 'Working'))
    except Exception:
        checks.append(('Website and database', 'down', 'Database not reachable'))
    last_sms = SmsLog.objects.filter(direction='out').first()
    if not settings.AT_API_KEY:
        checks.append(('SMS', 'info', 'Test mode (messages are logged, not sent)'))
    elif last_sms and last_sms.status == 'failed':
        checks.append(('SMS', 'warn', 'The last message failed to send'))
    else:
        checks.append(('SMS', 'ok', 'Working'))
    checks.append(('USSD', 'ok' if settings.AT_API_KEY else 'info',
                   f'Dial {settings.USSD_CODE}' if settings.AT_API_KEY else 'Test mode'))
    for provider, label in payments.PROVIDER_NAMES.items():
        checks.append((label, 'info' if payments.is_simulation(provider) else 'ok',
                       'Test mode (simulation)' if payments.is_simulation(provider) else 'Working'))
    overall = 'down' if any(c[1] == 'down' for c in checks) else (
        'warn' if any(c[1] == 'warn' for c in checks) else 'ok')
    return render(request, 'core/status.html', {'checks': checks, 'overall': overall,
                                                'now': timezone.localtime()})
