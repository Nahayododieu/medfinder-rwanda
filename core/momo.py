"""MTN Mobile Money Collections API (Request to Pay).

Docs: https://momodeveloper.mtn.com/api-documentation

Flow: get an access token -> POST requesttopay with a new X-Reference-Id
(the patient approves on their phone) -> GET requesttopay/{id} for status,
or receive the callback.

When MOMO_SUBSCRIPTION_KEY is not configured, the client runs in simulation
mode: requests are accepted and the status becomes SUCCESSFUL when checked.
This lets the full payment flow be demonstrated without sandbox keys.

Sandbox note: the sandbox only accepts the EUR currency, so amounts are sent
as numbers with MOMO_CURRENCY=EUR. In production set MOMO_CURRENCY=RWF.
"""
import logging
import uuid

import requests
from django.conf import settings

logger = logging.getLogger(__name__)
TIMEOUT = 20


class MomoError(Exception):
    pass


def is_simulation():
    return not (settings.MOMO_SUBSCRIPTION_KEY and settings.MOMO_API_USER and settings.MOMO_API_KEY)


def _headers(extra=None):
    h = {
        'Ocp-Apim-Subscription-Key': settings.MOMO_SUBSCRIPTION_KEY,
        'X-Target-Environment': settings.MOMO_TARGET_ENVIRONMENT,
    }
    if extra:
        h.update(extra)
    return h


def _token():
    url = f'{settings.MOMO_BASE_URL}/collection/token/'
    try:
        r = requests.post(
            url,
            auth=(settings.MOMO_API_USER, settings.MOMO_API_KEY),
            headers={'Ocp-Apim-Subscription-Key': settings.MOMO_SUBSCRIPTION_KEY},
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        return r.json()['access_token']
    except (requests.RequestException, KeyError, ValueError) as e:
        logger.error('MoMo token failed: %s', e)
        raise MomoError('Could not connect to MTN Mobile Money. Please try again.')


def request_to_pay(amount, phone, external_id, note):
    """Start a payment. Returns the provider reference (X-Reference-Id)."""
    reference = str(uuid.uuid4())
    if is_simulation():
        logger.info('MoMo (simulated) request %s for %s from %s', reference, amount, phone)
        return reference

    msisdn = phone.lstrip('+')
    headers = _headers({
        'Authorization': f'Bearer {_token()}',
        'X-Reference-Id': reference,
        'Content-Type': 'application/json',
    })
    if settings.MOMO_CALLBACK_URL:
        headers['X-Callback-Url'] = settings.MOMO_CALLBACK_URL
    body = {
        'amount': str(int(amount)),
        'currency': settings.MOMO_CURRENCY,
        'externalId': str(external_id),
        'payer': {'partyIdType': 'MSISDN', 'partyId': msisdn},
        'payerMessage': note[:160],
        'payeeNote': note[:160],
    }
    try:
        r = requests.post(f'{settings.MOMO_BASE_URL}/collection/v1_0/requesttopay',
                          json=body, headers=headers, timeout=TIMEOUT)
    except requests.RequestException as e:
        logger.error('MoMo requesttopay failed: %s', e)
        raise MomoError('Could not connect to MTN Mobile Money. Please try again.')
    if r.status_code != 202:
        logger.error('MoMo requesttopay rejected %s: %s', r.status_code, r.text[:300])
        raise MomoError('MTN Mobile Money rejected the payment request. Check the number and try again.')
    return reference


def get_status(reference):
    """Return (status, reason) where status is 'pending', 'successful' or 'failed'."""
    if is_simulation():
        return 'successful', ''
    headers = _headers({'Authorization': f'Bearer {_token()}'})
    try:
        r = requests.get(f'{settings.MOMO_BASE_URL}/collection/v1_0/requesttopay/{reference}',
                         headers=headers, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
    except (requests.RequestException, ValueError) as e:
        logger.error('MoMo status failed: %s', e)
        raise MomoError('Could not check the payment status. Please try again.')
    return map_status(data.get('status')), str(data.get('reason') or '')


def map_status(provider_status):
    s = (provider_status or '').upper()
    if s == 'SUCCESSFUL':
        return 'successful'
    if s in ('FAILED', 'REJECTED', 'TIMEOUT'):
        return 'failed'
    return 'pending'
