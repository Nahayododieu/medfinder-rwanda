"""Copy all data from the old db.sqlite3 file into the PostgreSQL database in DATABASE_URL.

    1. Put DATABASE_URL=postgresql://... in .env
    2. python manage.py move_to_postgres

The SQLite file is only read, never changed. The target database must be empty
(a fresh database): the command refuses to copy into one that already has data.
"""
import os
import tempfile

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connections

from core.models import Medicine, Pharmacy


class Command(BaseCommand):
    help = 'Copy all data from db.sqlite3 into the PostgreSQL database set in DATABASE_URL.'

    def handle(self, *args, **opts):
        if connections['default'].vendor != 'postgresql':
            raise CommandError('DATABASE_URL is not set to a PostgreSQL database in .env.')
        if 'sqlite_legacy' not in settings.DATABASES:
            raise CommandError('db.sqlite3 was not found next to manage.py.')

        self.stdout.write('1/3 Creating tables in PostgreSQL...')
        call_command('migrate', database='default', interactive=False, verbosity=0)
        if Pharmacy.objects.using('default').exists() or Medicine.objects.using('default').exists():
            raise CommandError('The PostgreSQL database already has data. Use a fresh, empty database.')

        self.stdout.write('2/3 Reading data from db.sqlite3...')
        fd, path = tempfile.mkstemp(suffix='.json', prefix='medfinder-move-')
        os.close(fd)
        try:
            call_command('dumpdata', database='sqlite_legacy', natural_foreign=True, natural_primary=True,
                         exclude=['contenttypes', 'auth.permission', 'sessions'], output=path, verbosity=0)
            self.stdout.write('3/3 Writing data into PostgreSQL...')
            call_command('loaddata', path, database='default', verbosity=0)
        finally:
            os.remove(path)

        old = {m: m.objects.using('sqlite_legacy').count() for m in (Pharmacy, Medicine)}
        new = {m: m.objects.using('default').count() for m in (Pharmacy, Medicine)}
        for m in old:
            self.stdout.write(f'  {m.__name__}: {old[m]} in SQLite, {new[m]} in PostgreSQL')
        if old != new:
            raise CommandError('Counts do not match; check the output above.')
        self.stdout.write(self.style.SUCCESS('Done. The website now uses PostgreSQL. Keep db.sqlite3 as a backup.'))
