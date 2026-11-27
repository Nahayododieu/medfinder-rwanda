"""
Django settings for MedFinder Rwanda.

All secrets and deployment-specific values come from environment variables
(loaded from a local .env file in development). See .env.example.
"""

import os
import secrets
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / '.env')

# Verify HTTPS certificates with the operating system's trust store. Some API
# hosts (e.g. Africa's Talking) send an incomplete certificate chain that the OS
# can complete but Python's bundled CA list cannot. Verification stays ON.
try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass


def env_bool(name, default=False):
    return os.getenv(name, str(default)).strip().lower() in ('1', 'true', 'yes', 'on')


def env_list(name, default=''):
    return [v.strip() for v in os.getenv(name, default).split(',') if v.strip()]


# --- Core security -----------------------------------------------------------
DEBUG = env_bool('DJANGO_DEBUG', True)

SECRET_KEY = os.getenv('DJANGO_SECRET_KEY', '')
if not SECRET_KEY:
    if not DEBUG:
        raise RuntimeError('DJANGO_SECRET_KEY must be set when DJANGO_DEBUG is off.')
    # Development only: a random key per process (sessions reset on restart).
    SECRET_KEY = 'dev-' + secrets.token_urlsafe(50)

ALLOWED_HOSTS = env_list('DJANGO_ALLOWED_HOSTS', 'localhost,127.0.0.1,[::1]' if DEBUG else '')
CSRF_TRUSTED_ORIGINS = env_list('DJANGO_CSRF_TRUSTED_ORIGINS')

# Render sets this to the site's address (e.g. medfinder.onrender.com): trust it automatically.
_render_host = os.getenv('RENDER_EXTERNAL_HOSTNAME', '')
if _render_host:
    ALLOWED_HOSTS.append(_render_host)
    CSRF_TRUSTED_ORIGINS.append(f'https://{_render_host}')

if DEBUG:
    # Lets the Africa's Talking simulator reach a tunnel (e.g. ngrok) in development.
    ALLOWED_HOSTS += env_list('DJANGO_EXTRA_DEV_HOSTS')
else:
    SECURE_SSL_REDIRECT = env_bool('DJANGO_SECURE_SSL_REDIRECT', True)
    SECURE_REDIRECT_EXEMPT = [r'^healthz/$']  # the host's internal health check uses plain HTTP
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = int(os.getenv('DJANGO_HSTS_SECONDS', '31536000'))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = env_bool('DJANGO_HSTS_PRELOAD', False)

SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = 'same-origin'
X_FRAME_OPTIONS = 'DENY'
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_AGE = 60 * 60 * 8  # 8 hours for pharmacy staff sessions


# --- Applications ------------------------------------------------------------
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.humanize',
    'core',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',  # serves CSS/JS efficiently in production
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'core.middleware.SecurityHeadersMiddleware',
    'core.middleware.LanguageMiddleware',  # Django's own messages (form errors, dates) in French when chosen
    'core.middleware.WelcomeMiddleware',  # first visit: welcome page with the language choice
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'core.i18n.language_context',
                'core.views_patient.basket_context',
            ],
            'builtins': ['core.templatetags.mf_i18n'],  # {% t "..." %} and {% since ... %} on every page
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'


# --- Database ----------------------------------------------------------------
# SQLite for development. For production set DATABASE_URL, e.g.
# postgres://user:password@host:5432/medfinder  (requires: pip install psycopg)
DATABASE_URL = os.getenv('DATABASE_URL', '').strip().strip('"\'').strip()
# Forgive a pasted "DATABASE_URL=..." or "psql '...'" in the hosting dashboard.
for _prefix in ('DATABASE_URL=', 'psql '):
    if DATABASE_URL.startswith(_prefix):
        DATABASE_URL = DATABASE_URL[len(_prefix):].strip().strip('"\'').strip()
if DATABASE_URL.startswith(('postgres://', 'postgresql://')):
    from urllib.parse import parse_qs, unquote
    _db = urlparse(DATABASE_URL)
    _query = {k: v[-1] for k, v in parse_qs(_db.query).items()}
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': unquote(_db.path.lstrip('/')),
            'USER': unquote(_db.username or ''),
            'PASSWORD': unquote(_db.password or ''),
            'HOST': _db.hostname or '',
            'PORT': str(_db.port or 5432),
            'CONN_MAX_AGE': 60,
            'CONN_HEALTH_CHECKS': True,
            # Hosted databases (Neon, Supabase, Render) need SSL; ?sslmode=... in the URL overrides this.
            'OPTIONS': {'sslmode': _query.get('sslmode', 'prefer'),
                        **({'channel_binding': _query['channel_binding']} if 'channel_binding' in _query else {})},
        }
    }
    # The old SQLite file stays reachable (read only by `manage.py move_to_postgres`).
    if (BASE_DIR / 'db.sqlite3').exists():
        DATABASES['sqlite_legacy'] = {'ENGINE': 'django.db.backends.sqlite3', 'NAME': BASE_DIR / 'db.sqlite3'}
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
            'OPTIONS': {
                # Take the write lock when a transaction starts, so two requests that read
                # then write queue up instead of failing with "database is locked".
                'transaction_mode': 'IMMEDIATE',
                'timeout': 20,  # seconds to wait for the lock
                # WAL lets pages keep reading while another request writes.
                'init_command': 'PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;',
            },
        }
    }

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'


# --- Authentication ----------------------------------------------------------
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LOGIN_URL = 'login'
LOGIN_REDIRECT_URL = 'dashboard'
LOGOUT_REDIRECT_URL = 'search'


# --- Internationalisation ----------------------------------------------------
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Africa/Kigali'
USE_I18N = True
USE_TZ = True


# --- Static files ------------------------------------------------------------
STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
# Uploads (licences, prescriptions, medicine photos). Render's free disk is wiped on every
# restart, so in production set CLOUDINARY_URL (cloudinary://key:secret@cloud_name) to keep them.
CLOUDINARY_URL = os.getenv('CLOUDINARY_URL', '').strip().strip('"\'').strip()
if CLOUDINARY_URL.startswith('CLOUDINARY_URL='):
    CLOUDINARY_URL = CLOUDINARY_URL[len('CLOUDINARY_URL='):]
if CLOUDINARY_URL:
    os.environ['CLOUDINARY_URL'] = CLOUDINARY_URL  # the Cloudinary library reads it from here
STORAGES = {
    'default': {'BACKEND': 'core.storage.CloudinaryStorage' if CLOUDINARY_URL.startswith('cloudinary://')
                else 'django.core.files.storage.FileSystemStorage'},
    # Production: compressed files with cache-busting names (run collectstatic first).
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage' if DEBUG
                    else 'whitenoise.storage.CompressedManifestStaticFilesStorage'},
}

CSRF_FAILURE_VIEW = 'core.views.csrf_failure'


# --- Cache (used by rate limiting) ---------------------------------------------
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'medfinder',
    }
}


# --- Email ---------------------------------------------------------------------
# Used for pharmacy password resets. Without EMAIL_HOST, emails are printed to the console.
_smtp_host = os.getenv('EMAIL_HOST', '')
# Use SMTP only once the login is complete (a user without a password would fail on every email).
if _smtp_host and (os.getenv('EMAIL_PASSWORD', '') or not os.getenv('EMAIL_USER', '')):
    MAILERS = {
        'default': {
            'BACKEND': 'django.core.mail.backends.smtp.EmailBackend',
            'OPTIONS': {
                'host': _smtp_host,
                'port': int(os.getenv('EMAIL_PORT', '587')),
                'username': os.getenv('EMAIL_USER', ''),
                'password': os.getenv('EMAIL_PASSWORD', ''),
                'use_tls': env_bool('EMAIL_USE_TLS', True),
                'timeout': 15,  # seconds: a slow mail server must not freeze the page
            },
        },
    }
else:
    MAILERS = {'default': {'BACKEND': 'django.core.mail.backends.console.EmailBackend'}}
DEFAULT_FROM_EMAIL = os.getenv('DEFAULT_FROM_EMAIL', 'MedFinder Rwanda <no-reply@medfinder.rw>')
# Public address used in links inside emails.
PUBLIC_HOST = os.getenv('PUBLIC_HOST', '') or os.getenv('RENDER_EXTERNAL_HOSTNAME', '') or 'medfinder-rwanda.onrender.com'
SUPPORT_PHONE = os.getenv('SUPPORT_PHONE', '+250 788 000 000')
SUPPORT_EMAIL = os.getenv('SUPPORT_EMAIL', 'support@medfinder.rw')

# --- Business identity and pricing (shown on the website) ---
BUSINESS_NAME = os.getenv('BUSINESS_NAME', 'MedFinder Rwanda')
BUSINESS_OWNER = os.getenv('BUSINESS_OWNER', '')           # legal owner / company name
BUSINESS_RDB_NUMBER = os.getenv('BUSINESS_RDB_NUMBER', '')  # company code once registered with RDB
FREE_TRIAL_DAYS = int(os.getenv('FREE_TRIAL_DAYS', '30'))
SUBSCRIPTION_PRICE_RWF = int(os.getenv('SUBSCRIPTION_PRICE_RWF', '5000'))  # monthly, per pharmacy
SUBSCRIPTION_MONTH_DAYS = 30            # one paid month extends coverage by this many days
SUBSCRIPTION_GRACE_DAYS = int(os.getenv('SUBSCRIPTION_GRACE_DAYS', '7'))  # still visible after expiry
HIDE_UNPAID_PHARMACIES = env_bool('HIDE_UNPAID_PHARMACIES', True)  # hide from search once grace ends
SUBSCRIPTION_REMINDER_DAYS = (7, 3, 1)  # SMS reminders this many days before coverage ends

BUSINESS_FOUNDER = os.getenv('BUSINESS_FOUNDER', '')
SUPPORT_WHATSAPP = os.getenv('SUPPORT_WHATSAPP', '')   # e.g. +250789377617; empty = use SUPPORT_PHONE
SOCIAL_LINKS = {k: v for k, v in {
    'Facebook': os.getenv('SOCIAL_FACEBOOK', ''),
    'Instagram': os.getenv('SOCIAL_INSTAGRAM', ''),
    'X': os.getenv('SOCIAL_X', ''),
    'LinkedIn': os.getenv('SOCIAL_LINKEDIN', ''),
}.items() if v}

# --- Stock, reservations and reviews -----------------------------------------
EXPIRY_ALERT_DAYS = int(os.getenv('EXPIRY_ALERT_DAYS', '60'))          # warn about stock expiring soon
RESERVATION_REMINDER_MINUTES = int(os.getenv('RESERVATION_REMINDER_MINUTES', '30'))  # SMS before hold ends

# --- Pharmacy login security -------------------------------------------------
# When on, pharmacy owners and staff must enter a 6-digit code sent by SMS after their password.
PHARMACY_LOGIN_2FA = env_bool('PHARMACY_LOGIN_2FA', False)

# --- Uploaded files (pharmacy license documents) ------------------------------
MEDIA_URL = '/media/'
MEDIA_ROOT = Path(os.getenv('MEDIA_ROOT', str(BASE_DIR / 'media')))
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024

# --- Backups --------------------------------------------------------------------
BACKUP_DIR = Path(os.getenv('BACKUP_DIR', str(BASE_DIR / 'backups')))
BACKUP_KEEP = int(os.getenv('BACKUP_KEEP', '14'))  # number of daily backups to keep

# --- Public API ---------------------------------------------------------------
RATE_LIMIT_API = (int(os.getenv('RATE_LIMIT_API', '120')), 60)  # requests per minute per IP


# --- Logging -------------------------------------------------------------------
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {'console': {'class': 'logging.StreamHandler'}},
    'loggers': {'core': {'handlers': ['console'], 'level': 'INFO'}},
}


# --- MedFinder business settings ---------------------------------------------
RESERVATION_HOLD_HOURS = int(os.getenv('RESERVATION_HOLD_HOURS', '2'))
LOW_STOCK_THRESHOLD = int(os.getenv('LOW_STOCK_THRESHOLD', '5'))
PLATFORM_COMMISSION_RATE = float(os.getenv('PLATFORM_COMMISSION_RATE', '0.03'))  # 3%

# Rate limits: (max requests, window in seconds) per client IP
RATE_LIMIT_LOGIN = (int(os.getenv('RATE_LIMIT_LOGIN', '5')), 300)

# Admin panel address. Keep it secret: set DJANGO_ADMIN_URL on the host (e.g. 'mf-control-x7Kq2/').
# The public /admin/ address then simply does not exist. Without the setting it stays at admin/.
ADMIN_URL = os.getenv('DJANGO_ADMIN_URL', '').strip().strip('/') or 'admin'
ADMIN_URL = ADMIN_URL + '/'
# Wrong passwords allowed on the admin login before a pause (per IP).
RATE_LIMIT_ADMIN_LOGIN = (int(os.getenv('RATE_LIMIT_ADMIN_LOGIN', '5')), 900)
RATE_LIMIT_RESERVE = (int(os.getenv('RATE_LIMIT_RESERVE', '10')), 600)
RATE_LIMIT_REGISTER = (int(os.getenv('RATE_LIMIT_REGISTER', '10')), 3600)


# --- Africa's Talking (SMS + USSD) -------------------------------------------
AT_USERNAME = os.getenv('AT_USERNAME', 'sandbox')
AT_API_KEY = os.getenv('AT_API_KEY', '')
AT_SENDER_ID = os.getenv('AT_SENDER_ID', '')  # optional alphanumeric sender
# Optional shared secret appended to callback URLs: /ussd/?token=...  /sms/incoming/?token=...
AT_CALLBACK_TOKEN = os.getenv('AT_CALLBACK_TOKEN', '')
USSD_CODE = os.getenv('USSD_CODE', '*384*123#')  # the service code assigned by Africa's Talking


# --- MTN Mobile Money (Collections API) --------------------------------------
# Leave MOMO_SUBSCRIPTION_KEY empty to run payments in simulation mode.
MOMO_BASE_URL = os.getenv('MOMO_BASE_URL', 'https://sandbox.momodeveloper.mtn.com')
MOMO_SUBSCRIPTION_KEY = os.getenv('MOMO_SUBSCRIPTION_KEY', '')
MOMO_API_USER = os.getenv('MOMO_API_USER', '')
MOMO_API_KEY = os.getenv('MOMO_API_KEY', '')
MOMO_TARGET_ENVIRONMENT = os.getenv('MOMO_TARGET_ENVIRONMENT', 'sandbox')
MOMO_CURRENCY = os.getenv('MOMO_CURRENCY', 'EUR')  # sandbox only accepts EUR; use RWF in production
MOMO_CALLBACK_URL = os.getenv('MOMO_CALLBACK_URL', '')


# --- Airtel Money (Airtel Africa Open API, collections) -----------------------
# Leave AIRTEL_CLIENT_ID empty to run Airtel payments in simulation mode.
AIRTEL_BASE_URL = os.getenv('AIRTEL_BASE_URL', 'https://openapiuat.airtel.africa')  # production: https://openapi.airtel.africa
AIRTEL_CLIENT_ID = os.getenv('AIRTEL_CLIENT_ID', '')
AIRTEL_CLIENT_SECRET = os.getenv('AIRTEL_CLIENT_SECRET', '')
AIRTEL_COUNTRY = os.getenv('AIRTEL_COUNTRY', 'RW')
AIRTEL_CURRENCY = os.getenv('AIRTEL_CURRENCY', 'RWF')


# --- Error tracking (optional): pip install sentry-sdk and set SENTRY_DSN -------
SENTRY_DSN = os.getenv('SENTRY_DSN', '')
if SENTRY_DSN:
    try:
        import sentry_sdk
        sentry_sdk.init(dsn=SENTRY_DSN, send_default_pii=False, traces_sample_rate=0.0,
                        environment='development' if DEBUG else 'production')
    except ImportError:
        pass
