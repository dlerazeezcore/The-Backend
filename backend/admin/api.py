from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from backend.admin.service import (
    create_fib_payment,
    esim_countries_index,
    esim_ping_status,
    load_email_configuration,
    load_esim_configuration,
    load_fib_configuration,
    load_permissions_config,
    load_visa_catalog,
    normalize_visa_catalog,
    permissions_status_payload,
    save_email_configuration,
    save_esim_configuration,
    save_fib_configuration,
    save_permissions_config,
    save_visa_catalog,
    send_email_test,
)
from backend.admin.subscriptions import (
    ADDONS,
    admin_delete_subscription,
    admin_update_subscription,
    grant_subscription_free,
    is_active,
    list_all_subscriptions,
    list_subscriptions_for_owner,
    update_addon_prices,
)
from backend.auth.api import read_request_payload, require_authenticated_user
from backend.auth.service import (
    effective_owner_user_id,
    find_user,
    is_company_admin,
    is_sub_user,
    is_super_admin,
    load_users,
)


def _to_number(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        if isinstance(value, (int, float)):
            return float(value)
        txt = str(value).strip().replace(",", "")
        if not txt:
            return default
        return float(txt)
    except Exception:
        return default


def _require_super_admin(request: Request) -> dict[str, Any]:
    current_user = require_authenticated_user(request)
    if not is_super_admin(current_user):
        raise HTTPException(status_code=403, detail="Forbidden.")
    return current_user


def _require_company_or_super(request: Request) -> dict[str, Any]:
    current_user = require_authenticated_user(request)
    if is_sub_user(current_user):
        raise HTTPException(status_code=403, detail="Company admin access required.")
    return current_user


def _company_users_payload(users: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for user in users:
        if not isinstance(user, dict) or not is_company_admin(user):
            continue
        label = user.get("company_name") or user.get("username") or user.get("email") or user.get("id")
        out.append(
            {
                "id": str(user.get("id") or ""),
                "label": str(label or ""),
                "company_name": str(user.get("company_name") or ""),
                "username": str(user.get("username") or ""),
                "email": str(user.get("email") or ""),
                "phone": str(user.get("phone") or ""),
                "role": str(user.get("role") or ""),
                "active": bool(user.get("active", True)),
                "credit": _to_number(user.get("credit"), 0),
                "cash": _to_number(user.get("cash"), 0),
                "preferred_payment": str(user.get("preferred_payment") or "cash"),
                "created_at": str(user.get("created_at") or ""),
            }
        )
    return out


def _sub_users_payload(users: list[dict[str, Any]]) -> dict[str, Any]:
    companies: list[dict[str, Any]] = []
    company_name_by_id: dict[str, str] = {}
    for user in users:
        if not isinstance(user, dict) or not is_company_admin(user):
            continue
        user_id = str(user.get("id") or "")
        companies.append(
            {
                "id": user_id,
                "company_name": str(user.get("company_name") or ""),
                "username": str(user.get("username") or ""),
            }
        )
        company_name_by_id[user_id] = str(user.get("company_name") or "")

    sub_users: list[dict[str, Any]] = []
    for user in users:
        if not isinstance(user, dict) or not is_sub_user(user):
            continue
        company_id = str(user.get("company_admin_id") or "")
        sub_users.append(
            {
                "id": str(user.get("id") or ""),
                "company_id": company_id,
                "company_name": company_name_by_id.get(company_id, str(user.get("company_name") or "")),
                "first_name": str(user.get("first_name") or ""),
                "last_name": str(user.get("last_name") or ""),
                "position": str(user.get("position") or ""),
                "username": str(user.get("username") or ""),
                "email": str(user.get("email") or ""),
                "active": bool(user.get("active", True)),
            }
        )
    return {"companies": companies, "subUsers": sub_users}


def create_router() -> APIRouter:
    router = APIRouter(tags=["admin"])

    @router.get("/api/permissions")
    async def permissions_get(request: Request) -> dict[str, Any]:
        _require_super_admin(request)
        return load_permissions_config()

    @router.post("/api/permissions")
    async def permissions_set(request: Request) -> dict[str, Any]:
        _require_super_admin(request)
        payload = await read_request_payload(request)
        return save_permissions_config(payload)

    @router.get("/api/permissions/status")
    async def permissions_status(request: Request) -> dict[str, Any]:
        _require_super_admin(request)
        return permissions_status_payload()

    @router.get("/api/other-apis/fib")
    async def fib_config_get(request: Request) -> dict[str, Any]:
        _require_super_admin(request)
        return load_fib_configuration()

    @router.post("/api/other-apis/fib")
    async def fib_config_set(request: Request) -> dict[str, Any]:
        _require_super_admin(request)
        payload = await read_request_payload(request)
        return save_fib_configuration(payload)

    @router.post("/api/other-apis/fib/create-payment")
    async def fib_create_payment_endpoint(request: Request) -> dict[str, Any]:
        _require_super_admin(request)
        payload = await read_request_payload(request)
        fib_policy = (load_permissions_config().get("apis") or {}).get("fib") or {}
        if not bool(fib_policy.get("enabled", True)):
            raise HTTPException(status_code=503, detail="FIB API is disabled by admin permissions.")
        amount = int(payload.get("amount") or 0)
        if amount <= 0:
            raise HTTPException(status_code=400, detail="amount must be greater than zero.")
        description = str(payload.get("description") or "Payment").strip() or "Payment"
        try:
            return create_fib_payment(amount, description)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/api/other-apis/email")
    async def email_config_get(request: Request) -> dict[str, Any]:
        _require_super_admin(request)
        return load_email_configuration()

    @router.post("/api/other-apis/email")
    async def email_config_set(request: Request) -> dict[str, Any]:
        _require_super_admin(request)
        payload = await read_request_payload(request)
        return save_email_configuration(payload)

    @router.post("/api/other-apis/email/test-send")
    async def email_test_send_endpoint(request: Request) -> dict[str, Any]:
        _require_super_admin(request)
        payload = await read_request_payload(request)
        to_email = str(payload.get("to_email") or "").strip()
        subject = str(payload.get("subject") or "Tulip Bookings test email").strip() or "Tulip Bookings test email"
        body = str(payload.get("body") or "Email configuration test successful.").strip() or "Email configuration test successful."
        if not to_email:
            raise HTTPException(status_code=400, detail="to_email is required.")
        ok, message = send_email_test(to_email, subject, body)
        if not ok:
            raise HTTPException(status_code=500, detail=message)
        return {"status": "ok", "message": "Test email sent."}

    @router.get("/api/other-apis/esim")
    async def esim_config_get(request: Request) -> dict[str, Any]:
        _require_super_admin(request)
        return load_esim_configuration()

    @router.post("/api/other-apis/esim")
    async def esim_config_set(request: Request) -> dict[str, Any]:
        _require_super_admin(request)
        payload = await read_request_payload(request)
        return save_esim_configuration(payload)

    @router.get("/api/other-apis/esim/ping")
    async def esim_ping_endpoint(request: Request) -> dict[str, Any]:
        _require_super_admin(request)
        try:
            return esim_ping_status()
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/api/esim/countries-index")
    async def esim_countries_index_endpoint(request: Request) -> dict[str, Any]:
        _require_company_or_super(request)
        try:
            return esim_countries_index()
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/admin/users/api/list")
    async def admin_users_list(request: Request) -> dict[str, Any]:
        _require_super_admin(request)
        users = load_users()
        return {"status": "ok", "users": _company_users_payload(users)}

    @router.get("/admin/sub-users/api/list")
    async def admin_sub_users_list(request: Request) -> dict[str, Any]:
        _require_super_admin(request)
        users = load_users()
        payload = _sub_users_payload(users)
        return {"status": "ok", **payload}

    @router.get("/admin/subscriptions/api/list")
    async def admin_subscriptions_list(request: Request) -> dict[str, Any]:
        _require_super_admin(request)
        users = load_users()
        subs = [row for row in list_all_subscriptions() if str((row or {}).get("addon") or "").strip() != "esim"]
        out: list[dict[str, Any]] = []
        for sub in subs:
            if not isinstance(sub, dict):
                continue
            owner_id = str(sub.get("owner_user_id") or "")
            owner = find_user(users, owner_id) if owner_id else None
            owner_name = owner_id
            if isinstance(owner, dict):
                owner_name = owner.get("company_name") or owner.get("username") or owner.get("email") or owner_id
            row = dict(sub)
            row["owner_name"] = owner_name
            out.append(row)
        addons = {k: v for k, v in (ADDONS or {}).items() if str(k).strip() != "esim"}
        return {"status": "ok", "subscriptions": out, "addons": addons}

    @router.post("/admin/subscriptions/api/addons/update")
    async def admin_addons_update(request: Request) -> dict[str, Any]:
        _require_super_admin(request)
        payload = await read_request_payload(request)
        addon = str(payload.get("addon") or "").strip()
        if addon == "esim":
            raise HTTPException(status_code=400, detail="eSIM is included by default and cannot be managed as a subscription add-on.")
        monthly_price = payload.get("monthly_price")
        yearly_price = payload.get("yearly_price")
        visible_raw = payload.get("visible")
        visible: bool | None = None
        if visible_raw is not None:
            if isinstance(visible_raw, str):
                visible = visible_raw.strip().lower() in {"1", "true", "yes", "on"}
            else:
                visible = bool(visible_raw)
        ok, message, addon_data = update_addon_prices(addon, monthly_price, yearly_price, visible=visible)
        if not ok or not addon_data:
            raise HTTPException(status_code=400, detail=message or "Failed.")
        return {"status": "ok", "addon": addon_data, "addons": ADDONS}

    @router.post("/admin/subscriptions/api/grant")
    async def admin_subscriptions_grant(request: Request) -> dict[str, Any]:
        current_user = _require_super_admin(request)
        payload = await read_request_payload(request)
        addon = str(payload.get("addon") or "").strip()
        if addon == "esim":
            raise HTTPException(status_code=400, detail="eSIM is included by default and cannot be granted as a subscription add-on.")
        period = str(payload.get("period") or "").strip().lower()
        user_id = str(payload.get("user_id") or "").strip()
        if not addon or not period or not user_id:
            raise HTTPException(status_code=400, detail="Missing addon, period, or user.")

        users = load_users()
        target = find_user(users, user_id)
        if not target:
            raise HTTPException(status_code=404, detail="User not found.")
        if is_sub_user(target) or not is_company_admin(target):
            raise HTTPException(status_code=400, detail="Add-ons can only be granted to company users.")

        ok, message, sub = grant_subscription_free(
            owner_user_id=user_id,
            addon=addon,
            period=period,
            granted_by_user_id=str(current_user.get("id") or ""),
        )
        if not ok or not sub:
            raise HTTPException(status_code=400, detail=message or "Failed.")
        return {"status": "ok", "subscription": sub}

    @router.post("/admin/subscriptions/api/{sub_id}/update")
    async def admin_subscriptions_update(request: Request, sub_id: str) -> dict[str, Any]:
        _require_super_admin(request)
        payload = await read_request_payload(request)
        ok, message, sub = admin_update_subscription(sub_id, payload if isinstance(payload, dict) else {})
        if not ok or not sub:
            raise HTTPException(status_code=400, detail=message or "Failed.")
        return {"status": "ok", "subscription": sub}

    @router.post("/admin/subscriptions/api/{sub_id}/delete")
    async def admin_subscriptions_delete(request: Request, sub_id: str) -> dict[str, Any]:
        _require_super_admin(request)
        deleted = admin_delete_subscription(sub_id)
        return {"status": "ok", "deleted": bool(deleted)}

    @router.post("/subscriptions/api/assign")
    async def subscriptions_assign(request: Request) -> dict[str, Any]:
        current_user = _require_company_or_super(request)
        payload = await read_request_payload(request)
        addon = str(payload.get("addon") or "").strip()
        target_user_id = str(payload.get("user_id") or "").strip()
        grant = bool(payload.get("grant", True))
        if not addon or not target_user_id:
            raise HTTPException(status_code=400, detail="Missing addon or user_id.")

        users = load_users()
        target_user = find_user(users, target_user_id)
        if not target_user or not is_sub_user(target_user):
            raise HTTPException(status_code=404, detail="Sub user not found.")

        owner_id = ""
        if is_super_admin(current_user):
            owner_id = str(payload.get("company_admin_id") or "").strip()
            if not owner_id:
                owner_id = str(target_user.get("company_admin_id") or "").strip()
            if not owner_id:
                raise HTTPException(status_code=400, detail="company_admin_id is required for super admin scope.")
        else:
            owner_id = effective_owner_user_id(current_user)

        if str(target_user.get("company_admin_id") or "") != owner_id:
            raise HTTPException(status_code=403, detail="Sub user does not belong to the requested company.")

        subs = list_subscriptions_for_owner(owner_id)
        sub = next((row for row in subs if str(row.get("addon") or "") == addon and is_active(row)), None)
        if not sub:
            raise HTTPException(status_code=400, detail="No active subscription found for this add-on.")

        assigned = sub.get("assigned_user_ids")
        assigned_ids = [str(x) for x in assigned] if isinstance(assigned, list) else []
        if grant:
            if target_user_id not in assigned_ids:
                assigned_ids.append(target_user_id)
        else:
            assigned_ids = [x for x in assigned_ids if x != target_user_id]

        ok, message, updated = admin_update_subscription(str(sub.get("id") or ""), {"assigned_user_ids": assigned_ids})
        if not ok or not updated:
            raise HTTPException(status_code=400, detail=message or "Failed.")
        return {"status": "ok", "subscription": updated}

    @router.get("/admin/visa-catalog/api/list")
    async def admin_visa_catalog_list(request: Request) -> dict[str, Any]:
        _require_super_admin(request)
        return {"status": "ok", "catalog": load_visa_catalog()}

    @router.post("/admin/visa-catalog/api/country/save")
    async def admin_visa_catalog_country_save(request: Request) -> dict[str, Any]:
        _require_super_admin(request)
        payload = await read_request_payload(request)
        country_id = str(payload.get("id") or "").strip() or f"vc_{uuid.uuid4().hex[:8]}"
        name = str(payload.get("name") or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="Country name is required.")
        code = str(payload.get("code") or "").strip().upper()
        visible = bool(payload.get("visible", True))
        sort_order = int(payload.get("sort_order") or 0)

        catalog = load_visa_catalog()
        countries = catalog.get("countries") if isinstance(catalog.get("countries"), list) else []
        row = next((c for c in countries if isinstance(c, dict) and str(c.get("id") or "") == country_id), None)
        if row:
            row["name"] = name
            row["code"] = code
            row["visible"] = visible
            row["sort_order"] = sort_order
        else:
            countries.append(
                {
                    "id": country_id,
                    "name": name,
                    "code": code,
                    "visible": visible,
                    "sort_order": sort_order,
                    "types": [],
                }
            )
        catalog["countries"] = countries
        saved = save_visa_catalog(catalog)
        return {"status": "ok", "catalog": saved}

    @router.post("/admin/visa-catalog/api/country/{country_id}/delete")
    async def admin_visa_catalog_country_delete(request: Request, country_id: str) -> dict[str, Any]:
        _require_super_admin(request)
        catalog = load_visa_catalog()
        countries = catalog.get("countries") if isinstance(catalog.get("countries"), list) else []
        catalog["countries"] = [c for c in countries if not (isinstance(c, dict) and str(c.get("id") or "") == str(country_id))]
        saved = save_visa_catalog(catalog)
        return {"status": "ok", "catalog": saved}

    @router.post("/admin/visa-catalog/api/type/save")
    async def admin_visa_catalog_type_save(request: Request) -> dict[str, Any]:
        _require_super_admin(request)
        payload = await read_request_payload(request)
        country_id = str(payload.get("country_id") or "").strip()
        if not country_id:
            raise HTTPException(status_code=400, detail="country_id is required.")

        type_id = str(payload.get("id") or "").strip() or f"vt_{uuid.uuid4().hex[:8]}"
        name = str(payload.get("name") or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="Visa type name is required.")
        try:
            price = float(payload.get("price") or 0)
        except Exception:
            price = 0.0
        days = str(payload.get("days") or "").strip()
        category = str(payload.get("category") or "").strip().lower()
        details = str(payload.get("details") or "").strip()
        required_documents = payload.get("required_documents")
        if isinstance(required_documents, str):
            required_documents = [x.strip() for x in required_documents.split("\n") if x.strip()]
        if not isinstance(required_documents, list):
            required_documents = []
        required_documents = [str(x).strip() for x in required_documents if str(x).strip()]
        visible = bool(payload.get("visible", True))
        sort_order = int(payload.get("sort_order") or 0)
        currency = str(payload.get("currency") or "IQD").strip().upper() or "IQD"

        catalog = load_visa_catalog()
        countries = catalog.get("countries") if isinstance(catalog.get("countries"), list) else []
        country = next((c for c in countries if isinstance(c, dict) and str(c.get("id") or "") == country_id), None)
        if not country:
            raise HTTPException(status_code=404, detail="Country not found.")
        types = country.get("types") if isinstance(country.get("types"), list) else []
        row = next((t for t in types if isinstance(t, dict) and str(t.get("id") or "") == type_id), None)
        if row:
            row.update(
                {
                    "name": name,
                    "days": days,
                    "category": category,
                    "price": price,
                    "currency": currency,
                    "details": details,
                    "required_documents": required_documents,
                    "visible": visible,
                    "sort_order": sort_order,
                }
            )
        else:
            types.append(
                {
                    "id": type_id,
                    "name": name,
                    "days": days,
                    "category": category,
                    "price": price,
                    "currency": currency,
                    "details": details,
                    "required_documents": required_documents,
                    "visible": visible,
                    "sort_order": sort_order,
                }
            )
        country["types"] = types
        catalog["countries"] = countries
        saved = save_visa_catalog(normalize_visa_catalog(catalog))
        return {"status": "ok", "catalog": saved}

    @router.post("/admin/visa-catalog/api/type/{type_id}/delete")
    async def admin_visa_catalog_type_delete(request: Request, type_id: str) -> dict[str, Any]:
        _require_super_admin(request)
        payload = await read_request_payload(request)
        country_id = str(payload.get("country_id") or "").strip()
        if not country_id:
            raise HTTPException(status_code=400, detail="country_id is required.")

        catalog = load_visa_catalog()
        countries = catalog.get("countries") if isinstance(catalog.get("countries"), list) else []
        country = next((c for c in countries if isinstance(c, dict) and str(c.get("id") or "") == country_id), None)
        if not country:
            raise HTTPException(status_code=404, detail="Country not found.")
        types = country.get("types") if isinstance(country.get("types"), list) else []
        country["types"] = [t for t in types if not (isinstance(t, dict) and str(t.get("id") or "") == str(type_id))]
        catalog["countries"] = countries
        saved = save_visa_catalog(catalog)
        return {"status": "ok", "catalog": saved}

    return router
