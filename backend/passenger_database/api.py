from __future__ import annotations

import os
from datetime import datetime
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from backend.auth.api import (
    create_auth_compat_router,
    create_auth_router,
    get_authenticated_owner_user_id,
    require_authenticated_user,
)
from backend.auth.service import is_super_admin
from backend.core.runtime import configure_cors, load_project_env
from backend.pending.api import create_router as create_pending_router
from backend.passenger_database.service import (
    backfill_esim_history_for_member,
    compute_view_profile,
    create_member,
    create_profile,
    find_members_by_query,
    history_for_member,
    list_esim_orders_for_owner,
    load_profiles,
    save_profiles,
)
from backend.transactions.api import create_router as create_transactions_router

load_project_env(__file__)


BUILD_ID = "backend-passenger-database-v1"


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _allow_public_signup() -> bool:
    return str(os.getenv("PASSENGER_DB_ALLOW_PUBLIC_SIGNUP") or "false").strip().lower() in {"1", "true", "yes", "on"}


def _allow_public_forgot_password() -> bool:
    return str(os.getenv("PASSENGER_DB_ALLOW_PUBLIC_FORGOT_PASSWORD") or "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _passenger_database_enabled_for_user(user: dict[str, Any]) -> bool:
    if is_super_admin(user):
        return True
    service_access = user.get("service_access")
    if not isinstance(service_access, dict):
        return True
    return bool(service_access.get("passenger_database", True))


def _resolve_owner_id(request: Request, body: dict[str, Any] | None = None) -> str:
    body = body if isinstance(body, dict) else {}
    authenticated_owner_id = get_authenticated_owner_user_id(request)
    if authenticated_owner_id:
        return authenticated_owner_id
    owner_id = (
        str(body.get("owner_user_id") or "").strip()
        or str(request.query_params.get("owner_id") or "").strip()
        or str(request.headers.get("X-Owner-Id") or "").strip()
        or str(os.getenv("PASSENGER_DB_DEFAULT_OWNER_ID") or "").strip()
    )
    if owner_id:
        return owner_id
    raise HTTPException(
        status_code=401,
        detail=(
            "Authentication or owner_id is required. Provide a Bearer token, X-Owner-Id, "
            "owner_id query parameter, owner_user_id in the JSON body, or "
            "PASSENGER_DB_DEFAULT_OWNER_ID."
        ),
    )


def _find_profile(profiles: list[dict[str, Any]], owner_id: str, profile_id: str) -> dict[str, Any]:
    for profile in profiles:
        if str(profile.get("id") or "") != str(profile_id):
            continue
        if str(profile.get("owner_user_id") or "") != str(owner_id):
            raise HTTPException(status_code=403, detail="Profile belongs to a different owner.")
        return profile
    raise HTTPException(status_code=404, detail="Profile not found.")


def _find_member(profile: dict[str, Any], member_id: str) -> dict[str, Any]:
    for member in profile.get("members") or []:
        if str(member.get("id") or "") == str(member_id):
            return member
    raise HTTPException(status_code=404, detail="Member not found.")


def _sanitize_profiles_for_owner(owner_id: str) -> list[dict[str, Any]]:
    profiles = []
    for profile in load_profiles():
        if str(profile.get("owner_user_id") or "") == str(owner_id):
            profiles.append(profile)
    return profiles


def _clean_passports(passports: Any) -> list[dict[str, str]]:
    if not isinstance(passports, list):
        return []

    cleaned: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in passports:
        if not isinstance(item, dict):
            continue
        number = str(item.get("number") or "").strip()
        if not number:
            continue
        key = number.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(
            {
                "number": number,
                "expiry_date": str(item.get("expiry_date") or "").strip(),
                "issue_place": str(item.get("issue_place") or "").strip(),
                "nationality": str(item.get("nationality") or "").strip(),
            }
        )
    return cleaned


async def _read_json_payload(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def create_router() -> APIRouter:
    router = APIRouter(tags=["passenger-database"])

    @router.get("/profiles")
    def list_profiles(request: Request, q: str = "") -> dict[str, Any]:
        owner_id = _resolve_owner_id(request)
        profiles = _sanitize_profiles_for_owner(owner_id)
        if q.strip():
            profiles = find_members_by_query(profiles, q)
        return {
            "status": "ok",
            "owner_user_id": owner_id,
            "results": [compute_view_profile(profile) for profile in profiles],
        }

    @router.get("/search")
    def search_profiles(request: Request, q: str = "") -> dict[str, Any]:
        return list_profiles(request=request, q=q)

    @router.get("/profiles/{profile_id}")
    def get_profile(request: Request, profile_id: str) -> dict[str, Any]:
        owner_id = _resolve_owner_id(request)
        profile = _find_profile(load_profiles(), owner_id, profile_id)
        return {"status": "ok", "profile": compute_view_profile(profile)}

    @router.post("/profiles")
    async def create_profile_endpoint(request: Request) -> dict[str, Any]:
        payload = await _read_json_payload(request)
        owner_id = _resolve_owner_id(request, payload)
        profiles = load_profiles()
        profile = create_profile(
            owner_user_id=owner_id,
            label=str(payload.get("label") or "").strip(),
            phone=str(payload.get("phone") or "").strip(),
            allowed_user_ids=[
                str(item).strip()
                for item in (payload.get("allowed_user_ids") or [])
                if str(item).strip()
            ],
        )
        profiles.append(profile)
        save_profiles(profiles)
        return {"status": "ok", "profile": compute_view_profile(profile)}

    @router.put("/profiles/{profile_id}")
    async def update_profile_endpoint(request: Request, profile_id: str) -> dict[str, Any]:
        payload = await _read_json_payload(request)
        owner_id = _resolve_owner_id(request, payload)
        profiles = load_profiles()
        profile = _find_profile(profiles, owner_id, profile_id)
        profile["label"] = str(payload.get("label") or profile.get("label") or "").strip()
        profile["phone"] = str(payload.get("phone") or profile.get("phone") or "").strip()
        if "allowed_user_ids" in payload:
            profile["allowed_user_ids"] = [
                str(item).strip()
                for item in (payload.get("allowed_user_ids") or [])
                if str(item).strip()
            ]
        profile["updated_at"] = _now_iso()
        save_profiles(profiles)
        return {"status": "ok", "profile": compute_view_profile(profile)}

    @router.delete("/profiles/{profile_id}")
    def delete_profile_endpoint(request: Request, profile_id: str) -> dict[str, Any]:
        owner_id = _resolve_owner_id(request)
        profiles = load_profiles()
        _find_profile(profiles, owner_id, profile_id)
        filtered = [
            profile
            for profile in profiles
            if not (
                str(profile.get("id") or "") == str(profile_id)
                and str(profile.get("owner_user_id") or "") == str(owner_id)
            )
        ]
        save_profiles(filtered)
        return {"status": "ok", "deleted": True}

    @router.post("/profiles/{profile_id}/members")
    async def create_member_endpoint(request: Request, profile_id: str) -> dict[str, Any]:
        payload = await _read_json_payload(request)
        owner_id = _resolve_owner_id(request, payload)
        profiles = load_profiles()
        profile = _find_profile(profiles, owner_id, profile_id)
        try:
            member = create_member(payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        profile.setdefault("members", []).append(member)
        profile["updated_at"] = _now_iso()
        save_profiles(profiles)
        return {"status": "ok", "profile": compute_view_profile(profile)}

    @router.put("/profiles/{profile_id}/members/{member_id}")
    async def update_member_endpoint(request: Request, profile_id: str, member_id: str) -> dict[str, Any]:
        payload = await _read_json_payload(request)
        owner_id = _resolve_owner_id(request, payload)
        profiles = load_profiles()
        profile = _find_profile(profiles, owner_id, profile_id)
        member = _find_member(profile, member_id)

        for field_name in (
            "title",
            "first_name",
            "last_name",
            "dob",
            "nationality",
            "national_id_number",
            "phone",
            "notes",
        ):
            if field_name in payload:
                member[field_name] = str(payload.get(field_name) or "").strip()

        if "passports" in payload:
            passports = _clean_passports(payload.get("passports"))
            member["passports"] = passports
            if passports:
                member["nationality"] = str(passports[0].get("nationality") or member.get("nationality") or "").strip()

        member["updated_at"] = _now_iso()
        profile["updated_at"] = _now_iso()
        save_profiles(profiles)
        return {"status": "ok", "profile": compute_view_profile(profile)}

    @router.delete("/profiles/{profile_id}/members/{member_id}")
    def delete_member_endpoint(request: Request, profile_id: str, member_id: str) -> dict[str, Any]:
        owner_id = _resolve_owner_id(request)
        profiles = load_profiles()
        profile = _find_profile(profiles, owner_id, profile_id)
        _find_member(profile, member_id)
        profile["members"] = [
            member for member in (profile.get("members") or []) if str(member.get("id") or "") != str(member_id)
        ]
        profile["updated_at"] = _now_iso()
        save_profiles(profiles)
        return {"status": "ok", "profile": compute_view_profile(profile)}

    @router.get("/members/{member_id}/history")
    def member_history_endpoint(request: Request, member_id: str) -> dict[str, Any]:
        owner_id = _resolve_owner_id(request)
        profiles = _sanitize_profiles_for_owner(owner_id)

        profile_match: dict[str, Any] | None = None
        member_match: dict[str, Any] | None = None
        for profile in profiles:
            for member in profile.get("members") or []:
                if str(member.get("id") or "") == str(member_id):
                    profile_match = profile
                    member_match = member
                    break
            if profile_match:
                break

        if not profile_match or not member_match:
            raise HTTPException(status_code=404, detail="Member not found.")

        try:
            orders = list_esim_orders_for_owner(owner_id)
            backfill_esim_history_for_member(owner_id, profile_match, member_match, orders)
        except Exception:
            pass

        return {"status": "ok", "history": history_for_member(owner_id, member_id)}

    return router


def create_app() -> FastAPI:
    app = FastAPI(title="The Book Passenger Database Backend", version="1.0.0")
    configure_cors(app)
    app.include_router(create_auth_router(), prefix="/api/auth")
    app.include_router(create_auth_compat_router())
    app.include_router(create_pending_router())
    app.include_router(create_transactions_router())

    @app.middleware("http")
    async def _protect_passenger_database_routes(request: Request, call_next):
        path = request.url.path
        if not _allow_public_signup() and path in {"/api/auth/signup", "/signup"}:
            return JSONResponse(
                status_code=403,
                content={"detail": "Public signup is disabled for this standalone service."},
            )
        if not _allow_public_forgot_password() and path in {"/api/auth/forgot-password", "/forgot-password"}:
            return JSONResponse(
                status_code=403,
                content={"detail": "Public password reset is disabled for this standalone service."},
            )
        if path.startswith("/api/passenger-database") or path.startswith("/passenger-database/api"):
            try:
                user = require_authenticated_user(request)
                if not _passenger_database_enabled_for_user(user):
                    raise HTTPException(status_code=403, detail="Passenger database service is disabled for this account.")
            except HTTPException as exc:
                return JSONResponse(status_code=int(exc.status_code), content={"detail": exc.detail})
        return await call_next(request)

    router = create_router()
    app.include_router(router, prefix="/api/passenger-database")
    app.include_router(router, prefix="/passenger-database/api", include_in_schema=False)

    @app.get("/__build")
    async def build() -> dict[str, str]:
        return {"build": BUILD_ID, "service": "passenger-database"}

    @app.get("/health")
    async def health() -> dict[str, object]:
        return {
            "ok": True,
            "service": "passenger-database",
            "build": BUILD_ID,
            "default_owner_configured": bool(str(os.getenv("PASSENGER_DB_DEFAULT_OWNER_ID") or "").strip()),
        }

    return app


app = create_app()
