from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.auth.api import (
    create_auth_compat_router,
    create_auth_router,
    read_request_payload,
    require_authenticated_user,
)
from backend.auth.service import (
    display_name,
    effective_owner_user_id,
    find_user,
    is_sub_user,
    is_super_admin,
    load_users,
)
from backend.core.runtime import configure_cors, load_project_env
from backend.esim.esimaccess.service import (
    cancel_profile as esimaccess_cancel_profile,
    balance_query as esimaccess_balance_query,
    is_configured as esimaccess_is_configured,
    list_locations as esimaccess_list_locations,
    list_packages as esimaccess_list_packages,
    order_profiles as esimaccess_order_profiles,
    query_profiles as esimaccess_query_profiles,
    revoke_profile as esimaccess_revoke_profile,
    send_sms as esimaccess_send_sms,
    set_webhook as esimaccess_set_webhook,
    suspend_profile as esimaccess_suspend_profile,
    topup_profiles as esimaccess_topup_profiles,
    unsuspend_profile as esimaccess_unsuspend_profile,
    usage_query as esimaccess_usage_query,
)
from backend.esim.esimaccess.store import (
    list_esimaccess_orders_for_agent,
    list_esimaccess_orders_for_owner,
    record_esimaccess_order,
    update_esimaccess_order_by_reference,
)
from backend.transactions.store import load_transactions_items, save_transactions_items

APP_DIR = Path(__file__).resolve().parent
load_project_env(__file__)
DEFAULT_SMDP_ADDRESS = os.getenv("ESIMACCESS_DEFAULT_SMDP", "rsp-eu.simlessly.com")
BUILD_ID = "backend-esimaccess-v1"


def _allow_public_signup() -> bool:
    return str(os.getenv("ESIMACCESS_ALLOW_PUBLIC_SIGNUP") or "false").strip().lower() in {"1", "true", "yes", "on"}


def _allow_public_forgot_password() -> bool:
    return str(os.getenv("ESIMACCESS_ALLOW_PUBLIC_FORGOT_PASSWORD") or "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _to_int(value: object, default: int = 0) -> int:
    try:
        if value is None:
            return default
        if isinstance(value, bool):
            return int(value)
        text = str(value).strip()
        if not text:
            return default
        return int(float(text))
    except Exception:
        return default


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_iso_dt(value: object) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    normalized = raw.replace("Z", "+00:00")
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return datetime.strptime(normalized, fmt)
        except Exception:
            continue
    # eSIMAccess often returns 2025-03-19T18:00:00+0000
    for fmt in ("%Y-%m-%dT%H:%M:%S%z",):
        try:
            return datetime.strptime(raw, fmt)
        except Exception:
            continue
    return None


def _days_left_from_expiry(expired_time: object) -> int | None:
    dt = _parse_iso_dt(expired_time)
    if not dt:
        return None
    now = datetime.now(timezone.utc)
    delta = dt.astimezone(timezone.utc) - now
    return max(0, int(delta.total_seconds() // 86400))


def _access_price_divisor() -> int:
    div = _to_int(os.getenv("ESIMACCESS_PRICE_DIVISOR"), default=100)
    return max(1, div)


def _access_price_to_usd_minor(raw_value: object) -> int:
    raw = _to_int(raw_value, default=0)
    if raw <= 0:
        return 0
    return int(round(float(raw) / float(_access_price_divisor())))


def _parse_location_codes(raw: object) -> list[str]:
    text = str(raw or "").strip()
    if not text:
        return []
    out: list[str] = []
    for part in text.split(","):
        code = str(part or "").strip().upper()
        if len(code) == 2 and code.isalpha() and code not in out:
            out.append(code)
    return out


def _normalize_access_item(item: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    package_code = str(item.get("packageCode") or "").strip()
    plan_slug = str(item.get("slug") or "").strip()
    if not package_code and not plan_slug:
        return None
    plan_name = str(item.get("name") or package_code or plan_slug).strip()
    iso_list = _parse_location_codes(item.get("location"))
    countries = [{"iso": iso, "name": iso, "region": ""} for iso in iso_list]
    volume_bytes = _to_int(item.get("volume"), default=0)
    data_amount_mb = int(round(volume_bytes / (1024.0 * 1024.0))) if volume_bytes > 0 else 0
    duration = _to_int(item.get("duration"), default=0)
    if duration <= 0:
        duration = _to_int(item.get("unusedValidTime"), default=0)
    data_type = _to_int(item.get("dataType"), default=1)
    daily_plan = data_type == 2 or "/day" in plan_name.lower() or "daily" in plan_slug.lower()
    unlimited = data_type == 4 or daily_plan
    provider_price_raw = _to_int(item.get("price"), default=0)
    price_minor = _access_price_to_usd_minor(provider_price_raw)
    bundle_ref = plan_slug if daily_plan and plan_slug else package_code or plan_slug
    return {
        "bundleName": f"ea::{bundle_ref}",
        "description": plan_name,
        "dataAmountMb": data_amount_mb,
        "durationDays": duration,
        "unlimited": unlimited,
        "allowanceMode": "per_day" if daily_plan else "total",
        "price": {
            "finalMinor": price_minor,
            "currency": str(item.get("currencyCode") or "USD").upper(),
        },
        "countries": countries,
        "provider": "esimaccess",
        "providerBundleCode": package_code,
        "providerSlug": plan_slug,
        "providerDataType": data_type,
        "provider_price_minor": price_minor,
        "provider_price_raw": provider_price_raw,
        "provider_currency": str(item.get("currencyCode") or "USD").upper(),
    }


def _is_access_success(payload: dict[str, Any] | None) -> bool:
    return bool(isinstance(payload, dict) and payload.get("success"))


def _activation_lpa_from_access_row(row: dict[str, Any] | None) -> str:
    data = row if isinstance(row, dict) else {}
    raw_code = str(data.get("ac") or data.get("activationCode") or "").strip()
    if not raw_code:
        return ""
    if raw_code.upper().startswith("LPA:"):
        return raw_code
    if raw_code.startswith("1$"):
        return f"LPA:{raw_code}"
    smdp_address = str(
        data.get("smdpAddress")
        or data.get("smdp")
        or data.get("smdpServerAddress")
        or data.get("smdpServer")
        or DEFAULT_SMDP_ADDRESS
    ).strip()
    if smdp_address:
        return f"LPA:1${smdp_address}${raw_code}"
    return raw_code


def _extract_install_url_from_access_row(row: dict[str, Any] | None) -> str:
    data = row if isinstance(row, dict) else {}
    explicit = [
        str(data.get("shortUrl") or "").strip(),
        str(data.get("quickInstallUrl") or "").strip(),
        str(data.get("installUrl") or "").strip(),
    ]
    for url in explicit:
        if url.startswith("http://") or url.startswith("https://"):
            return url

    seen: set[int] = set()
    candidates: list[str] = []

    def _walk(value: object) -> None:
        if id(value) in seen:
            return
        seen.add(id(value))
        if isinstance(value, str):
            text = value.strip()
            if text.startswith("http://") or text.startswith("https://"):
                candidates.append(text)
            return
        if isinstance(value, dict):
            for child in value.values():
                _walk(child)
            return
        if isinstance(value, list):
            for child in value:
                _walk(child)

    _walk(data)
    for url in candidates:
        lower = url.lower()
        if ("p.qrsim.net/" in lower and not lower.endswith(".png")) or "esimsetup.apple.com/" in lower:
            return url
    for url in candidates:
        lower = url.lower()
        if "p.qrsim.net/" in lower and lower.endswith(".png"):
            return url[:-4]
    return candidates[0] if candidates else ""


def _map_access_query_to_order(order_no: str, response: dict[str, Any] | None) -> dict[str, Any]:
    response = response if isinstance(response, dict) else {}
    obj = response.get("obj") if isinstance(response.get("obj"), dict) else {}
    esim_list = obj.get("esimList") if isinstance(obj, dict) else []
    if not isinstance(esim_list, list):
        esim_list = []
    row = esim_list[0] if esim_list and isinstance(esim_list[0], dict) else {}
    activation_code = _activation_lpa_from_access_row(row)
    install_url = _extract_install_url_from_access_row(row)
    iccid = str(row.get("iccid") or "").strip()
    esim_status = str(row.get("esimStatus") or "").strip().upper()
    smdp_status = str(row.get("smdpStatus") or "").strip().upper()
    created_at = str(row.get("createTime") or row.get("createdAt") or "").strip()

    status = "processing"
    if esim_status in {"CANCEL", "REVOKED"}:
        status = "revoked"
    elif activation_code:
        status = "completed"

    status_msg = " ".join(part for part in [esim_status, smdp_status] if part).strip()
    return {
        "provider": "esimaccess",
        "status": status,
        "statusMessage": status_msg,
        "orderReference": order_no,
        "orderNo": order_no,
        "activationCodes": [activation_code] if activation_code else [],
        "iccidList": [iccid] if iccid else [],
        "quickInstallUrl": install_url,
        "createdAt": created_at,
        "raw": response,
    }


def _esim_enabled_for_user(user: dict[str, Any]) -> bool:
    if is_super_admin(user):
        return True
    service_access = user.get("service_access")
    if not isinstance(service_access, dict):
        return True
    return bool(service_access.get("esim", True))


def _require_esim_user(request: Request) -> dict[str, Any]:
    user = require_authenticated_user(request)
    if not _esim_enabled_for_user(user):
        raise HTTPException(status_code=403, detail="eSIM service is disabled for this account.")
    return user


def _fetch_access_packages(location_code: str = "") -> tuple[dict[str, Any], list[dict[str, Any]]]:
    payload = {
        "locationCode": str(location_code or "").strip().upper(),
        "type": "BASE",
        "packageCode": "",
        "slug": "",
        "iccid": "",
    }
    package_list: list[dict[str, Any]] = []
    raw_responses: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for body in (payload, {**payload, "dataType": 2}):
        try:
            response = esimaccess_list_packages(body)
        except ValueError as exc:
            msg = str(exc)
            if "Missing ESIMACCESS_ACCESS_CODE" in msg:
                raise HTTPException(
                    status_code=503,
                    detail="eSIM supplier is not configured. Set ESIMACCESS_ACCESS_CODE in backend environment.",
                ) from exc
            raise HTTPException(status_code=503, detail=f"eSIM supplier unavailable: {msg}") from exc
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"eSIM supplier unavailable: {exc}") from exc
        if not _is_access_success(response):
            raise HTTPException(
                status_code=400,
                detail=response.get("errorMsg") or response.get("errorCode") or "eSIMAccess package query failed.",
            )
        raw_responses.append(response)
        obj = response.get("obj") if isinstance(response.get("obj"), dict) else {}
        rows = obj.get("packageList") if isinstance(obj, dict) else []
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            key = "|".join(
                [
                    str(row.get("packageCode") or "").strip(),
                    str(row.get("slug") or "").strip(),
                    str(_to_int(row.get("dataType"), default=1)),
                ]
            )
            if key in seen_keys:
                continue
            seen_keys.add(key)
            package_list.append(row)
    return {"success": True, "obj": {"packageList": package_list}, "responses": raw_responses}, package_list


def _resolve_package_for_order(payload: dict[str, Any]) -> tuple[str, str, int, dict[str, Any] | None]:
    body = payload if isinstance(payload, dict) else {}
    bundle_name = str(body.get("bundleName") or "").strip()
    package_code = str(body.get("packageCode") or "").strip()
    plan_slug = str(body.get("slug") or body.get("providerSlug") or "").strip()
    location_code = str(
        body.get("locationCode")
        or body.get("countryIso")
        or body.get("country_iso")
        or ""
    ).strip().upper()

    if not package_code and bundle_name.startswith("ea::") and not plan_slug:
        package_code = bundle_name.split("ea::", 1)[1].strip()

    matched_package: dict[str, Any] | None = None
    if (not package_code and not plan_slug) or (not _to_int(body.get("provider_price_raw"), default=0)):
        _, package_list = _fetch_access_packages(location_code=location_code)
        for row in package_list:
            if not isinstance(row, dict):
                continue
            row_code = str(row.get("packageCode") or "").strip()
            row_slug = str(row.get("slug") or "").strip()
            row_name = str(row.get("name") or "").strip()
            normalized = _normalize_access_item(row)
            normalized_bundle = str(normalized.get("bundleName") or "").strip() if isinstance(normalized, dict) else ""
            if plan_slug and row_slug == plan_slug:
                matched_package = row
                package_code = row_code
                break
            if package_code and row_code == package_code:
                matched_package = row
                break
            if bundle_name and (normalized_bundle == bundle_name or row_name == bundle_name):
                matched_package = row
                package_code = row_code
                plan_slug = row_slug
                break

    normalized_match = _normalize_access_item(matched_package) if isinstance(matched_package, dict) else None
    allowance_mode = str(body.get("allowanceMode") or "").strip().lower()
    if not allowance_mode and isinstance(normalized_match, dict):
        allowance_mode = str(normalized_match.get("allowanceMode") or "").strip().lower()

    if allowance_mode == "per_day" and not plan_slug and isinstance(normalized_match, dict):
        plan_slug = str(normalized_match.get("providerSlug") or "").strip()
    if allowance_mode == "per_day" and not plan_slug:
        raise HTTPException(status_code=400, detail="bundleName or slug is required for eSIMAccess day-pass order.")
    if allowance_mode != "per_day" and not package_code:
        raise HTTPException(status_code=400, detail="bundleName or packageCode is required for eSIMAccess order.")

    unit_price_raw = _to_int(body.get("provider_price_raw"), default=0)
    if unit_price_raw <= 0:
        unit_price_raw = _to_int(body.get("unit_price_raw"), default=0)
    if unit_price_raw <= 0 and isinstance(matched_package, dict):
        unit_price_raw = _to_int(matched_package.get("price"), default=0)
    if unit_price_raw <= 0:
        minor = _to_int(body.get("unit_price_minor"), default=0)
        if minor > 0:
            unit_price_raw = minor * _access_price_divisor()
    if unit_price_raw <= 0 and isinstance(matched_package, dict):
        normalized = _normalize_access_item(matched_package)
        if isinstance(normalized, dict):
            minor = _to_int(normalized.get("provider_price_minor"), default=0)
            if minor > 0:
                unit_price_raw = minor * _access_price_divisor()
    if unit_price_raw <= 0:
        raise HTTPException(status_code=400, detail="Unable to resolve eSIMAccess package price.")

    if not allowance_mode and isinstance(normalized_match, dict):
        allowance_mode = str(normalized_match.get("allowanceMode") or "total").strip().lower()
    final_bundle_name = bundle_name
    if not final_bundle_name:
        if isinstance(normalized_match, dict):
            final_bundle_name = str(normalized_match.get("bundleName") or "").strip()
        elif allowance_mode == "per_day" and plan_slug:
            final_bundle_name = f"ea::{plan_slug}"
        else:
            final_bundle_name = f"ea::{package_code}"
    if isinstance(matched_package, dict):
        matched_package = dict(matched_package)
        matched_package["_resolved_allowance_mode"] = allowance_mode or "total"
        matched_package["_resolved_slug"] = plan_slug
    return package_code, final_bundle_name, unit_price_raw, matched_package


def _owner_company_name(current_user: dict[str, Any], users: list[dict[str, Any]]) -> str:
    owner_id = effective_owner_user_id(current_user)
    owner = find_user(users, owner_id) if owner_id else None
    if isinstance(owner, dict):
        return str(owner.get("company_name") or owner.get("company") or owner.get("username") or "").strip()
    return str(
        current_user.get("company_name")
        or current_user.get("company")
        or current_user.get("username")
        or ""
    ).strip()


def _upsert_order_snapshot(
    *,
    current_user: dict[str, Any],
    request_payload: dict[str, Any],
    provider_result: dict[str, Any],
) -> None:
    reference = str(provider_result.get("orderReference") or provider_result.get("orderNo") or "").strip()
    if not reference:
        return
    owner_id = effective_owner_user_id(current_user)
    agent_id = str(current_user.get("id") or "").strip()
    users = load_users()
    meta = request_payload.get("_meta") if isinstance(request_payload.get("_meta"), dict) else {}
    quantity = max(1, _to_int(request_payload.get("quantity"), default=1))
    total_iqd = _to_int(request_payload.get("total_iqd"), default=0)
    if total_iqd <= 0:
        total_iqd = _to_int(meta.get("total_iqd"), default=0)
    if total_iqd <= 0:
        unit_iqd = _to_int(meta.get("unit_price_iqd_minor"), default=0)
        if unit_iqd > 0:
            total_iqd = unit_iqd * quantity

    row = {
        "owner_user_id": owner_id,
        "company_name": _owner_company_name(current_user, users),
        "agent_user_id": agent_id,
        "agent_name": display_name(current_user),
        "customer_name": str(request_payload.get("customerName") or request_payload.get("customer_name") or "").strip(),
        "customer_phone": str(request_payload.get("customerPhone") or request_payload.get("customer_phone") or "").strip(),
        "bundle_name": str(request_payload.get("bundleName") or request_payload.get("bundle_name") or "").strip(),
        "bundle_description": str(meta.get("bundle_description") or "").strip(),
        "country_name": str(meta.get("country_name") or "").strip(),
        "country_iso": str(meta.get("country_iso") or "").strip().upper(),
        "quantity": quantity,
        "total_iqd": total_iqd if total_iqd > 0 else None,
        "currency": str(request_payload.get("currency") or "IQD").strip().upper(),
        "status": str(provider_result.get("status") or "processing").strip().lower(),
        "status_message": str(provider_result.get("statusMessage") or "").strip(),
        "order_reference": reference,
        "activation_codes": provider_result.get("activationCodes") if isinstance(provider_result.get("activationCodes"), list) else [],
        "activation_code": str((provider_result.get("activationCodes") or [""])[0] or "").strip()
        if isinstance(provider_result.get("activationCodes"), list)
        else "",
        "quick_install_url": str(provider_result.get("quickInstallUrl") or "").strip(),
        "provider": "esimaccess",
        "payment_method": str(request_payload.get("payment_method") or "").strip().lower(),
        "payment_fib": bool(request_payload.get("payment_fib")),
        "payment_amount_iqd": total_iqd if total_iqd > 0 else None,
        "created_at": str(provider_result.get("createdAt") or "").strip() or None,
        "raw_query": provider_result.get("raw") if isinstance(provider_result.get("raw"), dict) else {},
    }

    updated = update_esimaccess_order_by_reference(
        reference,
        {
            "status": row["status"],
            "status_message": row["status_message"],
            "activation_codes": row["activation_codes"],
            "activation_code": row["activation_code"],
            "quick_install_url": row["quick_install_url"],
            "raw_query": row["raw_query"],
        },
    )
    if not updated:
        record_esimaccess_order(row)


def _normalize_report_row(row: dict[str, Any], current_user: dict[str, Any]) -> dict[str, Any]:
    item = dict(row if isinstance(row, dict) else {})
    activation_codes = item.get("activation_codes")
    activation_codes = activation_codes if isinstance(activation_codes, list) else []
    activation_code = str(item.get("activation_code") or "").strip()
    if not activation_code and activation_codes:
        activation_code = str(activation_codes[0] or "").strip()

    bundle_name = str(item.get("bundle_name") or "").strip()
    bundle_description = str(item.get("bundle_description") or "").strip()
    customer_name = str(item.get("customer_name") or "").strip()
    company_name = str(item.get("company_name") or "").strip() or str(
        current_user.get("company_name")
        or current_user.get("company")
        or ""
    ).strip()
    agent_name = str(item.get("agent_name") or "").strip() or display_name(current_user)
    country_name = str(item.get("country_name") or "").strip()
    country_iso = str(item.get("country_iso") or "").strip().upper()
    created_at = str(item.get("created_at") or item.get("updated_at") or "").strip()

    item["order_reference"] = str(item.get("order_reference") or item.get("orderReference") or item.get("id") or "").strip()
    item["created_at"] = created_at
    item["customer_name"] = customer_name or "—"
    item["company_name"] = company_name or "—"
    item["agent_name"] = agent_name or "—"
    item["bundle_name"] = bundle_name or "eSIM"
    item["bundle_description"] = bundle_description or item["bundle_name"]
    item["country_name"] = country_name or "—"
    item["country_iso"] = country_iso
    item["status"] = str(item.get("status") or "processing").strip().lower()
    item["provider"] = str(item.get("provider") or "esimaccess").strip().lower()
    item["total_iqd"] = _to_int(item.get("total_iqd"), default=0)
    item["currency"] = str(item.get("currency") or "IQD").strip().upper()
    item["activation_codes"] = activation_codes
    item["activation_code"] = activation_code
    item["lpa"] = activation_code
    item["qrPayload"] = activation_code
    item["quickInstallUrl"] = str(item.get("quick_install_url") or item.get("quickInstallUrl") or "").strip()
    return item


def _query_provider_order(order_reference: str) -> dict[str, Any]:
    query_payload = {"orderNo": str(order_reference or "").strip(), "iccid": "", "pager": {"pageNum": 1, "pageSize": 20}}
    try:
        response = esimaccess_query_profiles(query_payload)
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=f"eSIM supplier unavailable: {exc}") from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"eSIM supplier unavailable: {exc}") from exc
    if not _is_access_success(response):
        raise HTTPException(status_code=400, detail=response)
    obj = response.get("obj") if isinstance(response.get("obj"), dict) else {}
    esim_list = obj.get("esimList") if isinstance(obj, dict) else []
    if not isinstance(esim_list, list) or not esim_list:
        raise HTTPException(status_code=404, detail="Order not found.")
    return _map_access_query_to_order(str(order_reference or "").strip(), response)


def _provider_call(fn: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
    try:
        response = fn(*args, **kwargs)
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=f"eSIM supplier unavailable: {exc}") from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"eSIM supplier unavailable: {exc}") from exc
    if not isinstance(response, dict):
        raise HTTPException(status_code=502, detail="Invalid eSIM supplier response.")
    if not _is_access_success(response):
        raise HTTPException(status_code=400, detail=response)
    return response


def _orders_for_user(user: dict[str, Any]) -> list[dict[str, Any]]:
    owner_id = effective_owner_user_id(user)
    if is_sub_user(user):
        return list_esimaccess_orders_for_agent(owner_id, str(user.get("id") or ""))
    return list_esimaccess_orders_for_owner(owner_id)


def _find_order_for_user(user: dict[str, Any], order_reference: str) -> dict[str, Any] | None:
    target = str(order_reference or "").strip()
    if not target:
        return None
    for row in _orders_for_user(user):
        if not isinstance(row, dict):
            continue
        if str(row.get("order_reference") or row.get("orderReference") or "").strip() == target:
            return row
    return None


def _find_order_for_user_by_esim_identity(
    user: dict[str, Any],
    *,
    esim_tran_no: str = "",
    iccid: str = "",
) -> dict[str, Any] | None:
    target_tran = str(esim_tran_no or "").strip()
    target_iccid = str(iccid or "").strip()
    if not target_tran and not target_iccid:
        return None
    for row in _orders_for_user(user):
        if not isinstance(row, dict):
            continue
        raw_query = row.get("raw_query")
        raw_query = raw_query if isinstance(raw_query, dict) else {}
        obj = raw_query.get("obj")
        obj = obj if isinstance(obj, dict) else {}
        esim_list = obj.get("esimList")
        esim_list = esim_list if isinstance(esim_list, list) else []
        first = esim_list[0] if esim_list and isinstance(esim_list[0], dict) else {}
        row_iccid = str(
            row.get("iccid")
            or first.get("iccid")
            or (row.get("iccidList") or [""])[0]
            or ""
        ).strip()
        row_tran = str(row.get("esimTranNo") or first.get("esimTranNo") or "").strip()
        if target_tran and row_tran and row_tran == target_tran:
            return row
        if target_iccid and row_iccid and row_iccid == target_iccid:
            return row
    return None


def _split_customer_name(name: str) -> tuple[str, str]:
    clean = " ".join(str(name or "").strip().split())
    if not clean:
        return "", ""
    parts = clean.split(" ", 1)
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[1]


def _event_amount_iqd(payload: dict[str, Any], fallback: int = 0) -> int:
    amount = _to_int(payload.get("amount_iqd"), default=0)
    if amount <= 0:
        amount = _to_int(payload.get("total_iqd"), default=0)
    if amount <= 0:
        meta = payload.get("_meta")
        if isinstance(meta, dict):
            amount = _to_int(meta.get("total_iqd"), default=0)
    if amount <= 0:
        return _to_int(fallback, default=0)
    return amount


def _record_esim_transaction_event(
    *,
    current_user: dict[str, Any],
    action: str,
    order_row: dict[str, Any] | None,
    payload: dict[str, Any] | None = None,
    amount_iqd: int = 0,
    status: str = "successful",
    note: str = "",
) -> dict[str, Any]:
    event = str(action or "").strip().lower()
    if not event:
        raise HTTPException(status_code=400, detail="Missing transaction event.")

    payload = payload if isinstance(payload, dict) else {}
    order_row = order_row if isinstance(order_row, dict) else {}
    now = _now_iso()
    owner_id = effective_owner_user_id(current_user)
    agent_id = str(current_user.get("id") or "").strip()
    agent_name = display_name(current_user)
    order_reference = str(
        payload.get("order_reference")
        or payload.get("orderReference")
        or order_row.get("order_reference")
        or ""
    ).strip()
    company_name = str(order_row.get("company_name") or _owner_company_name(current_user, load_users()) or "").strip()
    customer_name = str(payload.get("customer_name") or order_row.get("customer_name") or "").strip()
    first_name, last_name = _split_customer_name(customer_name)
    bundle_name = str(payload.get("bundle_name") or order_row.get("bundle_name") or "").strip()
    country_name = str(payload.get("country_name") or order_row.get("country_name") or "").strip()
    country_iso = str(payload.get("country_iso") or order_row.get("country_iso") or "").strip().upper()
    iccid = str(payload.get("iccid") or order_row.get("iccid") or "").strip()
    esim_tran_no = str(payload.get("esimTranNo") or payload.get("esim_tran_no") or "").strip()
    if not esim_tran_no:
        raw_query = order_row.get("raw_query")
        raw_query = raw_query if isinstance(raw_query, dict) else {}
        obj = raw_query.get("obj")
        obj = obj if isinstance(obj, dict) else {}
        esim_list = obj.get("esimList")
        esim_list = esim_list if isinstance(esim_list, list) else []
        first_item = esim_list[0] if esim_list and isinstance(esim_list[0], dict) else {}
        esim_tran_no = str(first_item.get("esimTranNo") or "").strip()
        if not iccid:
            iccid = str(first_item.get("iccid") or "").strip()

    final_amount = _to_int(amount_iqd, default=0)
    if final_amount == 0:
        final_amount = _event_amount_iqd(payload, fallback=_to_int(order_row.get("total_iqd"), default=0))
    event_key = f"{owner_id}:{order_reference}:{event}:{str(payload.get('transactionId') or payload.get('idempotencyKey') or '').strip()}"

    tx_items = load_transactions_items()
    existing: dict[str, Any] | None = None
    for row in tx_items:
        if not isinstance(row, dict):
            continue
        details = row.get("details")
        details = details if isinstance(details, dict) else {}
        if str(details.get("esim_event_key") or "").strip() == event_key and event_key.strip(":"):
            existing = row
            break
        if not existing and order_reference and str(details.get("order_reference") or "").strip() == order_reference:
            if str(details.get("esim_event") or "").strip() == event:
                existing = row
                break

    tx = existing or {
        "id": uuid.uuid4().hex,
        "ts": now,
        "service": "sim",
        "status": status,
        "price": final_amount,
        "currency": "IQD",
        "company_admin_id": owner_id,
        "by_user_id": agent_id,
        "by": agent_name,
        "user_id": owner_id,
        "provider_id": "esimaccess",
        "booking_code": order_reference,
        "first_name": first_name,
        "last_name": last_name,
    }
    tx["updated_at"] = now
    tx["status"] = str(status or tx.get("status") or "successful").strip().lower()
    tx["service"] = "sim"
    tx["price"] = final_amount
    tx["currency"] = "IQD"
    tx["company_admin_id"] = owner_id
    tx["by_user_id"] = agent_id
    tx["by"] = agent_name
    tx["provider_id"] = "esimaccess"
    tx["booking_code"] = order_reference
    tx["company_name"] = company_name
    tx["first_name"] = first_name
    tx["last_name"] = last_name

    details = tx.get("details")
    details = details if isinstance(details, dict) else {}
    details["esim_event"] = event
    details["esim_event_key"] = event_key
    details["order_reference"] = order_reference
    details["bundle_name"] = bundle_name
    details["country_name"] = country_name
    details["country_iso"] = country_iso
    details["iccid"] = iccid
    details["esim_tran_no"] = esim_tran_no
    details["customer_name"] = customer_name
    details["company_name"] = company_name
    details["provider_id"] = "esimaccess"
    details["action"] = event
    if note:
        details["note"] = note
    tx["details"] = details

    if existing is None:
        tx_items.append(tx)
    save_transactions_items(tx_items)
    return tx


def _usage_items_from_response(response: dict[str, Any]) -> list[dict[str, Any]]:
    obj = response.get("obj") if isinstance(response.get("obj"), dict) else {}
    esim_usage_list = obj.get("esimUsageList") if isinstance(obj, dict) else []
    if not isinstance(esim_usage_list, list):
        return []
    return [row for row in esim_usage_list if isinstance(row, dict)]


def _usage_summary(item: dict[str, Any], *, expired_time: str = "") -> dict[str, Any]:
    total_bytes = _to_int(item.get("totalData"), default=0)
    used_bytes = _to_int(item.get("dataUsage"), default=0)
    remaining_bytes = max(0, total_bytes - used_bytes) if total_bytes > 0 else 0
    remaining_mb = round(remaining_bytes / (1024 * 1024), 2) if remaining_bytes else 0
    remaining_gb = round(remaining_bytes / (1024 * 1024 * 1024), 3) if remaining_bytes else 0
    return {
        "esim_tran_no": str(item.get("esimTranNo") or "").strip(),
        "used_bytes": used_bytes,
        "total_bytes": total_bytes,
        "remaining_bytes": remaining_bytes,
        "remaining_mb": remaining_mb,
        "remaining_gb": remaining_gb,
        "last_update_time": str(item.get("lastUpdateTime") or "").strip(),
        "expired_time": str(expired_time or "").strip(),
        "days_left": _days_left_from_expiry(expired_time),
    }


def _first_raw_esim_item(mapped_order: dict[str, Any] | None) -> dict[str, Any]:
    mapped_order = mapped_order if isinstance(mapped_order, dict) else {}
    raw = mapped_order.get("raw")
    raw = raw if isinstance(raw, dict) else {}
    obj = raw.get("obj")
    obj = obj if isinstance(obj, dict) else {}
    esim_list = obj.get("esimList")
    esim_list = esim_list if isinstance(esim_list, list) else []
    if esim_list and isinstance(esim_list[0], dict):
        return esim_list[0]
    return {}


def _identity_payload_from_mapped_order(mapped_order: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    row = _first_raw_esim_item(mapped_order)
    esim_tran_no = str(row.get("esimTranNo") or "").strip()
    iccid = str(row.get("iccid") or "").strip()
    payload: dict[str, Any] = {}
    if esim_tran_no:
        payload["esimTranNo"] = esim_tran_no
    elif iccid:
        payload["iccid"] = iccid
    return payload, row


def create_router() -> APIRouter:
    router = APIRouter(tags=["esimaccess"])

    @router.get("/api/esim/access/settings")
    async def esimaccess_settings(request: Request) -> dict[str, Any]:
        _require_esim_user(request)
        return {
            "status": "ok",
            "provider": "esimaccess",
            "configured": esimaccess_is_configured(),
            "base_url": str(os.getenv("ESIMACCESS_BASE_URL") or "").strip() or "https://api.esimaccess.com",
            "price_divisor": _access_price_divisor(),
        }

    @router.post("/api/esim/access/packages")
    async def esimaccess_packages(request: Request) -> dict[str, Any]:
        _require_esim_user(request)
        payload = await read_request_payload(request)
        location_code = str(payload.get("locationCode") or payload.get("countryIso") or payload.get("country_iso") or "").strip().upper()
        raw_response, package_rows = _fetch_access_packages(location_code=location_code)
        bundles: list[dict[str, Any]] = []
        for row in package_rows:
            normalized = _normalize_access_item(row)
            if normalized:
                bundles.append(normalized)
        return {"status": "ok", "provider": "esimaccess", "count": len(bundles), "items": bundles, "raw": raw_response}

    @router.get("/api/esim/bundles")
    async def esimaccess_bundles(request: Request, locationCode: str = Query(default="")) -> dict[str, Any]:
        _require_esim_user(request)
        _, package_rows = _fetch_access_packages(location_code=locationCode)
        bundles: list[dict[str, Any]] = []
        for row in package_rows:
            normalized = _normalize_access_item(row)
            if normalized:
                bundles.append(normalized)
        return {"items": bundles, "bundles": bundles, "provider": "esimaccess"}

    @router.post("/api/esim/access/orders")
    @router.post("/api/esim/orders")
    async def esimaccess_create_order(request: Request) -> dict[str, Any]:
        user = _require_esim_user(request)
        payload = await read_request_payload(request)
        package_code, bundle_name, unit_price_raw, matched_package = _resolve_package_for_order(payload)
        quantity = max(1, _to_int(payload.get("quantity"), default=1))
        allowance_mode = ""
        plan_slug = ""
        if isinstance(matched_package, dict):
            allowance_mode = str(matched_package.get("_resolved_allowance_mode") or "").strip().lower()
            plan_slug = str(matched_package.get("_resolved_slug") or "").strip()
        transaction_id = str(payload.get("idempotencyKey") or payload.get("idempotency_key") or "").strip() or f"tb-{uuid.uuid4().hex[:24]}"
        period_num = 1
        if allowance_mode == "per_day":
            period_num = max(
                1,
                _to_int(
                    payload.get("periodNum") or payload.get("period_num") or payload.get("durationDays") or 1,
                    default=1,
                ),
            )
        package_item = {"count": quantity, "price": unit_price_raw}
        if allowance_mode == "per_day":
            package_item["slug"] = plan_slug
            package_item["periodNum"] = period_num
        else:
            package_item["packageCode"] = package_code
        order_payload = {
            "transactionId": transaction_id,
            "amount": unit_price_raw * quantity * period_num,
            "packageInfoList": [package_item],
        }
        order_resp = _provider_call(esimaccess_order_profiles, order_payload)
        order_obj = order_resp.get("obj") if isinstance(order_resp.get("obj"), dict) else {}
        order_no = str(order_obj.get("orderNo") or "").strip()
        if not order_no:
            raise HTTPException(status_code=400, detail="eSIMAccess orderNo missing.")

        provider_order = _query_provider_order(order_no)
        provider_order["provider"] = "esimaccess"
        provider_order["bundleName"] = bundle_name
        provider_order["transactionId"] = str(order_obj.get("transactionId") or transaction_id)
        provider_order["allowanceMode"] = allowance_mode or "total"
        if allowance_mode == "per_day":
            provider_order["periodNum"] = period_num
            provider_order["providerSlug"] = plan_slug
        provider_order["raw_order"] = order_resp

        _upsert_order_snapshot(current_user=user, request_payload=payload, provider_result=provider_order)
        order_row = _find_order_for_user(user, order_no)
        _record_esim_transaction_event(
            current_user=user,
            action="purchase",
            order_row=order_row,
            payload={**payload, "order_reference": order_no, "transactionId": provider_order.get("transactionId")},
            amount_iqd=_event_amount_iqd(payload, fallback=_to_int((order_row or {}).get("total_iqd"), default=0)),
            status="successful",
        )
        return provider_order

    @router.get("/api/esim/access/orders")
    @router.get("/api/esim/orders")
    @router.get("/esim/api/orders", include_in_schema=False)
    async def esimaccess_list_orders(
        request: Request,
        pageNum: int = Query(default=1, ge=1),
        pageSize: int = Query(default=50, ge=1, le=100),
    ) -> dict[str, Any]:
        user = _require_esim_user(request)
        query_payload = {"orderNo": "", "iccid": "", "pager": {"pageNum": pageNum, "pageSize": pageSize}}
        try:
            response = esimaccess_query_profiles(query_payload)
        except ValueError as exc:
            raise HTTPException(status_code=503, detail=f"eSIM supplier unavailable: {exc}") from exc
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"eSIM supplier unavailable: {exc}") from exc
        if not _is_access_success(response):
            raise HTTPException(status_code=400, detail=response)
        obj = response.get("obj") if isinstance(response.get("obj"), dict) else {}
        esim_list = obj.get("esimList") if isinstance(obj, dict) else []
        if not isinstance(esim_list, list):
            esim_list = []
        items: list[dict[str, Any]] = []
        for row in esim_list:
            if not isinstance(row, dict):
                continue
            order_no = str(row.get("orderNo") or "").strip()
            if not order_no:
                continue
            mapped = _map_access_query_to_order(order_no, {"success": True, "obj": {"esimList": [row]}})
            items.append(mapped)
            _upsert_order_snapshot(current_user=user, request_payload={"bundleName": ""}, provider_result=mapped)
        return {"status": "ok", "provider": "esimaccess", "count": len(items), "items": items}

    @router.get("/api/esim/access/orders/{order_id}")
    @router.get("/api/esim/orders/{order_id}")
    @router.get("/esim/api/orders/{order_id}", include_in_schema=False)
    async def esimaccess_get_order(request: Request, order_id: str) -> dict[str, Any]:
        user = _require_esim_user(request)
        mapped = _query_provider_order(str(order_id or "").strip())
        _upsert_order_snapshot(current_user=user, request_payload={"bundleName": ""}, provider_result=mapped)
        return mapped

    @router.post("/api/esim/access/orders/query")
    async def esimaccess_query_orders(request: Request) -> dict[str, Any]:
        _require_esim_user(request)
        payload = await read_request_payload(request)
        body = {
            "orderNo": str(payload.get("orderNo") or "").strip(),
            "iccid": str(payload.get("iccid") or "").strip(),
            "pager": {
                "pageNum": max(1, _to_int((payload.get("pager") or {}).get("pageNum"), default=1)),
                "pageSize": max(1, min(100, _to_int((payload.get("pager") or {}).get("pageSize"), default=20))),
            },
        }
        try:
            response = esimaccess_query_profiles(body)
        except ValueError as exc:
            raise HTTPException(status_code=503, detail=f"eSIM supplier unavailable: {exc}") from exc
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"eSIM supplier unavailable: {exc}") from exc
        if not _is_access_success(response):
            raise HTTPException(status_code=400, detail=response)
        return {"status": "ok", "provider": "esimaccess", "response": response}

    @router.post("/api/esim/access/regions")
    @router.post("/api/esim/regions")
    async def esimaccess_regions(request: Request) -> dict[str, Any]:
        _require_esim_user(request)
        payload = await read_request_payload(request)
        response = _provider_call(esimaccess_list_locations, payload if isinstance(payload, dict) else {})
        obj = response.get("obj") if isinstance(response.get("obj"), dict) else {}
        locations = obj.get("locationList") if isinstance(obj, dict) else []
        if not isinstance(locations, list):
            locations = []
        return {"status": "ok", "provider": "esimaccess", "count": len(locations), "items": locations, "raw": response}

    @router.post("/api/esim/access/topups/options")
    @router.post("/api/esim/topups/options")
    async def esimaccess_topup_options(request: Request) -> dict[str, Any]:
        _require_esim_user(request)
        payload = await read_request_payload(request)
        body = {
            "type": "TOPUP",
            "locationCode": str(payload.get("locationCode") or payload.get("countryIso") or payload.get("country_iso") or "").strip().upper(),
            "packageCode": str(payload.get("packageCode") or "").strip(),
            "slug": str(payload.get("slug") or "").strip(),
            "iccid": str(payload.get("iccid") or "").strip(),
        }
        response = _provider_call(esimaccess_list_packages, body)
        obj = response.get("obj") if isinstance(response.get("obj"), dict) else {}
        package_rows = obj.get("packageList") if isinstance(obj, dict) else []
        if not isinstance(package_rows, list):
            package_rows = []
        bundles: list[dict[str, Any]] = []
        for row in package_rows:
            normalized = _normalize_access_item(row) if isinstance(row, dict) else None
            if normalized:
                bundles.append(normalized)
        return {"status": "ok", "provider": "esimaccess", "count": len(bundles), "items": bundles, "raw": response}

    @router.post("/api/esim/access/topups")
    @router.post("/api/esim/topups")
    async def esimaccess_topup(request: Request) -> dict[str, Any]:
        user = _require_esim_user(request)
        payload = await read_request_payload(request)
        response = _provider_call(esimaccess_topup_profiles, payload if isinstance(payload, dict) else {})

        order_reference = str(payload.get("order_reference") or payload.get("orderReference") or payload.get("orderNo") or "").strip()
        order_row: dict[str, Any] | None = _find_order_for_user(user, order_reference) if order_reference else None
        if not order_row:
            order_row = _find_order_for_user_by_esim_identity(
                user,
                esim_tran_no=str(payload.get("esimTranNo") or "").strip(),
                iccid=str(payload.get("iccid") or "").strip(),
            )
            if order_row:
                order_reference = str(order_row.get("order_reference") or "").strip()

        # Refresh local snapshot when we can map this top-up to an order reference.
        if order_reference:
            try:
                mapped = _query_provider_order(order_reference)
                _upsert_order_snapshot(current_user=user, request_payload={"bundleName": ""}, provider_result=mapped)
                order_row = _find_order_for_user(user, order_reference) or order_row
            except Exception:
                pass

        tx = _record_esim_transaction_event(
            current_user=user,
            action="topup",
            order_row=order_row,
            payload={**payload, "order_reference": order_reference},
            amount_iqd=_event_amount_iqd(payload, fallback=_to_int((order_row or {}).get("total_iqd"), default=0)),
            status="successful",
        )
        return {
            "status": "ok",
            "provider": "esimaccess",
            "action": "topup",
            "order_reference": order_reference,
            "transaction_id": str(tx.get("id") or ""),
            "provider_response": response,
        }

    @router.post("/api/esim/access/esims/usage")
    @router.post("/api/esim/esims/usage")
    async def esimaccess_usage(request: Request) -> dict[str, Any]:
        _require_esim_user(request)
        payload = await read_request_payload(request)
        body = payload if isinstance(payload, dict) else {}
        if not isinstance(body.get("esimTranNoList"), list):
            esim_tran_no = str(body.get("esimTranNo") or "").strip()
            if esim_tran_no:
                body = {**body, "esimTranNoList": [esim_tran_no]}
        response = _provider_call(esimaccess_usage_query, body)
        items = _usage_items_from_response(response)
        summaries = [_usage_summary(item) for item in items]
        return {"status": "ok", "provider": "esimaccess", "count": len(summaries), "items": summaries, "raw": response}

    @router.get("/api/esim/access/orders/{order_id}/usage")
    @router.get("/api/esim/orders/{order_id}/usage")
    @router.get("/esim/api/orders/{order_id}/usage", include_in_schema=False)
    async def esimaccess_usage_by_order(request: Request, order_id: str) -> dict[str, Any]:
        user = _require_esim_user(request)
        mapped = _query_provider_order(str(order_id or "").strip())
        _upsert_order_snapshot(current_user=user, request_payload={"bundleName": ""}, provider_result=mapped)
        identity_payload, row = _identity_payload_from_mapped_order(mapped)
        if not identity_payload:
            raise HTTPException(status_code=400, detail="No ICCID/esimTranNo found for this order.")
        usage_payload = {"esimTranNoList": [identity_payload.get("esimTranNo")]} if identity_payload.get("esimTranNo") else identity_payload
        usage_response = _provider_call(esimaccess_usage_query, usage_payload)
        usage_items = _usage_items_from_response(usage_response)
        summary = _usage_summary(usage_items[0], expired_time=str(row.get("expiredTime") or "").strip()) if usage_items else {}
        return {
            "status": "ok",
            "provider": "esimaccess",
            "order_reference": str(mapped.get("orderReference") or order_id),
            "usage": summary,
            "order": mapped,
            "raw_usage": usage_response,
        }

    @router.post("/api/esim/access/esims/cancel")
    @router.post("/api/esim/esims/cancel")
    async def esimaccess_cancel(request: Request) -> dict[str, Any]:
        user = _require_esim_user(request)
        payload = await read_request_payload(request)
        response = _provider_call(esimaccess_cancel_profile, payload if isinstance(payload, dict) else {})
        order_row = _find_order_for_user_by_esim_identity(
            user,
            esim_tran_no=str(payload.get("esimTranNo") or "").strip(),
            iccid=str(payload.get("iccid") or "").strip(),
        )
        if order_row:
            update_esimaccess_order_by_reference(str(order_row.get("order_reference") or ""), {"status": "cancelled", "status_message": "cancelled"})
            order_row = _find_order_for_user(user, str(order_row.get("order_reference") or "")) or order_row
        tx = _record_esim_transaction_event(
            current_user=user,
            action="cancel",
            order_row=order_row,
            payload=payload,
            amount_iqd=0,
            status="successful",
        )
        return {"status": "ok", "provider": "esimaccess", "action": "cancel", "transaction_id": str(tx.get("id") or ""), "provider_response": response}

    @router.post("/api/esim/access/esims/suspend")
    @router.post("/api/esim/esims/suspend")
    async def esimaccess_suspend(request: Request) -> dict[str, Any]:
        _require_esim_user(request)
        payload = await read_request_payload(request)
        response = _provider_call(esimaccess_suspend_profile, payload if isinstance(payload, dict) else {})
        return {"status": "ok", "provider": "esimaccess", "action": "suspend", "provider_response": response}

    @router.post("/api/esim/access/esims/unsuspend")
    @router.post("/api/esim/esims/unsuspend")
    async def esimaccess_unsuspend(request: Request) -> dict[str, Any]:
        _require_esim_user(request)
        payload = await read_request_payload(request)
        response = _provider_call(esimaccess_unsuspend_profile, payload if isinstance(payload, dict) else {})
        return {"status": "ok", "provider": "esimaccess", "action": "unsuspend", "provider_response": response}

    @router.post("/api/esim/access/esims/revoke")
    @router.post("/api/esim/esims/revoke")
    async def esimaccess_revoke(request: Request) -> dict[str, Any]:
        user = _require_esim_user(request)
        payload = await read_request_payload(request)
        response = _provider_call(esimaccess_revoke_profile, payload if isinstance(payload, dict) else {})
        order_row = _find_order_for_user_by_esim_identity(
            user,
            esim_tran_no=str(payload.get("esimTranNo") or "").strip(),
            iccid=str(payload.get("iccid") or "").strip(),
        )
        if order_row:
            update_esimaccess_order_by_reference(str(order_row.get("order_reference") or ""), {"status": "revoked", "status_message": "revoked"})
        tx = _record_esim_transaction_event(
            current_user=user,
            action="revoke",
            order_row=order_row,
            payload=payload,
            amount_iqd=0,
            status="successful",
        )
        return {"status": "ok", "provider": "esimaccess", "action": "revoke", "transaction_id": str(tx.get("id") or ""), "provider_response": response}

    @router.post("/api/esim/access/esims/send-sms")
    @router.post("/api/esim/esims/send-sms")
    async def esimaccess_send_sms_api(request: Request) -> dict[str, Any]:
        _require_esim_user(request)
        payload = await read_request_payload(request)
        response = _provider_call(esimaccess_send_sms, payload if isinstance(payload, dict) else {})
        return {"status": "ok", "provider": "esimaccess", "action": "send_sms", "provider_response": response}

    @router.post("/api/esim/access/webhooks/provider/register")
    @router.post("/api/esim/webhooks/provider/register")
    async def esimaccess_set_provider_webhook(request: Request) -> dict[str, Any]:
        _require_esim_user(request)
        payload = await read_request_payload(request)
        webhook = str(payload.get("webhook") or "").strip()
        if not webhook:
            raise HTTPException(status_code=400, detail="webhook is required.")
        response = _provider_call(esimaccess_set_webhook, {"webhook": webhook})
        return {"status": "ok", "provider": "esimaccess", "action": "set_webhook", "provider_response": response}

    @router.post("/api/esim/orders/{order_id}/cancel")
    @router.post("/esim/api/orders/{order_id}/cancel", include_in_schema=False)
    async def esimaccess_cancel_order(request: Request, order_id: str) -> dict[str, Any]:
        user = _require_esim_user(request)
        mapped = _query_provider_order(str(order_id or "").strip())
        identity_payload, row = _identity_payload_from_mapped_order(mapped)
        if not identity_payload:
            raise HTTPException(status_code=400, detail="No ICCID/esimTranNo found for this order.")
        response = _provider_call(esimaccess_cancel_profile, identity_payload)
        _upsert_order_snapshot(
            current_user=user,
            request_payload={"bundleName": "", "order_reference": str(order_id or "")},
            provider_result={**mapped, "status": "cancelled", "statusMessage": "cancelled"},
        )
        order_row = _find_order_for_user(user, str(order_id or ""))
        tx = _record_esim_transaction_event(
            current_user=user,
            action="cancel",
            order_row=order_row,
            payload={**identity_payload, "order_reference": str(order_id or "")},
            amount_iqd=0,
            status="successful",
        )
        return {
            "status": "ok",
            "provider": "esimaccess",
            "action": "cancel",
            "order_reference": str(order_id or ""),
            "esim_tran_no": str(row.get("esimTranNo") or ""),
            "transaction_id": str(tx.get("id") or ""),
            "provider_response": response,
        }

    @router.post("/api/esim/orders/{order_id}/refund")
    @router.post("/esim/api/orders/{order_id}/refund", include_in_schema=False)
    async def esimaccess_refund_order(request: Request, order_id: str) -> dict[str, Any]:
        user = _require_esim_user(request)
        mapped = _query_provider_order(str(order_id or "").strip())
        identity_payload, row = _identity_payload_from_mapped_order(mapped)
        if not identity_payload:
            raise HTTPException(status_code=400, detail="No ICCID/esimTranNo found for this order.")
        # eSIMAccess docs define refund via cancel endpoint for unused profiles.
        response = _provider_call(esimaccess_cancel_profile, identity_payload)
        order_row = _find_order_for_user(user, str(order_id or ""))
        refunded_amount = -abs(_to_int((order_row or {}).get("total_iqd"), default=0))
        tx = _record_esim_transaction_event(
            current_user=user,
            action="refund",
            order_row=order_row,
            payload={**identity_payload, "order_reference": str(order_id or "")},
            amount_iqd=refunded_amount,
            status="successful",
            note="Refund via eSIMAccess cancel endpoint",
        )
        return {
            "status": "ok",
            "provider": "esimaccess",
            "action": "refund",
            "order_reference": str(order_id or ""),
            "esim_tran_no": str(row.get("esimTranNo") or ""),
            "transaction_id": str(tx.get("id") or ""),
            "provider_response": response,
        }

    @router.post("/api/esim/orders/{order_id}/topup")
    @router.post("/esim/api/orders/{order_id}/topup", include_in_schema=False)
    async def esimaccess_topup_order(request: Request, order_id: str) -> dict[str, Any]:
        user = _require_esim_user(request)
        payload = await read_request_payload(request)
        mapped = _query_provider_order(str(order_id or "").strip())
        identity_payload, row = _identity_payload_from_mapped_order(mapped)
        if not identity_payload:
            raise HTTPException(status_code=400, detail="No ICCID/esimTranNo found for this order.")
        package_code = str(payload.get("packageCode") or "").strip()
        if not package_code:
            raise HTTPException(status_code=400, detail="packageCode is required for top-up.")
        topup_payload = {**identity_payload, "packageCode": package_code}
        if str(payload.get("transactionId") or "").strip():
            topup_payload["transactionId"] = str(payload.get("transactionId") or "").strip()
        if str(payload.get("amount") or "").strip():
            topup_payload["amount"] = str(payload.get("amount") or "").strip()
        response = _provider_call(esimaccess_topup_profiles, topup_payload)
        order_row = _find_order_for_user(user, str(order_id or ""))
        tx = _record_esim_transaction_event(
            current_user=user,
            action="topup",
            order_row=order_row,
            payload={**payload, **topup_payload, "order_reference": str(order_id or "")},
            amount_iqd=_event_amount_iqd(payload, fallback=0),
            status="successful",
        )
        return {
            "status": "ok",
            "provider": "esimaccess",
            "action": "topup",
            "order_reference": str(order_id or ""),
            "esim_tran_no": str(row.get("esimTranNo") or ""),
            "transaction_id": str(tx.get("id") or ""),
            "provider_response": response,
        }

    @router.get("/api/esim/access/balance")
    @router.get("/api/esim/balance")
    async def esimaccess_balance(request: Request) -> dict[str, Any]:
        _require_esim_user(request)
        return _provider_call(esimaccess_balance_query)

    @router.get("/reports/esim/api/list")
    async def esim_report_list(
        request: Request,
        successful_only: bool = Query(default=False),
    ) -> dict[str, Any]:
        user = _require_esim_user(request)
        owner_id = effective_owner_user_id(user)
        if is_sub_user(user):
            orders = list_esimaccess_orders_for_agent(owner_id, str(user.get("id") or ""))
        else:
            orders = list_esimaccess_orders_for_owner(owner_id)

        if successful_only:
            success_statuses = {"successful", "success", "completed", "issued", "active"}
            orders = [row for row in orders if str(row.get("status") or "").strip().lower() in success_statuses]

        normalized = [_normalize_report_row(row, user) for row in orders if isinstance(row, dict)]

        # If no locally tracked rows exist yet, fallback to live provider list so report isn't empty.
        if not normalized:
            try:
                query_payload = {"orderNo": "", "iccid": "", "pager": {"pageNum": 1, "pageSize": 100}}
                response = esimaccess_query_profiles(query_payload)
                if _is_access_success(response):
                    obj = response.get("obj") if isinstance(response.get("obj"), dict) else {}
                    esim_list = obj.get("esimList") if isinstance(obj, dict) else []
                    if isinstance(esim_list, list):
                        for raw in esim_list:
                            if not isinstance(raw, dict):
                                continue
                            order_no = str(raw.get("orderNo") or "").strip()
                            if not order_no:
                                continue
                            mapped = _map_access_query_to_order(order_no, {"success": True, "obj": {"esimList": [raw]}})
                            _upsert_order_snapshot(current_user=user, request_payload={"bundleName": ""}, provider_result=mapped)
                            normalized.append(_normalize_report_row({"order_reference": order_no, **mapped}, user))
            except Exception:
                pass

        return {"status": "ok", "count": len(normalized), "orders": normalized}

    return router


def _mount_colocated_frontend(app: FastAPI) -> None:
    candidates = [
        APP_DIR / "frontend" / "dist",
        APP_DIR / "frontend",
    ]
    for static_dir in candidates:
        if static_dir.exists() and (static_dir / "index.html").exists():
            app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="esimaccess-frontend")
            return


def create_app() -> FastAPI:
    app = FastAPI(title="The Book eSIMAccess Backend", version="1.0.0")
    configure_cors(app)
    app.include_router(create_auth_router(), prefix="/api/auth")
    app.include_router(create_auth_compat_router())
    app.include_router(create_router())

    @app.middleware("http")
    async def _protect_public_signup(request: Request, call_next):
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
        return await call_next(request)

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "ok": True,
            "service": "esimaccess",
            "build": BUILD_ID,
            "provider_configured": esimaccess_is_configured(),
        }

    @app.get("/__build")
    async def build() -> dict[str, str]:
        return {"build": BUILD_ID, "service": "esimaccess"}

    _mount_colocated_frontend(app)
    return app
