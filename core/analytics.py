"""Demand analytics and shortage forecasting.

Forecasting uses ordinary least-squares linear regression on daily search
counts per medicine (pure Python, so no heavy dependencies are needed for the
prototype). With more pilot data this can be swapped for a scikit-learn or
seasonal model; the inputs (SearchLog rows) and outputs stay the same.
"""
from collections import Counter, defaultdict
from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

from .models import Inventory, Medicine, Payment, Pharmacy, Reservation, SearchLog


def linear_forecast(series, horizon=7):
    """Fit y = a + b*x to a daily series and return (forecast_total, slope).

    Returns the sum of predicted values over the next `horizon` days,
    clamped at zero, and the daily slope (trend).
    """
    n = len(series)
    if n == 0:
        return 0.0, 0.0
    if n == 1:
        return float(series[0]) * horizon, 0.0
    mean_x = (n - 1) / 2
    mean_y = sum(series) / n
    sxx = sum((x - mean_x) ** 2 for x in range(n))
    sxy = sum((x - mean_x) * (y - mean_y) for x, y in enumerate(series))
    slope = sxy / sxx if sxx else 0.0
    intercept = mean_y - slope * mean_x
    total = sum(max(0.0, intercept + slope * (n + h)) for h in range(horizon))
    return total, slope


def _daily_series(rows, start, days):
    """rows: iterable of (date, count). Returns a list of counts for each day."""
    by_day = {d: c for d, c in rows}
    return [by_day.get((start + timedelta(days=i)), 0) for i in range(days)]


def _bars(labels, values):
    peak = max(values) if values and max(values) > 0 else 1
    return [{'label': l, 'value': v, 'pct': round(v * 100 / peak, 1)} for l, v in zip(labels, values)]


def platform_summary(days=30):
    now = timezone.now()
    since = now - timedelta(days=days)
    today = timezone.localdate()
    start = today - timedelta(days=days - 1)

    searches = SearchLog.objects.filter(created_at__gte=since)
    total_searches = searches.count()
    found = searches.filter(found=True).count()

    reservations = Reservation.objects.filter(created_at__gte=since)
    total_res = reservations.count()
    picked = reservations.filter(status='done').count()
    cancelled = reservations.filter(status='cancelled').count()

    payments = Payment.objects.filter(created_at__gte=since, status='successful')
    paid_sum = payments.aggregate(s=Sum('amount'))['s'] or Decimal('0')
    commission = payments.aggregate(s=Sum('commission'))['s'] or Decimal('0')

    search_rows = (searches.annotate(d=TruncDate('created_at')).values('d')
                   .annotate(c=Count('id')).values_list('d', 'c'))
    res_rows = (reservations.annotate(d=TruncDate('created_at')).values('d')
                .annotate(c=Count('id')).values_list('d', 'c'))
    labels = [(start + timedelta(days=i)).strftime('%d %b') for i in range(days)]
    search_series = _daily_series(search_rows, start, days)
    res_series = _daily_series(res_rows, start, days)

    top_searched = (searches.values('query').annotate(
        total=Count('id'), hits=Count('id', filter=Q(found=True)))
        .order_by('-total')[:10])
    top_searched = [dict(r, found_pct=round(r['hits'] * 100 / r['total'])) for r in top_searched]

    unmet = (searches.filter(found=False).values('query', 'district')
             .annotate(total=Count('id')).order_by('-total')[:10])

    by_channel = dict(searches.values_list('channel').annotate(c=Count('id')))
    status_counts = dict(reservations.values_list('status').annotate(c=Count('id')))

    return {
        'days': days,
        'kpi': {
            'searches': total_searches,
            'found_rate': round(found * 100 / total_searches) if total_searches else 0,
            'reservations': total_res,
            'pickups': picked,
            'pickup_rate': round(picked * 100 / total_res) if total_res else 0,
            'cancelled': cancelled,
            'paid_sum': paid_sum,
            'commission': commission,
            'pharmacies': Pharmacy.objects.filter(verified=True).count(),
            'pending_pharmacies': Pharmacy.objects.filter(verified=False).count(),
        },
        'search_bars': _bars(labels, search_series),
        'reservation_bars': _bars(labels, res_series),
        'top_searched': top_searched,
        'unmet': list(unmet),
        'by_channel': {'web': by_channel.get('web', 0), 'ussd': by_channel.get('ussd', 0)},
        'status_counts': status_counts,
        'forecast': medicine_forecasts(),
    }


def medicine_forecasts(history_days=28, horizon=7, limit=8):
    """Forecast next-week searches per medicine and flag shortage risk."""
    today = timezone.localdate()
    start = today - timedelta(days=history_days - 1)
    since = timezone.now() - timedelta(days=history_days)

    logs = (SearchLog.objects.filter(created_at__gte=since, medicine__isnull=False)
            .annotate(d=TruncDate('created_at'))
            .values_list('medicine_id', 'd', 'found'))
    per_med = defaultdict(Counter)
    unmet = Counter()
    totals = Counter()
    for med_id, d, was_found in logs:
        per_med[med_id][d] += 1
        totals[med_id] += 1
        if not was_found:
            unmet[med_id] += 1

    stock = dict(Inventory.objects.filter(pharmacy__verified=True)
                 .values_list('medicine_id').annotate(s=Sum('quantity')))
    stocking = dict(Inventory.objects.filter(pharmacy__verified=True, quantity__gt=0)
                    .values_list('medicine_id').annotate(c=Count('pharmacy', distinct=True)))
    names = dict(Medicine.objects.filter(pk__in=totals.keys()).values_list('pk', 'name'))

    out = []
    for med_id, total in totals.most_common(limit):
        series = [per_med[med_id].get(start + timedelta(days=i), 0) for i in range(history_days)]
        predicted, slope = linear_forecast(series, horizon)
        available = stock.get(med_id, 0) or 0
        unmet_share = unmet[med_id] / total if total else 0
        if available == 0 or unmet_share >= 0.5:
            risk = 'high'
        elif available < predicted or unmet_share >= 0.2:
            risk = 'medium'
        else:
            risk = 'low'
        out.append({
            'medicine': names.get(med_id, '?'),
            'searches': total,
            'unmet_pct': round(unmet_share * 100),
            'forecast': round(predicted),
            'trend': 'up' if slope > 0.05 else 'down' if slope < -0.05 else 'flat',
            'stock': available,
            'pharmacies': stocking.get(med_id, 0),
            'risk': risk,
        })
    return out


def pharmacy_insights(pharmacy, days=30):
    """What patients in this pharmacy's district searched for but could not find."""
    since = timezone.now() - timedelta(days=days)
    missed = (SearchLog.objects.filter(created_at__gte=since, found=False, district=pharmacy.district)
              .values('query').annotate(total=Count('id')).order_by('-total')[:5])
    month_start = timezone.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    done = Reservation.objects.filter(inventory__pharmacy=pharmacy, status='done', completed_at__gte=month_start)
    revenue = sum((r.total for r in done.select_related('inventory')), Decimal('0'))
    return {
        'missed': list(missed),
        'pickups_month': done.count(),
        'revenue_month': revenue,
    }
