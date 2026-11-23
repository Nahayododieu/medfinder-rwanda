class SecurityHeadersMiddleware:
    """Extra browser security headers not set by Django itself."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        # Only the site itself may ask for location; no camera, microphone or payment APIs.
        response.setdefault('Permissions-Policy',
                            'geolocation=(self), camera=(), microphone=(), payment=(), usb=()')
        response.setdefault('X-Permitted-Cross-Domain-Policies', 'none')
        return response


class WelcomeMiddleware:
    """Send a person's first visit to the public pages through the welcome page (language choice).

    Never for search engines, link previews (WhatsApp, Facebook...), uptime monitors, scripts,
    logged-in users, pharmacy/admin pages, SMS reservation links, payment callbacks or APIs.
    """
    PUBLIC_PREFIXES = ('/medicines/', '/find-pharmacy/', '/help/', '/about/', '/contact/', '/pharmacies/',
                       '/privacy/', '/terms/')
    NOT_A_PERSON = ('bot', 'crawl', 'spider', 'slurp', 'preview', 'facebookexternalhit', 'facebot', 'whatsapp',
                    'telegram', 'twitter', 'linkedin', 'embedly', 'discord', 'skype', 'curl', 'wget', 'python',
                    'httpclient', 'okhttp', 'uptime', 'monitor', 'pingdom', 'lighthouse', 'headless', 'go-http',
                    'java/', 'axios', 'node-fetch', 'render')

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if self._should_welcome(request):
            from urllib.parse import quote
            from django.shortcuts import redirect
            from django.urls import reverse
            response = redirect(f"{reverse('welcome')}?next={quote(request.get_full_path(), safe='/?=&')}")
            response['Cache-Control'] = 'no-store'
            return response
        return self.get_response(request)

    def _should_welcome(self, request):
        from .views_browse import WELCOME_COOKIE
        if request.method != 'GET' or request.COOKIES.get(WELCOME_COOKIE):
            return False
        path = request.path
        if not (path == '/' or path.startswith(self.PUBLIC_PREFIXES)):
            return False
        if 'text/html' not in request.headers.get('Accept', ''):
            return False
        agent = request.headers.get('User-Agent', '').lower()
        if 'mozilla' not in agent or any(word in agent for word in self.NOT_A_PERSON):
            return False
        user = getattr(request, 'user', None)
        return not (user and user.is_authenticated)
