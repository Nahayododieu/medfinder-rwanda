"""Licence checks that help an administrator decide whether to verify a pharmacy.

The official source is the "List of licensed human retail pharmacies" that Rwanda FDA publishes as a PDF
on rwandafda.gov.rw. An administrator downloads it and uploads it in the admin panel (Rwanda FDA licence
lists). Each pharmacy is then compared with the newest list: licence number, name, district and expiry.
The matching does not depend on the PDF's exact layout: it looks for the licence number as a whole word
sequence on a line, then reads the name, district and dates around it.
"""
import io
import re
from datetime import date, timedelta

from django.utils import timezone

from .utils import is_valid_rw_mobile

LIST_MAX_AGE_DAYS = 183  # Rwanda FDA updates the list a few times a year
NAME_STOPWORDS = {'PHARMACY', 'PHARMACIE', 'PHARMA', 'LTD', 'LIMITED', 'SARL', 'THE', 'AND', 'CO', 'COMPANY',
                  'RWANDA', 'BRANCH'}
DATE_RE = re.compile(r'\b(\d{1,2})\s*[/.\-]\s*(\d{1,2})\s*[/.\-]\s*(\d{4})\b')

OK, WARN, FAIL = 'ok', 'warn', 'fail'


class ListReadError(Exception):
    """The uploaded list could not be read as text."""


def extract_text(filename, data):
    """Text of an uploaded list: PDF (with a text layer), CSV or plain text."""
    name = (filename or '').lower()
    if name.endswith('.pdf') or data[:5] == b'%PDF-':
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(data))
            text = '\n'.join((page.extract_text() or '') for page in reader.pages)
        except Exception as exc:
            raise ListReadError(f'This PDF could not be read ({type(exc).__name__}).') from exc
        if len(text.strip()) < 50:
            raise ListReadError('This PDF has no readable text (it may be a scanned image). '
                                'Download the list again from rwandafda.gov.rw, or upload it as CSV.')
        return text
    for encoding in ('utf-8-sig', 'cp1252', 'latin-1'):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ListReadError('Upload the list as a PDF, CSV or text file.')


def _pieces(text):
    return [p for p in re.split(r'[^A-Z0-9]+', text.upper()) if p]


def normalise_number(value):
    return ''.join(_pieces(value or ''))


def _contains_number(line_pieces, number):
    """True if the licence number appears as whole consecutive pieces (so '123' does not match '41234')."""
    if not number:
        return False
    for i in range(len(line_pieces)):
        joined = ''
        for piece in line_pieces[i:i + 8]:
            joined += piece
            if joined == number:
                return True
            if not number.startswith(joined):
                break
    return False


def name_words(name):
    return [w for w in _pieces(name) if len(w) >= 3 and w not in NAME_STOPWORDS and not w.isdigit()]


def _dates(text):
    found = []
    for d, m, y in DATE_RE.findall(text):
        try:
            found.append(date(int(y), int(m), int(d)))
        except ValueError:
            continue
    return found


def find_on_list(pharmacy, licence_list):
    """Compare one pharmacy with an uploaded Rwanda FDA list. Returns a dict describing what matched."""
    lines = [ln.strip() for ln in licence_list.text.splitlines() if ln.strip()]
    number = normalise_number(pharmacy.license_no)
    words = name_words(pharmacy.name)
    result = {'found': False, 'context': '', 'name_ok': False, 'district_ok': False, 'expiry': None,
              'name_hits': []}
    if len(number) >= 3:
        for i, line in enumerate(lines):
            if _contains_number(_pieces(line), number):
                # A table row can wrap onto the next lines in the PDF text: look at a small window.
                context = ' '.join(lines[max(0, i - 1):i + 3])
                upper = context.upper()
                result.update(found=True, context=line[:300])
                result['name_ok'] = bool(words) and sum(w in upper for w in words) >= max(1, (len(words) + 1) // 2)
                result['district_ok'] = bool(pharmacy.district) and pharmacy.district.upper() in upper
                dates = _dates(' '.join(lines[i:i + 3]))
                result['expiry'] = max(dates) if dates else None
                break
    if not result['found'] and words:
        # Not found by number: is the pharmacy on the list under another number? (typing mistake)
        for line in lines:
            upper = line.upper()
            if all(w in upper for w in words):
                result['name_hits'].append(line[:200])
                if len(result['name_hits']) == 3:
                    break
    return result


def automatic_checks(pharmacy):
    """[(label, state, detail)] for the admin page. state is OK, WARN or FAIL."""
    from .models import FdaLicenceList, Pharmacy
    checks = []
    today = timezone.localdate()

    if pharmacy.license_document:
        checks.append(('Licence document uploaded', OK, 'Open it with the link above and compare it with the details.'))
    else:
        checks.append(('Licence document uploaded', FAIL, 'No document. Ask the pharmacy to send a photo or scan.'))

    number = normalise_number(pharmacy.license_no)
    twins = [p for p in Pharmacy.objects.exclude(pk=pharmacy.pk).only('license_no', 'name')
             if normalise_number(p.license_no) == number]
    if twins:
        checks.append(('Licence number used only once', FAIL,
                       'Same licence number as: ' + ', '.join(p.name for p in twins[:3])))
    else:
        checks.append(('Licence number used only once', OK, 'No other pharmacy on MedFinder uses this number.'))

    latest = FdaLicenceList.objects.order_by('-list_date', '-uploaded_at').first()
    if not latest:
        checks.append(('On the official Rwanda FDA list', WARN,
                       'No Rwanda FDA list uploaded yet. Add one in "Rwanda FDA licence lists" to check automatically.'))
    else:
        label = f'On the official Rwanda FDA list ({latest.list_date:%B %Y})'
        if (today - latest.list_date).days > LIST_MAX_AGE_DAYS:
            checks.append(('Rwanda FDA list is recent', WARN,
                           f'The newest uploaded list is from {latest.list_date:%d %b %Y}. Upload the latest one.'))
        match = find_on_list(pharmacy, latest)
        if match['found']:
            checks.append((label, OK, f'Found: “{match["context"]}”'))
            checks.append(('Name matches the Rwanda FDA list', OK if match['name_ok'] else WARN,
                           'Matches.' if match['name_ok'] else 'The name on the list looks different. Compare carefully.'))
            checks.append(('District matches the Rwanda FDA list', OK if match['district_ok'] else WARN,
                           'Matches.' if match['district_ok'] else
                           f'“{pharmacy.district}” was not found next to this licence on the list.'))
            if match['expiry']:
                state = FAIL if match['expiry'] < today else (WARN if match['expiry'] < today + timedelta(days=30) else OK)
                checks.append(('Licence valid (expiry on the list)', state,
                               f'Expires {match["expiry"]:%d %b %Y}' + (' (EXPIRED)' if state == FAIL else '')))
            else:
                checks.append(('Licence valid (expiry on the list)', WARN, 'No expiry date found next to this licence.'))
        else:
            detail = 'This licence number is not on the list. Do not verify unless Rwanda FDA confirms it.'
            if match['name_hits']:
                detail += ' A pharmacy with a similar name is listed as: ' + ' | '.join(f'“{h}”' for h in match['name_hits'])
            checks.append((label, FAIL, detail))

    if pharmacy.license_expiry:
        state = FAIL if pharmacy.license_expiry < today else OK
        checks.append(('Licence expiry entered by admin', state, f'{pharmacy.license_expiry:%d %b %Y}'
                       + (' (EXPIRED: hidden from patients)' if state == FAIL else '')))

    checks.append(('Phone number is a Rwandan mobile', OK if is_valid_rw_mobile(pharmacy.phone) else WARN,
                   pharmacy.phone if is_valid_rw_mobile(pharmacy.phone) else
                   f'{pharmacy.phone}: not a mobile number; call it to confirm it works.'))
    return checks


def suggested_expiry(pharmacy):
    """The expiry date printed next to this licence on the newest Rwanda FDA list, if any."""
    from .models import FdaLicenceList
    latest = FdaLicenceList.objects.order_by('-list_date', '-uploaded_at').first()
    if not latest:
        return None
    match = find_on_list(pharmacy, latest)
    return match['expiry'] if match['found'] else None
