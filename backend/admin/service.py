from __future__ import annotations

import json
import os
import tempfile
from typing import Any

from backend.esim.esimaccess.service import (
    balance_query as esimaccess_balance_query,
    is_configured as esimaccess_is_configured,
    list_locations as esimaccess_list_locations,
)
from backend.core.paths import DATA_DIR
from backend.supabase import load_or_seed, save
from backend.esim.oasis.service import (
    list_bundles as esimoasis_list_bundles,
    load_config as load_esim_config,
    ping as esimoasis_ping,
    save_config as save_esim_config,
)
from backend.gateway.permissions_store import (
    _api_policy,
    _compute_schedule_windows,
    _load_permissions,
    _save_permissions,
    _service_enabled,
    _ticketing_schedule_allows,
)
from backend.communications.corevia_email.service import (
    load_config as load_email_config,
    save_config as save_email_config,
    send_email,
)
from backend.payments.fib.service import (
    create_payment as fib_create_payment,
    load_config as load_fib_config,
    save_config as save_fib_config,
)

VISA_CATALOG_PATH = DATA_DIR / "visa_catalog.json"


def _load_local_json(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8") or "null")
            if payload is not None:
                return payload
    except Exception:
        pass
    return default


def _save_local_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = tempfile.NamedTemporaryFile("w", delete=False, dir=str(path.parent), encoding="utf-8")
    try:
        json.dump(value, tmp, ensure_ascii=False, indent=2)
        tmp.flush()
        os.fsync(tmp.fileno())
    finally:
        tmp.close()
    os.replace(tmp.name, path)


def load_permissions_config() -> dict[str, Any]:
    data = _load_permissions()
    return data if isinstance(data, dict) else {}


def save_permissions_config(payload: dict[str, Any]) -> dict[str, Any]:
    return _save_permissions(payload if isinstance(payload, dict) else {})


def permissions_status_payload() -> dict[str, Any]:
    cfg = load_permissions_config()
    services_cfg = cfg.get("services") if isinstance(cfg.get("services"), dict) else {}
    services_out: dict[str, Any] = {}
    for key in sorted(services_cfg.keys()):
        services_out[str(key)] = {"enabled": _service_enabled(str(key), cfg)}

    apis_cfg = cfg.get("apis") if isinstance(cfg.get("apis"), dict) else {}
    apis_out: dict[str, Any] = {}
    for api_id in sorted(apis_cfg.keys()):
        pol = _api_policy(str(api_id), cfg)
        apis_out[str(api_id)] = {
            "enabled": bool(pol.get("enabled")),
            "sellable_mode": str(pol.get("sellable_mode") or "online"),
            "schedule_ok": bool(pol.get("schedule_ok")),
            "is_online_now": bool(pol.get("is_online_now")),
            "schedule": _compute_schedule_windows(pol.get("schedule") or {}),
        }

    providers_cfg = cfg.get("providers") if isinstance(cfg.get("providers"), dict) else {}
    providers_out: dict[str, Any] = {}
    for code, row in providers_cfg.items():
        if not isinstance(row, dict):
            continue
        availability = bool(row.get("availability_enabled", True))
        blocked_suppliers = row.get("blocked_suppliers") if isinstance(row.get("blocked_suppliers"), list) else []
        if str(code) in [str(x).strip() for x in blocked_suppliers]:
            availability = False
        ticketing_mode = str(row.get("ticketing_mode") or "full").strip().lower()
        schedule_cfg = row.get("ticketing_schedule") if isinstance(row.get("ticketing_schedule"), dict) else {}
        schedule_ok = _ticketing_schedule_allows(schedule_cfg)
        providers_out[str(code)] = {
            "availability": availability,
            "ticketing_mode": "full" if ticketing_mode == "full" else "availability_only",
            "ticketing_schedule_ok": schedule_ok,
            "ticketing_effective": availability and ticketing_mode == "full" and schedule_ok,
            "schedule": _compute_schedule_windows(schedule_cfg),
        }

    return {"services": services_out, "apis": apis_out, "providers": providers_out}


def load_fib_configuration() -> dict[str, Any]:
    data = load_fib_config()
    return data if isinstance(data, dict) else {"accounts": [], "active_account_id": ""}


def save_fib_configuration(payload: dict[str, Any]) -> dict[str, Any]:
    data = save_fib_config(payload if isinstance(payload, dict) else {})
    return data if isinstance(data, dict) else {"accounts": [], "active_account_id": ""}


def create_fib_payment(
    amount_iqd: int,
    description: str,
    *,
    options: dict[str, Any] | None = None,
    selector: dict[str, str] | None = None,
) -> dict[str, Any]:
    return fib_create_payment(
        int(amount_iqd),
        str(description or "Payment").strip() or "Payment",
        options=options,
        selector=selector,
    )


def load_email_configuration() -> dict[str, Any]:
    data = load_email_config()
    return data if isinstance(data, dict) else {"accounts": [], "active_account_id": ""}


def save_email_configuration(payload: dict[str, Any]) -> dict[str, Any]:
    data = save_email_config(payload if isinstance(payload, dict) else {})
    return data if isinstance(data, dict) else {"accounts": [], "active_account_id": ""}


def send_email_test(to_email: str, subject: str, body: str) -> tuple[bool, str]:
    return send_email(str(to_email or "").strip(), str(subject or "").strip(), str(body or "").strip())


def load_esim_configuration() -> dict[str, Any]:
    data = load_esim_config()
    if not isinstance(data, dict):
        return {"accounts": [], "active_account_id": "", "settings": {}, "fx_history": []}
    if not isinstance(data.get("accounts"), list):
        data["accounts"] = []
    if not isinstance(data.get("settings"), dict):
        data["settings"] = {}
    if not isinstance(data.get("fx_history"), list):
        data["fx_history"] = []
    data["active_account_id"] = str(data.get("active_account_id") or "")
    return data


def save_esim_configuration(payload: dict[str, Any]) -> dict[str, Any]:
    data = save_esim_config(payload if isinstance(payload, dict) else {})
    return load_esim_configuration() if not isinstance(data, dict) else data


def _is_oasis_configured(cfg: dict[str, Any]) -> bool:
    accounts = cfg.get("accounts") if isinstance(cfg.get("accounts"), list) else []
    active_id = str(cfg.get("active_account_id") or "").strip()
    for row in accounts:
        if not isinstance(row, dict):
            continue
        if active_id and str(row.get("id") or "") != active_id:
            continue
        key_id = str(row.get("key_id") or "").strip()
        secret = str(row.get("secret") or "").strip()
        if key_id and secret:
            return True
    env_key = str(os.getenv("ESIM_OASIS_KEY_ID") or "").strip()
    env_secret = str(os.getenv("ESIM_OASIS_SECRET") or "").strip()
    return bool(env_key and env_secret)


def esim_ping_status() -> dict[str, Any]:
    cfg = load_esim_configuration()
    access_cfg = esimaccess_is_configured()
    oasis_cfg = _is_oasis_configured(cfg)
    access_state: dict[str, Any] = {"configured": access_cfg, "ok": False, "error": ""}
    oasis_state: dict[str, Any] = {"configured": oasis_cfg, "ok": False, "error": ""}

    if access_cfg:
        try:
            esimaccess_balance_query()
            access_state["ok"] = True
        except Exception as exc:
            access_state["error"] = str(exc)

    if oasis_cfg:
        try:
            esimoasis_ping()
            oasis_state["ok"] = True
        except Exception as exc:
            oasis_state["error"] = str(exc)

    return {
        "status": "ok",
        "providers": {
            "esim_access": access_state,
            "esim_oasis": oasis_state,
        },
    }


def _to_int(value: Any, default: int = 0) -> int:
    try:
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        txt = str(value or "").strip().replace(",", "")
        if not txt:
            return default
        if "." in txt:
            return int(float(txt))
        return int(txt)
    except Exception:
        return default


def _region_key(value: Any) -> str:
    raw = str(value or "").strip().lower()
    return "".join(ch for ch in raw if ch.isalnum())


def _esim_settings(cfg: dict[str, Any]) -> dict[str, Any]:
    settings = cfg.get("settings") if isinstance(cfg.get("settings"), dict) else {}
    return {
        "allowed_countries": [str(x).strip().upper() for x in (settings.get("allowed_countries") or []) if str(x).strip()],
        "blocked_countries": [str(x).strip().upper() for x in (settings.get("blocked_countries") or []) if str(x).strip()],
        "allowed_regions": [str(x).strip() for x in (settings.get("allowed_regions") or []) if str(x).strip()],
        "blocked_regions": [str(x).strip() for x in (settings.get("blocked_regions") or []) if str(x).strip()],
    }


def _country_allowed(iso: str, region: str, settings: dict[str, Any]) -> bool:
    iso_u = str(iso or "").strip().upper()
    region_k = _region_key(region)
    allowed_countries = {str(x).strip().upper() for x in (settings.get("allowed_countries") or []) if str(x).strip()}
    blocked_countries = {str(x).strip().upper() for x in (settings.get("blocked_countries") or []) if str(x).strip()}
    allowed_regions = {_region_key(x) for x in (settings.get("allowed_regions") or []) if str(x).strip()}
    blocked_regions = {_region_key(x) for x in (settings.get("blocked_regions") or []) if str(x).strip()}

    if iso_u and iso_u in blocked_countries:
        return False
    if region_k and region_k in blocked_regions:
        return False
    if not allowed_countries and not allowed_regions:
        return True
    return bool((iso_u and iso_u in allowed_countries) or (region_k and region_k in allowed_regions))


def _add_country_bucket(
    buckets: dict[str, dict[str, Any]],
    *,
    iso: str,
    name: str,
    region: str,
    price_minor: int | None,
) -> None:
    iso_u = str(iso or "").strip().upper()
    name_s = str(name or "").strip() or iso_u
    if not name_s:
        return
    key = iso_u or name_s.lower()
    row = buckets.get(key)
    if not isinstance(row, dict):
        row = {
            "name": name_s,
            "iso": iso_u,
            "region": str(region or "").strip(),
            "count": 0,
            "min": price_minor,
        }
        buckets[key] = row
    row["count"] = _to_int(row.get("count"), default=0) + 1
    if str(region or "").strip() and not str(row.get("region") or "").strip():
        row["region"] = str(region).strip()
    if price_minor is not None:
        current = row.get("min")
        if current is None or _to_int(current, default=0) <= 0 or price_minor < _to_int(current, default=0):
            row["min"] = price_minor


def _extract_access_location_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    queue: list[Any] = [payload]
    while queue:
        node = queue.pop(0)
        if isinstance(node, list):
            queue.extend(node)
            continue
        if not isinstance(node, dict):
            continue

        code = str(
            node.get("locationCode")
            or node.get("location_code")
            or node.get("countryCode")
            or node.get("country_code")
            or node.get("iso")
            or node.get("code")
            or ""
        ).strip().upper()
        name = str(
            node.get("locationName")
            or node.get("location_name")
            or node.get("countryName")
            or node.get("country_name")
            or node.get("name")
            or ""
        ).strip()
        region = str(
            node.get("region")
            or node.get("regionName")
            or node.get("region_name")
            or node.get("type")
            or node.get("group")
            or ""
        ).strip()
        if code or name:
            rows.append({"iso": code, "name": name, "region": region})

        for val in node.values():
            if isinstance(val, (dict, list)):
                queue.append(val)
    return rows


def _countries_index_from_esimaccess(settings: dict[str, Any]) -> dict[str, Any]:
    response = esimaccess_list_locations({})
    if not isinstance(response, dict):
        raise ValueError("Unexpected eSIMAccess location response.")
    rows = _extract_access_location_rows(response)
    buckets: dict[str, dict[str, Any]] = {}
    for row in rows:
        iso = str(row.get("iso") or "").strip().upper()
        name = str(row.get("name") or "").strip()
        region = str(row.get("region") or "").strip()
        if not _country_allowed(iso, region, settings):
            continue
        _add_country_bucket(buckets, iso=iso, name=name, region=region, price_minor=None)

    items = list(buckets.values())
    items.sort(key=lambda x: (str(x.get("name") or "").lower(), str(x.get("iso") or "")))
    return {"status": "ok", "provider": "esimaccess", "items": items}


def _bundle_price_minor(item: dict[str, Any]) -> int:
    if not isinstance(item, dict):
        return 0
    price = item.get("price") if isinstance(item.get("price"), dict) else {}
    for key in ("finalMinor", "saleMinor", "retailMinor", "amountMinor", "minor"):
        val = price.get(key)
        parsed = _to_int(val, default=0)
        if parsed > 0:
            return parsed
    for key in ("price_minor", "priceMinor", "price"):
        parsed = _to_int(item.get(key), default=0)
        if parsed > 0:
            return parsed
    return 0


def _countries_index_from_esimoasis(settings: dict[str, Any]) -> dict[str, Any]:
    data = esimoasis_list_bundles(params=None)
    bundles = data.get("items") if isinstance(data.get("items"), list) else data.get("bundles")
    bundles = bundles if isinstance(bundles, list) else []
    buckets: dict[str, dict[str, Any]] = {}
    for item in bundles:
        if not isinstance(item, dict):
            continue
        price_minor = _bundle_price_minor(item)
        countries = item.get("countries")
        countries = countries if isinstance(countries, list) else []
        for country in countries:
            if not isinstance(country, dict):
                continue
            iso = str(country.get("iso") or country.get("code") or "").strip().upper()
            name = str(country.get("name") or "").strip()
            region = str(country.get("region") or "").strip()
            if not _country_allowed(iso, region, settings):
                continue
            _add_country_bucket(
                buckets,
                iso=iso,
                name=name,
                region=region,
                price_minor=price_minor if price_minor > 0 else None,
            )

    items = list(buckets.values())
    items.sort(key=lambda x: (str(x.get("name") or "").lower(), str(x.get("iso") or "")))
    return {"status": "ok", "provider": "esim_oasis", "items": items}


def esim_countries_index() -> dict[str, Any]:
    cfg = load_esim_configuration()
    settings = _esim_settings(cfg)

    if esimaccess_is_configured():
        try:
            return _countries_index_from_esimaccess(settings)
        except Exception:
            pass

    if _is_oasis_configured(cfg):
        return _countries_index_from_esimoasis(settings)

    raise ValueError("eSIM supplier is not configured.")


def _normalize_visa_type(row: dict[str, Any]) -> dict[str, Any]:
    required_documents = row.get("required_documents")
    if isinstance(required_documents, str):
        required_documents = [x.strip() for x in required_documents.split("\n") if x.strip()]
    if not isinstance(required_documents, list):
        required_documents = []
    return {
        "id": str(row.get("id") or "").strip(),
        "name": str(row.get("name") or "").strip(),
        "days": str(row.get("days") or "").strip(),
        "category": str(row.get("category") or "").strip().lower(),
        "price": float(row.get("price") or 0),
        "currency": str(row.get("currency") or "IQD").strip().upper() or "IQD",
        "details": str(row.get("details") or "").strip(),
        "required_documents": [str(x).strip() for x in required_documents if str(x).strip()],
        "visible": bool(row.get("visible", True)),
        "sort_order": _to_int(row.get("sort_order"), default=0),
    }


def _normalize_visa_country(row: dict[str, Any]) -> dict[str, Any]:
    types = row.get("types")
    types = types if isinstance(types, list) else []
    normalized_types: list[dict[str, Any]] = []
    for type_row in types:
        if not isinstance(type_row, dict):
            continue
        normalized = _normalize_visa_type(type_row)
        if normalized.get("id") or normalized.get("name"):
            normalized_types.append(normalized)
    normalized_types.sort(key=lambda x: (_to_int(x.get("sort_order"), default=0), str(x.get("name") or "").lower()))
    return {
        "id": str(row.get("id") or "").strip(),
        "name": str(row.get("name") or "").strip(),
        "code": str(row.get("code") or "").strip().upper(),
        "visible": bool(row.get("visible", True)),
        "sort_order": _to_int(row.get("sort_order"), default=0),
        "types": normalized_types,
    }


def normalize_visa_catalog(payload: dict[str, Any] | None) -> dict[str, Any]:
    src = payload if isinstance(payload, dict) else {}
    countries = src.get("countries")
    countries = countries if isinstance(countries, list) else []
    normalized_countries: list[dict[str, Any]] = []
    for country in countries:
        if not isinstance(country, dict):
            continue
        normalized = _normalize_visa_country(country)
        if normalized.get("id") or normalized.get("name"):
            normalized_countries.append(normalized)
    normalized_countries.sort(key=lambda x: (_to_int(x.get("sort_order"), default=0), str(x.get("name") or "").lower()))
    return {"countries": normalized_countries}


def load_visa_catalog() -> dict[str, Any]:
    data = load_or_seed(
        doc_key="visa_catalog",
        default={"countries": []},
        local_loader=lambda: _load_local_json(VISA_CATALOG_PATH, {"countries": []}),
    )
    return normalize_visa_catalog(data if isinstance(data, dict) else {"countries": []})


def save_visa_catalog(catalog: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_visa_catalog(catalog if isinstance(catalog, dict) else {"countries": []})
    save(
        doc_key="visa_catalog",
        value=normalized,
        local_saver=lambda value: _save_local_json(VISA_CATALOG_PATH, value if isinstance(value, dict) else {"countries": []}),
    )
    return normalized
