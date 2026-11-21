from django.core.management.base import BaseCommand

from core.billing import send_reminders


class Command(BaseCommand):
    help = 'SMS pharmacies whose trial or subscription is about to end or has ended (run once a day).'

    def handle(self, *args, **options):
        n = send_reminders()
        self.stdout.write(self.style.SUCCESS(f'Sent {n} subscription reminder(s).'))
