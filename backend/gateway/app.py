from __future__ import annotations

import os

from fastapi import FastAPI
from starlette.concurrency import run_in_threadpool

from backend.admin.api import create_router as create_admin_router
from backend.auth.api import create_auth_compat_router, create_auth_router
from backend.core.runtime import configure_cors, load_project_env
from backend.pending.api import create_router as create_pending_router
from backend.passenger_database.api import create_router as create_database_router
from backend.transactions.api import create_router as create_transactions_router
from backend.flights.wings.services.wings_client import get_client_from_env
from backend.gateway.flights_utils import _wings_config_missing
from backend.gateway.routers import (
    esim_router,
    esim_app_router,
    flights_router,
    notifications_router,
    payments_router,
    permissions_router,
    telegram_support_router,
)

load_project_env(__file__)

BUILD_ID = "backend-live-wings-fix-v2"

app = FastAPI(title="The Book Backend (API only)", version="1.0.0")
configure_cors(app)


# Routers
app.include_router(notifications_router)
app.include_router(permissions_router)
app.include_router(flights_router)
app.include_router(payments_router)
app.include_router(esim_router)
app.include_router(esim_app_router)
app.include_router(telegram_support_router)
app.include_router(create_auth_router(), prefix="/api/auth")
app.include_router(create_auth_compat_router())
app.include_router(create_pending_router())
app.include_router(create_transactions_router())
app.include_router(create_admin_router())

database_router = create_database_router()
app.include_router(database_router, prefix="/api/passenger-database")
app.include_router(database_router, prefix="/passenger-database/api", include_in_schema=False)


@app.on_event("startup")
async def _startup_check():
    # Fail fast (so you don’t get “mystery 500” later)
    client = get_client_from_env()
    if not client or _wings_config_missing():
        # We don't crash the server hard; we just make it explicit in logs/health.
        # But you can change this to raise RuntimeError(...) if you prefer hard-fail.
        print(
            "WARNING: WINGS credentials not configured. "
            "Set WINGS_AUTH_TOKEN (or AUTH_TOKEN) and optionally WINGS_BASE_URL/SEARCH_URL/BOOK_URL."
        )
    prewarm_enabled = str(os.getenv("ESIM_PREWARM_ON_STARTUP", "1")).strip().lower() in {"1", "true", "yes", "on"}
    if prewarm_enabled:
        try:
            from backend.gateway.routers.esim import prewarm_esim_runtime_caches

            result = await run_in_threadpool(prewarm_esim_runtime_caches)
            print(f"eSIM cache prewarm done: {result}")
        except Exception as exc:
            print(f"WARNING: eSIM cache prewarm skipped: {exc}")
    try:
        from backend.communications.Telegram.service import (
            ensure_telegram_webhook_registered,
            get_telegram_bot_identity,
            get_telegram_webhook_info,
        )

        try:
            bot_identity = await run_in_threadpool(get_telegram_bot_identity)
            print(f"telegram bot identity: {bot_identity}")
        except Exception as exc:
            print(f"WARNING: telegram bot identity check failed: {exc}")

        sync_result = None
        last_error: Exception | None = None
        for attempt in range(1, 6):
            try:
                sync_result = await run_in_threadpool(ensure_telegram_webhook_registered)
                print(f"telegram webhook startup sync: {sync_result}")
                last_error = None
                break
            except Exception as exc:
                last_error = exc
                print(f"WARNING: telegram webhook startup sync attempt {attempt} failed: {exc}")
                if attempt < 5:
                    await asyncio.sleep(float(attempt))

        if last_error is not None:
            print(f"WARNING: telegram webhook startup sync failed: {last_error}")
        else:
            try:
                webhook_info = await run_in_threadpool(get_telegram_webhook_info)
                print(f"telegram webhook post-sync info: {webhook_info}")
            except Exception as exc:
                print(f"WARNING: telegram webhook post-sync check failed: {exc}")
    except Exception as exc:
        print(f"WARNING: telegram webhook startup sync failed: {exc}")


@app.get("/__build")
async def build():
    return {"build": BUILD_ID, "mode": "wings"}


@app.get("/health")
async def health():
    client = get_client_from_env()
    ok = bool(client) and not _wings_config_missing()
    return {
        "ok": ok,
        "build": BUILD_ID,
        "mode": "wings",
        "wings_configured": ok,
    }
