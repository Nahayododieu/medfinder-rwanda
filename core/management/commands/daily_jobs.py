from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Run every daily task: backup, subscription reminders, expiry alerts, reservation expiry.'

    def handle(self, *args, **options):
        for name in ('backup_db', 'subscription_reminders', 'pharmacy_alerts', 'expire_reservations'):
            self.stdout.write(f'> {name}')
            call_command(name, stdout=self.stdout)
