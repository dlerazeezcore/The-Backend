from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from backend.auth.api import create_auth_router, require_authenticated_user
from backend.auth.service import is_super_admin
from backend.core.runtime import configure_cors, load_project_env

from backend.flights.wings.services.wings_client import get_client_from_env
from backend.gateway.flights_utils import _wings_config_missing
from backend.gateway.routers.flights import router as flights_router

load_project_env(__file__)


BUILD_ID = "backend-flights-wings-v1"


def _flights_enabled_for_user(user: dict[str, Any]) -> bool:
    if is_super_admin(user):
        return True
    service_access = user.get("service_access")
    if isinstance(service_access, dict) and not bool(service_access.get("flights", True)):
        return False
    api_access = user.get("api_access")
    if isinstance(api_access, dict) and not bool(api_access.get("ota", True)):
        return False
    return True


def _allow_public_signup() -> bool:
    return str(os.getenv("FLIGHTS_ALLOW_PUBLIC_SIGNUP") or "false").strip().lower() in {"1", "true", "yes", "on"}


def _allow_public_forgot_password() -> bool:
    return str(os.getenv("FLIGHTS_ALLOW_PUBLIC_FORGOT_PASSWORD") or "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def create_app() -> FastAPI:
    app = FastAPI(title="The Book Flights Backend (WINGS)", version="1.0.0")
    configure_cors(app)
    app.include_router(create_auth_router(), prefix="/api/auth")

    @app.middleware("http")
    async def _protect_flights_routes(request: Request, call_next):
        path = request.url.path
        if not _allow_public_signup() and path == "/api/auth/signup":
            return JSONResponse(
                status_code=403,
                content={"detail": "Public signup is disabled for this standalone service."},
            )
        if not _allow_public_forgot_password() and path == "/api/auth/forgot-password":
            return JSONResponse(
                status_code=403,
                content={"detail": "Public password reset is disabled for this standalone service."},
            )
        if path in {"/api/availability", "/api/book"}:
            try:
                current_user = require_authenticated_user(request)
                if not _flights_enabled_for_user(current_user):
                    raise HTTPException(status_code=403, detail="Flights service is disabled for this account.")
            except HTTPException as exc:
                return JSONResponse(status_code=int(exc.status_code), content={"detail": exc.detail})
        return await call_next(request)

    app.include_router(flights_router)

    @app.get("/__build")
    async def build() -> dict[str, str]:
        return {"build": BUILD_ID, "service": "flights", "provider": "wings"}

    @app.get("/health")
    async def health() -> dict[str, object]:
        client = get_client_from_env()
        configured = bool(client) and not _wings_config_missing()
        return {
            "ok": configured,
            "service": "flights",
            "provider": "wings",
            "build": BUILD_ID,
            "wings_configured": configured,
        }

    return app


app = create_app()
