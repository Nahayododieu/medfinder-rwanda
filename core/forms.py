from decimal import Decimal

from django import forms
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password

from .models import INSURER_CHOICES, Medicine, MedicineRequest, Pharmacy
from .utils import DISTRICT_CHOICES, is_valid_rw_mobile, normalize_phone

PHONE_HELP = 'Rwandan mobile number, e.g. 0788 123 456'


def clean_rw_phone(value):
    value = (value or '').strip()
    if not is_valid_rw_mobile(value):
        raise forms.ValidationError('Enter a valid Rwandan mobile number (MTN 078/079 or Airtel 072/073).')
    return normalize_phone(value)


class StockForm(forms.Form):
    medicine = forms.ModelChoiceField(queryset=Medicine.objects.order_by('name'), empty_label='Choose a medicine…')
    quantity = forms.IntegerField(
        min_value=0, max_value=1_000_000,
        widget=forms.NumberInput(attrs={'placeholder': 'Quantity'}),
    )
    price = forms.DecimalField(
        min_value=0, max_digits=10, decimal_places=2,
        widget=forms.NumberInput(attrs={'placeholder': 'Price (RWF)'}),
    )
    expiry_date = forms.DateField(required=False, widget=forms.DateInput(attrs={'type': 'date', 'title': 'Expiry date (optional)'}))


LICENSE_TYPES = ('.pdf', '.jpg', '.jpeg', '.png')


def clean_license_file(f):
    if not f:
        return f
    if f.size > 5 * 1024 * 1024:
        raise forms.ValidationError('The license file is too large (maximum 5 MB).')
    if not f.name.lower().endswith(LICENSE_TYPES):
        raise forms.ValidationError('Upload the license as a PDF, JPG or PNG file.')
    head = f.read(8)
    f.seek(0)
    if not (head.startswith(b'%PDF') or head.startswith(b'\xff\xd8\xff') or head.startswith(b'\x89PNG')):
        raise forms.ValidationError('This file does not look like a PDF or image.')
    return f


def clean_prescription_file(f):
    """A prescription photo or scan: JPG, PNG or PDF up to 5 MB, checked by content, not only name."""
    if not f:
        return f
    if f.size > 5 * 1024 * 1024:
        raise forms.ValidationError('The prescription file is too large (maximum 5 MB).')
    if not f.name.lower().endswith(LICENSE_TYPES):
        raise forms.ValidationError('Upload the prescription as a photo (JPG or PNG) or a PDF.')
    head = f.read(8)
    f.seek(0)
    if not (head.startswith(b'%PDF') or head.startswith(b'\xff\xd8\xff') or head.startswith(b'\x89PNG')):
        raise forms.ValidationError('This file does not look like a photo or PDF.')
    return f


def photo_kind(head):
    """'jpg', 'png', 'webp' or 'gif' from the first bytes of a file, else ''."""
    if head.startswith(b'\xff\xd8\xff'):
        return 'jpg'
    if head.startswith(b'\x89PNG'):
        return 'png'
    if head[:4] == b'RIFF' and head[8:12] == b'WEBP':
        return 'webp'
    if head[:6] in (b'GIF87a', b'GIF89a'):
        return 'gif'
    return ''


def clean_medicine_photo(f):
    """A photo of the medicine box, up to 10 MB, checked by its content (not its name).

    The browser normally converts any picture to a small JPG first; this check is the safety net.
    Windows names (.jfif, .jpe, ...) are fine as long as the content is a real JPG/PNG/WebP/GIF.
    """
    if not f:
        return f
    if f.size > 10 * 1024 * 1024:
        raise forms.ValidationError('The photo is too large (maximum 10 MB). Please choose a smaller picture.')
    head = f.read(12)
    f.seek(0)
    kind = photo_kind(head)
    if not kind:
        raise forms.ValidationError('This file is not a picture we can show. Please choose a JPG, PNG, WebP or GIF picture.')
    f.name = f'photo.{kind}'  # store with the extension that matches the real content
    return f


class MedicinePhotoForm(forms.Form):
    photo = forms.FileField()

    def clean_photo(self):
        return clean_medicine_photo(self.cleaned_data.get('photo'))


class DeliveryMixin:
    """Pickup (default) or home delivery with an address."""

    def clean(self):
        data = super().clean()
        if data.get('delivery') and len((data.get('address') or '').strip()) < 5:
            self.add_error('address', 'Please enter a delivery address (area, street and a landmark).')
        return data


class ReservationForm(DeliveryMixin, forms.Form):
    phone = forms.CharField(max_length=20)
    quantity = forms.IntegerField(min_value=1, max_value=1000)
    consent = forms.BooleanField(required=True, error_messages={
        'required': 'Please accept the privacy notice so we can send your pickup code.'})
    delivery = forms.BooleanField(required=False)
    address = forms.CharField(max_length=255, required=False)
    prescription = forms.FileField(required=False)

    def clean_phone(self):
        return clean_rw_phone(self.cleaned_data['phone'])

    def clean_prescription(self):
        return clean_prescription_file(self.cleaned_data.get('prescription'))


class CheckoutForm(DeliveryMixin, forms.Form):
    phone = forms.CharField(max_length=20)
    consent = forms.BooleanField(required=True, error_messages={
        'required': 'Please accept the privacy notice so we can send your pickup code.'})
    delivery = forms.BooleanField(required=False)
    address = forms.CharField(max_length=255, required=False)
    prescription = forms.FileField(required=False)

    def clean_phone(self):
        return clean_rw_phone(self.cleaned_data['phone'])

    def clean_prescription(self):
        return clean_prescription_file(self.cleaned_data.get('prescription'))


class PrescriptionForm(forms.Form):
    prescription = forms.FileField()

    def clean_prescription(self):
        return clean_prescription_file(self.cleaned_data.get('prescription'))


class PatientPhoneForm(forms.Form):
    phone = forms.CharField(max_length=20, label='Your phone number',
                            widget=forms.TextInput(attrs={'inputmode': 'tel', 'autocomplete': 'tel',
                                                          'placeholder': '07…'}))

    def clean_phone(self):
        return clean_rw_phone(self.cleaned_data['phone'])


class PaymentForm(forms.Form):
    """Mobile money number: MTN (078/079) goes to MoMo, Airtel (072/073) to Airtel Money."""
    phone = forms.CharField(max_length=20, label='Mobile money number')

    def clean_phone(self):
        return clean_rw_phone(self.cleaned_data['phone'])

    @property
    def provider(self):
        from .payments import provider_for
        return provider_for(self.cleaned_data['phone'])


class SubscriptionPayForm(PaymentForm):
    months = forms.TypedChoiceField(coerce=int, choices=[(1, '1 month'), (3, '3 months'),
                                                         (6, '6 months'), (12, '12 months')])


class TrackForm(forms.Form):
    reservation = forms.IntegerField(min_value=1, label='Reservation number')
    code = forms.RegexField(regex=r'^\d{6}$', label='Pickup code',
                            error_messages={'invalid': 'The pickup code has 6 digits.'})


class PharmacyRegisterForm(forms.Form):
    username = forms.CharField(max_length=150, help_text='Used to log in.')
    email = forms.EmailField(help_text='Used to reset your password if you forget it.')
    password = forms.CharField(widget=forms.PasswordInput, help_text='At least 8 characters, not only numbers.')
    password_confirm = forms.CharField(widget=forms.PasswordInput, label='Confirm password')
    name = forms.CharField(max_length=150, label='Pharmacy name')
    license_no = forms.CharField(max_length=50, label='License number', help_text='As issued by Rwanda FDA.')
    license_document = forms.FileField(label='License document',
                                       help_text='Photo or scan of your Rwanda FDA license: PDF, JPG or PNG, max 5 MB.')
    district = forms.ChoiceField(choices=[('', 'Choose district…')] + DISTRICT_CHOICES)
    address = forms.CharField(max_length=200, required=False, help_text='Street, landmark or building (optional).')
    phone = forms.CharField(max_length=20, help_text='Patients call this number. Also used for SMS stock updates.')
    latitude = forms.FloatField(min_value=-2.9, max_value=-1.0)
    longitude = forms.FloatField(min_value=28.8, max_value=30.95)
    agree = forms.BooleanField(label='I confirm this pharmacy is licensed and I accept the terms and privacy notice.')

    def clean_username(self):
        username = self.cleaned_data['username'].strip()
        if User.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError('This username is taken.')
        return username

    def clean_license_no(self):
        license_no = self.cleaned_data['license_no'].strip().upper()
        if Pharmacy.objects.filter(license_no__iexact=license_no).exists():
            raise forms.ValidationError('This license is already registered.')
        return license_no

    def clean_phone(self):
        return clean_rw_phone(self.cleaned_data['phone'])

    def clean_license_document(self):
        return clean_license_file(self.cleaned_data.get('license_document'))

    def clean(self):
        data = super().clean()
        password = data.get('password')
        if password:
            if password != data.get('password_confirm'):
                self.add_error('password_confirm', 'The passwords do not match.')
            else:
                try:
                    validate_password(password, User(username=data.get('username', '')))
                except forms.ValidationError as e:
                    self.add_error('password', e)
        return data


class PharmacyProfileForm(forms.ModelForm):
    email = forms.EmailField(help_text='Used to reset your password.')
    insurer_list = forms.MultipleChoiceField(
        label='Insurance accepted', required=False, choices=INSURER_CHOICES,
        widget=forms.CheckboxSelectMultiple, help_text='Patients can filter pharmacies by insurance.')
    license_document = forms.FileField(required=False, label='License document',
                                       help_text='Upload a new file to replace the current one.')

    class Meta:
        model = Pharmacy
        fields = ['name', 'district', 'address', 'phone', 'open_24h', 'opens_at', 'closes_at',
                  'offers_delivery', 'delivery_fee', 'delivery_area', 'latitude', 'longitude']
        widgets = {
            'opens_at': forms.TimeInput(attrs={'type': 'time'}, format='%H:%M'),
            'closes_at': forms.TimeInput(attrs={'type': 'time'}, format='%H:%M'),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['email'].initial = self.instance.owner.email
        self.fields['insurer_list'].initial = self.instance.insurer_codes
        self.fields['latitude'].min_value = -2.9
        self.fields['latitude'].max_value = -1.0
        self.fields['longitude'].min_value = 28.8
        self.fields['longitude'].max_value = 30.95
        self.fields['delivery_fee'].required = False  # blank means free delivery (or no delivery)

    def clean_delivery_fee(self):
        fee = self.cleaned_data.get('delivery_fee')
        return Decimal('0') if fee is None else fee

    def clean_phone(self):
        return clean_rw_phone(self.cleaned_data['phone'])

    def clean_latitude(self):
        v = self.cleaned_data['latitude']
        if not -2.9 <= v <= -1.0:
            raise forms.ValidationError('The location must be inside Rwanda.')
        return v

    def clean_longitude(self):
        v = self.cleaned_data['longitude']
        if not 28.8 <= v <= 30.95:
            raise forms.ValidationError('The location must be inside Rwanda.')
        return v

    def clean(self):
        data = super().clean()
        if not data.get('open_24h') and bool(data.get('opens_at')) != bool(data.get('closes_at')):
            raise forms.ValidationError('Enter both the opening and the closing time, or tick "Open 24 hours".')
        fee = data.get('delivery_fee')
        if fee is not None and (fee < 0 or fee > 50000):
            self.add_error('delivery_fee', 'Enter a delivery fee between 0 and 50,000 RWF.')
        return data

    def clean_license_document(self):
        return clean_license_file(self.cleaned_data.get('license_document'))

    def save(self, commit=True):
        pharmacy = super().save(commit=False)
        pharmacy.insurers = ','.join(self.cleaned_data.get('insurer_list') or [])
        new_license = self.cleaned_data.get('license_document')
        if new_license:
            pharmacy.license_document = new_license
        pharmacy.save()
        pharmacy.owner.email = self.cleaned_data['email']
        pharmacy.owner.save(update_fields=['email'])
        return pharmacy


class ReviewForm(forms.Form):
    rating = forms.TypedChoiceField(coerce=int, choices=[(i, str(i)) for i in range(5, 0, -1)],
                                    widget=forms.RadioSelect)
    comment = forms.CharField(max_length=500, required=False, widget=forms.Textarea(attrs={'rows': 3}))


class StaffForm(forms.Form):
    username = forms.CharField(max_length=150)
    email = forms.EmailField(required=False)
    password = forms.CharField(widget=forms.PasswordInput, help_text='Give it to the pharmacist in person.')

    def clean_username(self):
        username = self.cleaned_data['username'].strip()
        if User.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError('This username is taken.')
        return username

    def clean(self):
        data = super().clean()
        if data.get('password'):
            try:
                validate_password(data['password'], User(username=data.get('username', '')))
            except forms.ValidationError as e:
                self.add_error('password', e)
        return data


class LoginCodeForm(forms.Form):
    code = forms.RegexField(regex=r'^\d{6}$', label='6-digit code',
                            error_messages={'invalid': 'Enter the 6 digits from the SMS.'},
                            widget=forms.TextInput(attrs={'inputmode': 'numeric', 'autocomplete': 'one-time-code'}))


class MedicineRequestForm(forms.ModelForm):
    class Meta:
        model = MedicineRequest
        fields = ['name', 'generic_name', 'note']
        labels = {'name': 'Medicine name and strength', 'note': 'Note for the administrator'}
        widgets = {'name': forms.TextInput(attrs={'placeholder': 'e.g. Azithromycin 500mg'})}


class StockImportForm(forms.Form):
    file = forms.FileField(help_text='CSV with columns: medicine, quantity, price')

    def clean_file(self):
        f = self.cleaned_data['file']
        if f.size > 512 * 1024:
            raise forms.ValidationError('The file is too large (maximum 512 KB).')
        if not f.name.lower().endswith('.csv'):
            raise forms.ValidationError('Please upload a .csv file (Excel: File > Save As > CSV).')
        return f
