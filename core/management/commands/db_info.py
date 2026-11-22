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
        backend = settings.STORAGES['default']['BACKEND']
        if backend.endswith('CloudinaryStorage'):
            cloud = os.environ.get('CLOUDINARY_URL', '').rsplit('@', 1)[-1]
            self.stdout.write(f'STORAGE CHECK: uploads are kept on Cloudinary (cloud "{cloud}")')
        else:
            raw = os.getenv('CLOUDINARY_URL', '')
            why = 'CLOUDINARY_URL is not set' if not raw else \
                f'CLOUDINARY_URL does not start with cloudinary:// (it starts with {raw[:3]!r})'
            self.stdout.write(f'STORAGE CHECK: uploads are on the local disk ({why}). '
                              'On Render they are lost on every restart!')
