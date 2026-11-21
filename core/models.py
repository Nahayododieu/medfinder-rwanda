from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone
from django.utils.crypto import get_random_string

from .utils import DISTRICT_CHOICES

# Insurance schemes a pharmacy can accept (code, label).
INSURER_CHOICES = [
    ('mutuelle', 'Mutuelle de Santé (CBHI)'),
    ('rssb', 'RSSB medical scheme'),
    ('mmi', 'MMI'),
    ('private', 'Private insurers'),
]


PRESCRIPTION_STATUS = [
    ('', 'Not needed'),
    ('missing', 'Patient will bring it'),
    ('uploaded', 'Uploaded, to check'),
    ('approved', 'Approved'),
    ('rejected', 'Rejected'),
]


def prescription_upload_path(instance, filename):
    ext = (filename.rsplit('.', 1)[-1] if '.' in filename else 'jpg').lower()[:5]
    return f'prescriptions/{timezone.now():%Y/%m}/rx-{get_random_string(16)}.{ext}'


def license_upload_path(instance, filename):
    ext = (filename.rsplit('.', 1)[-1] if '.' in filename else 'pdf').lower()[:5]
    return f'licenses/{timezone.now():%Y/%m}/{instance.license_no or "pharmacy"}-{timezone.now():%H%M%S}.{ext}'


class Pharmacy(models.Model):
    owner = models.OneToOneField(User, on_delete=models.CASCADE)
    name = models.CharField(max_length=150)
    license_no = models.CharField(max_length=50, unique=True)
    license_document = models.FileField(upload_to=license_upload_path, blank=True,
                                        help_text='Scan or photo of the Rwanda FDA license (PDF, JPG or PNG).')
    district = models.CharField(max_length=60, choices=DISTRICT_CHOICES)
    address = models.CharField(max_length=200, blank=True)
    phone = models.CharField(max_length=20)
    latitude = models.FloatField()
    longitude = models.FloatField()
    insurers = models.CharField(max_length=100, blank=True,
                                help_text='Comma-separated insurer codes: ' + ', '.join(c for c, _ in INSURER_CHOICES))
    verified = models.BooleanField(default=False, db_index=True)
    open_24h = models.BooleanField('Open 24 hours', default=False)
    opens_at = models.TimeField(null=True, blank=True, help_text='e.g. 08:00')
    closes_at = models.TimeField(null=True, blank=True, help_text='e.g. 21:00')
    offers_delivery = models.BooleanField('Offers home delivery', default=False)
    delivery_fee = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal('0'),
                                       help_text='Delivery fee in RWF added to each delivered order.')
    delivery_area = models.CharField(max_length=150, blank=True,
                                     help_text='Where you deliver, e.g. "Kicukiro and Remera, same day".')
    trial_ends_at = models.DateField(null=True, blank=True,
                                     help_text='Set automatically when the pharmacy is first verified.')
    paid_until = models.DateField(null=True, blank=True, help_text='Last day covered by a paid subscription.')
    last_reminder = models.CharField(max_length=40, blank=True, editable=False)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name_plural = 'Pharmacies'
        ordering = ['name']

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        # The free trial starts the first time the pharmacy becomes visible to patients.
        if self.verified and not self.trial_ends_at:
            self.trial_ends_at = timezone.localdate() + timedelta(days=settings.FREE_TRIAL_DAYS)
            if kwargs.get('update_fields') is not None:
                kwargs['update_fields'] = list(set(kwargs['update_fields']) | {'trial_ends_at'})
        super().save(*args, **kwargs)

    # --- Subscription -------------------------------------------------------
    @property
    def coverage_end(self):
        """Last day the pharmacy is covered by its trial or a paid subscription."""
        dates = [d for d in (self.trial_ends_at, self.paid_until) if d]
        return max(dates) if dates else None

    @property
    def subscription_status(self):
        """unverified | trial | active | grace | overdue"""
        if not self.verified:
            return 'unverified'
        end = self.coverage_end
        today = timezone.localdate()
        if end is None or end >= today:
            return 'active' if self.paid_until and self.paid_until >= today else 'trial'
        if today <= end + timedelta(days=settings.SUBSCRIPTION_GRACE_DAYS):
            return 'grace'
        return 'overdue'

    @property
    def days_left(self):
        end = self.coverage_end
        return (end - timezone.localdate()).days if end else None

    @property
    def hidden_for_nonpayment(self):
        return settings.HIDE_UNPAID_PHARMACIES and self.subscription_status == 'overdue'

    # --- Insurance ------------------------------------------------------------
    @property
    def insurer_codes(self):
        return [c for c in (self.insurers or '').split(',') if c]

    @property
    def insurer_labels(self):
        labels = dict(INSURER_CHOICES)
        return [labels[c] for c in self.insurer_codes if c in labels]

    # --- Opening hours --------------------------------------------------------
    @property
    def is_open_now(self):
        """True/False when hours are known, None when the pharmacy has not set them."""
        if self.open_24h:
            return True
        if not (self.opens_at and self.closes_at):
            return None
        now = timezone.localtime().time()
        if self.opens_at <= self.closes_at:
            return self.opens_at <= now < self.closes_at
        return now >= self.opens_at or now < self.closes_at  # e.g. 20:00-02:00

    @property
    def hours_display(self):
        if self.open_24h:
            return 'Open 24 hours'
        if self.opens_at and self.closes_at:
            return f'{self.opens_at:%H:%M}–{self.closes_at:%H:%M}'
        return ''


class PharmacyStaff(models.Model):
    """Extra login for a pharmacist working at a pharmacy (the owner is Pharmacy.owner)."""
    ROLES = [('pharmacist', 'Pharmacist')]

    pharmacy = models.ForeignKey(Pharmacy, on_delete=models.CASCADE, related_name='staff')
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='pharmacy_staff')
    role = models.CharField(max_length=20, choices=ROLES, default='pharmacist')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = 'Pharmacy staff'

    def __str__(self):
        return f'{self.user.get_username()} ({self.get_role_display()}, {self.pharmacy})'


class Medicine(models.Model):
    name = models.CharField(max_length=150)
    generic_name = models.CharField(max_length=150, blank=True)
    category = models.CharField(max_length=80, blank=True)
    requires_prescription = models.BooleanField(default=False)
    # Official reference price (e.g. RSSB reimbursement tariff). Only fill in from a published
    # source; it is shown to patients with its source and date, never as a pharmacy's price.
    reference_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True,
                                          help_text='Official price in RWF from a published source. Leave empty if unknown.')
    reference_unit = models.CharField(max_length=40, blank=True, help_text='e.g. per tablet, per bottle, per inhaler')
    reference_source = models.CharField(max_length=150, blank=True,
                                        help_text='e.g. RSSB medicines tariff list (required if a price is entered)')
    reference_date = models.DateField(null=True, blank=True, help_text='Date of the published price list')

    class Meta:
        ordering = ['name']

    def clean(self):
        from django.core.exceptions import ValidationError
        if self.reference_price is not None and not (self.reference_source and self.reference_date):
            raise ValidationError('A reference price needs its source and the date of the price list.')

    def __str__(self):
        if self.generic_name and self.generic_name.lower() != self.name.lower():
            return f'{self.name} ({self.generic_name})'
        return self.name


class Inventory(models.Model):
    pharmacy = models.ForeignKey(Pharmacy, on_delete=models.CASCADE)
    medicine = models.ForeignKey(Medicine, on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField(default=0, db_index=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    expiry_date = models.DateField(null=True, blank=True, help_text='Earliest expiry date of this stock.')
    expiry_alert_sent = models.BooleanField(default=False, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('pharmacy', 'medicine')
        verbose_name_plural = 'Inventory'

    def __str__(self):
        return f'{self.medicine.name} @ {self.pharmacy.name}'

    @property
    def is_low(self):
        return self.quantity <= settings.LOW_STOCK_THRESHOLD

    @property
    def expiry_state(self):
        """'expired', 'soon' (within EXPIRY_ALERT_DAYS) or '' ."""
        if not self.expiry_date:
            return ''
        days = (self.expiry_date - timezone.localdate()).days
        if days < 0:
            return 'expired'
        return 'soon' if days <= settings.EXPIRY_ALERT_DAYS else ''


class StockChange(models.Model):
    """Audit log: who changed a pharmacy's stock or price, when and from where."""
    SOURCES = [('web', 'Dashboard'), ('csv', 'CSV import'), ('sms', 'SMS'), ('ussd', 'USSD'),
               ('admin', 'Admin'), ('delete', 'Removed')]

    pharmacy = models.ForeignKey(Pharmacy, on_delete=models.CASCADE, related_name='stock_changes')
    medicine = models.ForeignKey(Medicine, on_delete=models.CASCADE)
    user = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)
    source = models.CharField(max_length=10, choices=SOURCES)
    old_quantity = models.IntegerField(null=True, blank=True)
    new_quantity = models.IntegerField(null=True, blank=True)
    old_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    new_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.medicine} {self.old_quantity}→{self.new_quantity} ({self.get_source_display()})'


class Order(models.Model):
    """Several medicines reserved together at one pharmacy, with one pickup code and one payment."""
    patient_phone = models.CharField(max_length=20)
    pharmacy = models.ForeignKey(Pharmacy, on_delete=models.CASCADE, related_name='orders')
    pickup_code = models.CharField(max_length=6)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'Order #{self.pk} at {self.pharmacy}'

    @property
    def lines(self):
        return list(self.items.select_related('inventory__medicine').prefetch_related('payments'))

    @property
    def open_lines(self):
        return [r for r in self.lines if r.is_open]

    @property
    def total(self):
        return sum((r.total for r in self.lines if r.status != 'cancelled'), Decimal('0'))

    @property
    def unpaid_total(self):
        return sum((r.total for r in self.open_lines if not r.is_paid), Decimal('0'))

    @property
    def delivery(self):
        return next((r for r in self.lines if r.delivery), None)

    @property
    def status(self):
        states = {r.status for r in self.lines}
        if states <= {'cancelled'}:
            return 'cancelled'
        if states <= {'done', 'cancelled'}:
            return 'done'
        if 'pending' in states:
            return 'pending'
        return 'ready'


class Reservation(models.Model):
    STATUS = [
        ('pending', 'Pending'),
        ('ready', 'Ready'),
        ('done', 'Picked up'),
        ('cancelled', 'Cancelled'),
    ]
    SOURCE = [('web', 'Website'), ('ussd', 'USSD')]

    patient_phone = models.CharField(max_length=20)
    inventory = models.ForeignKey(Inventory, on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField(default=1)
    pickup_code = models.CharField(max_length=6)
    status = models.CharField(max_length=10, choices=STATUS, default='pending')
    source = models.CharField(max_length=10, choices=SOURCE, default='web')
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    cancel_reason = models.CharField(max_length=60, blank=True)
    reminder_sent = models.BooleanField(default=False, editable=False)
    order = models.ForeignKey(Order, null=True, blank=True, on_delete=models.SET_NULL, related_name='items')
    prescription = models.FileField(upload_to=prescription_upload_path, blank=True)
    prescription_status = models.CharField(max_length=10, choices=PRESCRIPTION_STATUS, blank=True, default='')
    delivery = models.BooleanField(default=False)
    delivery_address = models.CharField(max_length=255, blank=True)
    delivery_fee = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal('0'))

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['status', 'created_at'])]

    def __str__(self):
        return f'#{self.pk} {self.inventory.medicine.name} x{self.quantity}'

    @property
    def subtotal(self):
        return self.inventory.price * self.quantity

    @property
    def total(self):
        return self.subtotal + (self.delivery_fee or Decimal('0'))

    @property
    def needs_prescription(self):
        return self.inventory.medicine.requires_prescription

    @property
    def expires_at(self):
        return self.created_at + timedelta(hours=settings.RESERVATION_HOLD_HOURS)

    @property
    def is_open(self):
        return self.status in ('pending', 'ready')

    @property
    def is_paid(self):
        return self.payments.filter(status='successful').exists()


class Payment(models.Model):
    STATUS = [
        ('pending', 'Pending'),
        ('successful', 'Successful'),
        ('failed', 'Failed'),
        ('refund_due', 'Refund due'),
    ]
    PROVIDERS = [('mtn', 'MTN Mobile Money'), ('airtel', 'Airtel Money')]

    reservation = models.ForeignKey(Reservation, on_delete=models.CASCADE, related_name='payments')
    payer_phone = models.CharField(max_length=20)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    commission = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0'))
    currency = models.CharField(max_length=5, default='RWF')
    provider = models.CharField(max_length=10, choices=PROVIDERS, default='mtn')
    # One mobile money request can pay several reservations of an order, so the reference is shared.
    provider_reference = models.CharField(max_length=64, db_index=True)
    status = models.CharField(max_length=12, choices=STATUS, default='pending')
    simulated = models.BooleanField(default=False)
    failure_reason = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.get_provider_display()} {self.amount} ({self.status})'

    @property
    def pharmacy_share(self):
        return self.amount - self.commission


class SearchLog(models.Model):
    CHANNELS = [('web', 'Website'), ('ussd', 'USSD')]

    query = models.CharField(max_length=150)
    medicine = models.ForeignKey(Medicine, null=True, blank=True, on_delete=models.SET_NULL)
    district = models.CharField(max_length=60, blank=True)
    found = models.BooleanField()
    result_count = models.PositiveIntegerField(default=0)
    channel = models.CharField(max_length=10, choices=CHANNELS, default='web')
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.query} ({"found" if self.found else "not found"})'


class Review(models.Model):
    """A patient's rating after picking up a reservation. Shown only once approved."""
    STATUS = [('pending', 'Waiting for moderation'), ('approved', 'Approved'), ('rejected', 'Rejected')]

    reservation = models.OneToOneField(Reservation, on_delete=models.CASCADE, related_name='review')
    pharmacy = models.ForeignKey(Pharmacy, on_delete=models.CASCADE, related_name='reviews')
    rating = models.PositiveSmallIntegerField(choices=[(i, str(i)) for i in range(1, 6)])
    comment = models.CharField(max_length=500, blank=True)
    status = models.CharField(max_length=10, choices=STATUS, default='pending', db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.rating}★ {self.pharmacy} ({self.status})'


class Testimonial(models.Model):
    """Quotes from partner pharmacies, shown on the home page when published (with their permission)."""
    name = models.CharField(max_length=100)
    role = models.CharField(max_length=100, help_text='e.g. Pharmacist, Kimironko Pharmacy')
    quote = models.CharField(max_length=400)
    published = models.BooleanField(default=False, help_text='Only publish with written permission.')
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ['order', 'id']

    def __str__(self):
        return f'{self.name} ({self.role})'


class SubscriptionPayment(models.Model):
    """A pharmacy's monthly listing fee, paid by MoMo or recorded by the administrator."""
    STATUS = [('pending', 'Pending'), ('successful', 'Successful'), ('failed', 'Failed')]
    METHODS = [('momo', 'MTN Mobile Money'), ('airtel', 'Airtel Money'), ('cash', 'Cash / bank (recorded by admin)')]

    pharmacy = models.ForeignKey(Pharmacy, on_delete=models.CASCADE, related_name='subscription_payments')
    months = models.PositiveSmallIntegerField(default=1)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    method = models.CharField(max_length=10, choices=METHODS, default='momo')
    payer_phone = models.CharField(max_length=20, blank=True)
    provider_reference = models.CharField(max_length=64, unique=True)
    status = models.CharField(max_length=12, choices=STATUS, default='pending')
    simulated = models.BooleanField(default=False)
    failure_reason = models.CharField(max_length=200, blank=True)
    period_start = models.DateField(null=True, blank=True)
    period_end = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.pharmacy} · {self.months} month(s) · {self.amount:.0f} RWF ({self.status})'

    @property
    def invoice_number(self):
        return f'MF-{self.created_at:%Y}-{self.pk:05d}'


class MedicineRequest(models.Model):
    """A pharmacy asks the administrator to add a medicine to the shared catalogue."""
    STATUS = [('open', 'Open'), ('added', 'Added'), ('rejected', 'Rejected')]

    pharmacy = models.ForeignKey(Pharmacy, on_delete=models.CASCADE, related_name='medicine_requests')
    name = models.CharField(max_length=150)
    generic_name = models.CharField(max_length=150, blank=True)
    note = models.CharField(max_length=300, blank=True)
    status = models.CharField(max_length=10, choices=STATUS, default='open')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.name} ({self.pharmacy})'


class SmsLog(models.Model):
    """Every SMS sent or received, so the system is auditable and demo-able without credit."""
    DIRECTION = [('out', 'Outgoing'), ('in', 'Incoming')]
    STATUS = [('sent', 'Sent'), ('failed', 'Failed'), ('simulated', 'Simulated'), ('received', 'Received')]

    direction = models.CharField(max_length=3, choices=DIRECTION, default='out')
    phone = models.CharField(max_length=20)
    message = models.TextField()
    status = models.CharField(max_length=10, choices=STATUS)
    error = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'SMS log'

    def __str__(self):
        return f'{self.get_direction_display()} {self.phone}: {self.message[:40]}'


class StockAlert(models.Model):
    """A patient asked to get an SMS when a medicine is back in stock (optionally in one district)."""
    medicine = models.ForeignKey(Medicine, on_delete=models.CASCADE, related_name='stock_alerts')
    phone = models.CharField(max_length=20)
    district = models.CharField(max_length=60, blank=True, choices=DISTRICT_CHOICES)
    created_at = models.DateTimeField(auto_now_add=True)
    notified_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        where = f' in {self.district}' if self.district else ''
        state = 'sent' if self.notified_at else 'waiting'
        return f'{self.medicine.name}{where} → {self.phone} ({state})'
