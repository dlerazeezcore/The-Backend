from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from backend.auth.service import (
    DEFAULT_TOKEN_MAX_AGE_SECONDS,
    EXPOSE_TEMP_PASSWORD,
    authenticate_user,
    effective_owner_user_id,
    find_user,
    find_user_by_identifier,
    is_company_admin,
    is_sub_user,
    is_super_admin,
    issue_token,
    load_users,
    public_user,
    save_users,
    set_password_fields,
    resolve_token,
    revoke_user_tokens,
    create_user_and_notify,
    reset_password_and_notify,
)


def _auth_token_from_request(request: Request) -> str:
    auth_header = str(request.headers.get("Authorization") or "").strip()
    if auth_header.lower().startswith("bearer "):
        return auth_header.split(" ", 1)[1].strip()
    return str(request.cookies.get("auth_token") or "").strip()


def get_authenticated_user(request: Request) -> dict[str, Any] | None:
    return resolve_token(_auth_token_from_request(request))


def require_authenticated_user(request: Request) -> dict[str, Any]:
    user = get_authenticated_user(request)
    if user:
        return user
    raise HTTPException(status_code=401, detail="Unauthorized")


def _normalize_sub_user_service_access(
    payload: dict[str, Any] | None = None,
    *,
    owner_service_access: dict[str, Any] | None = None,
    existing: dict[str, Any] | None = None,
) -> dict[str, bool]:
    keys = (
        "flights",
        "hotels",
        "esim",
        "airport_transportation",
        "car_rental",
        "cip_services",
        "passenger_database",
        "visa",
        "pending",
        "transactions",
    )
    owner_cfg = owner_service_access if isinstance(owner_service_access, dict) else {}
    current_cfg = existing if isinstance(existing, dict) else {}
    patch_cfg = payload if isinstance(payload, dict) else {}

    out: dict[str, bool] = {}
    for key in keys:
        if key == "transactions":
            out[key] = True
            continue

        owner_enabled = bool(owner_cfg.get(key, True))
        current_enabled = bool(current_cfg.get(key, owner_enabled))
        if key in patch_cfg:
            wanted = bool(patch_cfg.get(key))
        else:
            wanted = current_enabled
        out[key] = bool(wanted and owner_enabled)

    out["transportation"] = bool(
        out.get("airport_transportation", True)
        or out.get("car_rental", True)
        or out.get("cip_services", True)
    )
    return out


def _normalize_company_api_access_for_sub_user(owner_api_access: dict[str, Any] | None) -> dict[str, bool]:
    keys = ("ota", "esim_oasis", "esim_access", "fib", "email")
    src = owner_api_access if isinstance(owner_api_access, dict) else {}
    return {key: bool(src.get(key, True)) for key in keys}


def _serialize_user_details(user: dict[str, Any]) -> dict[str, Any]:
    account_role = "super_admin" if is_super_admin(user) else "sub_user" if is_sub_user(user) else "company_admin"
    service_access = user.get("service_access") if isinstance(user.get("service_access"), dict) else {}
    return {
        "id": str(user.get("id") or ""),
        "username": str(user.get("username") or ""),
        "email": str(user.get("email") or ""),
        "company_name": str(user.get("company_name") or user.get("company") or ""),
        "phone": str(user.get("phone") or ""),
        "role": account_role,
        "is_admin": is_super_admin(user),
        "is_sub_user": is_sub_user(user),
        "company_admin_id": str(user.get("company_admin_id") or ""),
        "first_name": str(user.get("first_name") or ""),
        "last_name": str(user.get("last_name") or ""),
        "position": str(user.get("position") or ""),
        "cash": user.get("cash", 0),
        "credit": user.get("credit", 0),
        "preferred_payment": str(user.get("preferred_payment") or "cash"),
        "active": bool(user.get("active", True)),
        "service_access": service_access,
        "created_at": str(user.get("created_at") or ""),
    }


def _resolve_company_owner_for_company_admin_scope(
    *,
    current_user: dict[str, Any],
    users: list[dict[str, Any]],
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if is_sub_user(current_user):
        raise HTTPException(status_code=403, detail="Company admin access required.")

    if is_super_admin(current_user):
        owner_id = str((payload or {}).get("company_admin_id") or "").strip()
        if not owner_id:
            raise HTTPException(
                status_code=400,
                detail="company_admin_id is required when authenticated as super_admin.",
            )
        owner = find_user(users, owner_id)
        if not owner or not is_company_admin(owner):
            raise HTTPException(status_code=404, detail="Company admin not found.")
        return owner

    owner = find_user(users, str(current_user.get("id") or ""))
    if not owner:
        raise HTTPException(status_code=401, detail="Authenticated user was not found.")
    if not is_company_admin(owner):
        raise HTTPException(status_code=403, detail="Company admin access required.")
    return owner


def _read_form_payload(form: Any) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for key, value in form.items():
        data[key] = value
    return data


async def read_request_payload(request: Request) -> dict[str, Any]:
    content_type = str(request.headers.get("content-type") or "").lower()

    if "application/json" in content_type:
        try:
            data = await request.json()
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    if "application/x-www-form-urlencoded" in content_type or "multipart/form-data" in content_type:
        try:
            form = await request.form()
        except Exception:
            return {}
        return _read_form_payload(form)

    try:
        data = await request.json()
    except Exception:
        try:
            form = await request.form()
        except Exception:
            return {}
        return _read_form_payload(form)
    return data if isinstance(data, dict) else {}


def create_auth_router() -> APIRouter:
    router = APIRouter(tags=["auth"])

    @router.post("/login")
    async def login(request: Request) -> dict[str, Any]:
        payload = await read_request_payload(request)
        identifier = str(payload.get("identifier") or payload.get("email") or payload.get("username") or "").strip()
        password = str(payload.get("password") or "").strip()
        if not identifier or not password:
            raise HTTPException(status_code=400, detail="identifier and password are required.")

        user, error = authenticate_user(identifier, password)
        if not user:
            raise HTTPException(status_code=401, detail=error or "Invalid credentials.")

        token = issue_token(user)
        return {
            "status": "ok",
            "message": "Login successful.",
            "token": token,
            "token_type": "bearer",
            "expires_in": DEFAULT_TOKEN_MAX_AGE_SECONDS,
            "user": public_user(user),
        }

    @router.get("/me")
    async def me(request: Request) -> dict[str, Any]:
        user = require_authenticated_user(request)
        return {"status": "ok", "user": public_user(user)}

    @router.get("/users")
    async def users(request: Request) -> dict[str, Any]:
        current_user = require_authenticated_user(request)
        owner_id = effective_owner_user_id(current_user)
        visible: list[dict[str, Any]] = []
        for row in load_users():
            if not isinstance(row, dict):
                continue
            if is_super_admin(current_user):
                visible.append(public_user(row))
                continue
            row_owner_id = effective_owner_user_id(row)
            if row_owner_id == owner_id:
                visible.append(public_user(row))
        return {"status": "ok", "users": visible}

    @router.get("/sub-users")
    async def sub_users(request: Request) -> dict[str, Any]:
        current_user = require_authenticated_user(request)
        owner_id = effective_owner_user_id(current_user)
        visible: list[dict[str, Any]] = []
        for row in load_users():
            if not isinstance(row, dict):
                continue
            if not is_sub_user(row):
                continue
            if is_super_admin(current_user):
                visible.append(public_user(row))
                continue
            row_owner_id = effective_owner_user_id(row)
            if row_owner_id == owner_id:
                visible.append(public_user(row))
        return {"status": "ok", "users": visible}

    @router.post("/company-admin/sub-users", status_code=201)
    async def create_company_admin_sub_user(request: Request) -> dict[str, Any]:
        current_user = require_authenticated_user(request)
        payload = await read_request_payload(request)
        users = load_users()
        owner = _resolve_company_owner_for_company_admin_scope(current_user=current_user, users=users, payload=payload)

        first_name = str(payload.get("first_name") or "").strip()
        last_name = str(payload.get("last_name") or "").strip()
        position = str(payload.get("position") or "").strip()
        email = str(payload.get("email") or "").strip()
        username = str(payload.get("username") or "").strip()
        password = str(payload.get("password") or "").strip()

        if not first_name or not last_name or not username or not password:
            raise HTTPException(
                status_code=400,
                detail="first_name, last_name, username, and password are required.",
            )
        if email and "@" not in email:
            raise HTTPException(status_code=400, detail="Please enter a valid email address.")
        if find_user_by_identifier(users, username):
            raise HTTPException(status_code=400, detail="Username already exists")
        if email and find_user_by_identifier(users, email):
            raise HTTPException(status_code=400, detail="Email already exists")

        service_access = _normalize_sub_user_service_access(
            {},
            owner_service_access=owner.get("service_access") if isinstance(owner, dict) else {},
            existing=None,
        )
        new_user: dict[str, Any] = {
            "id": uuid.uuid4().hex,
            "type": "sub_user",
            "role": "sub_user",
            "company_admin_id": str(owner.get("id") or ""),
            "company_name": str(owner.get("company_name") or owner.get("company") or owner.get("username") or ""),
            "first_name": first_name,
            "last_name": last_name,
            "position": position,
            "email": email,
            "username": username,
            "active": True,
            "credit": 0,
            "cash": 0,
            "preferred_payment": str(owner.get("preferred_payment") or "cash"),
            "apis": list(owner.get("apis") or []) if isinstance(owner.get("apis"), list) else [],
            "service_access": service_access,
            "api_access": _normalize_company_api_access_for_sub_user(owner.get("api_access")),
            "commission": list(owner.get("commission") or []) if isinstance(owner.get("commission"), list) else [],
            "markup": list(owner.get("markup") or []) if isinstance(owner.get("markup"), list) else [],
            "auth_token_version": 0,
            "phone": "",
            "contact": "",
        }
        set_password_fields(new_user, password)
        users.append(new_user)
        save_users(users)
        return {
            "status": "success",
            "message": "Sub-user created successfully",
            "user": _serialize_user_details(new_user),
        }

    @router.put("/company-admin/sub-users/{sub_user_id}")
    async def update_company_admin_sub_user(request: Request, sub_user_id: str) -> dict[str, Any]:
        current_user = require_authenticated_user(request)
        payload = await read_request_payload(request)
        users = load_users()
        owner = _resolve_company_owner_for_company_admin_scope(current_user=current_user, users=users, payload=payload)

        sub = find_user(users, str(sub_user_id))
        if not sub or not is_sub_user(sub):
            raise HTTPException(status_code=404, detail="Sub-user not found")

        if not is_super_admin(current_user) and str(sub.get("company_admin_id") or "") != str(owner.get("id") or ""):
            raise HTTPException(status_code=403, detail="Not authorized")

        username = str(payload.get("username") or "").strip() if "username" in payload else None
        email = str(payload.get("email") or "").strip() if "email" in payload else None

        if username:
            for row in users:
                if not isinstance(row, dict):
                    continue
                if str(row.get("id") or "") == str(sub.get("id") or ""):
                    continue
                if str(row.get("username") or "").strip().lower() == username.lower():
                    raise HTTPException(status_code=400, detail="Username already exists")
            sub["username"] = username

        if email is not None:
            if email and "@" not in email:
                raise HTTPException(status_code=400, detail="Please enter a valid email address.")
            if email:
                for row in users:
                    if not isinstance(row, dict):
                        continue
                    if str(row.get("id") or "") == str(sub.get("id") or ""):
                        continue
                    if str(row.get("email") or "").strip().lower() == email.lower():
                        raise HTTPException(status_code=400, detail="Email already exists")
            sub["email"] = email

        if "first_name" in payload:
            sub["first_name"] = str(payload.get("first_name") or "").strip()
        if "last_name" in payload:
            sub["last_name"] = str(payload.get("last_name") or "").strip()
        if "position" in payload:
            sub["position"] = str(payload.get("position") or "").strip()
        if "active" in payload:
            sub["active"] = bool(payload.get("active"))

        new_password = str(payload.get("new_password") or "").strip()
        if new_password:
            set_password_fields(sub, new_password)
            sub["auth_token_version"] = int(sub.get("auth_token_version") or 0) + 1

        if "service_access" in payload:
            service_payload = payload.get("service_access")
            sub["service_access"] = _normalize_sub_user_service_access(
                service_payload if isinstance(service_payload, dict) else {},
                owner_service_access=owner.get("service_access") if isinstance(owner, dict) else {},
                existing=sub.get("service_access") if isinstance(sub.get("service_access"), dict) else {},
            )
        else:
            sub["service_access"] = _normalize_sub_user_service_access(
                {},
                owner_service_access=owner.get("service_access") if isinstance(owner, dict) else {},
                existing=sub.get("service_access") if isinstance(sub.get("service_access"), dict) else {},
            )

        sub["company_admin_id"] = str(owner.get("id") or "")
        sub["company_name"] = str(owner.get("company_name") or owner.get("company") or owner.get("username") or "")
        sub["preferred_payment"] = str(owner.get("preferred_payment") or "cash")
        sub["api_access"] = _normalize_company_api_access_for_sub_user(owner.get("api_access"))
        sub["apis"] = list(owner.get("apis") or []) if isinstance(owner.get("apis"), list) else []
        sub["commission"] = list(owner.get("commission") or []) if isinstance(owner.get("commission"), list) else []
        sub["markup"] = list(owner.get("markup") or []) if isinstance(owner.get("markup"), list) else []

        save_users(users)
        return {
            "status": "success",
            "message": "Sub-user updated successfully",
            "user": _serialize_user_details(sub),
        }

    @router.post("/company-admin/sub-users/{sub_user_id}/toggle")
    async def toggle_company_admin_sub_user(request: Request, sub_user_id: str) -> dict[str, Any]:
        current_user = require_authenticated_user(request)
        payload = await read_request_payload(request)
        query_company_admin_id = str(request.query_params.get("company_admin_id") or "").strip()
        if query_company_admin_id and not str((payload or {}).get("company_admin_id") or "").strip():
            payload["company_admin_id"] = query_company_admin_id

        users = load_users()

        sub = find_user(users, str(sub_user_id))
        if not sub or not is_sub_user(sub):
            raise HTTPException(status_code=404, detail="Sub-user not found")

        if is_super_admin(current_user) and not str((payload or {}).get("company_admin_id") or "").strip():
            payload["company_admin_id"] = str(sub.get("company_admin_id") or "").strip()

        owner = _resolve_company_owner_for_company_admin_scope(
            current_user=current_user,
            users=users,
            payload=payload,
        )

        if not is_super_admin(current_user) and str(sub.get("company_admin_id") or "") != str(owner.get("id") or ""):
            raise HTTPException(status_code=403, detail="Not authorized")

        sub["active"] = not bool(sub.get("active", True))
        save_users(users)
        return {
            "status": "success",
            "message": "Sub-user status updated",
            "user": {
                "id": str(sub.get("id") or ""),
                "username": str(sub.get("username") or ""),
                "active": bool(sub.get("active", True)),
            },
        }

    @router.delete("/company-admin/sub-users/{sub_user_id}")
    async def delete_company_admin_sub_user(request: Request, sub_user_id: str) -> dict[str, Any]:
        current_user = require_authenticated_user(request)
        payload = await read_request_payload(request)
        query_company_admin_id = str(request.query_params.get("company_admin_id") or "").strip()
        if query_company_admin_id and not str((payload or {}).get("company_admin_id") or "").strip():
            payload["company_admin_id"] = query_company_admin_id

        users = load_users()

        sub = find_user(users, str(sub_user_id))
        if not sub or not is_sub_user(sub):
            raise HTTPException(status_code=404, detail="Sub-user not found")

        if is_super_admin(current_user) and not str((payload or {}).get("company_admin_id") or "").strip():
            payload["company_admin_id"] = str(sub.get("company_admin_id") or "").strip()

        owner = _resolve_company_owner_for_company_admin_scope(
            current_user=current_user,
            users=users,
            payload=payload,
        )

        if not is_super_admin(current_user) and str(sub.get("company_admin_id") or "") != str(owner.get("id") or ""):
            raise HTTPException(status_code=403, detail="Not authorized")

        users = [row for row in users if not (isinstance(row, dict) and str(row.get("id") or "") == str(sub_user_id))]
        save_users(users)
        return {"status": "success", "message": "Sub-user deleted successfully"}

    @router.post("/admin/users", status_code=201)
    async def create_admin_company_user(request: Request) -> dict[str, Any]:
        current_user = require_authenticated_user(request)
        if not is_super_admin(current_user):
            raise HTTPException(status_code=403, detail="Super admin access required")

        payload = await read_request_payload(request)
        company_name = str(payload.get("company_name") or "").strip()
        username = str(payload.get("username") or "").strip()
        email = str(payload.get("email") or "").strip()
        password = str(payload.get("password") or "").strip()
        phone = str(payload.get("phone") or "").strip()
        preferred_payment = str(payload.get("preferred_payment") or "cash").strip().lower()
        credit = payload.get("credit", 0)
        cash = payload.get("cash", 0)

        if not company_name or not username or not password:
            raise HTTPException(status_code=400, detail="company_name, username, and password are required.")
        if preferred_payment not in {"cash", "credit"}:
            preferred_payment = "cash"
        if email and "@" not in email:
            raise HTTPException(status_code=400, detail="Please enter a valid email address.")

        users = load_users()
        if find_user_by_identifier(users, username):
            raise HTTPException(status_code=400, detail="Username already exists")
        if email and find_user_by_identifier(users, email):
            raise HTTPException(status_code=400, detail="Email already exists")

        try:
            credit_value = float(credit or 0)
        except Exception:
            credit_value = 0.0
        try:
            cash_value = float(cash or 0)
        except Exception:
            cash_value = 0.0

        new_user: dict[str, Any] = {
            "id": uuid.uuid4().hex,
            "type": "user",
            "role": "company_admin",
            "company_name": company_name,
            "username": username,
            "email": email,
            "phone": phone,
            "contact": phone,
            "active": True,
            "credit": int(credit_value) if int(credit_value) == credit_value else credit_value,
            "cash": int(cash_value) if int(cash_value) == cash_value else cash_value,
            "preferred_payment": preferred_payment,
            "apis": [],
            "service_access": _normalize_sub_user_service_access({}),
            "api_access": _normalize_company_api_access_for_sub_user(None),
            "commission": [],
            "markup": [],
            "auth_token_version": 0,
        }
        set_password_fields(new_user, password)
        users.append(new_user)
        save_users(users)
        return {
            "status": "success",
            "message": "Company user created successfully",
            "user": _serialize_user_details(new_user),
        }

    @router.delete("/admin/users/{user_id}")
    async def delete_admin_company_user(request: Request, user_id: str) -> dict[str, Any]:
        current_user = require_authenticated_user(request)
        if not is_super_admin(current_user):
            raise HTTPException(status_code=403, detail="Super admin access required")

        users = load_users()
        target = find_user(users, str(user_id))
        if not target:
            raise HTTPException(status_code=404, detail="User not found")
        if is_super_admin(target):
            raise HTTPException(status_code=400, detail="Cannot delete super admin user.")

        users = [
            row for row in users
            if not (
                isinstance(row, dict)
                and (
                    str(row.get("id") or "") == str(user_id)
                    or str(row.get("company_admin_id") or "") == str(user_id)
                )
            )
        ]
        save_users(users)
        return {
            "status": "success",
            "message": "Company user and all sub-users deleted successfully",
        }

    @router.post("/logout")
    async def logout(request: Request) -> dict[str, Any]:
        user = get_authenticated_user(request)
        if user:
            revoke_user_tokens(str(user.get("id") or ""))
        return {"status": "ok", "message": "Logged out."}

    @router.post("/signup")
    async def signup(request: Request) -> dict[str, Any]:
        payload = await read_request_payload(request)
        email = str(payload.get("email") or "").strip()
        username = str(payload.get("username") or "").strip()
        company_name = str(payload.get("company_name") or payload.get("company") or username).strip()
        contact = str(payload.get("contact") or payload.get("phone") or "").strip()

        if not email or "@" not in email:
            raise HTTPException(status_code=400, detail="Please enter a valid email address.")
        if not username:
            raise HTTPException(status_code=400, detail="Please enter a username.")

        try:
            user, temp_password = create_user_and_notify(
                email=email,
                username=username,
                company_name=company_name or username,
                contact=contact,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        response: dict[str, Any] = {
            "status": "ok",
            "message": "Account created successfully. Check your email for your password.",
            "user": public_user(user),
        }
        if EXPOSE_TEMP_PASSWORD:
            response["temp_password"] = temp_password
        return response

    @router.post("/forgot-password")
    async def forgot_password(request: Request) -> dict[str, Any]:
        payload = await read_request_payload(request)
        identifier = str(payload.get("identifier") or payload.get("email") or payload.get("username") or "").strip()
        if not identifier:
            raise HTTPException(status_code=400, detail="Please enter your email or username.")

        try:
            user, temp_password = reset_password_and_notify(identifier)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        response: dict[str, Any] = {
            "status": "ok",
            "message": "Password reset email sent. Please check your inbox.",
            "user": public_user(user),
        }
        if EXPOSE_TEMP_PASSWORD:
            response["temp_password"] = temp_password
        return response

    return router


def create_auth_compat_router() -> APIRouter:
    router = APIRouter(include_in_schema=False)

    api_router = create_auth_router()

    @router.post("/login")
    async def login_alias(request: Request) -> dict[str, Any]:
        for route in api_router.routes:
            if getattr(route, "path", None) == "/login":
                return await route.endpoint(request)
        raise HTTPException(status_code=500, detail="Auth route not available.")

    @router.api_route("/logout", methods=["GET", "POST"])
    async def logout_alias(request: Request) -> dict[str, Any]:
        for route in api_router.routes:
            if getattr(route, "path", None) == "/logout":
                return await route.endpoint(request)
        raise HTTPException(status_code=500, detail="Auth route not available.")

    @router.post("/signup")
    async def signup_alias(request: Request) -> dict[str, Any]:
        for route in api_router.routes:
            if getattr(route, "path", None) == "/signup":
                return await route.endpoint(request)
        raise HTTPException(status_code=500, detail="Auth route not available.")

    @router.post("/forgot-password")
    async def forgot_password_alias(request: Request) -> dict[str, Any]:
        for route in api_router.routes:
            if getattr(route, "path", None) == "/forgot-password":
                return await route.endpoint(request)
        raise HTTPException(status_code=500, detail="Auth route not available.")

    return router


def get_authenticated_owner_user_id(request: Request) -> str | None:
    user = get_authenticated_user(request)
    if not user:
        return None
    return effective_owner_user_id(user)
