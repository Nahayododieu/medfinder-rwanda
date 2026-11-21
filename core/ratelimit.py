"""Simple per-IP rate limiting backed by Django's cache.

For multi-server production use a shared cache (Redis/Memcached) so limits
apply across processes.
"""
from functools import wraps

from django.core.cache import cache
from django.shortcuts import render

from .utils import client_ip


def is_limited(request, scope, limit, period):
    key = f'rl:{scope}:{client_ip(request)}'
    if cache.add(key, 1, period):
        return False
    try:
        count = cache.incr(key)
    except ValueError:  # expired between add and incr
        cache.add(key, 1, period)
        return False
    return count > limit


def ratelimit(scope, rate):
    """Decorator limiting POST requests. `rate` is (max requests, seconds)."""
    limit, period = rate

    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            if request.method == 'POST' and is_limited(request, scope, limit, period):
                return render(request, 'core/rate_limited.html',
                              {'minutes': max(1, period // 60)}, status=429)
            return view(request, *args, **kwargs)
        return wrapper
    return decorator
