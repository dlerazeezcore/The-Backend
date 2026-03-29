from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI

from backend.auth.api import create_auth_router
from backend.core.runtime import configure_cors, load_project_env

from .router import router


BUILD_ID = "telegram-support-v1"
APP_DIR = Path(__file__).resolve().parent

load_project_env(__file__)
app = FastAPI(title="Telegram Support Backend", version="1.0.0")
configure_cors(app)
app.include_router(create_auth_router(), prefix="/api/auth")
app.include_router(router)


@app.get("/health")
async def health() -> dict[str, object]:
    return {"ok": True, "build": BUILD_ID, "service": "telegram_support"}


@app.get("/__build")
async def build() -> dict[str, str]:
    return {"build": BUILD_ID, "service": "telegram_support"}
