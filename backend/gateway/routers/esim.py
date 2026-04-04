from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from backend.auth.service import display_name, effective_owner_user_id, is_sub_user, resolve_token
from backend.esim.esimaccess.store import (
    load_esimaccess_orders_items,
    list_esimaccess_orders_for_agent,
    list_esimaccess_orders_for_owner,
    record_esimaccess_order,
    update_esimaccess_order_by_reference,
)
from backend.esim.esimaccess.service import (
    balance_query as esim_access_balance_query,
    cancel_profile as esim_access_cancel_profile,
    is_configured as esim_access_is_configured,
    list_packages as esim_access_list_packages,
    order_profiles as esim_access_order_profiles,
    query_profiles as esim_access_query_profiles,
    topup_profiles as esim_access_topup_profiles,
)
from backend.esim.oasis.service import (
    balance as esim_balance,
    create_order as esim_create_order,
    get_order as esim_get_order,
    list_bundles as esim_list_bundles,
    list_orders as esim_list_orders,
    load_config as load_esim_config,
    ping as esim_ping,
    quote as esim_quote,
    save_config as save_esim_config,
)
from backend.gateway.esim_shared import (
    cache_delete_prefix,
    cache_get,
    cache_set,
    load_catalog_cache_with_fallback,
    save_catalog_cache,
)
from backend.gateway.admin_auth import require_super_admin_request
from backend.gateway.permissions_store import _api_policy, _service_enabled
from backend.communications.twilio_whatsapp.service import send_whatsapp_many
from backend.transactions.store import load_transactions_items, save_transactions_items

router = APIRouter()
DEFAULT_SMDP_ADDRESS = os.getenv("ESIMACCESS_DEFAULT_SMDP", "rsp-eu.simlessly.com")

_ESIM_BUNDLES_CACHE: dict[str, dict] = {}
_ESIM_BUNDLES_TTL_SEC = 300
_TERMINAL_ESIM_STATUS_RE = re.compile(
    r"\b(expired|refund|refunded|refunding|rfd|cancel|cancelled|canceled|cancelling|canceling|cnl|revoke|revoked|revoking|rvk|void|voided|terminated|closed)\b",
    re.IGNORECASE,
)


def clear_esim_runtime_caches() -> None:
    _ESIM_BUNDLES_CACHE.clear()
    cache_delete_prefix("esim:web:")


def _notify_manual_mode_whatsapp(policy: dict, message: str) -> None:
    try:
        if not isinstance(policy, dict):
            return
        if str(policy.get("sellable_mode") or "").strip().lower() != "manual":
            return
        if not bool(policy.get("notify_whatsapp_enabled")):
            return
        recipients = policy.get("notify_whatsapp_numbers") or []
        if not isinstance(recipients, list) or not recipients:
            return
        send_whatsapp_many(recipients, message)
    except Exception:
        return


def _auth_token_from_request(request: Request) -> str:
    auth_header = str(request.headers.get("Authorization") or "").strip()
    if auth_header.lower().startswith("bearer "):
        return auth_header.split(" ", 1)[1].strip()
    return str(request.cookies.get("auth_token") or "").strip()


def _require_authenticated_user(request: Request) -> dict:
    user = resolve_token(_auth_token_from_request(request))
    if user:
        return user
    raise HTTPException(status_code=401, detail="Unauthorized")


def _maybe_authenticated_user(request: Request) -> dict | None:
    token = _auth_token_from_request(request)
    if not token:
        return None
    return resolve_token(token)


def _ensure_esim_service_enabled() -> None:
    if not _service_enabled("esim"):
        raise HTTPException(status_code=503, detail="eSIM service is disabled by admin permissions.")


def _get_provider_policies() -> dict[str, dict]:
    return {
        "esim_oasis": _api_policy("esim_oasis"),
        "esim_access": _api_policy("esim_access"),
    }


def _provider_enabled(policies: dict[str, dict], provider: str) -> bool:
    row = policies.get(provider) if isinstance(policies, dict) else None
    if not isinstance(row, dict):
        return False
    return bool(row.get("enabled"))


def _ensure_any_esim_api_enabled() -> dict[str, dict]:
    policies = _get_provider_policies()
    if not _provider_enabled(policies, "esim_oasis") and not _provider_enabled(policies, "esim_access"):
        raise HTTPException(status_code=503, detail="All eSIM APIs are disabled by admin permissions.")
    return policies


def _esim_cache_key(params: dict | None, settings: dict, policies: dict[str, dict] | None = None) -> str:
    policies = policies if isinstance(policies, dict) else {}
    payload = {
        "params": params or {},
        "allowed": settings.get("allowed_countries") or [],
        "allowed_regions": settings.get("allowed_regions") or [],
        "blocked": settings.get("blocked_countries") or [],
        "blocked_regions": settings.get("blocked_regions") or [],
        "fx_rate": settings.get("fx_rate") or 0,
        "markup_percent": settings.get("markup_percent") or 0,
        "markup_fixed_iqd": settings.get("markup_fixed_iqd") or 0,
        "providers": {
            "esim_oasis": bool((policies.get("esim_oasis") or {}).get("enabled")),
            "esim_access": bool((policies.get("esim_access") or {}).get("enabled")),
        },
    }
    return json.dumps(payload, sort_keys=True)


def _countries_index_cache_key(settings: dict) -> str:
    payload = {
        "allowed": settings.get("allowed_countries") or [],
        "allowed_regions": settings.get("allowed_regions") or [],
        "blocked": settings.get("blocked_countries") or [],
        "blocked_regions": settings.get("blocked_regions") or [],
    }
    return f"esim:web:countries-index:{json.dumps(payload, sort_keys=True)}"


def _esim_cache_get(key: str) -> dict | None:
    item = _ESIM_BUNDLES_CACHE.get(key)
    if not item:
        return None
    ts = float(item.get("ts") or 0)
    if (time.time() - ts) > _ESIM_BUNDLES_TTL_SEC:
        _ESIM_BUNDLES_CACHE.pop(key, None)
        return None
    return item.get("value")


def _esim_cache_set(key: str, value: dict) -> None:
    _ESIM_BUNDLES_CACHE[key] = {"ts": time.time(), "value": value}


def _load_oasis_catalog_cache_with_fallback() -> dict:
    return load_catalog_cache_with_fallback(esim_list_bundles, cooldown_sec=120)


@router.get("/api/other-apis/esim")
async def esim_config_get(request: Request):
    require_super_admin_request(request)
    return load_esim_config()


@router.post("/api/other-apis/esim")
async def esim_config_set(request: Request, payload: dict):
    require_super_admin_request(request)
    try:
        cfg = save_esim_config(payload or {})
        clear_esim_runtime_caches()
        return cfg
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/other-apis/esim/ping")
async def esim_ping_endpoint(request: Request):
    require_super_admin_request(request)
    try:
        return esim_ping()
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/other-apis/esim/catalog-refresh")
async def esim_catalog_refresh(request: Request):
    require_super_admin_request(request)
    try:
        data = await run_in_threadpool(esim_list_bundles, params=None)
        if not isinstance(data, dict):
            raise HTTPException(status_code=400, detail="Invalid catalog response.")
        save_catalog_cache(data)
        clear_esim_runtime_caches()
        return {"status": "ok", "items": len(data.get("items") or data.get("bundles") or [])}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


def _esim_settings() -> dict:
    def _norm_codes(values: object) -> list[str]:
        if not isinstance(values, list):
            return []
        return [str(x).strip().upper() for x in values if str(x).strip()]

    def _norm_regions(values: object) -> list[str]:
        if not isinstance(values, list):
            return []
        return [str(x).strip() for x in values if str(x).strip()]

    cfg = load_esim_config()
    settings = cfg.get("settings") if isinstance(cfg.get("settings"), dict) else {}
    settings["allowed_countries"] = _norm_codes(settings.get("allowed_countries"))
    settings["allowed_regions"] = _norm_regions(settings.get("allowed_regions"))
    settings["blocked_countries"] = _norm_codes(settings.get("blocked_countries"))
    settings["blocked_regions"] = _norm_regions(settings.get("blocked_regions"))
    popular = settings.get("popular_destinations")
    if not isinstance(popular, list):
        popular = []
    norm_popular = []
    for item in popular:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        norm_popular.append(
            {
                "name": name,
                "iso": str(item.get("iso") or "").strip().upper(),
                "initials": str(item.get("initials") or "").strip().upper(),
            }
        )
    settings["popular_destinations"] = norm_popular
    return settings


def _region_key(value: object) -> str:
    s = str(value or "").strip().lower()
    return "".join(ch for ch in s if ch.isalnum())


def _country_allowed(country: dict, settings: dict) -> bool:
    iso = str(country.get("iso") or "").strip().upper()
    region = _region_key(country.get("region"))
    allowed_countries = {str(x).strip().upper() for x in (settings.get("allowed_countries") or []) if str(x).strip()}
    blocked_countries = {str(x).strip().upper() for x in (settings.get("blocked_countries") or []) if str(x).strip()}
    allowed_regions = {_region_key(x) for x in (settings.get("allowed_regions") or []) if str(x).strip()}
    blocked_regions = {_region_key(x) for x in (settings.get("blocked_regions") or []) if str(x).strip()}

    if iso and iso in blocked_countries:
        return False
    if region and region in blocked_regions:
        return False

    if not allowed_countries and not allowed_regions:
        return True

    return (iso and iso in allowed_countries) or (region and region in allowed_regions)


def _esim_apply_country_filter(item: dict, settings: dict) -> tuple[bool, dict]:
    countries = item.get("countries") or []
    if not isinstance(countries, list):
        countries = []
    filtered = []
    for c in countries:
        if not isinstance(c, dict):
            continue
        if _country_allowed(c, settings):
            filtered.append(c)
    if not filtered:
        return False, item
    item["countries"] = filtered
    return True, item


def _esim_apply_pricing(item: dict, settings: dict) -> dict:
    price = item.get("price") or {}
    try:
        usd_minor = price.get("finalMinor")
        if usd_minor is None:
            return item
        usd_minor = float(usd_minor)
        fx = float(settings.get("fx_rate") or 0)
        if fx <= 0:
            return item
        iqd = (usd_minor / 100.0) * fx
        pct = float(settings.get("markup_percent") or 0)
        if pct:
            iqd = iqd * (1 + pct / 100.0)
        fixed = float(settings.get("markup_fixed_iqd") or 0)
        if fixed:
            iqd += fixed
        iqd_final = int(round(iqd))
        price["finalMinor"] = iqd_final
        price["currency"] = "IQD"
        item["price"] = price
        item["price_usd_minor"] = int(usd_minor)
        item["fx_rate"] = fx
        item["markup_percent"] = pct
        item["markup_fixed_iqd"] = fixed
        return item
    except Exception:
        return item


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


def _access_price_divisor() -> int:
    # eSIM Access package price is often returned in a scaled integer.
    # Default divisor=100 maps values like 70000 -> 700 (USD minor, i.e. $7.00).
    div = _to_int(os.getenv("ESIMACCESS_PRICE_DIVISOR"), default=100)
    return max(1, div)


def _access_price_to_usd_minor(raw_value: object) -> int:
    raw = _to_int(raw_value, default=0)
    if raw <= 0:
        return 0
    divisor = _access_price_divisor()
    return int(round(float(raw) / float(divisor)))


def _bundle_price_minor(item: dict) -> int:
    price = item.get("price") if isinstance(item, dict) else {}
    if not isinstance(price, dict):
        return 0
    return _to_int(price.get("finalMinor"), default=0)


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _split_customer_name(customer_name: object) -> tuple[str, str]:
    parts = [part for part in str(customer_name or "").strip().split() if part]
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def _provider_call(fn, *args, **kwargs) -> dict:
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


def _query_access_provider_order(order_reference: str) -> dict:
    query_payload = {"orderNo": str(order_reference or "").strip(), "iccid": "", "pager": {"pageNum": 1, "pageSize": 20}}
    response = _provider_call(esim_access_query_profiles, query_payload)
    obj = response.get("obj") if isinstance(response.get("obj"), dict) else {}
    esim_list = obj.get("esimList") if isinstance(obj, dict) else []
    if not isinstance(esim_list, list) or not esim_list:
        raise HTTPException(status_code=404, detail="Order not found.")
    return _map_access_query_to_order(str(order_reference or "").strip(), response)


def _has_terminal_esim_status_signal(*values: object) -> bool:
    for value in values:
        text = str(value or "").strip().lower()
        if text and _TERMINAL_ESIM_STATUS_RE.search(text):
            return True
    return False


def _stored_esimaccess_order_snapshot(order_reference: str) -> dict | None:
    target = str(order_reference or "").strip()
    if not target:
        return None

    for row in load_esimaccess_orders_items():
        if not isinstance(row, dict):
            continue
        if str(row.get("order_reference") or row.get("orderReference") or "").strip() == target:
            return row
    return None


def _map_stored_snapshot_to_order(order_reference: str, snapshot: dict) -> dict:
    activation_codes = snapshot.get("activation_codes") if isinstance(snapshot.get("activation_codes"), list) else []
    iccid = str(snapshot.get("iccid") or "").strip()
    status_message = str(snapshot.get("status_message") or snapshot.get("statusMessage") or snapshot.get("status") or "").strip()
    normalized_status = "expired" if _has_terminal_esim_status_signal(
        snapshot.get("status"),
        snapshot.get("status_message"),
        snapshot.get("statusMessage"),
        snapshot.get("raw_query"),
    ) else str(snapshot.get("status") or "processing").strip().lower()

    return {
        "provider": "esim_access",
        "status": normalized_status,
        "statusMessage": status_message,
        "orderReference": str(order_reference or "").strip(),
        "orderNo": str(order_reference or "").strip(),
        "activationCodes": activation_codes,
        "iccidList": [iccid] if iccid else [],
        "quickInstallUrl": str(snapshot.get("quick_install_url") or "").strip(),
        "raw": {
            "snapshot": snapshot,
        },
    }


def _first_raw_esim_item(mapped_order: dict | None) -> dict:
    mapped = mapped_order if isinstance(mapped_order, dict) else {}
    raw = mapped.get("raw") if isinstance(mapped.get("raw"), dict) else {}
    obj = raw.get("obj") if isinstance(raw.get("obj"), dict) else {}
    esim_list = obj.get("esimList") if isinstance(obj.get("esimList"), list) else []
    if esim_list and isinstance(esim_list[0], dict):
        return esim_list[0]
    return {}


def _identity_payload_from_mapped_order(mapped_order: dict) -> tuple[dict, dict]:
    row = _first_raw_esim_item(mapped_order)
    esim_tran_no = str(row.get("esimTranNo") or "").strip()
    iccid = str(row.get("iccid") or "").strip()
    payload: dict = {}
    if esim_tran_no:
        payload["esimTranNo"] = esim_tran_no
    elif iccid:
        payload["iccid"] = iccid
    return payload, row


def _orders_for_user(user: dict) -> list[dict]:
    owner_id = effective_owner_user_id(user)
    if is_sub_user(user):
        return list_esimaccess_orders_for_agent(owner_id, str(user.get("id") or ""))
    return list_esimaccess_orders_for_owner(owner_id)


def _find_order_for_user(user: dict, order_reference: str) -> dict | None:
    target = str(order_reference or "").strip()
    if not target:
        return None
    for row in _orders_for_user(user):
        if not isinstance(row, dict):
            continue
        if str(row.get("order_reference") or row.get("orderReference") or "").strip() == target:
            return row
    return None


def _event_amount_iqd(payload: dict, fallback: int = 0) -> int:
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


def _record_esimaccess_event_transaction(
    *,
    current_user: dict,
    action: str,
    order_row: dict | None,
    payload: dict | None = None,
    amount_iqd: int = 0,
    note: str = "",
) -> dict:
    event = str(action or "").strip().lower()
    if not event:
        raise HTTPException(status_code=400, detail="Missing transaction event.")

    payload = payload if isinstance(payload, dict) else {}
    row = order_row if isinstance(order_row, dict) else {}
    owner_id = effective_owner_user_id(current_user)
    order_reference = str(payload.get("order_reference") or payload.get("orderReference") or row.get("order_reference") or "").strip()
    tx_id_hint = str(payload.get("transactionId") or payload.get("idempotencyKey") or "").strip()
    event_key = f"{owner_id}:{order_reference}:{event}:{tx_id_hint}"

    tx_items = load_transactions_items()
    for item in tx_items:
        if not isinstance(item, dict):
            continue
        details = item.get("details") if isinstance(item.get("details"), dict) else {}
        if str(details.get("esim_event_key") or "").strip() == event_key and event_key.strip(":"):
            return item
        if not tx_id_hint and str(details.get("order_reference") or "").strip() == order_reference:
            if str(details.get("esim_event") or "").strip().lower() == event:
                return item

    customer_name = str(payload.get("customer_name") or row.get("customer_name") or "").strip()
    first_name, last_name = _split_customer_name(customer_name)
    company_name = str(row.get("company_name") or current_user.get("company_name") or current_user.get("company") or "").strip()
    iccid, esim_tran_no = _esimaccess_identity_from_order(row)
    final_amount = _to_int(amount_iqd, default=0)
    if final_amount == 0:
        final_amount = _event_amount_iqd(payload, fallback=_to_int(row.get("total_iqd"), default=0))

    now = _now_iso()
    tx = {
        "id": uuid4().hex,
        "ts": now,
        "updated_at": now,
        "service": "sim",
        "status": "successful",
        "price": final_amount,
        "currency": str(row.get("currency") or "IQD").strip().upper(),
        "company_admin_id": owner_id,
        "by_user_id": str(current_user.get("id") or "").strip(),
        "by": display_name(current_user),
        "user_id": owner_id,
        "provider_id": "esimaccess",
        "booking_code": order_reference,
        "company_name": company_name,
        "first_name": first_name,
        "last_name": last_name,
        "details": {
            "esim_event": event,
            "esim_event_key": event_key,
            "order_reference": order_reference,
            "bundle_name": str(payload.get("bundle_name") or row.get("bundle_name") or "").strip(),
            "country_name": str(payload.get("country_name") or row.get("country_name") or "").strip(),
            "country_iso": str(payload.get("country_iso") or row.get("country_iso") or "").strip().upper(),
            "iccid": iccid,
            "esim_tran_no": esim_tran_no,
            "customer_name": customer_name,
            "company_name": company_name,
            "provider_id": "esimaccess",
            "action": event,
        },
    }
    if note:
        tx["details"]["note"] = note
    tx_items.append(tx)
    save_transactions_items(tx_items)
    return tx


def _persist_esimaccess_order_snapshot(
    *,
    current_user: dict,
    request_payload: dict,
    provider_result: dict,
) -> dict | None:
    reference = str(provider_result.get("orderReference") or provider_result.get("orderNo") or "").strip()
    if not reference:
        return None

    payload = request_payload if isinstance(request_payload, dict) else {}
    provider_data = provider_result if isinstance(provider_result, dict) else {}
    meta = payload.get("_meta") if isinstance(payload.get("_meta"), dict) else {}
    quantity = max(1, _to_int(payload.get("quantity"), default=1))
    total_iqd = _to_int(payload.get("total_iqd"), default=0)
    if total_iqd <= 0:
        total_iqd = _to_int(meta.get("total_iqd"), default=0)
    if total_iqd <= 0:
        unit_iqd = _to_int(meta.get("unit_price_iqd_minor"), default=0)
        if unit_iqd > 0:
            total_iqd = unit_iqd * quantity

    row = {
        "owner_user_id": effective_owner_user_id(current_user),
        "company_name": str(current_user.get("company_name") or current_user.get("company") or "").strip(),
        "agent_user_id": str(current_user.get("id") or "").strip(),
        "agent_name": display_name(current_user),
        "customer_name": str(payload.get("customerName") or payload.get("customer_name") or "").strip(),
        "customer_phone": str(payload.get("customerPhone") or payload.get("customer_phone") or "").strip(),
        "bundle_name": str(payload.get("bundleName") or payload.get("bundle_name") or "").strip(),
        "bundle_description": str(meta.get("bundle_description") or "").strip(),
        "country_name": str(meta.get("country_name") or "").strip(),
        "country_iso": str(meta.get("country_iso") or "").strip().upper(),
        "quantity": quantity,
        "total_iqd": total_iqd if total_iqd > 0 else None,
        "currency": str(payload.get("currency") or "IQD").strip().upper(),
        "status": str(provider_data.get("status") or "processing").strip().lower(),
        "status_message": str(provider_data.get("statusMessage") or "").strip(),
        "order_reference": reference,
        "activation_codes": provider_data.get("activationCodes") if isinstance(provider_data.get("activationCodes"), list) else [],
        "activation_code": str((provider_data.get("activationCodes") or [""])[0] or "").strip()
        if isinstance(provider_data.get("activationCodes"), list)
        else "",
        "quick_install_url": str(provider_data.get("quickInstallUrl") or "").strip(),
        "provider": "esimaccess",
        "payment_method": str(payload.get("payment_method") or "").strip().lower(),
        "payment_fib": bool(payload.get("payment_fib")),
        "payment_amount_iqd": total_iqd if total_iqd > 0 else None,
        "created_at": str(provider_data.get("createdAt") or provider_data.get("created_at") or "").strip() or None,
        "raw_query": provider_data.get("raw") if isinstance(provider_data.get("raw"), dict) else {},
    }

    updated = update_esimaccess_order_by_reference(reference, row)
    if isinstance(updated, dict):
        return updated
    return record_esimaccess_order(row)


def _esimaccess_identity_from_order(order_row: dict) -> tuple[str, str]:
    iccid = str(order_row.get("iccid") or "").strip()
    esim_tran_no = str(order_row.get("esimTranNo") or order_row.get("esim_tran_no") or "").strip()
    raw_query = order_row.get("raw_query") if isinstance(order_row.get("raw_query"), dict) else {}
    obj = raw_query.get("obj") if isinstance(raw_query.get("obj"), dict) else {}
    esim_list = obj.get("esimList") if isinstance(obj.get("esimList"), list) else []
    first = esim_list[0] if esim_list and isinstance(esim_list[0], dict) else {}
    if not iccid:
        iccid = str(first.get("iccid") or "").strip()
    if not esim_tran_no:
        esim_tran_no = str(first.get("esimTranNo") or "").strip()
    return iccid, esim_tran_no


def _sync_esimaccess_purchase_transaction(order_row: dict) -> dict | None:
    if not isinstance(order_row, dict):
        return None

    provider = str(order_row.get("provider") or "").strip().lower()
    if provider not in {"esimaccess", "esim_access"}:
        return None

    owner_id = str(order_row.get("owner_user_id") or "").strip()
    order_reference = str(order_row.get("order_reference") or order_row.get("orderReference") or "").strip()
    if not owner_id or not order_reference:
        return None

    tx_items = load_transactions_items()
    for row in tx_items:
        if not isinstance(row, dict):
            continue
        details = row.get("details") if isinstance(row.get("details"), dict) else {}
        existing_reference = str(details.get("order_reference") or row.get("booking_code") or "").strip()
        if existing_reference != order_reference:
            continue
        existing_service = str(row.get("service") or "").strip().lower()
        existing_provider = str(row.get("provider_id") or details.get("provider_id") or "").strip().lower()
        existing_event = str(details.get("esim_event") or details.get("action") or "").strip().lower()
        if existing_event in {"cancel", "refund", "topup"}:
            continue
        if existing_event == "purchase":
            return row
        if existing_service in {"sim", "esim", "e-sim", "e_sim"} and existing_provider in {"", "esimaccess", "esim_access"}:
            return row

    customer_name = str(order_row.get("customer_name") or "").strip()
    first_name, last_name = _split_customer_name(customer_name)
    company_name = str(order_row.get("company_name") or "").strip()
    amount_iqd = _to_int(order_row.get("total_iqd"), default=0)
    created_at = str(order_row.get("created_at") or order_row.get("updated_at") or "").strip() or _now_iso()
    iccid, esim_tran_no = _esimaccess_identity_from_order(order_row)

    tx = {
        "id": uuid4().hex,
        "ts": created_at,
        "updated_at": _now_iso(),
        "service": "sim",
        "status": str(order_row.get("status") or "completed").strip().lower(),
        "price": amount_iqd if amount_iqd > 0 else None,
        "currency": str(order_row.get("currency") or "IQD").strip().upper(),
        "company_admin_id": owner_id,
        "by_user_id": str(order_row.get("agent_user_id") or "").strip(),
        "by": str(order_row.get("agent_name") or "").strip() or company_name or "User",
        "user_id": owner_id,
        "provider_id": "esimaccess",
        "booking_code": order_reference,
        "company_name": company_name,
        "first_name": first_name,
        "last_name": last_name,
        "details": {
            "esim_event": "purchase",
            "esim_event_key": f"{owner_id}:{order_reference}:purchase:",
            "order_reference": order_reference,
            "bundle_name": str(order_row.get("bundle_name") or "").strip(),
            "country_name": str(order_row.get("country_name") or "").strip(),
            "country_iso": str(order_row.get("country_iso") or "").strip().upper(),
            "customer_name": customer_name,
            "company_name": company_name,
            "provider_id": "esimaccess",
            "action": "purchase",
            "iccid": iccid,
            "esim_tran_no": esim_tran_no,
            "quick_install_url": str(order_row.get("quick_install_url") or "").strip(),
        },
    }
    tx_items.append(tx)
    save_transactions_items(tx_items)
    return tx


def _backfill_esimaccess_purchase_transactions(orders: list[dict]) -> None:
    for row in orders:
        if not isinstance(row, dict):
            continue
        provider = str(row.get("provider") or "").strip().lower()
        status = str(row.get("status") or "").strip().lower()
        if provider not in {"esimaccess", "esim_access"}:
            continue
        if status not in {"successful", "success", "completed", "issued", "active"}:
            continue
        _sync_esimaccess_purchase_transaction(row)


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


def _name_key(text: object) -> str:
    s = str(text or "").strip().lower()
    return "".join(ch for ch in s if ch.isalnum())


def _tier_key(item: dict) -> str:
    desc = str(item.get("description") or item.get("bundleName") or "").strip().lower()
    if not desc:
        return ""
    text = re.sub(r"[^a-z0-9]+", " ", desc)
    # Remove country name tokens so different naming orders still match.
    countries = item.get("countries") if isinstance(item, dict) else []
    if isinstance(countries, list):
        for country in countries:
            if not isinstance(country, dict):
                continue
            country_name = str(country.get("name") or "").strip().lower()
            if not country_name:
                continue
            country_key = re.sub(r"[^a-z0-9]+", " ", country_name).strip()
            if not country_key:
                continue
            text = re.sub(rf"\b{re.escape(country_key)}\b", " ", text)
    # Remove common non-tier tokens and metrics already covered by structured fields.
    text = re.sub(r"\b\d+\s*(gb|mb|day|days)\b", " ", text)
    text = re.sub(r"\b(esim|e sim|v\d+|day|days|gb|mb|unlimited)\b", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return _name_key(text)


def _data_size_key(mb: int, unlimited: bool) -> str:
    if unlimited:
        return "unlimited"
    if mb > 0:
        # Normalize binary vs decimal data sizing (e.g. 1024MB ~= 1000MB, 512MB ~= 500MB)
        canonical = [50, 100, 200, 300, 500, 1000, 2000, 3000, 5000, 10000, 15000, 20000, 30000, 50000, 100000]
        near = min(canonical, key=lambda c: abs(c - mb))
        threshold = max(20, int(round(float(near) * 0.10)))
        if abs(mb - near) <= threshold:
            mb = near
    if mb <= 0:
        return "0mb"
    if mb >= 1024:
        gb = round((float(mb) / 1024.0), 1)
        return f"{gb:.1f}gb"
    if mb >= 100:
        coarse = int(round(float(mb) / 100.0) * 100)
        return f"{coarse}mb"
    coarse = int(round(float(mb) / 50.0) * 50)
    return f"{max(50, coarse)}mb"


def _normalize_oasis_item(item: dict) -> dict:
    out = dict(item or {})
    bundle_name = str(out.get("bundleName") or "").strip()
    if not bundle_name:
        bundle_name = str(out.get("id") or "").strip() or f"oasis::{uuid4().hex[:12]}"
        out["bundleName"] = bundle_name
    out["provider"] = "esim_oasis"
    out["providerBundleCode"] = bundle_name
    return out


def _normalize_access_item(item: dict) -> dict | None:
    if not isinstance(item, dict):
        return None
    package_code = str(item.get("packageCode") or "").strip()
    plan_slug = str(item.get("slug") or "").strip()
    if not package_code and not plan_slug:
        return None
    plan_name = str(item.get("name") or package_code or plan_slug).strip()
    iso_list = _parse_location_codes(item.get("location"))
    country_name_hint = ""
    m_country = re.match(r"^(.+?)\s+\d", plan_name)
    if m_country and len(iso_list) == 1:
        country_name_hint = str(m_country.group(1) or "").strip()
    countries = []
    for iso in iso_list:
        countries.append({"iso": iso, "name": country_name_hint or iso, "region": ""})

    volume_bytes = _to_int(item.get("volume"), default=0)
    data_amount_mb = int(round(volume_bytes / (1024.0 * 1024.0))) if volume_bytes > 0 else 0
    duration = _to_int(item.get("duration"), default=0)
    if duration <= 0:
        duration = _to_int(item.get("unusedValidTime"), default=0)
    data_type = _to_int(item.get("dataType"), default=1)
    daily_plan = data_type == 2 or "/day" in plan_name.lower() or "daily" in plan_slug.lower()
    unlimited = data_type == 4
    provider_price_raw = _to_int(item.get("price"), default=0)
    price_minor = _access_price_to_usd_minor(provider_price_raw)
    bundle_ref = plan_slug if daily_plan and plan_slug else package_code or plan_slug

    out = {
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
        "provider": "esim_access",
        "providerBundleCode": package_code,
        "providerSlug": plan_slug,
        "providerDataType": data_type,
        "provider_price_minor": price_minor,
        "provider_price_raw": provider_price_raw,
        "provider_currency": str(item.get("currencyCode") or "USD").upper(),
    }
    return out


def _dedupe_key(item: dict) -> str:
    countries = item.get("countries") if isinstance(item, dict) else []
    if not isinstance(countries, list):
        countries = []
    iso_list: list[str] = []
    for country in countries:
        if not isinstance(country, dict):
            continue
        iso = str(country.get("iso") or "").strip().upper()
        if iso and iso not in iso_list:
            iso_list.append(iso)
    iso_list.sort()
    data_mb = _to_int(item.get("dataAmountMb"), default=0)
    unlimited = bool(item.get("unlimited"))
    payload = {
        "iso": iso_list,
        "data_key": _data_size_key(data_mb, unlimited),
        "duration_days": _to_int(item.get("durationDays"), default=0),
        "unlimited": unlimited,
        "allowance_mode": str(item.get("allowanceMode") or "total").strip().lower(),
        "tier_key": _tier_key(item),
    }
    return json.dumps(payload, sort_keys=True)


def _merge_and_dedupe_items(items: list[dict]) -> list[dict]:
    if not items:
        return []
    grouped: dict[str, list[dict]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        key = _dedupe_key(item)
        bucket = grouped.get(key)
        if bucket is None:
            grouped[key] = [item]
            continue
        bucket.append(item)

    out: list[dict] = []
    for bucket in grouped.values():
        if not bucket:
            continue
        providers = {str(row.get("provider") or "").strip().lower() for row in bucket if isinstance(row, dict)}
        if len(providers) <= 1:
            out.extend(bucket)
            continue
        chosen = bucket[0]
        for row in bucket[1:]:
            chosen_price = _bundle_price_minor(chosen)
            row_price = _bundle_price_minor(row)
            if chosen_price <= 0 and row_price > 0:
                chosen = row
                continue
            if row_price > 0 and (chosen_price <= 0 or row_price < chosen_price):
                chosen = row
        out.append(chosen)

    out.sort(
        key=lambda row: (
            _bundle_price_minor(row) if _bundle_price_minor(row) > 0 else 10**18,
            str(row.get("description") or row.get("bundleName") or ""),
        )
    )
    return out


def _load_access_catalog(params: dict | None = None) -> list[dict]:
    if not esim_access_is_configured():
        return []
    params = params if isinstance(params, dict) else {}
    payload = {
        "locationCode": str(params.get("locationCode") or "").strip().upper(),
        "type": "BASE",
        "packageCode": "",
        "slug": "",
        "iccid": "",
    }
    out = []
    seen_keys: set[str] = set()
    for body in (payload, {**payload, "dataType": 2}):
        response = esim_access_list_packages(body)
        if not bool(response.get("success")):
            raise ValueError(response.get("errorMsg") or response.get("errorCode") or "eSIM Access package query failed.")
        obj = response.get("obj") if isinstance(response.get("obj"), dict) else {}
        package_list = obj.get("packageList") if isinstance(obj, dict) else []
        if not isinstance(package_list, list):
            continue
        for package in package_list:
            normalized = _normalize_access_item(package)
            if not normalized:
                continue
            key = "|".join(
                [
                    str(normalized.get("providerBundleCode") or "").strip(),
                    str(normalized.get("providerSlug") or "").strip(),
                    str(normalized.get("allowanceMode") or "").strip(),
                ]
            )
            if key in seen_keys:
                continue
            seen_keys.add(key)
            out.append(normalized)
    return out


def _load_merged_catalog(params: dict | None, settings: dict, policies: dict[str, dict]) -> dict:
    params = params if isinstance(params, dict) else {}
    items_all: list[dict] = []
    provider_errors: dict[str, str] = {}

    if _provider_enabled(policies, "esim_oasis"):
        try:
            oasis_data = _load_oasis_catalog_cache_with_fallback()
            oasis_items = oasis_data.get("items") or oasis_data.get("bundles") or []
            for item in oasis_items:
                if not isinstance(item, dict):
                    continue
                items_all.append(_normalize_oasis_item(item))
        except Exception as exc:
            provider_errors["esim_oasis"] = str(exc)

    if _provider_enabled(policies, "esim_access"):
        try:
            items_all.extend(_load_access_catalog(params))
        except Exception as exc:
            provider_errors["esim_access"] = str(exc)

    filtered_items: list[dict] = []
    for item in items_all:
        if not isinstance(item, dict):
            continue
        ok, filtered_item = _esim_apply_country_filter(dict(item), settings)
        if not ok:
            continue
        filtered_items.append(_esim_apply_pricing(filtered_item, settings))

    deduped_items = _merge_and_dedupe_items(filtered_items)
    payload = {
        "items": deduped_items,
        "bundles": deduped_items,
        "providers": {
            "esim_oasis": {
                "enabled": _provider_enabled(policies, "esim_oasis"),
                "error": provider_errors.get("esim_oasis"),
            },
            "esim_access": {
                "enabled": _provider_enabled(policies, "esim_access"),
                "configured": esim_access_is_configured(),
                "error": provider_errors.get("esim_access"),
            },
        },
        "counts": {
            "before_dedupe": len(filtered_items),
            "after_dedupe": len(deduped_items),
        },
    }
    return payload


def _find_bundle_in_merged_catalog(bundle_name: str, settings: dict, policies: dict[str, dict]) -> dict | None:
    target = str(bundle_name or "").strip()
    if not target:
        return None
    merged = _load_merged_catalog(params={}, settings=settings, policies=policies)
    items = merged.get("items") if isinstance(merged, dict) else []
    if not isinstance(items, list):
        return None
    for item in items:
        if not isinstance(item, dict):
            continue
        if str(item.get("bundleName") or "").strip() == target:
            return item
    return None


def _is_access_success(payload: dict | None) -> bool:
    if not isinstance(payload, dict):
        return False
    return bool(payload.get("success"))


def _activation_lpa_from_access_row(row: dict | None) -> str:
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


def _extract_install_url_from_access_row(row: dict | None) -> str:
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
            for v in value.values():
                _walk(v)
            return
        if isinstance(value, list):
            for v in value:
                _walk(v)

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


def _map_access_query_to_order(order_no: str, response: dict | None) -> dict:
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

    status = "processing"
    if esim_status in {"CANCEL", "REVOKED"}:
        status = "revoked"
    elif activation_code:
        status = "completed"

    status_msg = " ".join(part for part in [esim_status, smdp_status] if part).strip()
    return {
        "provider": "esim_access",
        "status": status,
        "statusMessage": status_msg,
        "orderReference": order_no,
        "orderNo": order_no,
        "activationCodes": [activation_code] if activation_code else [],
        "iccidList": [iccid] if iccid else [],
        "quickInstallUrl": install_url,
        "raw": response,
    }


def _build_countries_index_payload(data: dict | None, settings: dict) -> dict:
    if not isinstance(data, dict):
        return {"items": []}
    items = data.get("items") or data.get("bundles") or []
    if not isinstance(items, list):
        return {"items": []}
    buckets: dict[str, dict] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        bundle_price = _bundle_price_minor(item)
        bundle_price = bundle_price if bundle_price > 0 else None
        countries = item.get("countries") or []
        if not isinstance(countries, list):
            continue
        for c in countries:
            if not isinstance(c, dict):
                continue
            if not _country_allowed(c, settings):
                continue
            name = str(c.get("name") or "").strip()
            iso = str(c.get("iso") or "").strip().upper()
            region = str(c.get("region") or "").strip()
            if not name and not iso:
                continue
            display_name = name or iso
            key = iso or display_name.lower()
            row = buckets.get(key)
            if row is None:
                row = {
                    "name": display_name,
                    "iso": iso,
                    "region": region,
                    "count": 0,
                    "min": bundle_price,
                }
                buckets[key] = row
            row["count"] = _to_int(row.get("count"), default=0) + 1
            if region and not str(row.get("region") or "").strip():
                row["region"] = region
            if bundle_price is not None:
                cur_min = _to_int(row.get("min"), default=0)
                if cur_min <= 0 or bundle_price < cur_min:
                    row["min"] = bundle_price
    out = list(buckets.values())
    out.sort(key=lambda x: (str(x.get("name") or "").lower(), str(x.get("iso") or "")))
    return {"items": out}


def prewarm_esim_runtime_caches() -> dict:
    """Warm eSIM catalog/index caches so first user request is fast after cold start."""
    result = {"ok": False, "bundles_cached": False, "countries_index_cached": False}
    _ensure_esim_service_enabled()
    policies = _ensure_any_esim_api_enabled()
    settings = _esim_settings()
    params: dict = {}
    merged = _load_merged_catalog(params=params, settings=settings, policies=policies)
    _esim_cache_set(_esim_cache_key(params, settings, policies), merged)
    result["bundles_cached"] = True

    countries_payload = _build_countries_index_payload(merged, settings)
    cache_set(_countries_index_cache_key(settings), countries_payload, ttl_sec=180)
    result["countries_index_cached"] = True
    result["ok"] = True
    return result


@router.get("/esim/api/bundles", include_in_schema=False)
@router.get("/api/esim/bundles")
async def esim_bundles(request: Request):
    try:
        _ensure_esim_service_enabled()
        policies = _ensure_any_esim_api_enabled()
        params = dict(request.query_params)
        settings = _esim_settings()
        cache_key = _esim_cache_key(params, settings, policies)
        cached = _esim_cache_get(cache_key)
        if cached:
            return cached
        data = _load_merged_catalog(params=params, settings=settings, policies=policies)
        _esim_cache_set(cache_key, data)
        return data
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/esim/api/countries-index", include_in_schema=False)
@router.get("/api/esim/countries-index")
async def esim_countries_index():
    try:
        _ensure_esim_service_enabled()
        policies = _ensure_any_esim_api_enabled()
        settings = _esim_settings()
        cache_key = _countries_index_cache_key(settings)
        cached = cache_get(cache_key)
        if cached is not None:
            return cached
        data = _load_merged_catalog(params={}, settings=settings, policies=policies)
        payload = _build_countries_index_payload(data, settings)
        cache_set(cache_key, payload, ttl_sec=180)
        return payload
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/esim/api/quote", include_in_schema=False)
@router.post("/api/esim/quote")
async def esim_quote_endpoint(payload: dict):
    try:
        _ensure_esim_service_enabled()
        policies = _ensure_any_esim_api_enabled()
        body = payload or {}
        bundle_name = str(body.get("bundleName") or "").strip()
        settings = _esim_settings()
        data = _find_bundle_in_merged_catalog(bundle_name, settings, policies)
        if data is None:
            raise HTTPException(status_code=404, detail="Bundle not found in cached catalog.")
        return data
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/esim/api/orders", include_in_schema=False)
@router.post("/api/esim/orders")
async def esim_order_create(request: Request, payload: dict):
    try:
        _ensure_esim_service_enabled()
        policies = _ensure_any_esim_api_enabled()
        settings = _esim_settings()
        body = dict(payload or {})
        bundle_name = str(body.get("bundleName") or "").strip()
        if not bundle_name:
            raise HTTPException(status_code=400, detail="bundleName is required.")
        selected_bundle = _find_bundle_in_merged_catalog(bundle_name, settings, policies)
        if not selected_bundle:
            raise HTTPException(status_code=404, detail="Bundle not found.")
        provider = str(selected_bundle.get("provider") or "esim_oasis").strip().lower()
        provider_policy = policies.get(provider) if isinstance(policies, dict) else {}
        if not isinstance(provider_policy, dict) or not bool(provider_policy.get("enabled")):
            raise HTTPException(status_code=503, detail=f"{provider} API is disabled by admin permissions.")

        if "idempotencyKey" not in body and body.get("idempotency_key"):
            body["idempotencyKey"] = body.pop("idempotency_key")

        if not bool(provider_policy.get("schedule_ok")) or str(provider_policy.get("sellable_mode") or "online") != "online":
            _notify_manual_mode_whatsapp(
                provider_policy,
                f"Tulip Bookings - eSIM pending request\nProvider: {provider}\nReason: eSIM selling is set to manual/offline mode by admin permissions.",
            )
            return {
                "pending": True,
                "status": "pending",
                "pending_kind": "esim_manual_fulfillment",
                "provider": provider,
                "reason": "eSIM selling is set to manual/offline mode by admin permissions.",
            }

        if provider == "esim_oasis":
            idempotency_key = str(body.get("idempotencyKey") or "").strip() or None
            oasis_data = esim_create_order(body, idempotency_key=idempotency_key)
            if isinstance(oasis_data, dict):
                oasis_data.setdefault("provider", "esim_oasis")
            return oasis_data

        if provider != "esim_access":
            raise HTTPException(status_code=400, detail=f"Unsupported provider: {provider}")

        quantity = max(1, _to_int(body.get("quantity"), default=1))
        package_code = str(selected_bundle.get("providerBundleCode") or "").strip()
        plan_slug = str(selected_bundle.get("providerSlug") or "").strip()
        allowance_mode = str(selected_bundle.get("allowanceMode") or "total").strip().lower()
        if not package_code and bundle_name.startswith("ea::") and allowance_mode != "per_day":
            package_code = bundle_name.split("ea::", 1)[1]
        if allowance_mode != "per_day" and not package_code:
            raise HTTPException(status_code=400, detail="Invalid eSIM Access package code.")
        if allowance_mode == "per_day" and not plan_slug:
            raise HTTPException(status_code=400, detail="Invalid eSIM Access day-pass slug.")

        unit_price_raw = _to_int(selected_bundle.get("provider_price_raw"), default=0)
        if unit_price_raw <= 0:
            unit_price_raw = _to_int(selected_bundle.get("provider_price_minor"), default=0)
        unit_price_minor = unit_price_raw
        if unit_price_minor <= 0:
            unit_price_minor = _to_int(selected_bundle.get("price_usd_minor"), default=0)
        if unit_price_minor <= 0:
            unit_price_minor = _bundle_price_minor(selected_bundle)
        if unit_price_minor <= 0:
            raise HTTPException(status_code=400, detail="Unable to resolve eSIM Access package price.")

        transaction_id = str(body.get("idempotencyKey") or "").strip() or f"tb-{uuid4().hex[:24]}"
        period_num = 1
        if allowance_mode == "per_day":
            period_num = max(
                1,
                _to_int(
                    body.get("periodNum")
                    or body.get("period_num")
                    or body.get("durationDays")
                    or selected_bundle.get("durationDays"),
                    default=1,
                ),
            )
        order_item = {"count": quantity, "price": unit_price_minor}
        if allowance_mode == "per_day":
            order_item["slug"] = plan_slug
            order_item["periodNum"] = period_num
        else:
            order_item["packageCode"] = package_code
        order_payload = {
            "transactionId": transaction_id,
            "amount": unit_price_minor * quantity * period_num,
            "packageInfoList": [order_item],
        }
        order_resp = esim_access_order_profiles(order_payload)
        if not _is_access_success(order_resp):
            raise HTTPException(status_code=400, detail=order_resp)
        order_obj = order_resp.get("obj") if isinstance(order_resp.get("obj"), dict) else {}
        order_no = str(order_obj.get("orderNo") or "").strip()
        if not order_no:
            raise HTTPException(status_code=400, detail="eSIM Access orderNo missing.")

        query_payload = {"orderNo": order_no, "iccid": "", "pager": {"pageNum": 1, "pageSize": 20}}
        try:
            query_resp = esim_access_query_profiles(query_payload)
            mapped = _map_access_query_to_order(order_no, query_resp)
        except Exception:
            mapped = _map_access_query_to_order(order_no, {})
        mapped["provider"] = "esim_access"
        mapped["transactionId"] = str(order_obj.get("transactionId") or transaction_id)
        mapped["allowanceMode"] = allowance_mode
        if allowance_mode == "per_day":
            mapped["periodNum"] = period_num
            mapped["providerSlug"] = plan_slug
        mapped["raw_order"] = order_resp
        current_user = _maybe_authenticated_user(request)
        if current_user:
            try:
                order_row = _persist_esimaccess_order_snapshot(
                    current_user=current_user,
                    request_payload=body,
                    provider_result=mapped,
                )
                if order_row:
                    _sync_esimaccess_purchase_transaction(order_row)
            except Exception:
                pass
        return mapped
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/esim/api/orders", include_in_schema=False)
@router.get("/api/esim/orders")
async def esim_orders_list(request: Request):
    try:
        _ensure_esim_service_enabled()
        policies = _ensure_any_esim_api_enabled()
        params = dict(request.query_params)
        settings = _esim_settings()

        oasis_items: list[dict] = []
        oasis_raw: dict | None = None
        if _provider_enabled(policies, "esim_oasis"):
            oasis_data = esim_list_orders(params=params or None)
            if isinstance(oasis_data, dict):
                oasis_raw = dict(oasis_data)
                items = oasis_data.get("items") or []
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    row = dict(item)
                    row["provider"] = "esim_oasis"
                    if settings.get("fx_rate"):
                        try:
                            usd_minor = float(row.get("totalMinor"))
                        except Exception:
                            usd_minor = None
                        if usd_minor is not None:
                            fx = float(settings.get("fx_rate") or 0)
                            pct = float(settings.get("markup_percent") or 0)
                            fixed = float(settings.get("markup_fixed_iqd") or 0)
                            iqd = (usd_minor / 100.0) * fx
                            if pct:
                                iqd = iqd * (1 + pct / 100.0)
                            if fixed:
                                iqd += fixed
                            row["total_iqd"] = int(round(iqd))
                    oasis_items.append(row)

        access_items: list[dict] = []
        if _provider_enabled(policies, "esim_access"):
            page_num = max(1, _to_int(params.get("pageNum"), default=1))
            page_size = max(1, min(100, _to_int(params.get("pageSize"), default=50)))
            query_payload = {"orderNo": "", "iccid": "", "pager": {"pageNum": page_num, "pageSize": page_size}}
            access_data = esim_access_query_profiles(query_payload)
            if _is_access_success(access_data):
                obj = access_data.get("obj") if isinstance(access_data.get("obj"), dict) else {}
                esim_list = obj.get("esimList") if isinstance(obj, dict) else []
                if isinstance(esim_list, list):
                    for row in esim_list:
                        if not isinstance(row, dict):
                            continue
                        order_no = str(row.get("orderNo") or "").strip()
                        mapped = _map_access_query_to_order(order_no, {"obj": {"esimList": [row]}, "success": True})
                        access_items.append(mapped)

        if _provider_enabled(policies, "esim_oasis") and not _provider_enabled(policies, "esim_access"):
            if isinstance(oasis_raw, dict):
                oasis_raw["items"] = oasis_items
                return oasis_raw
            return {"items": oasis_items}
        if _provider_enabled(policies, "esim_access") and not _provider_enabled(policies, "esim_oasis"):
            return {"items": access_items}
        return {"items": oasis_items + access_items}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/esim/api/orders/{order_id}", include_in_schema=False)
@router.get("/api/esim/orders/{order_id}")
async def esim_order_get(order_id: str):
    try:
        _ensure_esim_service_enabled()
        policies = _ensure_any_esim_api_enabled()
        settings = _esim_settings()
        last_error = None
        stored_snapshot = _stored_esimaccess_order_snapshot(str(order_id or "").strip())

        if stored_snapshot and _has_terminal_esim_status_signal(
            stored_snapshot.get("status"),
            stored_snapshot.get("status_message"),
            stored_snapshot.get("statusMessage"),
            stored_snapshot.get("raw_query"),
        ):
            return _map_stored_snapshot_to_order(str(order_id or "").strip(), stored_snapshot)

        if _provider_enabled(policies, "esim_oasis"):
            try:
                data = esim_get_order(order_id)
                if isinstance(data, dict) and settings.get("fx_rate"):
                    try:
                        usd_minor = float(data.get("totalMinor"))
                    except Exception:
                        usd_minor = None
                    if usd_minor is not None:
                        fx = float(settings.get("fx_rate") or 0)
                        pct = float(settings.get("markup_percent") or 0)
                        fixed = float(settings.get("markup_fixed_iqd") or 0)
                        iqd = (usd_minor / 100.0) * fx
                        if pct:
                            iqd = iqd * (1 + pct / 100.0)
                        if fixed:
                            iqd += fixed
                        data["total_iqd"] = int(round(iqd))
                data["provider"] = "esim_oasis"
                return data
            except Exception as exc:
                last_error = exc

        if _provider_enabled(policies, "esim_access"):
            query_payload = {"orderNo": str(order_id or "").strip(), "iccid": "", "pager": {"pageNum": 1, "pageSize": 20}}
            access_data = esim_access_query_profiles(query_payload)
            if _is_access_success(access_data):
                mapped = _map_access_query_to_order(str(order_id or "").strip(), access_data)
                if stored_snapshot and _has_terminal_esim_status_signal(
                    stored_snapshot.get("status"),
                    stored_snapshot.get("status_message"),
                    stored_snapshot.get("statusMessage"),
                    stored_snapshot.get("raw_query"),
                ):
                    return _map_stored_snapshot_to_order(str(order_id or "").strip(), stored_snapshot)
                return mapped
            last_error = access_data

        if stored_snapshot:
            return _map_stored_snapshot_to_order(str(order_id or "").strip(), stored_snapshot)

        if last_error is not None:
            raise HTTPException(status_code=400, detail=str(last_error))
        raise HTTPException(status_code=404, detail="Order not found.")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/esim/orders/{order_id}/cancel")
@router.post("/esim/api/orders/{order_id}/cancel", include_in_schema=False)
@router.post("/api/esim/access/orders/{order_id}/cancel", include_in_schema=False)
@router.post("/api/esim/access/order/{order_id}/cancel", include_in_schema=False)
@router.post("/api/esim/order/{order_id}/cancel", include_in_schema=False)
async def esim_order_cancel(request: Request, order_id: str):
    _ensure_esim_service_enabled()
    policies = _ensure_any_esim_api_enabled()
    if not _provider_enabled(policies, "esim_access"):
        raise HTTPException(status_code=503, detail="esim_access API is disabled by admin permissions.")
    user = _require_authenticated_user(request)
    mapped = _query_access_provider_order(str(order_id or "").strip())
    identity_payload, row = _identity_payload_from_mapped_order(mapped)
    if not identity_payload:
        raise HTTPException(status_code=400, detail="No ICCID/esimTranNo found for this order.")
    response = _provider_call(esim_access_cancel_profile, identity_payload)
    snapshot = _persist_esimaccess_order_snapshot(
        current_user=user,
        request_payload={"bundleName": "", "order_reference": str(order_id or "")},
        provider_result={**mapped, "status": "cancelled", "statusMessage": "cancelled"},
    )
    order_row = snapshot if isinstance(snapshot, dict) else _find_order_for_user(user, str(order_id or ""))
    tx = _record_esimaccess_event_transaction(
        current_user=user,
        action="cancel",
        order_row=order_row,
        payload={**identity_payload, "order_reference": str(order_id or "")},
        amount_iqd=0,
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
@router.post("/api/esim/access/orders/{order_id}/refund", include_in_schema=False)
@router.post("/api/esim/access/order/{order_id}/refund", include_in_schema=False)
@router.post("/api/esim/order/{order_id}/refund", include_in_schema=False)
async def esim_order_refund(request: Request, order_id: str):
    _ensure_esim_service_enabled()
    policies = _ensure_any_esim_api_enabled()
    if not _provider_enabled(policies, "esim_access"):
        raise HTTPException(status_code=503, detail="esim_access API is disabled by admin permissions.")
    user = _require_authenticated_user(request)
    mapped = _query_access_provider_order(str(order_id or "").strip())
    identity_payload, row = _identity_payload_from_mapped_order(mapped)
    if not identity_payload:
        raise HTTPException(status_code=400, detail="No ICCID/esimTranNo found for this order.")
    # eSIMAccess refunds are handled via supplier cancel for eligible profiles.
    response = _provider_call(esim_access_cancel_profile, identity_payload)
    snapshot = _persist_esimaccess_order_snapshot(
        current_user=user,
        request_payload={"bundleName": "", "order_reference": str(order_id or "")},
        provider_result={**mapped, "status": "refunded", "statusMessage": "refunded"},
    )
    order_row = snapshot if isinstance(snapshot, dict) else _find_order_for_user(user, str(order_id or ""))
    refunded_amount = -abs(_to_int((order_row or {}).get("total_iqd"), default=0))
    tx = _record_esimaccess_event_transaction(
        current_user=user,
        action="refund",
        order_row=order_row,
        payload={**identity_payload, "order_reference": str(order_id or "")},
        amount_iqd=refunded_amount,
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
@router.post("/api/esim/access/orders/{order_id}/topup", include_in_schema=False)
@router.post("/api/esim/access/order/{order_id}/topup", include_in_schema=False)
@router.post("/api/esim/order/{order_id}/topup", include_in_schema=False)
async def esim_order_topup(request: Request, order_id: str, payload: dict):
    _ensure_esim_service_enabled()
    policies = _ensure_any_esim_api_enabled()
    if not _provider_enabled(policies, "esim_access"):
        raise HTTPException(status_code=503, detail="esim_access API is disabled by admin permissions.")
    user = _require_authenticated_user(request)
    body = payload if isinstance(payload, dict) else {}
    mapped = _query_access_provider_order(str(order_id or "").strip())
    identity_payload, row = _identity_payload_from_mapped_order(mapped)
    if not identity_payload:
        raise HTTPException(status_code=400, detail="No ICCID/esimTranNo found for this order.")
    package_code = str(body.get("packageCode") or "").strip()
    if not package_code:
        raise HTTPException(status_code=400, detail="packageCode is required for top-up.")
    topup_payload = {**identity_payload, "packageCode": package_code}
    if str(body.get("transactionId") or "").strip():
        topup_payload["transactionId"] = str(body.get("transactionId") or "").strip()
    if str(body.get("amount") or "").strip():
        topup_payload["amount"] = str(body.get("amount") or "").strip()
    response = _provider_call(esim_access_topup_profiles, topup_payload)
    order_row = _find_order_for_user(user, str(order_id or ""))
    tx = _record_esimaccess_event_transaction(
        current_user=user,
        action="topup",
        order_row=order_row,
        payload={**body, **topup_payload, "order_reference": str(order_id or "")},
        amount_iqd=_event_amount_iqd(body, fallback=0),
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


@router.get("/reports/esim/api/list", include_in_schema=False)
async def esim_report_list(request: Request):
    _ensure_esim_service_enabled()
    user = _require_authenticated_user(request)
    owner_id = effective_owner_user_id(user)

    if is_sub_user(user):
        orders = list_esimaccess_orders_for_agent(owner_id, str(user.get("id") or ""))
    else:
        orders = list_esimaccess_orders_for_owner(owner_id)

    try:
        _backfill_esimaccess_purchase_transactions(orders)
    except Exception:
        pass

    success_statuses = {"successful", "success", "completed", "issued", "active"}
    filtered_orders = []
    for row in orders:
        if not isinstance(row, dict):
            continue
        status = str(row.get("status") or "").strip().lower()
        if status in success_statuses:
            filtered_orders.append(row)

    return {"status": "ok", "orders": filtered_orders}


@router.get("/esim/api/balance", include_in_schema=False)
@router.get("/api/esim/balance")
async def esim_balance_get():
    try:
        _ensure_esim_service_enabled()
        policies = _ensure_any_esim_api_enabled()
        oasis_data = None
        access_data = None
        errors: list[str] = []

        if _provider_enabled(policies, "esim_oasis"):
            try:
                oasis_data = esim_balance()
            except Exception as exc:
                errors.append(f"esim_oasis: {exc}")
        if _provider_enabled(policies, "esim_access"):
            try:
                access_data = esim_access_balance_query()
            except Exception as exc:
                errors.append(f"esim_access: {exc}")

        if oasis_data is not None and access_data is None:
            return oasis_data
        if access_data is not None and oasis_data is None:
            return access_data
        if oasis_data is not None and access_data is not None:
            return {"esim_oasis": oasis_data, "esim_access": access_data}
        raise HTTPException(status_code=400, detail="; ".join(errors) if errors else "Balance query failed.")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/esim/api/settings", include_in_schema=False)
@router.get("/api/esim/settings")
async def esim_public_settings():
    _ensure_esim_service_enabled()
    settings = _esim_settings()
    oasis_api = _api_policy("esim_oasis")
    access_api = _api_policy("esim_access")
    return {
        "allowed_countries": settings.get("allowed_countries") or [],
        "allowed_regions": settings.get("allowed_regions") or [],
        "blocked_countries": settings.get("blocked_countries") or [],
        "blocked_regions": settings.get("blocked_regions") or [],
        "fx_rate": settings.get("fx_rate") or 0,
        "roe_rate": settings.get("fx_rate") or 0,
        "markup_percent": settings.get("markup_percent") or 0,
        "markup_fixed_iqd": settings.get("markup_fixed_iqd") or 0,
        "popular_destinations": settings.get("popular_destinations") or [],
        "api_enabled": bool(oasis_api.get("enabled")) or bool(access_api.get("enabled")),
        "sellable_mode": str(oasis_api.get("sellable_mode") or "online"),
        "schedule_ok": bool(oasis_api.get("schedule_ok")),
        "is_online_now": bool(oasis_api.get("is_online_now")),
        "providers": {
            "esim_oasis": {
                "enabled": bool(oasis_api.get("enabled")),
                "sellable_mode": str(oasis_api.get("sellable_mode") or "online"),
                "schedule_ok": bool(oasis_api.get("schedule_ok")),
                "is_online_now": bool(oasis_api.get("is_online_now")),
            },
            "esim_access": {
                "enabled": bool(access_api.get("enabled")),
                "sellable_mode": str(access_api.get("sellable_mode") or "online"),
                "schedule_ok": bool(access_api.get("schedule_ok")),
                "is_online_now": bool(access_api.get("is_online_now")),
                "configured": esim_access_is_configured(),
            },
        },
    }
