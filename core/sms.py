"""Outgoing SMS through Africa's Talking. Every message is recorded in SmsLog.

Without AT_API_KEY the message is logged as 'simulated' so the full flow can be
demonstrated offline (see Admin > SMS logs or /tools/sms/).
"""
import logging

from django.conf import settings

from .models import SmsLog
from .utils import normalize_phone

logger = logging.getLogger(__name__)
_ready = False

__all__ = ['send_sms', 'normalize_phone']


def send_sms(phone, message):
    """Send an SMS. Never raises: a failed SMS must not break a reservation."""
    global _ready
    number = normalize_phone(phone)

    if not settings.AT_API_KEY:
        logger.info('SMS (simulated) to %s: %s', number, message)
        SmsLog.objects.create(phone=number, message=message, status='simulated')
        return False

    try:
        import africastalking
        if not _ready:
            africastalking.initialize(settings.AT_USERNAME, settings.AT_API_KEY)
            _ready = True
        kwargs = {'sender_id': settings.AT_SENDER_ID} if settings.AT_SENDER_ID else {}
        africastalking.SMS.send(message, [number], **kwargs)
        SmsLog.objects.create(phone=number, message=message, status='sent')
        return True
    except Exception as e:  # network, auth, invalid number...
        logger.error('SMS failed: %s', e)
        try:
            SmsLog.objects.create(phone=number, message=message, status='failed', error=str(e)[:255])
        except Exception:
            pass
        return False
