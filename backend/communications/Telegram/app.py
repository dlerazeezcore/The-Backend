from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI

from backend.auth.api import create_auth_router
from backend.core.runtime import configure_cors, load_project_env

from .router import router
from .service import ensure_telegram_webhook_registered


BUILD_ID = "telegram-support-v1"
APP_DIR = Path(__file__).resolve().parent

load_project_env(__file__)
app = FastAPI(title="Telegram Support Backend", version="1.0.0")
configure_cors(app)
app.include_router(create_auth_router(), prefix="/api/auth")
app.include_router(router)


@app.on_event("startup")
async def _startup_sync_telegram_webhook() -> None:
    try:
        result = ensure_telegram_webhook_registered()
        print(f"telegram webhook startup sync: {result}")
    except Exception as exc:
        print(f"WARNING: telegram webhook startup sync failed: {exc}")


@app.get("/health")
async def health() -> dict[str, object]:
    return {"ok": True, "build": BUILD_ID, "service": "telegram_support"}


@app.get("/__build")
async def build() -> dict[str, str]:
    return {"build": BUILD_ID, "service": "telegram_support"}
