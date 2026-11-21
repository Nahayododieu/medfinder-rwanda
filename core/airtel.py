"""Airtel Money collections (Airtel Africa Open API).

Docs: https://developers.airtel.africa/ (Collection API: USSD push payment).
Flow: OAuth2 client-credentials token -> POST /merchant/v1/payments/ (the payer
approves on their phone) -> GET /standard/v1/payments/{id} for the status.

Without AIRTEL_CLIENT_ID the client runs in simulation mode, like momo.py.
"""
import logging
import uuid

import requests
from django.conf import settings

logger = logging.getLogger(__name__)
TIMEOUT = 20


class AirtelError(Exception):
    pass


def is_simulation():
    return not (settings.AIRTEL_CLIENT_ID and settings.AIRTEL_CLIENT_SECRET)


def _token():
    try:
        r = requests.post(f'{settings.AIRTEL_BASE_URL}/auth/oauth2/token', timeout=TIMEOUT, json={
            'client_id': settings.AIRTEL_CLIENT_ID,
            'client_secret': settings.AIRTEL_CLIENT_SECRET,
            'grant_type': 'client_credentials',
        })
        r.raise_for_status()
        return r.json()['access_token']
    except (requests.RequestException, KeyError, ValueError) as e:
        logger.error('Airtel token failed: %s', e)
        raise AirtelError('Could not connect to Airtel Money. Please try again.')


def _headers():
    return {
        'Authorization': f'Bearer {_token()}',
        'Content-Type': 'application/json',
        'X-Country': settings.AIRTEL_COUNTRY,
        'X-Currency': settings.AIRTEL_CURRENCY,
    }


def request_to_pay(amount, phone, external_id, note):
    """Send a payment prompt to an Airtel number. Returns the transaction id."""
    transaction_id = uuid.uuid4().hex[:20]
    if is_simulation():
        logger.info('Airtel (simulated) request %s for %s from %s', transaction_id, amount, phone)
        return transaction_id
    msisdn = phone.lstrip('+')
    if msisdn.startswith('250'):
        msisdn = msisdn[3:]  # Airtel expects the national number without the country code
    body = {
        'reference': note[:64],
        'subscriber': {'country': settings.AIRTEL_COUNTRY, 'currency': settings.AIRTEL_CURRENCY, 'msisdn': msisdn},
        'transaction': {'amount': int(amount), 'country': settings.AIRTEL_COUNTRY,
                        'currency': settings.AIRTEL_CURRENCY, 'id': transaction_id},
    }
    try:
        r = requests.post(f'{settings.AIRTEL_BASE_URL}/merchant/v1/payments/', json=body,
                          headers=_headers(), timeout=TIMEOUT)
        data = r.json()
    except (requests.RequestException, ValueError) as e:
        logger.error('Airtel payment failed: %s', e)
        raise AirtelError('Could not connect to Airtel Money. Please try again.')
    if r.status_code >= 400 or not (data.get('status') or {}).get('success', False):
        logger.error('Airtel payment rejected %s: %s', r.status_code, str(data)[:300])
        raise AirtelError('Airtel Money rejected the payment request. Check the number and try again.')
    return transaction_id


def get_status(transaction_id):
    """Return (status, reason): 'pending', 'successful' or 'failed'."""
    if is_simulation():
        return 'successful', ''
    try:
        r = requests.get(f'{settings.AIRTEL_BASE_URL}/standard/v1/payments/{transaction_id}',
                         headers=_headers(), timeout=TIMEOUT)
        r.raise_for_status()
        tx = (r.json().get('data') or {}).get('transaction') or {}
    except (requests.RequestException, ValueError) as e:
        logger.error('Airtel status failed: %s', e)
        raise AirtelError('Could not check the payment status. Please try again.')
    return map_status(tx.get('status')), str(tx.get('message') or '')


def map_status(code):
    code = (code or '').upper()
    if code == 'TS':
        return 'successful'
    if code in ('TF', 'TE'):  # failed / expired
        return 'failed'
    return 'pending'  # TA (ambiguous), TIP (in progress)
