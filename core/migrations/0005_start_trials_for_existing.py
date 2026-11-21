from datetime import timedelta

from django.conf import settings
from django.db import migrations
from django.utils import timezone


def start_trials(apps, schema_editor):
    """Pharmacies verified before subscriptions existed get a full free trial from today."""
    Pharmacy = apps.get_model('core', 'Pharmacy')
    trial_end = timezone.localdate() + timedelta(days=settings.FREE_TRIAL_DAYS)
    Pharmacy.objects.filter(verified=True, trial_ends_at__isnull=True).update(trial_ends_at=trial_end)


class Migration(migrations.Migration):
    dependencies = [('core', '0004_subscriptions')]
    operations = [migrations.RunPython(start_trials, migrations.RunPython.noop)]
