from django.core.management.base import BaseCommand

from core.services import expire_old_reservations


class Command(BaseCommand):
    help = 'Cancel unpaid reservations older than the hold time and restore stock (run every 5-10 minutes).'

    def handle(self, *args, **options):
        n = expire_old_reservations()
        self.stdout.write(self.style.SUCCESS(f'Expired {n} reservation(s).'))
