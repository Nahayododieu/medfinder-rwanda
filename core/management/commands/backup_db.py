"""Daily database backup: a compressed JSON dump that works for SQLite and PostgreSQL.

    python manage.py backup_db            # writes backups/medfinder-YYYYmmdd-HHMM.json.gz
    python manage.py loaddata <file>      # restore into an empty, migrated database

Keeps the newest BACKUP_KEEP files. Also copy the backups folder off the server
(e.g. to cloud storage) so a lost server does not lose the backups too.
"""
import gzip
import io

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    help = 'Write a compressed database backup and delete old ones.'

    def handle(self, *args, **options):
        folder = settings.BACKUP_DIR
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f'medfinder-{timezone.localtime():%Y%m%d-%H%M}.json.gz'
        buffer = io.StringIO()
        call_command('dumpdata', '--natural-foreign', '--natural-primary',
                     '--exclude=contenttypes', '--exclude=auth.permission', '--exclude=sessions',
                     '--exclude=admin.logentry', stdout=buffer)
        with gzip.open(path, 'wt', encoding='utf-8') as f:
            f.write(buffer.getvalue())
        backups = sorted(folder.glob('medfinder-*.json.gz'))
        for old in backups[:-settings.BACKUP_KEEP]:
            old.unlink()
        self.stdout.write(self.style.SUCCESS(f'Backup written: {path} ({path.stat().st_size // 1024} KB). '
                                             f'Keeping {min(len(backups), settings.BACKUP_KEEP)}.'))
