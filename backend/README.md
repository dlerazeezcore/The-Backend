# The Backend (FastAPI)

Backend services for The Book/The Backend product suite.

This README is code-verified against the current repository state on March 11, 2026.

## 1. Overview

This repository contains one primary FastAPI gateway and multiple standalone FastAPI apps.

The default production target is:

- `backend.gateway.app:app`

The standalone apps exist for domain-level split deployment when needed:

- Flights (`backend.flights.wings.api:create_app`)
- Passenger Database (`backend.passenger_database.api:create_app`)
- eSIMAccess (`backend.esim.esimaccess.api:create_app`)
- Corevia Email (`backend.communications.corevia_email.app:app`)

## 2. Architecture

| Area | Primary Files | Purpose |
| --- | --- | --- |
| Unified gateway | `backend/gateway/app.py`, `backend/app.py` | Main API surface and router composition |
| Auth | `backend/auth/api.py`, `backend/auth/service.py` | Main bearer-token auth and user management |
| Flights (Wings) | `backend/gateway/routers/flights.py`, `backend/flights/wings/*` | Availability + booking over Wings provider |
| eSIM unified | `backend/gateway/routers/esim.py`, `backend/esim/oasis/*`, `backend/esim/esimaccess/*` | Unified eSIM catalog, quote, order, and provider operations |
| eSIM app | `backend/gateway/routers/esim_app.py`, `backend/gateway/esim_app_store.py` | Consumer app flow with local token pattern |
| Payments (FIB) | `backend/gateway/routers/payments.py`, `backend/payments/fib/service.py` | FIB config + payment link creation |
| Notifications / Email | `backend/gateway/routers/notifications.py`, `backend/communications/corevia_email/*` | Email config and send operations |
| Permissions | `backend/gateway/routers/permissions.py`, `backend/gateway/permissions_store.py` | Service/API/provider toggles and schedules |
| Passenger database | `backend/passenger_database/*` | Profiles, members, member history |
| Pending | `backend/pending/*` | Manual fulfillment queue operations |
| Transactions | `backend/transactions/*` | Transaction history and query endpoints |
| Admin | `backend/admin/*` | Admin users, subscriptions, visa catalog, protected config endpoints |
| Persistence adapter | `backend/supabase/*` | Supabase-backed document store with local fallback |

## 3. Runtime Entry Points

Run from repo root (`/Users/laveencompany/Projects/The Backend`).

### Canonical unified gateway

```bash
uvicorn backend.gateway.app:app --host 0.0.0.0 --port 8000
```

### Compatibility alias

```bash
uvicorn backend.app:app --host 0.0.0.0 --port 8000
```

`backend/app.py` only re-exports `backend.gateway.app:app`.

### Standalone apps

```bash
uvicorn backend.flights.wings.app:app --host 0.0.0.0 --port 5050
uvicorn backend.passenger_database.app:app --host 0.0.0.0 --port 5060
uvicorn backend.esim.esimaccess.app:app --host 0.0.0.0 --port 5070
uvicorn backend.communications.corevia_email.app:app --host 0.0.0.0 --port 5080
```

## 4. Local Setup

### Prerequisites

- Python 3.11+ (project currently uses Python 3.12 in Docker)
- `pip`

### Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
```

### Quick verify

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/__build
curl http://127.0.0.1:8000/openapi.json
```

### Runtime dependencies

Dependencies are currently pinned in `backend/requirements.txt`:

- `fastapi==0.115.0`
- `uvicorn[standard]==0.30.6`
- `jinja2==3.1.4`
- `python-multipart==0.0.9`
- `requests==2.32.3`
- `httpx==0.27.0`
- `itsdangerous`
- `python-dotenv==1.0.1`

### Tests

There is currently no committed automated test suite in this repository.

## 5. Unified Gateway API (Categorized)

The unified gateway (`backend.gateway.app:app`) currently registers 150 routes, including hidden compatibility aliases.

### System

- `GET /health`
- `GET /__build`
- `GET /docs`
- `GET /openapi.json`
- `GET /redoc`

### Auth (`/api/auth/*`)

- `POST /api/auth/login`
- `POST /api/auth/signup`
- `POST /api/auth/forgot-password`
- `POST /api/auth/logout`
- `GET /api/auth/me`
- `GET /api/auth/users`
- `GET /api/auth/sub-users`
- `POST /api/auth/company-admin/sub-users`
- `PUT /api/auth/company-admin/sub-users/{sub_user_id}`
- `POST /api/auth/company-admin/sub-users/{sub_user_id}/toggle`
- `DELETE /api/auth/company-admin/sub-users/{sub_user_id}`
- `POST /api/auth/admin/users`
- `DELETE /api/auth/admin/users/{user_id}`

Hidden compatibility aliases exist:

- `POST /login`
- `POST /signup`
- `POST /forgot-password`
- `GET|POST /logout`

### Flights

- `POST /api/availability`
- `POST /api/book`

Runtime behavior notes:

- In unified gateway, these endpoints are not bearer-protected by middleware.
- They are still governed by OTA permissions/schedule logic.
- `POST /api/book` can return pending when ticketing is disabled by policy/schedule.

### eSIM Unified

Protected provider/admin config endpoints:

- `GET /api/other-apis/esim`
- `POST /api/other-apis/esim`
- `GET /api/other-apis/esim/ping`
- `POST /api/other-apis/esim/catalog-refresh`

Public/main product endpoints:

- `GET /api/esim/bundles`
- `GET /api/esim/countries-index`
- `POST /api/esim/quote`
- `POST /api/esim/orders`
- `GET /api/esim/orders`
- `GET /api/esim/orders/{order_id}`
- `GET /api/esim/balance`
- `GET /api/esim/settings`

Order action endpoints that require main auth token:

- `POST /api/esim/orders/{order_id}/cancel`
- `POST /api/esim/orders/{order_id}/refund`
- `POST /api/esim/orders/{order_id}/topup`

Hidden aliases are also registered under `/esim/api/*` and `/api/esim/access/*` legacy variants.

### eSIM App (`/api/esim-app/*`)

Auth and basic checks:

- `GET /api/esim-app/test-api`
- `POST /api/esim-app/signup`
- `POST /api/esim-app/login`

Super admin and users:

- `POST /api/esim-app/super-admin/check`
- `GET /api/esim-app/super-admin/list`
- `POST /api/esim-app/super-admin/add`
- `DELETE /api/esim-app/super-admin/remove`
- `GET /api/esim-app/users`
- `DELETE /api/esim-app/users/{user_id}`

Loyalty:

- `POST /api/esim-app/loyalty/grant`
- `GET /api/esim-app/loyalty/status`

Catalog and destination settings:

- `GET /api/esim-app/destinations`
- `GET /api/esim-app/countries`
- `GET /api/esim-app/destinations/popular`
- `POST /api/esim-app/destinations/popular`
- `DELETE /api/esim-app/destinations/popular`
- `GET /api/esim-app/countries/{country_code}/plans`
- `GET /api/esim-app/regions/{region_code}/plans`

App settings:

- `GET /api/esim-app/currency-settings/current`
- `POST /api/esim-app/currency-settings`
- `GET /api/esim-app/whitelist-settings/current`
- `POST /api/esim-app/whitelist-settings`
- `DELETE /api/esim-app/whitelist-settings`

Checkout and owned eSIM lifecycle:

- `GET /api/esim-app/my-esims`
- `POST /api/esim-app/fib/create-payment`
- `POST /api/esim-app/esims/{esim_id}/activate`
- `POST /api/esim-app/esims/{esim_id}/topup`
- `POST /api/esim-app/purchase/complete`
- `POST /api/esim-app/purchase/loyalty`

### Passenger Database

Primary endpoints:

- `GET /api/passenger-database/profiles`
- `GET /api/passenger-database/search`
- `GET /api/passenger-database/profiles/{profile_id}`
- `POST /api/passenger-database/profiles`
- `PUT /api/passenger-database/profiles/{profile_id}`
- `DELETE /api/passenger-database/profiles/{profile_id}`
- `POST /api/passenger-database/profiles/{profile_id}/members`
- `PUT /api/passenger-database/profiles/{profile_id}/members/{member_id}`
- `DELETE /api/passenger-database/profiles/{profile_id}/members/{member_id}`
- `GET /api/passenger-database/members/{member_id}/history`

Hidden legacy alias prefix:

- `/passenger-database/api/*`

Gateway owner resolution order for passenger DB routes:

1. Main bearer token owner
2. `owner_user_id` in JSON body
3. `owner_id` query param
4. `X-Owner-Id` header
5. `PASSENGER_DB_DEFAULT_OWNER_ID`

### Pending

- `GET /api/pending`
- `GET /api/pending/enriched`
- `POST /api/pending/{pending_id}/complete`
- `POST /api/pending/{pending_id}/reject`

All pending endpoints require main auth token and pending service access.

### Transactions

- `GET /api/transactions`
- `GET /api/transactions/{transaction_id}`
- `GET /api/transactions/by-pending/{pending_id}`

Supported filters on `GET /api/transactions`:

- `q`
- `status`
- `service`
- `pending_id`
- `limit`
- `offset`

All transaction endpoints require main auth token and transactions service access.

### Notifications / Email

Operational send endpoint:

- `POST /api/notify/email`

Protected config/test endpoints:

- `GET /api/other-apis/email`
- `POST /api/other-apis/email`
- `POST /api/other-apis/email/test-send`

### Payments / FIB

Protected config/checkout endpoints:

- `GET /api/other-apis/fib`
- `POST /api/other-apis/fib`
- `POST /api/other-apis/fib/create-payment`
- `GET /api/other-apis/fib/payments/{payment_id}/status`
- `POST /api/other-apis/fib/payments/{payment_id}/cancel`
- `POST /api/other-apis/fib/payments/{payment_id}/refund`

eSIM app checkout endpoint:

- `POST /api/esim-app/fib/create-payment`

Public callback/return handlers:

- `POST /fib/webhook`
- `GET|POST /fib/return`

Current backend FIB wrapper behavior:

- obtains OAuth token internally using stored `client_id` and `client_secret`
- calls `POST /protected/v1/payments` on FIB
- currently accepts only `amount` and `description` from callers
- currently sets `statusCallbackUrl` and `redirectUri` from `PUBLIC_BASE_URL`
- currently uses fixed defaults for `expiresIn` (`PT1H`), `category` (`ECOMMERCE`), and `refundableFor` (`PT48H`)

Official FIB docs reviewed (March 15, 2026):

- docs URL: `https://documenter.getpostman.com/view/30814842/2sB2j68V73`
- documented upstream flow: Authorization, Create Payment, Check Payment Status, Cancel Payment, Refund
- documented base URL (stage): `https://fib.stage.fib.iq`
- documented base URL (production): `https://fib.prod.fib.iq`

Missing in this backend compared with official FIB API:

- no caller-level support for optional create-payment fields from FIB docs (`statusCallbackUrl`, `redirectUri`, `expiresIn`, `refundableFor`, `category`)
- no standardized internal status lifecycle mapped from FIB statuses (`PAID`, `UNPAID`, `DECLINED`, refund progression)
- no non-`/api` alias route for create-payment (`/fib/create-payment`), so clients must use `/api/esim-app/fib/create-payment` or `/api/other-apis/fib/create-payment`

### Permissions

Protected endpoints:

- `GET /api/permissions`
- `POST /api/permissions`
- `GET /api/permissions/status`

### Admin / Back Office

User and sub-user admin:

- `GET /admin/users/api/list`
- `GET /admin/sub-users/api/list`

Subscriptions:

- `GET /admin/subscriptions/api/list`
- `POST /admin/subscriptions/api/addons/update`
- `POST /admin/subscriptions/api/grant`
- `POST /admin/subscriptions/api/{sub_id}/update`
- `POST /admin/subscriptions/api/{sub_id}/delete`
- `POST /subscriptions/api/assign`
- `GET /subscriptions/api/check`

Visa catalog:

- `GET /admin/visa-catalog/api/list`
- `POST /admin/visa-catalog/api/country/save`
- `POST /admin/visa-catalog/api/country/{country_id}/delete`
- `POST /admin/visa-catalog/api/type/save`
- `POST /admin/visa-catalog/api/type/{type_id}/delete`

## 6. Effective Security Notes (Important)

### Main auth

- `/api/auth/login` returns bearer tokens.
- Protected routes read `Authorization: Bearer <token>` or fallback `auth_token` cookie in some modules.

### Route duplication

Gateway includes both generic routers and admin router for some same paths:

- `/api/permissions*`
- `/api/other-apis/fib*`
- `/api/other-apis/email*`
- `/api/other-apis/esim*`
- `/api/esim/countries-index`

In practice:

- Config routes are super-admin protected in both implementations.
- `GET /api/esim/countries-index` is public via the eSIM router version registered earlier.

### eSIM app auth model (separate from main auth)

- Login/signup returns tokens like `local-<userId>`.
- Several routes derive user from `Authorization: Bearer local-<userId>`.
- Several admin routes rely on `adminPhone` payload/query style checks.
- Root admin phone is hardcoded in `backend/gateway/esim_app_store.py` as `+9647507343635`.
- Root admin password defaults to `StrongPass123` unless `ESIM_ROOT_ADMIN_PASSWORD` is set.

## 7. Standalone Apps: Scope and Differences

| App | Entrypoint | Port | Main Purpose | Auth/Security Difference |
| --- | --- | --- | --- | --- |
| Unified Gateway | `backend.gateway.app:app` | `8000` | All-in-one deployment | Mixed public/protected surface by route |
| Flights | `backend.flights.wings.app:app` | `5050` | Wings-only flow | `/api/availability` and `/api/book` require bearer auth and service/api access |
| Passenger DB | `backend.passenger_database.app:app` | `5060` | Passenger + pending + transactions | Middleware enforces auth for passenger DB prefixes |
| eSIMAccess | `backend.esim.esimaccess.app:app` | `5070` | Provider-oriented eSIM operations | eSIM endpoints require bearer auth and eSIM service access |
| Corevia Email | `backend.communications.corevia_email.app:app` | `5080` | Email config/send service | Config/test routes require super admin; send routes require auth |

Standalone-only signup/forgot-password public access is disabled by default using environment toggles.

## 8. Environment Variables (Categorized)

### Core runtime

- `BACKEND_CORS_ALLOW_ORIGINS`
- `BACKEND_CORS_ALLOW_ORIGIN_REGEX`
- `PUBLIC_BASE_URL`
- `ESIM_PREWARM_ON_STARTUP`

### Auth and session

- `AUTH_TOKEN_SECRET`
- `APP_SESSION_SECRET`
- `AUTH_TOKEN_MAX_AGE_SECONDS`
- `AUTH_STORE_LEGACY_PASSWORD`
- `AUTH_INCLUDE_TEMP_PASSWORD_IN_RESPONSE`

### Supabase

- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- `SUPABASE_SERVICE_KEY`
- `SUPABASE_ANON_KEY`
- `SUPABASE_STATE_TABLE`
- `SUPABASE_REQUIRED`
- `SUPABASE_TIMEOUT_SECONDS`

### Flights / Wings

- `WINGS_AUTH_TOKEN`
- `AUTH_TOKEN`
- `WINGS_BASE_URL`
- `SEARCH_URL`
- `BOOK_URL`
- `WINGS_SEARCH_URL`
- `WINGS_BOOK_URL`

### eSIM Oasis

- `ESIM_OASIS_KEY_ID`
- `ESIM_OASIS_SECRET`
- `ESIM_OASIS_BASE_URL`

### eSIMAccess

- `ESIMACCESS_ACCESS_CODE`
- `ESIM_ACCESS_CODE`
- `ESIMACCESS_SECRET_KEY`
- `ESIM_SECRET_KEY`
- `ESIMACCESS_BASE_URL`
- `ESIMACCESS_DEFAULT_SMDP`
- `ESIMACCESS_PRICE_DIVISOR`
- `ESIMACCESS_USE_SIGNATURE`
- `ESIMACCESS_TIMEOUT_SEC`

### FIB payments

- `FIB_BASE_URL`
- `FIB_CLIENT_ID`
- `FIB_CLIENT_SECRET`

### Email / SMTP

- `SMTP_URL`
- `SMTP_USER`
- `SMTP_PASS`
- `SMTP_FROM`
- `SMTP_FROM_NAME`
- `SMTP_REPLY_TO`
- `SMTP_STARTTLS`
- `SMTP_TIMEOUT`

### WhatsApp

- `WHATSAPP_WEBHOOK_URL`
- `WHATSAPP_ACCESS_TOKEN`
- `WHATSAPP_PHONE_NUMBER_ID`
- `WHATSAPP_API_VERSION`

### Passenger database

- `PASSENGER_DB_DEFAULT_OWNER_ID`

### eSIM app

- `ESIM_ROOT_ADMIN_PASSWORD`

### Standalone public-signup toggles (default off)

- `PASSENGER_DB_ALLOW_PUBLIC_SIGNUP`
- `COREVIA_EMAIL_ALLOW_PUBLIC_SIGNUP`
- `ESIMACCESS_ALLOW_PUBLIC_SIGNUP`
- `FLIGHTS_ALLOW_PUBLIC_SIGNUP`

### Standalone forgot-password toggles (default off)

- `PASSENGER_DB_ALLOW_PUBLIC_FORGOT_PASSWORD`
- `COREVIA_EMAIL_ALLOW_PUBLIC_FORGOT_PASSWORD`
- `ESIMACCESS_ALLOW_PUBLIC_FORGOT_PASSWORD`
- `FLIGHTS_ALLOW_PUBLIC_FORGOT_PASSWORD`

## 9. Persistence and Data Model

The backend uses Supabase document storage with local JSON fallback.

### Supabase-backed document keys

| Doc key | Owned by | Local fallback |
| --- | --- | --- |
| `users` | `backend/auth/service.py` via `supabase/auth/users_repo.py` | `backend/data/users.json` |
| `permissions` | `backend/gateway/permissions_store.py` | `backend/gateway/permissions.json` |
| `fib_config` | `backend/payments/fib/service.py` | `backend/payments/fib/config.json` |
| `email_config` | `backend/communications/corevia_email/service.py` | `backend/communications/corevia_email/config.json` |
| `esim_oasis_config` | `backend/esim/oasis/service.py` | `backend/esim/oasis/config.json` |
| `esim_app_store` | `backend/gateway/esim_app_store.py` | `backend/data/esim_app_store.json` |
| `pending` | `backend/pending/store.py` | `backend/data/pending.json` |
| `transactions` | `backend/transactions/store.py` | `backend/data/transactions.json` |
| `passenger_profiles` | `backend/passenger_database/service.py` | `backend/data/passenger_db/profiles.json` |
| `passenger_history` | `backend/passenger_database/service.py` | `backend/data/passenger_db/history.json` |
| `subscriptions` | `backend/admin/subscriptions.py` | `backend/data/subscriptions.json` |
| `addons` | `backend/admin/subscriptions.py` | `backend/data/addons.json` |
| `esimaccess_orders` | `backend/esim/esimaccess/store.py` | `backend/data/esimaccess_orders.json` |
| `esim_orders` (legacy read fallback) | `backend/esim/esimaccess/store.py` | `backend/data/esim_orders.json` |
| `visa_catalog` | `backend/admin/service.py` | `backend/data/visa_catalog.json` |

Additional cache files:

- eSIM merged catalog cache: `backend/esim/oasis/catalog_cache.json`

## 10. CORS Defaults

If not overridden by env, these origins are allowed:

- `http://localhost:5173`
- `http://localhost:3000`
- `http://localhost:4173`
- `http://127.0.0.1:5173`
- `http://127.0.0.1:3000`
- `http://127.0.0.1:4173`

Default regex includes:

- `https://.*\.figmaiframepreview\.figma\.site`

## 11. Docker

The Dockerfile is `backend/Dockerfile` and expects build context at repo root.

### Build

```bash
docker build -f backend/Dockerfile -t the-backend .
```

### Run

```bash
docker run --rm -p 8000:8000 --env-file .env the-backend
```

Container command:

- `uvicorn backend.gateway.app:app --host 0.0.0.0 --port ${PORT:-8000}`

## 12. Operational Notes

### Health semantics

- Unified gateway `/health` returns `ok: false` when Wings credentials are missing.
- Flights standalone behaves similarly.
- Passenger DB, eSIMAccess, and Email health checks report service-specific status.

### Gateway startup behavior

- Loads `.env` from detected project root.
- Performs Wings configuration warning check.
- Optionally prewarms eSIM cache if `ESIM_PREWARM_ON_STARTUP` is truthy.

### Permissions-driven behavior

- Flights booking can downgrade to pending when ticketing is disabled by schedule/policy.
- eSIM order flow can return pending/manual mode based on provider policy (`sellable_mode` and schedule).
- FIB and Email can be marked offline/disabled via permissions API.

## 13. Security Hardening Checklist

- Set `AUTH_TOKEN_SECRET` (or `APP_SESSION_SECRET`) to a strong value.
- Set `ESIM_ROOT_ADMIN_PASSWORD` and rotate it from default.
- Review hardcoded eSIM app root admin phone handling.
- Keep standalone public signup/forgot-password toggles disabled unless explicitly needed.
- Restrict CORS to known frontend origins.
- Use `SUPABASE_REQUIRED=true` in production if local fallback is not acceptable.
- Confirm which gateway routes are intentionally public before internet exposure.

## 14. Troubleshooting

- `404 /api/esim/access/settings` on unified gateway is expected. That route exists on standalone eSIMAccess app, not unified gateway.
- `401` means auth missing/invalid; `403` means authenticated but forbidden by role/service/api policy.
- `503` on FIB/Email/eSIM often indicates permissions schedule/manual-mode restrictions or missing provider config.
- Gateway `/health` being false usually indicates missing Wings config (`WINGS_AUTH_TOKEN` and endpoint/base URL settings).

## 15. Pre-Deployment Checklist

- Start `backend.gateway.app:app`.
- Verify `/health`, `/__build`, `/docs`, `/openapi.json`.
- Verify `/api/auth/login` with a real account.
- Verify required providers are configured (Wings, eSIM, FIB, SMTP).
- Verify `SUPABASE_*` values and document table availability.
- Verify CORS for production frontend origin.
- Verify only intended public routes are accessible without bearer auth.
