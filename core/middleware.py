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
