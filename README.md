# MedFinder Rwanda

A web and USSD platform for real-time pharmacy stock search, reservation, mobile-money payment and demand analytics.

## Features

| Area | What it does | Where |
|---|---|---|
| Search (FR1–FR4) | Brand or generic name search across verified pharmacies with stock. District filter. Sorted by distance (Haversine) when the browser shares a location, otherwise by price. Leaflet + OpenStreetMap map, directions link. | `/` |
| Pharmacy registration (FR5) | License, district, phone and a map pin picker. Hidden until an admin verifies it. | `/register/` |
| Pharmacy dashboard (FR6) | Add, update and remove stock; low-stock flags; open reservations; history; unmet demand in the district; profile editing. Each pharmacy can see only its own data. | `/dashboard/` |
| Reservations (FR7, FR9) | Stock held inside a transaction with row locks; 6-digit code from a secure random generator; mark ready, confirm with the code, or cancel (stock restored). | |
| SMS (FR8) | Africa's Talking. Pickup code, ready, cancelled, expired and payment messages. Every SMS is stored in `SmsLog`. With no API key it runs in simulation mode. | `/tools/sms/` |
| Auto-expiry (FR10) | Unpaid reservations older than 2 h are released on each search, dashboard and reservation view, and by a management command. | `manage.py expire_reservations` |
| MTN MoMo (FR11) | Collections API Request-to-Pay, status check and callback. Payment table with a 3% commission. Refund flag when a paid reservation is cancelled. Simulation mode without keys. | reservation page |
| Analytics (FR12) | KPIs, searches and reservations per day, top searches, unmet demand by district, 7-day linear-trend forecast with shortage risk, CSV export. | `/analytics/` (staff) |
| USSD | Search → choose pharmacy → quantity → confirm → reserve; check a reservation; help; Kinyarwanda. | `POST /ussd/`, simulator at `/tools/ussd/` |
| Pharmacy SMS updates | `STOCK <medicine> <qty> [price]`, `LIST`, `HELP` from the pharmacy's registered phone. | `POST /sms/incoming/` |
| Kinyarwanda | EN/RW switch for the patient pages and the USSD menu. | header |
| Patient self-service | Cancel your own unpaid reservation (stock released, pharmacy notified); track by number + code; medicine name suggestions while typing; "Open now / Closed" badges from pharmacy hours. | reservation page, `/track/` |
| Pharmacy tools | SMS alert for every new reservation; live "new reservation" banner on the dashboard; stock search; CSV import/export (from Excel); request new medicines for the catalogue; opening hours; change/reset password by email. | `/dashboard/` |
| Responsive design | Breakpoints for large monitors, laptops, tablets, phones, small phones, landscape phones, touch screens and printing; mobile menu; tables become cards on phones. | `site.css` |
| Pharmacy subscriptions | Free trial starts on verification; 30-day paid periods by MoMo (1/3/6/12 months) or cash recorded in admin; status trial/active/grace/overdue; SMS reminders 7/3/1 days before and after expiry; unpaid pharmacies hidden after the grace period (`HIDE_UNPAID_PHARMACIES`); MRR and revenue in Analytics. | `/dashboard/billing/`, Admin › Pharmacies |
| Trust pages | About, Contact (phone, WhatsApp, email, social links), Terms, Privacy, disclaimer on every result, refund rules on each reservation, admin-managed testimonials. | `/about/`, `/contact/` |
| Smarter search | Typo correction ("paracetmol"), filters for max price, open now and insurance (Mutuelle, RSSB, MMI, private), generic alternatives when out of stock, ratings from verified pickups (moderated), Call and Directions buttons. | `/` |
| Pharmacy tools | Staff accounts (owner / pharmacist), license upload (admins only can open it), expiry dates with dashboard and SMS alerts, sales and missed-demand reports with CSV export, stock audit log, USSD stock update (option 5 for pharmacy phones). | `/dashboard/reports/`, `/dashboard/staff/` |
| Payments | MTN MoMo and Airtel Money chosen from the number, receipts/invoices for subscriptions, monthly commission report with CSV. | `/analytics/commission/` |
| Platform | French, optional SMS two-factor login (`PHARMACY_LOGIN_2FA`), reservation reminder SMS 30 min before release, public JSON API with docs, installable web app with offline page, status page, daily backups, optional Sentry, CI on GitHub Actions. | `/developers/`, `/status/` |
| Browse & discover | Pharmacy directory with map and filters (district, open now, 24 h, insurance), pharmacy profile pages (stock list, hours, insurance, reviews, directions), medicine catalogue A–Z and by category, medicine pages with lowest/average/highest price and availability by district, price summary on search results. | `/find-pharmacy/`, `/medicines/` |
| Help & safety | Help centre (how to search, tips, what labels mean, USSD), Rwanda emergency numbers (SAMU 912, Police 112, RBC 114) in every footer. | `/help/` |
| Back-in-stock alerts | When nothing is in stock, a patient leaves a phone number (optionally a district) and gets one SMS when a verified pharmacy lists the medicine, whichever channel updated the stock. | `POST /alerts/stock/`, Admin › Stock alerts |
| SEO | `sitemap.xml` (linked from `robots.txt`), canonical and Open Graph tags. | `/sitemap.xml` |
| Production | WhiteNoise static files, custom 400/403/404/500 and expired-form pages, `/healthz/` health check, `robots.txt`, `Procfile` for gunicorn. | |
| Security | Env-based secrets; HTTPS, HSTS and secure cookies when DEBUG is off; CSRF; ownership checks; rate limits on login, register, reserve and pay; consent checkbox; privacy notice; optional callback token. | `/privacy/` |

## Run locally (Windows / PowerShell)

```powershell
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env          # then edit .env
python manage.py migrate
python manage.py seed_demo --admin   # demo data + admin / MedFinderAdmin2026!
python manage.py runserver
```

Open http://127.0.0.1:8000.

Demo pharmacy logins: `pharma_kimironko`, `pharma_remera`, `pharma_huye`, … (password `MedFinder2026!`). `pharma_bugesera` is unverified, so you can demonstrate verification in the admin.

Run the tests: `python manage.py test core` (114 tests, including T1–T15 from the report). GitHub Actions runs them on every push (`.github/workflows/tests.yml`).

## Connecting Africa's Talking (sandbox)

1. Put `AT_USERNAME=sandbox` and your sandbox `AT_API_KEY` in `.env`.
2. Expose your local server, e.g. `ngrok http 8000`, and add the host to `DJANGO_EXTRA_DEV_HOSTS`.
3. In the AT dashboard set:
   - USSD callback: `https://<host>/ussd/`
   - SMS incoming callback: `https://<host>/sms/incoming/`
   - If `AT_CALLBACK_TOKEN` is set, append `?token=<value>` to both.
4. Use the AT simulator to dial the code and read the SMS.

Without a tunnel, use the built-in `/tools/ussd/` and `/tools/sms/` pages (staff login).

## Connecting MTN MoMo (sandbox)

1. Subscribe to the **Collections** product at momodeveloper.mtn.com to get a subscription key.
2. Create a sandbox API user and API key (Provisioning API: `POST /v1_0/apiuser`, then `POST /v1_0/apiuser/{id}/apikey`).
3. Set `MOMO_SUBSCRIPTION_KEY`, `MOMO_API_USER`, `MOMO_API_KEY` in `.env`. The sandbox only accepts `MOMO_CURRENCY=EUR`; use `RWF` in production.
4. Optionally set `MOMO_CALLBACK_URL=https://<host>/momo/callback/`.

Leave the keys empty to use simulation mode, where payments succeed when "check payment" is pressed.

## Medicine catalogue and prices

- `python manage.py load_medicine_catalogue` adds about 150 common medicines used in Rwanda (name, generic name, category, prescription needed). It adds **no prices** and can be run again safely.
- Prices shown to patients come only from each pharmacy's own stock entries.
- An official **reference price** (for example from the RSSB medicines tariff) can be entered per medicine in the admin. It is shown only together with its source and the date of the price list, and the admin refuses a price without them.
- Before launch, remove the demo data loaded by `seed_demo`: its pharmacies, stock prices, reservations and search history are made up.

## Deployment checklist

- `DJANGO_DEBUG=False`, a strong `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS`.
- SMTP settings (`EMAIL_HOST` …) so password-reset emails are delivered; `python manage.py check --deploy` should report no errors.
- The `Procfile` runs migrations and `collectstatic`, then gunicorn (Render, Railway, Heroku-style hosts).
- PostgreSQL through `DATABASE_URL` (`pip install psycopg[binary]`).
- `python manage.py collectstatic`, then serve with gunicorn behind HTTPS (nginx or the host's TLS).
- Schedule `python manage.py expire_reservations` every 5–10 minutes (cron / Task Scheduler).
- Schedule `python manage.py subscription_reminders` once a day (e.g. 09:00) to SMS pharmacies whose trial or subscription is ending.
- Or schedule just `python manage.py daily_jobs` once a day: it runs the backup, subscription reminders, expiry alerts and reservation expiry together.
- Copy the `backups/` folder off the server regularly (restore with `python manage.py loaddata <file>`).
- Uploaded license files live in `MEDIA_ROOT`: do **not** let the web server serve that folder publicly; they are opened only through the admin.
- Optional: `pip install sentry-sdk` and set `SENTRY_DSN` for error alerts; point an uptime monitor (e.g. UptimeRobot) at `/healthz/`.
- Use a shared cache (Redis) so rate limits apply across processes.
- Daily database backups.

## Project layout

```
config/            settings, urls
core/
  models.py        Pharmacy, Medicine, Inventory, Reservation, Payment, SearchLog, SmsLog
  services.py      search, reservation, cancellation and expiry logic (transactions + row locks)
  channels.py      USSD menu and incoming SMS commands
  momo.py          MTN MoMo Collections client (with simulation mode)
  analytics.py     KPIs and the shortage forecast
  sms.py           Africa's Talking SMS sender (never raises)
  i18n.py          English / Kinyarwanda strings
  ratelimit.py     per-IP rate limiting
  views_browse.py  directory, catalogue, help, stock alerts, sitemap
  signals.py       sends back-in-stock SMS when inventory is saved
  views.py, forms.py, admin.py, urls.py, tests.py
  templates/, static/core/css/site.css
  management/commands/  expire_reservations, seed_demo
```
