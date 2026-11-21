"""Shared helpers: Rwandan phone numbers, districts, distance, client IP."""
import re
from math import asin, cos, radians, sin, sqrt

# The 30 districts of Rwanda, grouped by province.
DISTRICTS = [
    # Kigali City
    'Gasabo', 'Kicukiro', 'Nyarugenge',
    # Eastern
    'Bugesera', 'Gatsibo', 'Kayonza', 'Kirehe', 'Ngoma', 'Nyagatare', 'Rwamagana',
    # Northern
    'Burera', 'Gakenke', 'Gicumbi', 'Musanze', 'Rulindo',
    # Southern
    'Gisagara', 'Huye', 'Kamonyi', 'Muhanga', 'Nyamagabe', 'Nyanza', 'Nyaruguru', 'Ruhango',
    # Western
    'Karongi', 'Ngororero', 'Nyabihu', 'Nyamasheke', 'Rubavu', 'Rusizi', 'Rutsiro',
]
DISTRICT_CHOICES = [(d, d) for d in sorted(DISTRICTS)]

# MTN: 078, 079   Airtel: 072, 073
_RW_MOBILE = re.compile(r'^\+2507[2389]\d{7}$')


def normalize_phone(phone):
    """Convert 0788123456, 788123456, 250788123456 or +250 788 123 456 to +250788123456."""
    p = re.sub(r'[\s\-().]', '', phone or '')
    if p.startswith('+'):
        return p
    if p.startswith('00'):
        return '+' + p[2:]
    if p.startswith('250'):
        return '+' + p
    if p.startswith('0'):
        p = p[1:]
    return '+250' + p


def is_valid_rw_mobile(phone):
    return bool(_RW_MOBILE.match(normalize_phone(phone)))


def mask_phone(phone):
    """+250788123456 -> +250 78* *** 456 (for analytics/admin lists)."""
    p = normalize_phone(phone)
    if len(p) < 7:
        return p
    return f'{p[:6]}*****{p[-3:]}'


def distance_km(lat1, lon1, lat2, lon2):
    """Great-circle distance using the Haversine formula."""
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    a = sin((lat2 - lat1) / 2) ** 2 + cos(lat1) * cos(lat2) * sin((lon2 - lon1) / 2) ** 2
    return 6371 * 2 * asin(sqrt(a))


def client_ip(request):
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', 'unknown')
