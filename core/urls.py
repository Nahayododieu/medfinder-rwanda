from django.conf import settings
from django.contrib.auth import views as auth_views
from django.urls import path, re_path

from . import views, views_browse, views_extra, views_patient
from .ratelimit import ratelimit

urlpatterns = [
    # Patients
    path('', views.search, name='search'),
    path('reserve/<int:pk>/', views.reserve, name='reserve'),
    path('reservation/<int:pk>/<str:code>/', views.reservation_detail, name='reservation_detail'),
    path('reservation/<int:pk>/<str:code>/pay/', views.pay, name='pay'),
    path('reservation/<int:pk>/<str:code>/pay/status/', views.payment_status, name='payment_status'),
    path('reservation/<int:pk>/<str:code>/cancel/', views.reservation_cancel, name='reservation_cancel'),
    path('reservation/<int:pk>/<str:code>/review/', views.reservation_review, name='reservation_review'),
    path('reservation/<int:pk>/<str:code>/prescription/', views_patient.reservation_prescription,
         name='reservation_prescription'),
    path('reservation/<int:pk>/<str:code>/prescription/view/', views_patient.patient_prescription_file,
         name='patient_prescription_file'),
    path('basket/', views_patient.basket, name='basket'),
    path('basket/add/<int:pk>/', views_patient.basket_add, name='basket_add'),
    path('basket/keep/', views_patient.basket_conflict_cancel, name='basket_conflict_cancel'),
    path('basket/checkout/', views_patient.basket_checkout, name='basket_checkout'),
    path('order/<int:pk>/<str:code>/', views_patient.order_detail, name='order_detail'),
    path('order/<int:pk>/<str:code>/pay/', views_patient.order_pay, name='order_pay'),
    path('order/<int:pk>/<str:code>/pay/status/', views_patient.order_payment_status, name='order_payment_status'),
    path('order/<int:pk>/<str:code>/cancel/', views_patient.order_cancel, name='order_cancel'),
    path('order/<int:pk>/<str:code>/prescription/', views_patient.order_prescription, name='order_prescription'),
    path('my/', views_patient.my_reservations, name='my_reservations'),
    path('my/verify/', views_patient.my_verify, name='my_verify'),
    path('my/logout/', views_patient.my_logout, name='my_logout'),
    path('dashboard/prescription/<int:pk>/', views_patient.prescription_file, name='prescription_file'),
    path('medicines/', views_browse.medicine_list, name='medicine_list'),
    path('medicines/<int:pk>/', views_browse.medicine_detail, name='medicine_detail'),
    path('find-pharmacy/', views_browse.pharmacy_list, name='pharmacy_list'),
    path('find-pharmacy/<int:pk>/', views_browse.pharmacy_detail, name='pharmacy_detail'),
    path('help/', views_browse.help_page, name='help'),
    path('alerts/stock/', views_browse.stock_alert, name='stock_alert'),
    path('sitemap.xml', views_browse.sitemap_xml, name='sitemap'),
    path('about/', views_extra.about, name='about'),
    path('contact/', views_extra.contact, name='contact'),
    path('api/medicines/', views.medicine_suggest, name='medicine_suggest'),
    path('track/', views.track, name='track'),
    path('privacy/', views.privacy, name='privacy'),
    path('terms/', views.terms, name='terms'),
    path('pharmacies/', views.for_pharmacies, name='for_pharmacies'),
    path('lang/<str:lang>/', views.set_language, name='set_language'),
    path('welcome/', views_browse.welcome, name='welcome'),

    # Pharmacies
    path('login/', views.RateLimitedLoginView.as_view(), name='login'),
    path('login/verify/', views.login_verify, name='login_verify'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('register/', views.register, name='register'),
    path('password/change/', auth_views.PasswordChangeView.as_view(
        template_name='core/password_form.html', success_url='/password/change/done/'), name='password_change'),
    path('password/change/done/', auth_views.PasswordChangeDoneView.as_view(
        template_name='core/password_done.html'), name='password_change_done'),
    path('password/reset/', ratelimit('reset', settings.RATE_LIMIT_REGISTER)(auth_views.PasswordResetView.as_view(
        template_name='core/password_form.html', email_template_name='core/email/password_reset.txt',
        subject_template_name='core/email/password_reset_subject.txt', success_url='/password/reset/sent/')),
        name='password_reset'),
    path('password/reset/sent/', auth_views.PasswordResetDoneView.as_view(
        template_name='core/password_done.html'), name='password_reset_done'),
    path('password/reset/<uidb64>/<token>/', auth_views.PasswordResetConfirmView.as_view(
        template_name='core/password_form.html', success_url='/password/reset/complete/'),
        name='password_reset_confirm'),
    path('password/reset/complete/', auth_views.PasswordResetCompleteView.as_view(
        template_name='core/password_done.html'), name='password_reset_complete'),
    path('dashboard/', views.dashboard, name='dashboard'),
    path('dashboard/poll/', views.dashboard_poll, name='dashboard_poll'),
    path('dashboard/billing/', views.billing, name='billing'),
    path('dashboard/billing/check/', views.billing_check, name='billing_check'),
    path('dashboard/billing/invoice/<int:pk>/', views_extra.invoice, name='invoice'),
    path('dashboard/staff/', views_extra.staff, name='staff'),
    path('dashboard/staff/<int:pk>/remove/', views_extra.staff_remove, name='staff_remove'),
    path('dashboard/reports/', views_extra.reports, name='reports'),
    path('dashboard/request-medicine/', views.medicine_request, name='medicine_request'),
    path('dashboard/stock/import/', views.stock_import, name='stock_import'),
    path('dashboard/stock/export.csv', views.stock_export, name='stock_export'),
    path('dashboard/profile/', views.pharmacy_profile, name='pharmacy_profile'),
    path('dashboard/update/<int:pk>/', views.update_stock, name='update_stock'),
    path('dashboard/delete/<int:pk>/', views.delete_stock, name='delete_stock'),
    path('dashboard/photo/<int:pk>/', views.stock_photo, name='stock_photo'),
    path('dashboard/reservation/<int:pk>/<str:action>/', views.reservation_action, name='reservation_action'),

    # Administrator
    path('analytics/', views.analytics, name='analytics'),
    path('analytics/export.csv', views.analytics_export, name='analytics_export'),
    path('analytics/commission/', views_extra.commission_report, name='commission_report'),
    path('admin-files/license/<int:pk>/', views_extra.license_file, name='license_file'),

    # Public API, PWA, status
    path('api/v1/search/', views_extra.api_search, name='api_search'),
    path('api/v1/pharmacies/', views_extra.api_pharmacies, name='api_pharmacies'),
    path('api/v1/medicines/', views_extra.api_medicines, name='api_medicines'),
    path('developers/', views_extra.developers, name='developers'),
    path('manifest.webmanifest', views_extra.manifest, name='manifest'),
    path('sw.js', views_extra.service_worker, name='service_worker'),
    path('offline/', views_extra.offline, name='offline'),
    path('status/', views_extra.status, name='status'),

    # Africa's Talking + MTN MoMo callbacks
    path('ussd/', views.ussd, name='ussd'),
    path('sms/incoming/', views.sms_incoming, name='sms_incoming'),
    path('momo/callback/', views.momo_callback, name='momo_callback'),
    path('airtel/callback/', views.airtel_callback, name='airtel_callback'),

    # Medicine photos are public. With Cloudinary they are served from Cloudinary; without it
    # (local development) from the media folder. Licences and prescriptions are never served here.
    re_path(r'^media/(?P<path>medicine-photos/[\w./-]+)$', views_browse.medicine_photo_file, name='medicine_photo_file'),

    path('healthz/', views.healthz, name='healthz'),
    path('robots.txt', views.robots_txt, name='robots_txt'),

    # Staff demo tools
    path('tools/ussd/', views.tool_ussd, name='tool_ussd'),
    path('tools/ussd/step/', views.tool_ussd_step, name='tool_ussd_step'),
    path('tools/sms/', views.tool_sms, name='tool_sms'),
]
