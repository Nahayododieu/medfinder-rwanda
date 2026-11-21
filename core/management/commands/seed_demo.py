"""Load demo data: medicines, pharmacies, stock and 4 weeks of search history.

    python manage.py seed_demo                # demo pharmacies + data
    python manage.py seed_demo --admin        # also create admin / MedFinderAdmin2026!

Safe to run more than once (existing records are reused).
"""
import random
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from core.models import Inventory, Medicine, Pharmacy, Reservation, SearchLog

PASSWORD = 'MedFinder2026!'

MEDICINES = [
    # name, generic, category, prescription, typical price RWF
    ('Paracetamol 500mg', 'Paracetamol', 'Pain relief', False, 100),
    ('Panadol Extra', 'Paracetamol + Caffeine', 'Pain relief', False, 300),
    ('Ibuprofen 400mg', 'Ibuprofen', 'Pain relief', False, 150),
    ('Diclofenac 50mg', 'Diclofenac', 'Pain relief', False, 200),
    ('Amoxicillin 500mg', 'Amoxicillin', 'Antibiotic', True, 250),
    ('Augmentin 625mg', 'Amoxicillin + Clavulanic acid', 'Antibiotic', True, 1200),
    ('Ciprofloxacin 500mg', 'Ciprofloxacin', 'Antibiotic', True, 300),
    ('Metronidazole 250mg', 'Metronidazole', 'Antibiotic', True, 100),
    ('Coartem 20/120', 'Artemether + Lumefantrine', 'Antimalarial', True, 1500),
    ('ORS sachet', 'Oral rehydration salts', 'Rehydration', False, 200),
    ('Zinc 20mg', 'Zinc sulfate', 'Supplement', False, 150),
    ('Cetirizine 10mg', 'Cetirizine', 'Allergy', False, 100),
    ('Loratadine 10mg', 'Loratadine', 'Allergy', False, 150),
    ('Omeprazole 20mg', 'Omeprazole', 'Digestive', False, 200),
    ('Metformin 500mg', 'Metformin', 'Diabetes', True, 150),
    ('Amlodipine 5mg', 'Amlodipine', 'Blood pressure', True, 200),
    ('Salbutamol inhaler', 'Salbutamol', 'Respiratory', True, 3500),
    ('Vitamin C 500mg', 'Ascorbic acid', 'Supplement', False, 100),
    ('Folic acid 5mg', 'Folic acid', 'Supplement', False, 50),
    ('Ferrous sulfate 200mg', 'Iron', 'Supplement', False, 50),
    ('Albendazole 400mg', 'Albendazole', 'Antiparasitic', False, 300),
    ('Fluconazole 150mg', 'Fluconazole', 'Antifungal', True, 500),
    ('Hydrocortisone cream 1%', 'Hydrocortisone', 'Skin', False, 1500),
    ('Insulin (Actrapid)', 'Insulin human', 'Diabetes', True, 6000),
]

PHARMACIES = [
    # username, name, license, district, address, phone, lat, lng, verified
    ('pharma_nyarugenge', 'Pharmacie La Santé', 'RFDA-PH-1001', 'Nyarugenge', 'KN 4 Ave, Kigali CBD', '0788100101', -1.9441, 30.0619, True),
    ('pharma_kimironko', 'Kimironko Pharmacy', 'RFDA-PH-1002', 'Gasabo', 'Near Kimironko Market', '0788100102', -1.9494, 30.1260, True),
    ('pharma_remera', 'Remera Care Pharmacy', 'RFDA-PH-1003', 'Gasabo', 'KG 11 Ave, Remera', '0788100103', -1.9578, 30.1127, True),
    ('pharma_kicukiro', 'Kicukiro Wellness Pharmacy', 'RFDA-PH-1004', 'Kicukiro', 'Kicukiro Centre', '0788100104', -1.9706, 30.1044, True),
    ('pharma_huye', 'Huye Town Pharmacy', 'RFDA-PH-1005', 'Huye', 'Huye town centre', '0788100105', -2.5967, 29.7394, True),
    ('pharma_musanze', 'Musanze Family Pharmacy', 'RFDA-PH-1006', 'Musanze', 'Musanze town centre', '0788100106', -1.4998, 29.6347, True),
    ('pharma_rubavu', 'Gisenyi Lakeside Pharmacy', 'RFDA-PH-1007', 'Rubavu', 'Gisenyi, near the lake', '0788100107', -1.7024, 29.2564, True),
    ('pharma_bugesera', 'Nyamata New Pharmacy', 'RFDA-PH-1008', 'Bugesera', 'Nyamata town', '0788100108', -2.1480, 30.0940, False),
]

# Medicines that are deliberately scarce, so analytics shows shortage signals.
SCARCE = {'Insulin (Actrapid)', 'Salbutamol inhaler', 'Coartem 20/120'}


class Command(BaseCommand):
    help = 'Load demo medicines, pharmacies, stock and search history.'

    def add_arguments(self, parser):
        parser.add_argument('--admin', action='store_true', help='Create an admin superuser (admin).')
        parser.add_argument('--no-history', action='store_true', help='Skip generated search history.')

    @transaction.atomic
    def handle(self, *args, **opts):
        rng = random.Random(2026)

        meds = {}
        for name, generic, category, rx, price in MEDICINES:
            m, _ = Medicine.objects.get_or_create(name=name, defaults={
                'generic_name': generic, 'category': category, 'requires_prescription': rx})
            meds[name] = (m, price)

        pharmacies = []
        for username, name, lic, district, address, phone, lat, lng, verified in PHARMACIES:
            user, created = User.objects.get_or_create(username=username)
            if created:
                user.set_password(PASSWORD)
                user.save()
            p, _ = Pharmacy.objects.get_or_create(license_no=lic, defaults={
                'owner': user, 'name': name, 'district': district, 'address': address, 'phone': phone,
                'latitude': lat, 'longitude': lng, 'verified': verified})
            if not (p.open_24h or p.opens_at):
                from datetime import time
                if username == 'pharma_nyarugenge':
                    p.open_24h = True
                else:
                    p.opens_at, p.closes_at = time(7, 30), time(21, 0) if district != 'Huye' else time(19, 0)
                p.save(update_fields=['open_24h', 'opens_at', 'closes_at'])
            if not p.insurers:
                p.insurers = rng.choice(['mutuelle,rssb', 'mutuelle,rssb,mmi', 'rssb,private', 'mutuelle'])
                p.save(update_fields=['insurers'])
            if not user.email:
                user.email = f'{username}@example.rw'
                user.save(update_fields=['email'])
            pharmacies.append(p)

        for p in pharmacies:
            for name, (m, base) in meds.items():
                if name in SCARCE and rng.random() < 0.75:
                    continue
                if rng.random() < 0.25:
                    continue
                qty = rng.choice([0, 2, 4]) if name in SCARCE else rng.randint(3, 120)
                price = Decimal(round(base * rng.uniform(0.85, 1.3) / 10) * 10)
                Inventory.objects.get_or_create(pharmacy=p, medicine=m, defaults={'quantity': qty, 'price': price})

        if opts['admin'] and not User.objects.filter(username='admin').exists():
            User.objects.create_superuser('admin', '', 'MedFinderAdmin2026!')
            self.stdout.write('Created superuser admin / MedFinderAdmin2026!')

        if not opts['no_history'] and not SearchLog.objects.exists():
            self._history(rng, meds, pharmacies)

        self.stdout.write(self.style.SUCCESS(
            f'Demo data ready: {len(meds)} medicines, {len(pharmacies)} pharmacies. '
            f'Pharmacy logins: pharma_kimironko (etc.) / {PASSWORD}'))

    def _history(self, rng, meds, pharmacies):
        now = timezone.now()
        districts = [p.district for p in pharmacies] + ['Gasabo'] * 4 + ['Nyarugenge'] * 2 + ['Kamonyi', 'Rwamagana']
        popular = ['Paracetamol 500mg', 'Amoxicillin 500mg', 'Coartem 20/120', 'ORS sachet', 'Ibuprofen 400mg',
                   'Cetirizine 10mg', 'Metformin 500mg', 'Insulin (Actrapid)', 'Salbutamol inhaler', 'Omeprazole 20mg']
        weights = [14, 9, 8, 7, 6, 5, 4, 4, 3, 3]
        logs = []
        for day in range(28, 0, -1):
            growth = 1 + (28 - day) / 28  # demand grows during the pilot
            for _ in range(int(rng.randint(12, 20) * growth)):
                name = rng.choices(popular, weights)[0]
                m = meds[name][0]
                district = rng.choice(districts)
                has_stock = Inventory.objects.filter(medicine=m, quantity__gt=0, pharmacy__verified=True,
                                                     pharmacy__district=district).exists()
                found = has_stock or (name not in SCARCE and rng.random() < 0.6)
                when = now - timedelta(days=day, hours=rng.randint(0, 12), minutes=rng.randint(0, 59))
                logs.append(SearchLog(query=name.split()[0], medicine=m, district=district, found=found,
                                      result_count=rng.randint(1, 4) if found else 0,
                                      channel='ussd' if rng.random() < 0.2 else 'web', created_at=when))
        # A few searches for medicines that nobody stocks.
        for _ in range(15):
            logs.append(SearchLog(query=rng.choice(['Ventolin', 'Clexane', 'Misoprostol']), district=rng.choice(districts),
                                  found=False, created_at=now - timedelta(days=rng.randint(1, 27))))
        SearchLog.objects.bulk_create(logs)

        # Past reservations (completed or cancelled), for dashboards.
        stock = list(Inventory.objects.filter(pharmacy__verified=True, quantity__gt=5))
        for _ in range(60):
            inv = rng.choice(stock)
            r = Reservation.objects.create(
                patient_phone='+25078' + str(rng.randint(1000000, 9999999)),
                inventory=inv, quantity=rng.randint(1, 3),
                pickup_code=str(rng.randint(100000, 999999)),
                status=rng.choices(['done', 'cancelled'], [4, 1])[0],
                source='ussd' if rng.random() < 0.2 else 'web',
            )
            created = now - timedelta(days=rng.randint(0, 27), hours=rng.randint(1, 10))
            Reservation.objects.filter(pk=r.pk).update(
                created_at=created, completed_at=created + timedelta(minutes=rng.randint(15, 100)),
                cancel_reason='expired' if r.status == 'cancelled' else '')
