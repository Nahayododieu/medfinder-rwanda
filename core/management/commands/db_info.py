"""Print which database the site is using (never the password). Runs at start-up on Render."""
import os

from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Show which database is in use, without secrets.'

    def handle(self, *args, **options):
        db = settings.DATABASES['default']
        engine = db['ENGINE'].rsplit('.', 1)[-1]
        if engine == 'sqlite3':
            raw = os.getenv('DATABASE_URL', '')
            if not raw:
                why = 'DATABASE_URL is not set'
            else:
                why = (f'DATABASE_URL is set ({len(raw)} characters) but does not start with '
                       f'postgresql:// (it starts with {raw[:3]!r})')
            self.stdout.write(f'DATABASE CHECK: using temporary SQLite. {why}. Data will be lost on restart!')
        else:
            self.stdout.write(f"DATABASE CHECK: using {engine} at {db['HOST']} / {db['NAME']}")
        if settings.ADMIN_URL == 'admin/':
            self.stdout.write('ADMIN CHECK: the admin panel is at the public /admin/ address. '
                              'Set DJANGO_ADMIN_URL to a secret path to hide it.')
        else:
            self.stdout.write('ADMIN CHECK: the admin panel is at a secret address (DJANGO_ADMIN_URL); /admin/ is closed.')
        backend = settings.STORAGES['default']['BACKEND']
        if backend.endswith('CloudinaryStorage'):
            cloud = os.environ.get('CLOUDINARY_URL', '').rsplit('@', 1)[-1]
            self.stdout.write(f'STORAGE CHECK: uploads are kept on Cloudinary (cloud "{cloud}")')
            secret = os.environ.get('CLOUDINARY_URL', '').split('://', 1)[-1].rsplit('@', 1)[0].partition(':')[2]
            if not secret or set(secret) <= {'*'}:
                self.stdout.write('STORAGE CHECK: the API secret in CLOUDINARY_URL is missing or hidden (*****). '
                                  'Copy it again from Cloudinary with the secret revealed.')
            try:
                import cloudinary.api
                cloudinary.api.ping()
                self.stdout.write('STORAGE CHECK: Cloudinary login OK')
            except Exception as exc:  # the message never contains the secret
                self.stdout.write(f'STORAGE CHECK: Cloudinary login FAILED: {type(exc).__name__}: {str(exc)[:200]}')
        else:
            raw = os.getenv('CLOUDINARY_URL', '')
            why = 'CLOUDINARY_URL is not set' if not raw else \
                f'CLOUDINARY_URL does not start with cloudinary:// (it starts with {raw[:3]!r})'
            self.stdout.write(f'STORAGE CHECK: uploads are on the local disk ({why}). '
                              'On Render they are lost on every restart!')
