from datetime import timedelta

from django.conf import settings
from django.contrib import admin, messages
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html

from .billing import record_manual_payment, send_reminders
from .models import (Inventory, Medicine, MedicineRequest, Order, Payment, Pharmacy, PharmacyStaff, Reservation,
                     Review, SearchLog, SmsLog, StockAlert, StockChange, SubscriptionPayment, Testimonial)
from .services import log_stock_change
from .sms import send_sms

admin.site.site_header = 'MedFinder Rwanda administration'
admin.site.site_title = 'MedFinder admin'
admin.site.index_title = 'Platform management'


class InventoryInline(admin.TabularInline):
    model = Inventory
    extra = 0
    fields = ('medicine', 'quantity', 'price', 'updated_at')
    readonly_fields = ('updated_at',)
    autocomplete_fields = ('medicine',)


class SubscriptionStatusFilter(admin.SimpleListFilter):
    title = 'subscription'
    parameter_name = 'subscription'

    def lookups(self, request, model_admin):
        return [('trial', 'Free trial'), ('active', 'Paid, active'), ('grace', 'Payment due (grace)'),
                ('overdue', 'Overdue')]

    def queryset(self, request, queryset):
        if not self.value():
            return queryset
        today = timezone.localdate()
        cut = today - timedelta(days=settings.SUBSCRIPTION_GRACE_DAYS)
        q_active = Q(paid_until__gte=today)
        q_trial = ~q_active & (Q(trial_ends_at__gte=today) | Q(trial_ends_at__isnull=True, paid_until__isnull=True))
        q_grace = ~q_active & ~q_trial & (Q(trial_ends_at__gte=cut) | Q(paid_until__gte=cut))
        qs = queryset.filter(verified=True)
        return {
            'active': qs.filter(q_active),
            'trial': qs.filter(q_trial),
            'grace': qs.filter(q_grace),
            'overdue': qs.exclude(q_active | q_trial | q_grace),
        }[self.value()]


class SubscriptionPaymentInline(admin.TabularInline):
    model = SubscriptionPayment
    extra = 0
    can_delete = False
    fields = ('created_at', 'months', 'amount', 'method', 'status', 'period_start', 'period_end')
    readonly_fields = fields


@admin.register(Pharmacy)
class PharmacyAdmin(admin.ModelAdmin):
    list_display = ('name', 'license_no', 'district', 'phone', 'verified_badge', 'subscription',
                    'coverage_end_display', 'created_at')
    list_filter = ('verified', SubscriptionStatusFilter, 'district')
    search_fields = ('name', 'license_no', 'phone', 'owner__username')
    actions = ('verify_pharmacies', 'unverify_pharmacies', 'record_1_month', 'record_3_months',
               'record_12_months', 'extend_trial_14', 'send_reminders_now')
    inlines = [SubscriptionPaymentInline, InventoryInline]
    readonly_fields = ('created_at', 'map_link', 'subscription', 'license_link')
    fieldsets = (
        (None, {'fields': ('owner', 'name', 'license_no', 'license_link', 'license_document', 'verified')}),
        ('Subscription', {'fields': ('subscription', 'trial_ends_at', 'paid_until')}),
        ('Contact and location', {'fields': ('district', 'address', 'phone', 'latitude', 'longitude', 'map_link')}),
        ('Opening hours and insurance', {'fields': ('open_24h', 'opens_at', 'closes_at', 'insurers')}),
        ('Record', {'fields': ('created_at',)}),
    )

    @admin.display(description='License document')
    def license_link(self, obj):
        if obj.pk and obj.license_document:
            return format_html('<a href="{}" target="_blank" rel="noopener">Open the uploaded license</a> '
                               '(check the number matches before verifying)',
                               reverse('license_file', args=[obj.pk]))
        return 'No document uploaded'

    @admin.display(description='Verified', boolean=True, ordering='verified')
    def verified_badge(self, obj):
        return obj.verified

    @admin.display(description='Subscription')
    def subscription(self, obj):
        colours = {'trial': '#2d5a95', 'active': '#05634f', 'grace': '#7a5714', 'overdue': '#b4372a',
                   'unverified': '#64736e'}
        labels = {'trial': 'Free trial', 'active': 'Active', 'grace': 'Payment due',
                  'overdue': 'Hidden (unpaid)' if obj.hidden_for_nonpayment else 'Overdue',
                  'unverified': 'Not verified'}
        st = obj.subscription_status
        return format_html('<strong style="color:{}">{}</strong>', colours[st], labels[st])

    @admin.display(description='Covered until')
    def coverage_end_display(self, obj):
        return obj.coverage_end or '-'

    def _record(self, request, queryset, months):
        done = 0
        for p in queryset.filter(verified=True):
            record_manual_payment(p, months, note=f'Recorded by {request.user.get_username()}')
            done += 1
        skipped = queryset.count() - done
        msg = f'Recorded {months}-month payment for {done} pharmacy(ies).'
        if skipped:
            msg += f' Skipped {skipped} unverified.'
        self.message_user(request, msg, messages.SUCCESS)

    @admin.action(description='Record cash/bank payment: 1 month')
    def record_1_month(self, request, queryset):
        self._record(request, queryset, 1)

    @admin.action(description='Record cash/bank payment: 3 months')
    def record_3_months(self, request, queryset):
        self._record(request, queryset, 3)

    @admin.action(description='Record cash/bank payment: 12 months')
    def record_12_months(self, request, queryset):
        self._record(request, queryset, 12)

    @admin.action(description='Extend free trial by 14 days')
    def extend_trial_14(self, request, queryset):
        n = 0
        for p in queryset.filter(verified=True):
            base = max(p.trial_ends_at or timezone.localdate(), timezone.localdate())
            p.trial_ends_at = base + timedelta(days=14)
            p.last_reminder = ''
            p.save(update_fields=['trial_ends_at', 'last_reminder'])
            n += 1
        self.message_user(request, f'Trial extended for {n} pharmacy(ies).', messages.SUCCESS)

    @admin.action(description='Send due subscription reminders now (all pharmacies)')
    def send_reminders_now(self, request, queryset):
        n = send_reminders()
        self.message_user(request, f'Sent {n} reminder(s).', messages.SUCCESS)

    @admin.display(description='Map')
    def map_link(self, obj):
        if obj.pk:
            return format_html('<a href="https://www.openstreetmap.org/?mlat={}&mlon={}#map=17/{}/{}" '
                               'target="_blank" rel="noopener">Check location on OpenStreetMap</a>',
                               obj.latitude, obj.longitude, obj.latitude, obj.longitude)
        return '-'

    @admin.action(description='Verify selected pharmacies (show in search)')
    def verify_pharmacies(self, request, queryset):
        pending = list(queryset.filter(verified=False))
        for p in pending:
            p.verified = True
            p.save(update_fields=['verified'])  # also starts the free trial
            send_sms(p.phone, f'MedFinder: {p.name} is now verified and visible to patients. '
                              f'Your free trial runs until {p.trial_ends_at:%d %b %Y}. Keep your stock up to date.')
        self.message_user(request, f'{len(pending)} pharmacy(ies) verified.', messages.SUCCESS)

    @admin.action(description='Remove verification (hide from search)')
    def unverify_pharmacies(self, request, queryset):
        n = queryset.update(verified=False)
        self.message_user(request, f'{n} pharmacy(ies) hidden from search.', messages.WARNING)


@admin.register(Medicine)
class MedicineAdmin(admin.ModelAdmin):
    list_display = ('name', 'generic_name', 'category', 'requires_prescription', 'reference_price', 'reference_date')
    list_filter = ('category', 'requires_prescription')
    search_fields = ('name', 'generic_name')
    fieldsets = (
        (None, {'fields': ('name', 'generic_name', 'category', 'requires_prescription')}),
        ('Official reference price (only from a published source, e.g. RSSB tariff)', {
            'fields': ('reference_price', 'reference_unit', 'reference_source', 'reference_date')}),
    )


@admin.register(Inventory)
class InventoryAdmin(admin.ModelAdmin):
    list_display = ('medicine', 'pharmacy', 'quantity', 'price', 'expiry_date', 'updated_at')
    list_filter = ('pharmacy__district', 'pharmacy')
    search_fields = ('medicine__name', 'medicine__generic_name', 'pharmacy__name')
    autocomplete_fields = ('medicine', 'pharmacy')

    def save_model(self, request, obj, form, change):
        old = Inventory.objects.filter(pk=obj.pk).values('quantity', 'price').first() if change else None
        super().save_model(request, obj, form, change)
        log_stock_change(obj, 'admin', request.user, old and old['quantity'], old and old['price'])


@admin.register(StockChange)
class StockChangeAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'pharmacy', 'medicine', 'old_quantity', 'new_quantity', 'old_price',
                    'new_price', 'source', 'user')
    list_filter = ('source', 'pharmacy')
    search_fields = ('medicine__name', 'pharmacy__name', 'user__username')
    date_hierarchy = 'created_at'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False  # the audit log cannot be edited


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'pharmacy', 'rating', 'comment', 'status')
    list_filter = ('status', 'rating')
    search_fields = ('pharmacy__name', 'comment')
    readonly_fields = ('reservation', 'pharmacy', 'rating', 'comment', 'created_at')
    actions = ('approve', 'reject')

    @admin.action(description='Approve selected reviews (show publicly)')
    def approve(self, request, queryset):
        n = queryset.update(status='approved')
        self.message_user(request, f'{n} review(s) approved.', messages.SUCCESS)

    @admin.action(description='Reject selected reviews')
    def reject(self, request, queryset):
        n = queryset.update(status='rejected')
        self.message_user(request, f'{n} review(s) rejected.', messages.WARNING)


@admin.register(Testimonial)
class TestimonialAdmin(admin.ModelAdmin):
    list_display = ('name', 'role', 'published', 'order')
    list_editable = ('published', 'order')


@admin.register(PharmacyStaff)
class PharmacyStaffAdmin(admin.ModelAdmin):
    list_display = ('user', 'pharmacy', 'role', 'created_at')
    list_filter = ('role',)
    search_fields = ('user__username', 'pharmacy__name')


class PaymentInline(admin.TabularInline):
    model = Payment
    extra = 0
    can_delete = False
    readonly_fields = ('provider', 'payer_phone', 'amount', 'commission', 'status', 'provider_reference',
                       'simulated', 'created_at')
    fields = readonly_fields


class OrderLineInline(admin.TabularInline):
    model = Reservation
    extra = 0
    can_delete = False
    show_change_link = True
    fields = ('inventory', 'quantity', 'status', 'delivery_fee', 'prescription_status')
    readonly_fields = fields


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ('id', 'pharmacy', 'patient_phone', 'created_at')
    search_fields = ('id', 'patient_phone', 'pickup_code', 'pharmacy__name')
    readonly_fields = ('patient_phone', 'pharmacy', 'pickup_code', 'created_at')
    inlines = [OrderLineInline]
    date_hierarchy = 'created_at'


@admin.register(Reservation)
class ReservationAdmin(admin.ModelAdmin):
    list_display = ('id', 'medicine', 'pharmacy', 'quantity', 'patient_phone', 'status', 'delivery',
                    'prescription_status', 'source', 'created_at')
    list_filter = ('status', 'source', 'delivery', 'prescription_status', 'inventory__pharmacy__district')
    search_fields = ('id', 'patient_phone', 'pickup_code', 'inventory__medicine__name', 'inventory__pharmacy__name')
    readonly_fields = ('created_at', 'completed_at', 'pickup_code', 'order')
    inlines = [PaymentInline]
    date_hierarchy = 'created_at'

    @admin.display(ordering='inventory__medicine__name')
    def medicine(self, obj):
        return obj.inventory.medicine.name

    @admin.display(ordering='inventory__pharmacy__name')
    def pharmacy(self, obj):
        return obj.inventory.pharmacy.name


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ('reservation', 'provider', 'amount', 'commission', 'status', 'simulated', 'created_at')
    list_filter = ('status', 'provider', 'simulated')
    search_fields = ('provider_reference', 'payer_phone', 'reservation__id')
    readonly_fields = ('provider_reference', 'created_at', 'updated_at')


@admin.register(SubscriptionPayment)
class SubscriptionPaymentAdmin(admin.ModelAdmin):
    list_display = ('pharmacy', 'months', 'amount', 'method', 'status', 'period_start', 'period_end',
                    'simulated', 'created_at')
    list_filter = ('status', 'method', 'simulated')
    search_fields = ('pharmacy__name', 'provider_reference', 'payer_phone')
    readonly_fields = ('provider_reference', 'period_start', 'period_end', 'created_at', 'updated_at')
    date_hierarchy = 'created_at'

    def has_add_permission(self, request):
        return False  # use the "Record cash/bank payment" actions on Pharmacies so coverage is extended


@admin.register(SearchLog)
class SearchLogAdmin(admin.ModelAdmin):
    list_display = ('query', 'medicine', 'district', 'found', 'result_count', 'channel', 'created_at')
    list_filter = ('found', 'channel', 'district')
    search_fields = ('query',)
    date_hierarchy = 'created_at'


@admin.register(MedicineRequest)
class MedicineRequestAdmin(admin.ModelAdmin):
    list_display = ('name', 'generic_name', 'pharmacy', 'status', 'created_at')
    list_filter = ('status',)
    search_fields = ('name', 'generic_name', 'pharmacy__name')
    actions = ('add_to_catalogue', 'reject_requests')

    @admin.action(description='Add selected medicines to the catalogue')
    def add_to_catalogue(self, request, queryset):
        added = 0
        for req in queryset.filter(status='open').select_related('pharmacy'):
            Medicine.objects.get_or_create(name=req.name.strip(), defaults={'generic_name': req.generic_name.strip()})
            req.status = 'added'
            req.save(update_fields=['status'])
            send_sms(req.pharmacy.phone, f'MedFinder: "{req.name}" was added to the catalogue. '
                                         f'You can now list it in your stock.')
            added += 1
        self.message_user(request, f'{added} medicine(s) added to the catalogue.', messages.SUCCESS)

    @admin.action(description='Reject selected requests')
    def reject_requests(self, request, queryset):
        n = queryset.filter(status='open').update(status='rejected')
        self.message_user(request, f'{n} request(s) rejected.', messages.WARNING)


@admin.register(SmsLog)
class SmsLogAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'direction', 'phone', 'short_message', 'status')
    list_filter = ('direction', 'status')
    search_fields = ('phone', 'message')

    @admin.display(description='Message')
    def short_message(self, obj):
        return obj.message[:80]


@admin.register(StockAlert)
class StockAlertAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'medicine', 'district', 'phone', 'notified_at')
    list_filter = ('notified_at', 'district')
    search_fields = ('phone', 'medicine__name')
    raw_id_fields = ('medicine',)
