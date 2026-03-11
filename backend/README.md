# The Book Backend

This folder contains the backend platform for The Book.

It is centered around one unified FastAPI gateway and a small set of standalone FastAPI apps that can still be deployed separately when needed. The code is organized by domain under `backend/<domain>/...`, with provider-specific integrations nested inside each domain.

This README was verified against the code-level FastAPI route registration on March 8, 2026.

## Quick Handoff For Other AIs

This section is written so another Codex session can start from this README alone.

That other session should still verify the live deployment, but it should not need extra explanation from the user if it has either repo access, Playwright access to the frontend, or both.

### Zero-Context AI Runbook

1. Start with the default assumption that the intended backend is the unified gateway: `backend.gateway.app:app`.
2. Discover the real backend base URL from frontend config, runtime config, or Playwright network traffic before changing frontend code.
3. Verify the target backend with `GET /__build`, `GET /health`, and `GET /openapi.json`.
4. Treat the live OpenAPI schema and observed network traffic as higher priority than assumptions from route names.
5. Use `/api/auth/*` as the default auth system unless the frontend is clearly the separate eSIM consumer app.
6. Use `/api/esim-app/*` only for the separate eSIM consumer app flow.
7. Treat `/api/other-apis/*` and `/api/permissions*` as admin/config routes, not as normal product APIs.
8. Do not guess payload shapes when `/docs` or `/openapi.json` is available.

### Backend Discovery Workflow

If the backend base URL is not explicitly provided:

1. Search frontend code for config keys like `API`, `BASE_URL`, `BACKEND`, `VITE_`, `NEXT_PUBLIC_`, `PUBLIC_`, `axios.create`, and `fetch(`.
2. If a live frontend is available, inspect Playwright network traffic to capture the real API host.
3. If running locally and nothing overrides it, assume `http://127.0.0.1:8000`.
4. Probe the candidate backend with:
   - `GET <base>/__build`
   - `GET <base>/health`
   - `GET <base>/openapi.json`

### Route-Family Decision Rules

Use these rules to avoid mixing up the unified gateway and standalone apps:

- If `GET /api/esim/settings` exists, you are talking to the unified gateway eSIM surface.
- If `GET /api/esim/access/settings` exists, you are talking to the standalone eSIMAccess app.
- If `/api/esim/access/settings` returns `404`, that usually means the request hit the unified gateway, not that auth failed.
- If the response code is `401` or `403`, the route exists and auth is the issue.
- If `/openapi.json` includes `/api/pending*`, `/api/transactions*`, or `/admin/*`, you are almost certainly on the unified gateway.

### One-File Prompt For Another Codex Session

If you want to hand this backend to another Codex session using only this README, use a prompt like this:

```text
Read /Users/laveencompany/Projects/The Book/backend/README.md first and follow it as the backend discovery runbook.

Then:
- inspect the frontend with Playwright
- discover the real backend base URL from code or network traffic
- verify the live backend with /__build, /health, and /openapi.json
- assume the unified gateway unless the live contract proves a standalone app is deployed
- use live OpenAPI shapes instead of guessing payloads
- only use /api/esim-app/* if the frontend is the separate eSIM consumer app
- treat /api/other-apis/* and /api/permissions* as admin/config routes
```

The most important fixed facts in this repo:

- canonical deployment target: `backend.gateway.app:app`
- unified docs: `/docs`
- raw OpenAPI: `/openapi.json`
- health check: `/health`
- build info: `/__build`
- primary auth system: `/api/auth/*` with Bearer token
- separate eSIM consumer app auth exists under `/api/esim-app/*`

## Canonical Deployment

Use the unified gateway as the default deployment target:

```bash
uvicorn backend.gateway.app:app --host 0.0.0.0 --port 8000
```

Compatibility alias:

```bash
uvicorn backend.app:app --host 0.0.0.0 --port 8000
```

`backend/app.py` is intentionally only a compatibility alias to `backend.gateway.app`. The canonical router wiring lives in `backend/gateway/app.py`.

Backend-local deployment files:

- `backend/Dockerfile`
- `backend/requirements.txt`

## Backend Inventory

| Area | Kind | Entrypoint / Location | Exposed Prefixes | Notes |
| --- | --- | --- | --- | --- |
| Unified gateway | FastAPI app | `backend.gateway.app:app` | mixed `/api/*` | Primary production entrypoint |
| Auth | Gateway router | `backend/auth/api.py` | `/api/auth/*` | Main company-admin, sub-user, super-admin auth |
| Flights / Wings | Gateway router + standalone app | `backend/gateway/routers/flights.py`, `backend.flights.wings.app:app` | `/api/availability`, `/api/book` | Wings provider integration |
| eSIM unified | Gateway router | `backend/gateway/routers/esim.py` | `/api/esim/*`, `/api/other-apis/esim*` | Aggregates Oasis + eSIMAccess behavior for the unified backend |
| eSIM app | Gateway router | `backend/gateway/routers/esim_app.py` | `/api/esim-app/*` | Separate mobile/app-style user flow and local token model |
| Payments / FIB | Gateway router + service | `backend/gateway/routers/payments.py`, `backend/payments/fib/service.py` | `/api/other-apis/fib*`, `/api/esim-app/fib/create-payment` | Admin FIB config plus checkout payment-link creation |
| Passenger database | Gateway router + standalone app | `backend/passenger_database/api.py`, `backend.passenger_database.app:app` | `/api/passenger-database/*` | Profiles, members, history |
| Pending | Gateway router | `backend/pending/api.py` | `/api/pending*` | Manual completion / rejection queue |
| Transactions | Gateway router | `backend/transactions/api.py` | `/api/transactions*` | Reporting and transaction detail |
| Admin panel | Gateway router | `backend/admin/api.py` | `/admin/*`, `/subscriptions/api/assign` | Users, subscriptions, visa catalog, protected config routes |
| Notifications / email | Gateway router + standalone app | `backend/gateway/routers/notifications.py`, `backend.communications.corevia_email.app:app` | `/api/notify/email`, `/api/other-apis/email*` | Email send/config/test |
| Permissions | Gateway router | `backend/gateway/routers/permissions.py` | `/api/permissions*` | Feature and provider toggle state |
| eSIMAccess provider app | Standalone FastAPI app | `backend.esim.esimaccess.app:app` | `/api/esim/access/*`, `/api/esim/*` | Full provider-facing eSIMAccess surface |
| eSIM Oasis provider | Internal provider module | `backend/esim/oasis/service.py` | internal | Used by the unified eSIM router |
| Twilio / WhatsApp | Internal helper module | `backend/communications/twilio_whatsapp/service.py` | internal | Used for manual-mode notifications, not mounted as HTTP routes |
| Supabase adapter | Internal persistence layer | `backend/supabase/*` | internal | Canonical document-style persistence for backend state |

## Unified Gateway Route Map

The unified gateway currently exposes the following route families.

### System

- `GET /health`
  Gateway health. `ok` depends on Wings configuration being present.
- `GET /__build`
  Build metadata for the running gateway.
- `GET /docs`
  Swagger UI for the unified API.
- `GET /openapi.json`
  Raw OpenAPI schema for codegen or AI-assisted integration.
- `GET /redoc`
  ReDoc view of the schema.

### Auth

Public auth endpoints:

- `POST /api/auth/login`
- `POST /api/auth/signup`
- `POST /api/auth/forgot-password`

Protected auth endpoints:

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

Hidden compatibility aliases still exist, but are not included in OpenAPI:

- `POST /login`
- `POST /signup`
- `POST /forgot-password`
- `GET|POST /logout`

### Flights / Wings

- `POST /api/availability`
  Search available itineraries against Wings.
- `POST /api/book`
  Create a Wings booking from selected itinerary data.

Current gateway behavior: these endpoints are public and do not require the main Bearer auth token.

### eSIM Unified API

Public catalog and order endpoints:

- `GET /api/esim/bundles`
- `GET /api/esim/countries-index`
- `POST /api/esim/quote`
- `POST /api/esim/orders`
- `GET /api/esim/orders`
- `GET /api/esim/orders/{order_id}`
- `GET /api/esim/balance`
- `GET /api/esim/settings`

Provider config and operational endpoints:

- `GET /api/other-apis/esim`
- `POST /api/other-apis/esim`
- `GET /api/other-apis/esim/ping`
- `POST /api/other-apis/esim/catalog-refresh`

These `/api/other-apis/esim*` routes now require a main-auth super-admin Bearer token.

What this router does:

- merges Oasis and eSIMAccess catalog data
- applies admin/provider permission policies
- exposes a simplified order surface for the unified backend
- falls back to pending/manual mode when provider selling is offline or scheduled off

### eSIM Consumer App API

Auth and identity:

- `GET /api/esim-app/test-api`
- `POST /api/esim-app/signup`
- `POST /api/esim-app/login`

Super-admin controls:

- `POST /api/esim-app/super-admin/check`
- `GET /api/esim-app/super-admin/list`
- `POST /api/esim-app/super-admin/add`
- `DELETE /api/esim-app/super-admin/remove`

User and loyalty controls:

- `GET /api/esim-app/users`
- `DELETE /api/esim-app/users/{user_id}`
- `POST /api/esim-app/loyalty/grant`
- `GET /api/esim-app/loyalty/status`

Catalog and merchandising:

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

Owned eSIM lifecycle:

- `GET /api/esim-app/my-esims`
- `POST /api/esim-app/fib/create-payment`
- `POST /api/esim-app/esims/{esim_id}/activate`
- `POST /api/esim-app/esims/{esim_id}/topup`
- `POST /api/esim-app/purchase/complete`
- `POST /api/esim-app/purchase/loyalty`

This is not the same auth system as `/api/auth/*`. It uses a lightweight local-token flow where successful login/signup returns a token shaped like `local-<userId>`. Some routes also accept `userId` or `adminPhone` directly.

### Passenger Database

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

Legacy hidden prefix also exists:

- `/passenger-database/api/*`

Owner resolution works in this order:

1. Bearer token from the main auth system
2. `X-Owner-Id` header
3. `owner_id` query parameter
4. `owner_user_id` in JSON body
5. `PASSENGER_DB_DEFAULT_OWNER_ID`

### Pending

- `GET /api/pending`
- `GET /api/pending/enriched`
- `POST /api/pending/{pending_id}/complete`
- `POST /api/pending/{pending_id}/reject`

These endpoints require a valid main Bearer token and the account must have the pending service enabled.

### Transactions

- `GET /api/transactions`
- `GET /api/transactions/{transaction_id}`
- `GET /api/transactions/by-pending/{pending_id}`

`/api/transactions` supports query filters:

- `q`
- `status`
- `service`
- `pending_id`
- `limit`
- `offset`

These endpoints require a valid main Bearer token and the account must have the transactions service enabled.

### Notifications / Email

- `POST /api/notify/email`
- `GET /api/other-apis/email`
- `POST /api/other-apis/email`
- `POST /api/other-apis/email/test-send`

`/api/notify/email` is the operational send endpoint. The `/api/other-apis/email*` config/test routes now require a main-auth super-admin Bearer token.

### Payments / FIB

- `GET /api/other-apis/fib`
- `POST /api/other-apis/fib`
- `POST /api/other-apis/fib/create-payment`

These `/api/other-apis/fib*` routes now require a main-auth super-admin Bearer token.

For the separate eSIM consumer app checkout flow, use:

- `POST /api/esim-app/fib/create-payment`

### Permissions

- `GET /api/permissions`
- `POST /api/permissions`
- `GET /api/permissions/status`

This route family stores and reports service toggles, provider toggles, schedule state, and ticketing behavior. It now requires a main-auth super-admin Bearer token.

### Admin Panel / Back Office

Protected admin endpoints:

- `GET /admin/users/api/list`
- `GET /admin/sub-users/api/list`
- `GET /admin/subscriptions/api/list`
- `POST /admin/subscriptions/api/addons/update`
- `POST /admin/subscriptions/api/grant`
- `POST /admin/subscriptions/api/{sub_id}/update`
- `POST /admin/subscriptions/api/{sub_id}/delete`
- `POST /subscriptions/api/assign`
- `GET /admin/visa-catalog/api/list`
- `POST /admin/visa-catalog/api/country/save`
- `POST /admin/visa-catalog/api/country/{country_id}/delete`
- `POST /admin/visa-catalog/api/type/save`
- `POST /admin/visa-catalog/api/type/{type_id}/delete`

The admin router also defines protected versions of:

- `/api/permissions`
- `/api/permissions/status`
- `/api/other-apis/fib`
- `/api/other-apis/fib/create-payment`
- `/api/other-apis/email`
- `/api/other-apis/email/test-send`
- `/api/other-apis/esim`
- `/api/other-apis/esim/ping`
- `/api/esim/countries-index`

However, see the "Important Current Reality" section below for the actual effective behavior in the unified gateway.

## Standalone Apps

These apps still exist and can be deployed separately when a domain needs its own process.

### Unified gateway

```bash
uvicorn backend.gateway.app:app --reload --host 0.0.0.0 --port 8000
```

### Flights / Wings

```bash
uvicorn backend.flights.wings.app:app --reload --host 0.0.0.0 --port 5050
```

Exposes:

- `/api/auth/*`
- `POST /api/availability`
- `POST /api/book`
- `GET /health`
- `GET /__build`

Security model:

- `POST /api/availability` and `POST /api/book` now require a main-auth Bearer token
- user access is also checked against `service_access.flights` and `api_access.ota`
- public signup is disabled by default for this standalone service

### Passenger Database

```bash
uvicorn backend.passenger_database.app:app --reload --host 0.0.0.0 --port 5060
```

Exposes:

- full `/api/auth/*` auth surface
- full `/api/passenger-database/*` surface
- pending routes
- transaction routes
- hidden `/passenger-database/api/*` aliases
- `GET /health`
- `GET /__build`

Security model:

- passenger-database CRUD routes now require a main-auth Bearer token in the standalone app
- standalone requests can no longer bypass auth by supplying `owner_id`, `owner_user_id`, or `X-Owner-Id` alone
- public signup is disabled by default for this standalone service

### eSIMAccess Provider App

```bash
uvicorn backend.esim.esimaccess.app:app --reload --host 0.0.0.0 --port 5070
```

Exposes:

- full `/api/auth/*` auth surface
- eSIMAccess-specific provider endpoints under `/api/esim/access/*`
- alias endpoints under `/api/esim/*`
- hidden legacy aliases under `/esim/api/*`
- `GET /reports/esim/api/list`
- `GET /health`
- `GET /__build`

Security model:

- provider routes already require a main-auth Bearer token
- public signup is now disabled by default for this standalone service

The standalone eSIMAccess app has more provider-control endpoints than the unified gateway, including:

- package listing
- region lookup
- topup options and execution
- usage lookup
- cancel / suspend / unsuspend / revoke
- SMS send
- provider webhook registration
- refund and order-level topup / cancel

### Corevia Email

```bash
uvicorn backend.communications.corevia_email.app:app --reload --host 0.0.0.0 --port 5080
```

Exposes:

- `POST /api/auth/login`
- `GET /api/auth/me`
- `GET /api/other-apis/email`
- `POST /api/other-apis/email`
- `POST /api/other-apis/email/test-send`
- `POST /api/notify/email`
- `GET /api/email/config`
- `POST /api/email/config`
- `POST /api/email/test-send`
- `POST /api/email/send`
- `GET /health`
- `GET /__build`

This standalone app can also mount a colocated frontend if `frontend/dist/index.html` or `frontend/index.html` exists in the email app folder.

Security model:

- config and test routes are now super-admin only
- send routes now require a main-auth Bearer token
- public signup is disabled by default for this standalone service

## Auth And Access Model

### Main auth system

The primary B2B/admin auth system is `/api/auth/*`.

Typical flow:

1. `POST /api/auth/login`
2. store the returned bearer token
3. send `Authorization: Bearer <token>` on protected requests

Example:

```bash
curl -X POST "https://<backend>/api/auth/login" \
  -H "Content-Type: application/json" \
  -d '{
    "email": "dler@corevia-consultants.com",
    "password": "StrongPass123"
  }'
```

Example authenticated calls:

```bash
curl "https://<backend>/api/auth/me" \
  -H "Authorization: Bearer <token>"
```

```bash
curl "https://<backend>/api/passenger-database/profiles" \
  -H "Authorization: Bearer <token>"
```

```bash
curl "https://<backend>/api/pending" \
  -H "Authorization: Bearer <token>"
```

```bash
curl "https://<backend>/api/transactions?limit=20&offset=0" \
  -H "Authorization: Bearer <token>"
```

Roles in the main auth system:

- `super_admin`
- `company_admin`
- `sub_user`

Service access is enforced for some domains, especially:

- pending
- transactions
- sub-user service visibility

### eSIM app auth system

The `/api/esim-app/*` surface is separate from the main auth system.

Important current behavior:

- signup/login return a local token shaped like `local-<userId>`
- some routes read the user from `Authorization: Bearer local-<userId>`
- some routes accept `userId` directly
- super-admin routes use `adminPhone`
- the root admin phone is hardcoded in `backend/gateway/esim_app_store.py`
- the root admin password comes from `ESIM_ROOT_ADMIN_PASSWORD` and defaults to `StrongPass123`

If you are building a UI against `/api/esim-app/*`, treat it as a separate auth product, not as a variant of `/api/auth/*`.

## Frontend Integration Guidance

For most web frontends, use one backend base URL and let the frontend call the unified gateway.

Recommended integration pattern:

1. Use `/docs` or `/openapi.json` as the contract source.
2. Use `/api/auth/login` for B2B/admin flows.
3. Use the returned bearer token for protected domains.
4. Use `/api/passenger-database/*`, `/api/pending*`, `/api/transactions*`, and `/admin/*` for dashboard-like product areas.
5. Use `/api/availability` and `/api/book` for flight search and booking.
6. Use `/api/esim/*` for the unified eSIM catalog/order flow.
7. Use `/api/esim-app/*` only if you are building the separate eSIM consumer app experience.
8. Use `/api/esim-app/fib/create-payment` for consumer-app FIB checkout, not `/api/other-apis/fib/create-payment`.
9. If an existing frontend path returns `404`, check `/openapi.json` before rewriting business logic.
10. In this repo, `404 /api/esim/access/settings` usually means the frontend is talking to the unified gateway and should use `/api/esim/settings` instead.
11. For Playwright-driven fixes, inspect the actual request URLs first and then align the frontend to the deployed backend contract.

If you are handing this backend to another AI and want to give it only this README, give it a prompt shaped like this:

```text
Read /Users/laveencompany/Projects/The Book/backend/README.md first.
Use Playwright to inspect the frontend, discover the backend base URL from network/config, and verify the live contract with /openapi.json before editing anything.
Default to the unified gateway unless the deployed API proves a standalone app is being used.
```

## Important Current Reality

These points are based on the actual route registration and test calls against the FastAPI app, not on naming intent.

### Sensitive config routes are now locked down

In the unified gateway, these paths are registered by both the plain gateway routers and the protected admin router:

- `/api/permissions`
- `/api/permissions/status`
- `/api/other-apis/fib`
- `/api/other-apis/fib/create-payment`
- `/api/other-apis/email`
- `/api/other-apis/email/test-send`
- `/api/other-apis/esim`
- `/api/other-apis/esim/ping`

The gateway router versions of those config paths now enforce the same main-auth super-admin requirement as the admin router.

That means:

- unauthenticated callers now receive `401`
- authenticated non-super-admin callers receive `403`
- the duplicate admin-tagged routes still exist in code, but the earlier gateway routes are no longer public

The public eSIM catalog route `GET /api/esim/countries-index` remains intentionally public.

For public eSIM-app checkout, use `POST /api/esim-app/fib/create-payment`.

### Public gateway surfaces still include product APIs

Today, the unified gateway allows direct unauthenticated access to:

- flights search and booking
- unified eSIM catalog and ordering routes
- eSIM app routes

This README documents the current behavior so integrators do not build on false assumptions.

## Environment Variables

### Core / shared

- `BACKEND_CORS_ALLOW_ORIGINS`
- `BACKEND_CORS_ALLOW_ORIGIN_REGEX`
- `PUBLIC_BASE_URL`

Default CORS already allows:

- `http://localhost:3000`
- `http://localhost:4173`
- `http://localhost:5173`
- `http://127.0.0.1:3000`
- `http://127.0.0.1:4173`
- `http://127.0.0.1:5173`

### Auth / session

- `AUTH_TOKEN_SECRET`
- `APP_SESSION_SECRET`
- `AUTH_TOKEN_MAX_AGE_SECONDS`
- `AUTH_STORE_LEGACY_PASSWORD`
- `AUTH_INCLUDE_TEMP_PASSWORD_IN_RESPONSE`

`AUTH_TOKEN_SECRET` is the primary secret. `APP_SESSION_SECRET` is a fallback.

### Supabase

- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- `SUPABASE_SERVICE_KEY`
- `SUPABASE_ANON_KEY`
- `SUPABASE_STATE_TABLE`
- `SUPABASE_REQUIRED`
- `SUPABASE_TIMEOUT_SECONDS`

See also `backend/supabase/README.md`.

### Flights / Wings

- `WINGS_AUTH_TOKEN`
- `AUTH_TOKEN`
- `WINGS_BASE_URL`
- `SEARCH_URL`
- `BOOK_URL`
- `WINGS_SEARCH_URL`
- `WINGS_BOOK_URL`

Wings config resolution supports either a base URL or full search/book endpoint URLs.

### eSIM unified / Oasis

- `ESIM_OASIS_KEY_ID`
- `ESIM_OASIS_SECRET`
- `ESIM_OASIS_BASE_URL`
- `ESIM_PREWARM_ON_STARTUP`

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

### Payments / FIB

- `FIB_BASE_URL`
- `FIB_CLIENT_ID`
- `FIB_CLIENT_SECRET`

The gateway and admin APIs can also persist FIB accounts/config in backend state instead of relying only on env vars.

### Email / SMTP

- `SMTP_URL`
- `SMTP_USER`
- `SMTP_PASS`
- `SMTP_FROM`
- `SMTP_FROM_NAME`
- `SMTP_REPLY_TO`
- `SMTP_STARTTLS`
- `SMTP_TIMEOUT`

Email accounts/config can also be persisted through the API.

### WhatsApp

- `WHATSAPP_WEBHOOK_URL`
- `WHATSAPP_ACCESS_TOKEN`
- `WHATSAPP_PHONE_NUMBER_ID`
- `WHATSAPP_API_VERSION`

These are used by the internal WhatsApp helper for notifications and manual-mode workflows.

### Passenger database

- `PASSENGER_DB_DEFAULT_OWNER_ID`

### eSIM app

- `ESIM_ROOT_ADMIN_PASSWORD`

### Standalone signup toggles

- `PASSENGER_DB_ALLOW_PUBLIC_SIGNUP`
- `COREVIA_EMAIL_ALLOW_PUBLIC_SIGNUP`
- `ESIMACCESS_ALLOW_PUBLIC_SIGNUP`
- `FLIGHTS_ALLOW_PUBLIC_SIGNUP`

All of these default to disabled. Set one explicitly only if you intentionally want internet-facing public registration on that standalone service.

### Standalone password-reset toggles

- `PASSENGER_DB_ALLOW_PUBLIC_FORGOT_PASSWORD`
- `COREVIA_EMAIL_ALLOW_PUBLIC_FORGOT_PASSWORD`
- `ESIMACCESS_ALLOW_PUBLIC_FORGOT_PASSWORD`
- `FLIGHTS_ALLOW_PUBLIC_FORGOT_PASSWORD`

All of these also default to disabled. Set one explicitly only if you intentionally want anonymous password-reset requests exposed on that standalone service.

## Storage Model

This backend is not purely stateless.

Current storage layers:

- `backend/supabase/*`
  Canonical document-style persistence adapter for backend state.
- `backend/data/*`
  Local JSON fallback or legacy local state for development and compatibility.
- provider-local config files
  Some provider modules still support colocated JSON config files as fallback.

Important current reality:

- backend business logic is organized under `backend/`
- persistence is moving toward `backend/supabase/*`
- several modules still keep local JSON compatibility for development and fallback

Examples of persisted state managed this way:

- users
- passenger profiles and history
- pending items
- transactions
- FIB config
- email config
- eSIM config and cached catalog state
- eSIM app store data

## Local Development

Run from the repository root, not from inside a nested module folder.

Unified backend:

```bash
uvicorn backend.gateway.app:app --reload --host 0.0.0.0 --port 8000
```

Standalone apps:

```bash
uvicorn backend.flights.wings.app:app --reload --host 0.0.0.0 --port 5050
uvicorn backend.passenger_database.app:app --reload --host 0.0.0.0 --port 5060
uvicorn backend.esim.esimaccess.app:app --reload --host 0.0.0.0 --port 5070
uvicorn backend.communications.corevia_email.app:app --reload --host 0.0.0.0 --port 5080
```

Quick verification:

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/__build
open http://127.0.0.1:8000/docs
```

Quick OpenAPI export:

```bash
curl http://127.0.0.1:8000/openapi.json
```

## Deployment Guidance

For Koyeb or a similar platform, prefer one unified service first:

```bash
uvicorn backend.gateway.app:app --host 0.0.0.0 --port ${PORT:-8000}
```

If deploying with Docker, use:

```text
backend/Dockerfile
```

Recommended default:

- one service
- one base URL
- one OpenAPI surface
- one main auth system
- separate provider folders internally

Split deployments only when there is a real operational reason:

- different scaling profile
- different secret boundary
- different uptime requirement
- different team ownership

## Architecture Rules For Future Development

Use these rules if you want changes to stay maintainable:

- add new business areas as `backend/<domain>`
- add provider-specific integrations as `backend/<domain>/<provider>`
- keep the unified deployment wiring in `backend/gateway/app.py`
- keep `backend/app.py` as compatibility only
- keep cross-domain bootstrap code in `backend/core`
- keep route files thin and push business logic into service/store modules
- do not recreate a generic catch-all `services/` bucket
- if a provider needs standalone deployment, add a local `app.py` under that provider folder

Recommended naming pattern:

- payments: `backend/payments/fib`, future `backend/payments/qi`
- flights: `backend/flights/wings`
- eSIM: `backend/esim/esimaccess`, `backend/esim/oasis`
- communications: `backend/communications/corevia_email`, `backend/communications/twilio_whatsapp`

## Final Checklist Before A Frontend Connects

- deploy `backend.gateway.app:app`
- verify `/health`
- verify `/docs`
- verify `/openapi.json`
- confirm `/api/auth/login` works with a real test account
- decide whether the frontend uses the main auth system, the eSIM app auth system, or both
- confirm CORS allows the frontend origin
- confirm provider credentials are configured
- confirm Supabase env vars are present if production persistence is required
- confirm only intentionally public product routes are exposed without auth in your deployment
