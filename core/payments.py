"""One entry point for mobile money: picks MTN MoMo or Airtel Money from the phone number."""
from . import airtel, momo
from .utils import normalize_phone


class PaymentError(Exception):
    pass


PROVIDER_NAMES = {'mtn': 'MTN MoMo', 'airtel': 'Airtel Money'}


def provider_for(phone):
    """MTN: 078/079. Airtel: 072/073."""
    p = normalize_phone(phone)
    if p.startswith(('+25078', '+25079')):
        return 'mtn'
    if p.startswith(('+25072', '+25073')):
        return 'airtel'
    return None


def is_simulation(provider):
    return (momo if provider == 'mtn' else airtel).is_simulation()


def request_to_pay(provider, amount, phone, external_id, note):
    try:
        if provider == 'mtn':
            return momo.request_to_pay(amount, phone, external_id=external_id, note=note)
        return airtel.request_to_pay(amount, phone, external_id=external_id, note=note)
    except (momo.MomoError, airtel.AirtelError) as e:
        raise PaymentError(str(e))


def get_status(provider, reference):
    try:
        if provider == 'airtel':
            return airtel.get_status(reference)
        return momo.get_status(reference)
    except (momo.MomoError, airtel.AirtelError) as e:
        raise PaymentError(str(e))


def any_simulation():
    return momo.is_simulation() or airtel.is_simulation()
