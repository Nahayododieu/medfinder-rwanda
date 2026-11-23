"""Patient browsing pages: pharmacy directory and profiles, medicine catalogue,
help, back-in-stock alerts and the sitemap."""
import re

from django.conf import settings
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Avg, Count, Max, Min, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .billing import visible_q
from .i18n import get_lang, strings_for
from .models import INSURER_CHOICES, Inventory, Medicine, Pharmacy, Review, StockAlert
from .ratelimit import ratelimit
from .utils import DISTRICT_CHOICES, is_valid_rw_mobile, normalize_phone

def live_stock():
    """Stock patients may see: in stock, at a verified pharmacy not hidden for non-payment."""
    return Q(inventory__quantity__gt=0) & visible_q('inventory__pharmacy__')


def _ratings(pharmacy_ids):
    rows = (Review.objects.filter(status='approved', pharmacy_id__in=pharmacy_ids)
            .values('pharmacy_id').annotate(avg=Avg('rating'), n=Count('id')))
    return {r['pharmacy_id']: (round(r['avg'], 1), r['n']) for r in rows}


def _page(request, items, per_page):
    page = Paginator(items, per_page).get_page(request.GET.get('page'))
    params = request.GET.copy()
    params.pop('page', None)
    return page, params.urlencode()


def site_stats():
    """Numbers shown on the homepage."""
    pharmacies = list(Pharmacy.objects.filter(visible_q()))
    return {
        'pharmacies': len(pharmacies),
        'districts': len({p.district for p in pharmacies}),
        'open_now': sum(1 for p in pharmacies if p.is_open_now),
        'medicines': Medicine.objects.filter(live_stock()).distinct().count(),
    }


def categories_with_counts():
    return list(Medicine.objects.exclude(category='').values('category')
                .annotate(n=Count('id')).order_by('category'))


# ---------------------------------------------------------------------------
# Pharmacy directory
# ---------------------------------------------------------------------------
def pharmacy_list(request):
    q = request.GET.get('q', '').strip()[:80]
    district = request.GET.get('district', '')
    if district not in dict(DISTRICT_CHOICES):
        district = ''
    insurer = request.GET.get('insurer', '')
    if insurer not in dict(INSURER_CHOICES):
        insurer = ''
    open_now = request.GET.get('open_now') == '1'
    only_24h = request.GET.get('h24') == '1'
    delivers = request.GET.get('delivery') == '1'

    qs = Pharmacy.objects.filter(visible_q()).annotate(
        stock_count=Count('inventory', filter=Q(inventory__quantity__gt=0)))
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(address__icontains=q) | Q(district__icontains=q))
    if district:
        qs = qs.filter(district=district)
    if delivers:
        qs = qs.filter(offers_delivery=True)
    if only_24h:
        qs = qs.filter(open_24h=True)
    if insurer:
        qs = qs.filter(insurers__contains=insurer)
    pharmacies = list(qs.order_by('district', 'name'))
    if open_now:
        pharmacies = [p for p in pharmacies if p.is_open_now]

    ratings = _ratings([p.pk for p in pharmacies])
    for p in pharmacies:
        p.rating = ratings.get(p.pk)

    page, params = _page(request, pharmacies, 24)
    map_points = [{'lat': p.latitude, 'lng': p.longitude, 'name': p.name, 'district': p.district,
                   'url': reverse('pharmacy_detail', args=[p.pk])} for p in pharmacies]
    return render(request, 'core/pharmacy_list.html', {
        'page': page, 'params': params, 'total': len(pharmacies), 'q': q, 'district': district,
        'insurer': insurer, 'open_now': open_now, 'only_24h': only_24h, 'delivers': delivers,
        'districts': DISTRICT_CHOICES, 'insurers': INSURER_CHOICES, 'map_points': map_points,
        'filters_active': bool(q or district or insurer or open_now or only_24h or delivers),
    })


def pharmacy_detail(request, pk):
    pharmacy = get_object_or_404(Pharmacy.objects.filter(visible_q()), pk=pk)
    stock = list(Inventory.objects.filter(pharmacy=pharmacy, quantity__gt=0)
                 .select_related('medicine').order_by('medicine__name'))
    reviews = Review.objects.filter(pharmacy=pharmacy, status='approved')
    summary = reviews.aggregate(avg=Avg('rating'), n=Count('id'))
    return render(request, 'core/pharmacy_detail.html', {
        'pharmacy': pharmacy,
        'stock': stock,
        'categories': sorted({i.medicine.category for i in stock if i.medicine.category}),
        'reviews': reviews[:6],
        'rating': (round(summary['avg'], 1), summary['n']) if summary['n'] else None,
        'map_point': {'lat': pharmacy.latitude, 'lng': pharmacy.longitude, 'name': pharmacy.name},
    })


# ---------------------------------------------------------------------------
# Medicine catalogue
# ---------------------------------------------------------------------------
def medicine_list(request):
    q = request.GET.get('q', '').strip()[:80]
    category = request.GET.get('category', '').strip()[:80]
    letter = request.GET.get('letter', '').upper()[:1]
    if letter and not letter.isalpha():
        letter = ''
    only_available = request.GET.get('available') == '1'

    qs = Medicine.objects.annotate(
        pharmacy_count=Count('inventory', filter=live_stock(), distinct=True),
        min_price=Min('inventory__price', filter=live_stock()),
    )
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(generic_name__icontains=q))
    if category:
        qs = qs.filter(category__iexact=category)
    if letter:
        qs = qs.filter(name__istartswith=letter)
    if only_available:
        qs = qs.filter(pharmacy_count__gt=0)

    page, params = _page(request, qs.order_by('name'), 30)
    letters = sorted({n[:1].upper() for n in Medicine.objects.values_list('name', flat=True) if n[:1].isalpha()})
    return render(request, 'core/medicine_list.html', {
        'page': page, 'params': params, 'q': q, 'category': category, 'letter': letter,
        'only_available': only_available, 'letters': letters,
        'categories': categories_with_counts(),
        'filters_active': bool(q or category or letter or only_available),
    })


def _ingredient(generic_name):
    return re.split(r'\s*[+/,]\s*', generic_name or '')[0].strip()


def medicine_detail(request, pk):
    medicine = get_object_or_404(Medicine, pk=pk)
    items = list(Inventory.objects.filter(visible_q('pharmacy__'), medicine=medicine, quantity__gt=0)
                 .select_related('pharmacy').order_by('price', 'pharmacy__name'))
    prices = Inventory.objects.filter(visible_q('pharmacy__'), medicine=medicine, quantity__gt=0).aggregate(
        low=Min('price'), high=Max('price'), avg=Avg('price'))

    by_district = {}
    for i in items:
        d = by_district.setdefault(i.pharmacy.district, {'district': i.pharmacy.district, 'n': 0, 'low': i.price})
        d['n'] += 1
        d['low'] = min(d['low'], i.price)

    alternatives = []
    ingredient = _ingredient(medicine.generic_name)
    if len(ingredient) >= 3:
        alternatives = list(
            Medicine.objects.filter(Q(generic_name__icontains=ingredient) | Q(name__icontains=ingredient))
            .exclude(pk=medicine.pk)
            .annotate(pharmacy_count=Count('inventory', filter=live_stock(), distinct=True),
                      min_price=Min('inventory__price', filter=live_stock()))
            .order_by('-pharmacy_count', 'name')[:6])
        # Show how much a patient saves by choosing a cheaper brand with the same ingredient.
        low = prices.get('low')
        cheapest = min((m.min_price for m in alternatives if m.min_price is not None), default=None)
        for m in alternatives:
            m.saving = (low - m.min_price) if (low is not None and m.min_price is not None
                                               and m.min_price < low) else None
            m.cheapest = m.min_price is not None and m.min_price == cheapest
        alternatives.sort(key=lambda m: (m.saving is None, -(m.saving or 0), -m.pharmacy_count, m.name))

    return render(request, 'core/medicine_detail.html', {
        'medicine': medicine,
        'items': items[:12],
        'total': len(items),
        'prices': prices,
        'by_district': sorted(by_district.values(), key=lambda d: (-d['n'], d['district'])),
        'alternatives': alternatives,
        'districts': DISTRICT_CHOICES,
    })


# ---------------------------------------------------------------------------
# Help, alerts, sitemap
# ---------------------------------------------------------------------------
def help_page(request):
    return render(request, 'core/help.html')


@require_POST
@ratelimit('stock-alert', settings.RATE_LIMIT_RESERVE)
def stock_alert(request):
    T = strings_for(get_lang(request))
    target = request.POST.get('next', '')
    if not url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}):
        target = reverse('search')

    medicine = Medicine.objects.filter(pk=request.POST.get('medicine') or 0).first()
    phone = request.POST.get('phone', '')
    district = request.POST.get('district', '')
    if district not in dict(DISTRICT_CHOICES):
        district = ''
    if not medicine:
        messages.error(request, T['alert_unknown'])
    elif not is_valid_rw_mobile(phone):
        messages.error(request, T['alert_invalid'])
    elif request.POST.get('consent') != '1':
        messages.error(request, T['alert_consent_needed'])
    else:
        StockAlert.objects.get_or_create(medicine=medicine, phone=normalize_phone(phone), district=district,
                                         notified_at=None)
        messages.success(request, T['alert_saved'])
    return redirect(target)


def sitemap_xml(request):
    base = request.build_absolute_uri('/')[:-1]
    urls = [reverse(n) for n in ('search', 'medicine_list', 'pharmacy_list', 'help', 'track', 'about',
                                 'contact', 'for_pharmacies', 'terms', 'privacy')]
    urls += [reverse('pharmacy_detail', args=[pk]) for pk in
             Pharmacy.objects.filter(visible_q()).values_list('pk', flat=True)]
    urls += [reverse('medicine_detail', args=[pk]) for pk in Medicine.objects.values_list('pk', flat=True)]
    body = ''.join(f'<url><loc>{base}{u}</loc></url>' for u in urls)
    xml = ('<?xml version="1.0" encoding="UTF-8"?>'
           f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{body}</urlset>')
    return HttpResponse(xml, content_type='application/xml')



def medicine_photo_file(request, path):
    """Medicine photos on the local disk (development, or production without Cloudinary).

    Only the public medicine-photos folder is reachable here; licences and prescriptions are not.
    """
    from django.views.static import serve
    response = serve(request, path, document_root=settings.MEDIA_ROOT)
    response['Cache-Control'] = 'public, max-age=86400'
    response['X-Content-Type-Options'] = 'nosniff'
    return response


WELCOME_COOKIE = 'mf_welcome'


def _welcome_next(request, value):
    """Where to go after the welcome page: a page on this site, never the welcome page itself."""
    if (value and value.startswith('/') and not value.startswith('/welcome')
            and url_has_allowed_host_and_scheme(value, allowed_hosts={request.get_host()})):
        return value
    return reverse('search')


def _guess_language(request):
    """The language already chosen, else the browser's preferred one among ours, else English."""
    from .i18n import STRINGS
    if request.session.get('lang') in STRINGS:
        return request.session['lang']
    for part in request.headers.get('Accept-Language', '').split(','):
        code = part.split(';')[0].strip().lower()[:2]
        if code in STRINGS:
            return code
    return 'en'


def welcome(request):
    """First-visit page: what MedFinder does, and the language choice."""
    from .i18n import LANGUAGE_MENU, WELCOME_STRINGS
    if request.method == 'POST':
        lang = request.POST.get('lang', '')
        if lang in WELCOME_STRINGS:
            request.session['lang'] = lang
        response = redirect(_welcome_next(request, request.POST.get('next', '')))
        response.set_cookie(WELCOME_COOKIE, '1', max_age=365 * 24 * 3600, samesite='Lax',
                            secure=request.is_secure(), httponly=True)
        return response
    chosen = _guess_language(request)
    return render(request, 'core/welcome.html', {
        'W': strings_for(chosen), 'chosen': chosen, 'language_menu': LANGUAGE_MENU,
        'next': _welcome_next(request, request.GET.get('next', '')),
        'welcome_texts': WELCOME_STRINGS, 'ussd_code': settings.USSD_CODE,
    })
