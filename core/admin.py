from datetime import timedelta

from django.conf import settings
from django.contrib import admin, messages
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html, format_html_join

from .billing import record_manual_payment, send_reminders
from django import forms

from .licence_check import FAIL, OK, WARN, ListReadError, automatic_checks, extract_text, suggested_expiry
from .models import (FdaLicenceList, Inventory, Medicine, MedicineRequest, Order, Payment, Pharmacy, PharmacyStaff, Reservation,
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


class LicenceStatusFilter(admin.SimpleListFilter):
    title = 'licence'
    parameter_name = 'licence'

    def lookups(self, request, model_admin):
        return [('to_check', 'Waiting for licence check'), ('expired', 'Licence expired'),
                ('expiring', 'Expires within 30 days'), ('no_checklist', 'Verified without checklist')]

    def queryset(self, request, queryset):
        today = timezone.localdate()
        if self.value() == 'to_check':
            return queryset.filter(verified=False)
        if self.value() == 'expired':
            return queryset.filter(license_expiry__lt=today)
        if self.value() == 'expiring':
            return queryset.filter(license_expiry__gte=today, license_expiry__lte=today + timedelta(days=30))
        if self.value() == 'no_checklist':
            incomplete = Q()
            for f in Pharmacy.CHECK_FIELDS:
                incomplete |= Q(**{f: False})
            return queryset.filter(verified=True).filter(incomplete)
        return queryset


class PharmacyAdminForm(forms.ModelForm):
    class Meta:
        model = Pharmacy
        exclude = ('license_document',)  # private file: opened with the protected link instead

    def clean(self):
        data = super().clean()
        if not data.get('verified'):
            return data
        if not data.get('license_expiry'):
            data['license_expiry'] = suggested_expiry(self.instance)  # from the Rwanda FDA list, if found
        newly = not (self.instance.pk and Pharmacy.objects.filter(pk=self.instance.pk, verified=True).exists())
        problems = []
        if newly:
            problems += [str(Pharmacy._meta.get_field(f).verbose_name) for f in Pharmacy.CHECK_FIELDS if not data.get(f)]
            if not self.instance.license_document:
                problems.insert(0, 'A licence document must be uploaded')
            if not data.get('license_expiry'):
                problems.append('Enter the licence expiry date')
        if data.get('license_expiry') and data['license_expiry'] < timezone.localdate():
            problems.append('The licence has expired: it cannot be verified')
        if problems:
            raise forms.ValidationError(['This pharmacy cannot be verified yet:'] + problems)
        return data


@admin.register(Pharmacy)
class PharmacyAdmin(admin.ModelAdmin):
    form = PharmacyAdminForm
    list_display = ('name', 'license_no', 'district', 'phone', 'verified_badge', 'licence_status', 'subscription',
                    'coverage_end_display', 'created_at')
    list_filter = ('verified', LicenceStatusFilter, SubscriptionStatusFilter, 'district')
    search_fields = ('name', 'license_no', 'phone', 'owner__username')
    actions = ('verify_pharmacies', 'unverify_pharmacies', 'record_1_month', 'record_3_months',
               'record_12_months', 'extend_trial_14', 'send_reminders_now')
    inlines = [SubscriptionPaymentInline, InventoryInline]
    readonly_fields = ('created_at', 'map_link', 'subscription', 'license_link', 'automatic_checks_display',
                       'verified_record')
    fieldsets = (
        (None, {'fields': ('owner', 'name', 'license_no', 'license_link')}),
        ('Licence verification', {
            'description': 'Before verifying: open the licence document, read the automatic checks, tick every '
                           'item you checked yourself, then tick Verified. A pharmacy cannot be verified until '
                           'every item is ticked and the licence expiry date is entered.',
            'fields': ('automatic_checks_display', 'check_document', 'check_number', 'check_name', 'check_fda_list',
                       'check_location', 'check_phone', 'license_expiry', 'verification_notes', 'verified',
                       'verified_record')}),
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

    @admin.display(description='Automatic checks')
    def automatic_checks_display(self, obj):
        if not obj.pk:
            return '-'
        icons = {OK: ('✔', '#05634f'), WARN: ('!', '#7a5714'), FAIL: ('✘', '#b4372a')}
        rows = format_html_join('', '<tr><td style="color:{};font-weight:800;padding:4px 8px">{}</td>'
                                    '<td style="padding:4px 8px"><strong>{}</strong><br><span style="color:#555">{}</span>'
                                    '</td></tr>',
                                ((icons[st][1], icons[st][0], label, detail) for label, st, detail in automatic_checks(obj)))
        return format_html('<table style="border-collapse:collapse;max-width:760px">{}</table>'
                           '<p style="margin:6px 0 0;color:#555">Source: '
                           '<a href="https://rwandafda.gov.rw" target="_blank" rel="noopener">rwandafda.gov.rw</a> '
                           '(Rwanda FDA). Upload its newest list under “Rwanda FDA licence lists”.</p>', rows)

    @admin.display(description='Licence')
    def licence_status(self, obj):
        if obj.license_expired:
            return format_html('<strong style="color:#b4372a">Expired {}</strong>', obj.license_expiry.strftime('%d %b %Y'))
        if obj.verified and not obj.checklist_complete:
            return format_html('<span style="color:#7a5714">{}</span>', 'Verified without checklist')
        if obj.license_expiry:
            return format_html('<span style="color:#05634f">Valid to {}</span>', obj.license_expiry.strftime('%d %b %Y'))
        return format_html('<span style="color:#64736e">{}</span>', 'Not checked' if not obj.verified else 'No expiry date')

    @admin.display(description='Verified by')
    def verified_record(self, obj):
        if obj.verified_at:
            who = obj.verified_by.get_username() if obj.verified_by else 'unknown'
            return f'{who}, {timezone.localtime(obj.verified_at):%d %b %Y %H:%M}'
        return '-'

    def save_model(self, request, obj, form, change):
        newly_verified = obj.verified and not (change and Pharmacy.objects.filter(pk=obj.pk, verified=True).exists())
        if newly_verified:
            obj.verified_at, obj.verified_by = timezone.now(), request.user
        super().save_model(request, obj, form, change)
        if newly_verified:
            send_sms(obj.phone, f'MedFinder: {obj.name} is now verified and visible to patients. '
                                f'Your free trial runs until {obj.trial_ends_at:%d %b %Y}. Keep your stock up to date.')

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

    @admin.action(description='Verify selected pharmacies (only those with a complete licence checklist)')
    def verify_pharmacies(self, request, queryset):
        done, skipped = [], []
        for p in queryset.filter(verified=False):
            if p.missing_for_verification():
                skipped.append(p.name)
                continue
            p.verified, p.verified_at, p.verified_by = True, timezone.now(), request.user
            p.save(update_fields=['verified', 'verified_at', 'verified_by'])  # also starts the free trial
            send_sms(p.phone, f'MedFinder: {p.name} is now verified and visible to patients. '
                              f'Your free trial runs until {p.trial_ends_at:%d %b %Y}. Keep your stock up to date.')
            done.append(p.name)
        if done:
            self.message_user(request, f'{len(done)} pharmacy(ies) verified.', messages.SUCCESS)
        if skipped:
            self.message_user(request, 'Not verified (licence checklist incomplete, open each pharmacy to finish it): '
                              + ', '.join(skipped), messages.WARNING)

    @admin.action(description='Remove verification (hide from search)')
    def unverify_pharmacies(self, request, queryset):
        n = queryset.update(verified=False)
        self.message_user(request, f'{n} pharmacy(ies) hidden from search.', messages.WARNING)


class FdaLicenceListForm(forms.ModelForm):
    upload = forms.FileField(label='List file (PDF, CSV or text)',
                             help_text='Download “List of licensed human retail pharmacies” from rwandafda.gov.rw '
                                       '(Publications), then choose the file here.')

    class Meta:
        model = FdaLicenceList
        fields = ('list_date',)

    def clean_upload(self):
        f = self.cleaned_data['upload']
        if f.size > 15 * 1024 * 1024:
            raise forms.ValidationError('The file is too large (maximum 15 MB).')
        try:
            self.list_text = extract_text(f.name, f.read())
        except ListReadError as exc:
            raise forms.ValidationError(str(exc))
        return f


@admin.register(FdaLicenceList)
class FdaLicenceListAdmin(admin.ModelAdmin):
    form = FdaLicenceListForm
    list_display = ('__str__', 'line_count', 'source_name', 'uploaded_at', 'uploaded_by')
    readonly_fields = ('line_count', 'uploaded_at', 'uploaded_by', 'source_name', 'preview')

    def get_fields(self, request, obj=None):
        return ('list_date', 'upload') if obj is None else ('list_date', 'source_name', 'line_count', 'uploaded_at',
                                                            'uploaded_by', 'preview')

    def get_form(self, request, obj=None, **kwargs):
        if obj is not None:  # existing list: only the date can change
            kwargs['form'] = forms.modelform_factory(FdaLicenceList, fields=('list_date',))
        return super().get_form(request, obj, **kwargs)

    @admin.display(description='First lines of the list (to check it was read correctly)')
    def preview(self, obj):
        lines = [ln for ln in obj.text.splitlines() if ln.strip()][:25]
        return format_html('<pre style="white-space:pre-wrap;max-width:900px">{}</pre>', '\n'.join(lines))

    def save_model(self, request, obj, form, change):
        if not change:
            obj.text = form.list_text
            obj.line_count = sum(1 for ln in obj.text.splitlines() if ln.strip())
            obj.source_name = form.cleaned_data['upload'].name[:200]
            obj.uploaded_by = request.user
        super().save_model(request, obj, form, change)


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


# Block password guessing on the admin login: a few tries per IP, then a pause.
from .ratelimit import ratelimit as _ratelimit  # noqa: E402

admin.site.login = _ratelimit('admin-login', settings.RATE_LIMIT_ADMIN_LOGIN)(admin.site.login)
