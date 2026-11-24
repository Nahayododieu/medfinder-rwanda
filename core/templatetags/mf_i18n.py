"""Translation helpers for templates (loaded automatically on every page).

{% t "English text" %}            -> the text in the visitor's language (English if no translation yet)
{% t "Wait {n} minutes" n=5 %}    -> placeholders filled after translating
{% t some_variable %}             -> also works for form labels, help texts and error messages
{% since some_datetime %}         -> "3 days, 2 hours" in the visitor's language
"""
from django import template
from django.utils import timezone
from django.utils.timesince import timesince

from core.i18n_text import translate

register = template.Library()


@register.simple_tag(takes_context=True)
def t(context, text, **values):
    lang = context.get('lang') or 'en'
    out = translate(str(text), lang)
    return out.format(**values) if values else out


# (singular, plural) unit names; Kinyarwanda puts the number after the noun ("iminsi 3").
_RW_UNITS = [('year', 365 * 86400, 'umwaka', 'imyaka'), ('month', 30 * 86400, 'ukwezi', 'amezi'),
             ('week', 7 * 86400, 'icyumweru', 'ibyumweru'), ('day', 86400, 'umunsi', 'iminsi'),
             ('hour', 3600, 'isaha', 'amasaha'), ('minute', 60, 'umunota', 'iminota')]


def since_rw(value, now=None):
    seconds = int(((now or timezone.now()) - value).total_seconds())
    parts = []
    for _, size, one, many in _RW_UNITS:
        if seconds >= size:
            n, seconds = divmod(seconds, size)
            parts.append(f'{one if n == 1 else many} {n}')
            if len(parts) == 2:
                break
        elif parts:
            break
    return ', '.join(parts) or 'iminota 0'


@register.simple_tag(takes_context=True)
def since(context, value):
    if not value:
        return ''
    if (context.get('lang') or 'en') == 'rw':
        return since_rw(value)
    return timesince(value)  # English or French (Django translates it for French)
