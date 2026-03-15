from __future__ import annotations

import json
import os
import time
from copy import deepcopy
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from backend.supabase import load_or_seed as sb_load_or_seed
from backend.supabase.fib import (
    load_fib_accounts_doc,
    load_fib_frontend_routes_doc,
    load_fib_payment_accounts_doc,
    load_fib_settings_doc,
    save_fib_accounts_doc,
    save_fib_frontend_routes_doc,
    save_fib_payment_accounts_doc,
    save_fib_settings_doc,
)


CONFIG_DIR = Path(__file__).resolve().parent
CONFIG_PATH = CONFIG_DIR / "config.json"
ACCOUNTS_PATH = CONFIG_DIR / "accounts.json"
FRONTEND_ROUTES_PATH = CONFIG_DIR / "frontend_routes.json"
SETTINGS_PATH = CONFIG_DIR / "settings.json"
PAYMENT_ACCOUNT_MAP_PATH = Path(__file__).resolve().parent / "payment_accounts.json"
DEFAULT_FIB_BASE_URL = "https://fib.prod.fib.iq"
DEFAULT_ACCOUNT_ROW = {
    "id": "fib-prod",
    "label": "Production",
    "client_id": "",
    "client_secret": "",
    "base_url": DEFAULT_FIB_BASE_URL,
}
DEFAULT_CONFIG = {
    "accounts": [
        dict(DEFAULT_ACCOUNT_ROW)
    ],
    "active_account_id": "fib-prod",
}
DEFAULT_ACCOUNTS_DOC = {"accounts": [dict(DEFAULT_ACCOUNT_ROW)]}
DEFAULT_FRONTEND_ROUTES_DOC = {"routes": []}
DEFAULT_SETTINGS_DOC = {"active_account_id": "fib-prod"}
SELECTOR_ACCOUNT_ID_ALIASES = ("fib_account_id", "account_id")
SELECTOR_FRONTEND_KEY_ALIASES = ("fib_frontend_key", "frontend_key")
SELECTOR_ORIGIN_ALIASES = ("fib_origin", "frontend_origin", "origin")
SELECTOR_HOST_ALIASES = ("fib_host", "frontend_host", "host")
CREATE_PAYMENT_OPTION_ALIASES = {
    "status_callback_url": ("statusCallbackUrl", "status_callback_url"),
    "redirect_uri": ("redirectUri", "redirect_uri"),
    "expires_in": ("expiresIn", "expires_in"),
    "refundable_for": ("refundableFor", "refundable_for"),
    "category": ("category",),
}
VALID_PAYMENT_CATEGORIES = {
    "ERP",
    "POS",
    "ECOMMERCE",
    "UTILITY",
    "PAYROLL",
    "SUPPLIER",
    "LOAN",
    "GOVERNMENT",
    "MISCELLANEOUS",
    "OTHER",
}


def _fib_target_label(base_url: str) -> str:
    host = (urlparse(base_url).hostname or "").strip()
    return host or "configured FIB host"


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_string_list(*values: Any) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        if isinstance(value, list):
            items = value
        else:
            items = [value]
        for item in items:
            text = _clean_text(item)
            if not text:
                continue
            key = text.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(text)
    return out


def _normalize_origin(value: Any) -> str:
    text = _clean_text(value)
    if not text:
        return ""
    parsed = urlparse(text)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}".rstrip("/")
    return text.rstrip("/").lower()


def _normalize_host(value: Any) -> str:
    text = _clean_text(value)
    if not text:
        return ""
    parsed = urlparse(text)
    host = parsed.netloc or parsed.path or text
    host = host.strip().lower()
    if "@" in host:
        host = host.split("@", 1)[1]
    if ":" in host and host.count(":") == 1:
        host = host.split(":", 1)[0]
    return host.strip("/")


def _clean_url(value: Any, *, field_name: str) -> str:
    text = _clean_text(value)
    if not text:
        return ""
    parsed = urlparse(text)
    if not parsed.scheme:
        raise ValueError(f"{field_name} must be an absolute URL.")
    if parsed.scheme in {"http", "https"} and not parsed.netloc:
        raise ValueError(f"{field_name} must include a hostname.")
    return text


def _extract_from_sources(aliases: tuple[str, ...], *sources: Any) -> str:
    lowered_aliases = {alias.lower(): alias for alias in aliases}
    for source in sources:
        if not hasattr(source, "items"):
            continue
        source_dict = {str(key).lower(): value for key, value in source.items()}
        for lowered in lowered_aliases:
            if lowered in source_dict:
                return _clean_text(source_dict.get(lowered))
    return ""


def extract_account_selector(
    *,
    payload: dict | None = None,
    headers: Any = None,
    query_params: Any = None,
) -> dict[str, str]:
    account_id = _extract_from_sources(SELECTOR_ACCOUNT_ID_ALIASES, payload or {}, query_params, headers)
    frontend_key = _extract_from_sources(SELECTOR_FRONTEND_KEY_ALIASES, payload or {}, query_params, headers)
    explicit_origin = _extract_from_sources(SELECTOR_ORIGIN_ALIASES, payload or {}, query_params, headers)
    explicit_host = _extract_from_sources(SELECTOR_HOST_ALIASES, payload or {}, query_params, headers)
    header_origin = _extract_from_sources(("origin", "x-frontend-origin"), headers)
    referer = _extract_from_sources(("referer",), headers)
    header_host = _extract_from_sources(("x-frontend-host", "host"), headers)

    origin = _normalize_origin(explicit_origin or header_origin or referer)
    host = _normalize_host(explicit_host or header_host or origin or referer)
    return {
        "account_id": account_id,
        "frontend_key": frontend_key,
        "origin": origin,
        "host": host,
    }


def extract_create_payment_options(payload: dict | None) -> dict[str, Any]:
    body = payload if isinstance(payload, dict) else {}
    out: dict[str, Any] = {}
    for internal_key, aliases in CREATE_PAYMENT_OPTION_ALIASES.items():
        for alias in aliases:
            if alias in body:
                out[internal_key] = body.get(alias)
                break
    return out


def _load_local_json_file(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8") or "null")
            if data is not None:
                return data
    except Exception:
        pass
    return deepcopy(default)


def _save_local_json_file(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")


def _normalize_route_row(route: dict[str, Any] | None) -> dict[str, Any] | None:
    row = route if isinstance(route, dict) else {}
    account_id = _clean_text(row.get("account_id"))
    if not account_id:
        return None
    route_id = _clean_text(row.get("id")) or f"{account_id}-route"
    frontend_keys = _normalize_string_list(row.get("frontend_key"), row.get("frontend_keys"))
    frontend_origins = [_normalize_origin(value) for value in _normalize_string_list(row.get("frontend_origin"), row.get("frontend_origins"))]
    frontend_hosts = [_normalize_host(value) for value in _normalize_string_list(row.get("frontend_host"), row.get("frontend_hosts"))]
    return {
        "id": route_id,
        "account_id": account_id,
        "frontend_key": frontend_keys[0] if len(frontend_keys) == 1 else "",
        "frontend_keys": frontend_keys,
        "frontend_origin": frontend_origins[0] if len(frontend_origins) == 1 else "",
        "frontend_origins": frontend_origins,
        "frontend_host": frontend_hosts[0] if len(frontend_hosts) == 1 else "",
        "frontend_hosts": frontend_hosts,
    }


def _split_combined_config(cfg: dict | None) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    data = cfg if isinstance(cfg, dict) else {}
    accounts = data.get("accounts") if isinstance(data.get("accounts"), list) else []
    routes_in = data.get("routes") if isinstance(data.get("routes"), list) else []

    accounts_out: list[dict[str, Any]] = []
    routes_out: list[dict[str, Any]] = []
    for row in accounts:
        if not isinstance(row, dict):
            continue
        aid = _clean_text(row.get("id"))
        if not aid:
            continue
        accounts_out.append(
            {
                "id": aid,
                "label": _clean_text(row.get("label")),
                "client_id": _clean_text(row.get("client_id")),
                "client_secret": _clean_text(row.get("client_secret")),
                "base_url": _clean_text(row.get("base_url")) or DEFAULT_FIB_BASE_URL,
            }
        )
        route = _normalize_route_row(
            {
                "id": f"{aid}-route",
                "account_id": aid,
                "frontend_key": row.get("frontend_key"),
                "frontend_keys": row.get("frontend_keys"),
                "frontend_origin": row.get("frontend_origin"),
                "frontend_origins": row.get("frontend_origins"),
                "frontend_host": row.get("frontend_host"),
                "frontend_hosts": row.get("frontend_hosts"),
            }
        )
        if route and any(route.get(key) for key in ("frontend_key", "frontend_keys", "frontend_origin", "frontend_origins", "frontend_host", "frontend_hosts")):
            routes_out.append(route)

    for route in routes_in:
        normalized = _normalize_route_row(route if isinstance(route, dict) else None)
        if normalized:
            routes_out.append(normalized)

    deduped_routes: list[dict[str, Any]] = []
    seen_route_ids: set[str] = set()
    for route in routes_out:
        route_id = _clean_text(route.get("id"))
        if not route_id or route_id in seen_route_ids:
            continue
        seen_route_ids.add(route_id)
        deduped_routes.append(route)

    active_account_id = _clean_text(data.get("active_account_id"))
    if not active_account_id and accounts_out:
        active_account_id = _clean_text(accounts_out[0].get("id"))

    return (
        {"accounts": accounts_out or deepcopy(DEFAULT_ACCOUNTS_DOC["accounts"])},
        {"routes": deduped_routes},
        {"active_account_id": active_account_id or DEFAULT_SETTINGS_DOC["active_account_id"]},
    )


def _combine_config_docs(
    accounts_doc: dict[str, Any] | None,
    routes_doc: dict[str, Any] | None,
    settings_doc: dict[str, Any] | None,
) -> dict[str, Any]:
    accounts_rows = accounts_doc.get("accounts") if isinstance(accounts_doc, dict) and isinstance(accounts_doc.get("accounts"), list) else []
    routes_rows = routes_doc.get("routes") if isinstance(routes_doc, dict) and isinstance(routes_doc.get("routes"), list) else []
    settings = settings_doc if isinstance(settings_doc, dict) else {}

    routes_by_account: dict[str, dict[str, list[str]]] = {}
    for route in routes_rows:
        normalized = _normalize_route_row(route if isinstance(route, dict) else None)
        if not normalized:
            continue
        aid = _clean_text(normalized.get("account_id"))
        bucket = routes_by_account.setdefault(
            aid,
            {
                "frontend_keys": [],
                "frontend_origins": [],
                "frontend_hosts": [],
            },
        )
        bucket["frontend_keys"] = _normalize_string_list(bucket["frontend_keys"], normalized.get("frontend_key"), normalized.get("frontend_keys"))
        bucket["frontend_origins"] = [
            _normalize_origin(value)
            for value in _normalize_string_list(bucket["frontend_origins"], normalized.get("frontend_origin"), normalized.get("frontend_origins"))
        ]
        bucket["frontend_hosts"] = [
            _normalize_host(value)
            for value in _normalize_string_list(bucket["frontend_hosts"], normalized.get("frontend_host"), normalized.get("frontend_hosts"))
        ]

    accounts_out: list[dict[str, Any]] = []
    for row in accounts_rows:
        if not isinstance(row, dict):
            continue
        aid = _clean_text(row.get("id"))
        if not aid:
            continue
        route_info = routes_by_account.get(aid, {"frontend_keys": [], "frontend_origins": [], "frontend_hosts": []})
        accounts_out.append(
            {
                "id": aid,
                "label": _clean_text(row.get("label")),
                "client_id": _clean_text(row.get("client_id")),
                "client_secret": _clean_text(row.get("client_secret")),
                "base_url": _clean_text(row.get("base_url")) or DEFAULT_FIB_BASE_URL,
                "frontend_key": route_info["frontend_keys"][0] if len(route_info["frontend_keys"]) == 1 else "",
                "frontend_keys": route_info["frontend_keys"],
                "frontend_origin": route_info["frontend_origins"][0] if len(route_info["frontend_origins"]) == 1 else "",
                "frontend_origins": route_info["frontend_origins"],
                "frontend_host": route_info["frontend_hosts"][0] if len(route_info["frontend_hosts"]) == 1 else "",
                "frontend_hosts": route_info["frontend_hosts"],
            }
        )

    if not accounts_out:
        accounts_out = deepcopy(DEFAULT_CONFIG["accounts"])
    active_account_id = _clean_text(settings.get("active_account_id"))
    if not active_account_id and accounts_out:
        active_account_id = _clean_text(accounts_out[0].get("id"))
    return {
        "accounts": accounts_out,
        "active_account_id": active_account_id or DEFAULT_SETTINGS_DOC["active_account_id"],
    }


def _load_legacy_config_raw() -> dict:
    def _load_local() -> dict:
        return _load_local_json_file(CONFIG_PATH, {})

    data = sb_load_or_seed(doc_key="fib_config", default={}, local_loader=_load_local)
    return data if isinstance(data, dict) else {}


def _load_payment_account_map_raw() -> dict[str, dict[str, str]]:
    data = load_fib_payment_accounts_doc(
        local_loader=lambda: _load_local_json_file(PAYMENT_ACCOUNT_MAP_PATH, {"payments": {}})
    )
    if not isinstance(data, dict):
        data = {"payments": {}}
    payments = data.get("payments") if isinstance(data.get("payments"), dict) else {}
    cleaned = {
        str(key).strip(): str(value).strip()
        for key, value in payments.items()
        if str(key).strip() and str(value).strip()
    }
    return {"payments": cleaned}


def _save_payment_account_map_raw(data: dict[str, dict[str, str]]) -> None:
    out = data if isinstance(data, dict) else {"payments": {}}
    save_fib_payment_accounts_doc(value=out, local_saver=lambda value: _save_local_json_file(PAYMENT_ACCOUNT_MAP_PATH, value))


def _remember_payment_account(payment_id: str, account_id: str) -> None:
    pid = _clean_text(payment_id)
    aid = _clean_text(account_id)
    if not (pid and aid):
        return
    data = _load_payment_account_map_raw()
    payments = data.get("payments") if isinstance(data.get("payments"), dict) else {}
    if payments.get(pid) == aid:
        return
    payments[pid] = aid
    data["payments"] = payments
    _save_payment_account_map_raw(data)


def _load_config_raw() -> dict:
    legacy = _load_legacy_config_raw()
    legacy_accounts_doc, legacy_routes_doc, legacy_settings_doc = _split_combined_config(legacy)

    accounts_doc = load_fib_accounts_doc(
        local_loader=lambda: _load_local_json_file(ACCOUNTS_PATH, legacy_accounts_doc),
    )
    routes_doc = load_fib_frontend_routes_doc(
        local_loader=lambda: _load_local_json_file(FRONTEND_ROUTES_PATH, legacy_routes_doc),
    )
    settings_doc = load_fib_settings_doc(
        local_loader=lambda: _load_local_json_file(SETTINGS_PATH, legacy_settings_doc),
    )
    return _combine_config_docs(accounts_doc, routes_doc, settings_doc)


def _redact_account(account: dict) -> dict:
    row = account if isinstance(account, dict) else {}
    frontend_keys = _normalize_string_list(row.get("frontend_key"), row.get("frontend_keys"))
    frontend_origins = [_normalize_origin(value) for value in _normalize_string_list(row.get("frontend_origin"), row.get("frontend_origins"))]
    frontend_hosts = [_normalize_host(value) for value in _normalize_string_list(row.get("frontend_host"), row.get("frontend_hosts"))]
    return {
        "id": str(row.get("id") or "").strip(),
        "label": str(row.get("label") or "").strip(),
        "client_id": str(row.get("client_id") or "").strip(),
        "client_secret": "",
        "has_client_secret": bool(str(row.get("client_secret") or "").strip()),
        "base_url": str(row.get("base_url") or "").strip(),
        "frontend_key": frontend_keys[0] if len(frontend_keys) == 1 else "",
        "frontend_keys": frontend_keys,
        "frontend_origin": frontend_origins[0] if len(frontend_origins) == 1 else "",
        "frontend_origins": frontend_origins,
        "frontend_host": frontend_hosts[0] if len(frontend_hosts) == 1 else "",
        "frontend_hosts": frontend_hosts,
    }


def _redact_config(cfg: dict) -> dict:
    data = cfg if isinstance(cfg, dict) else {}
    accounts = data.get("accounts") if isinstance(data.get("accounts"), list) else []
    return {
        "accounts": [_redact_account(a) for a in accounts if isinstance(a, dict)],
        "active_account_id": str(data.get("active_account_id") or "").strip(),
    }


def load_config() -> dict:
    return _redact_config(_load_config_raw())


def save_config(cfg: dict) -> dict:
    if not isinstance(cfg, dict):
        cfg = {}
    current = _load_config_raw()
    existing_accounts = current.get("accounts") if isinstance(current.get("accounts"), list) else []
    existing_by_id = {
        str(row.get("id") or "").strip(): row
        for row in existing_accounts
        if isinstance(row, dict) and str(row.get("id") or "").strip()
    }
    accounts = cfg.get("accounts")
    combined_accounts = accounts if isinstance(accounts, list) else []
    merged_accounts: list[dict[str, Any]] = []
    for row in combined_accounts:
        if not isinstance(row, dict):
            continue
        aid = _clean_text(row.get("id"))
        if not aid:
            continue
        prev = existing_by_id.get(aid) if isinstance(existing_by_id.get(aid), dict) else {}
        incoming_secret = _clean_text(row.get("client_secret"))
        merged_accounts.append(
            {
                **row,
                "client_secret": incoming_secret or _clean_text(prev.get("client_secret")),
            }
        )

    combined_cfg = {
        "accounts": merged_accounts,
        "active_account_id": cfg.get("active_account_id", current.get("active_account_id")),
        "routes": cfg.get("routes"),
    }
    accounts_doc, routes_doc, settings_doc = _split_combined_config(combined_cfg)

    save_fib_accounts_doc(
        value=accounts_doc,
        local_saver=lambda value: _save_local_json_file(ACCOUNTS_PATH, value),
    )
    save_fib_frontend_routes_doc(
        value=routes_doc,
        local_saver=lambda value: _save_local_json_file(FRONTEND_ROUTES_PATH, value),
    )
    save_fib_settings_doc(
        value=settings_doc,
        local_saver=lambda value: _save_local_json_file(SETTINGS_PATH, value),
    )

    out = _combine_config_docs(accounts_doc, routes_doc, settings_doc)
    _save_local_json_file(CONFIG_PATH, out)
    return _redact_config(out)


def _selector_has_values(selector: dict[str, str] | None) -> bool:
    data = selector if isinstance(selector, dict) else {}
    return any(_clean_text(data.get(key)) for key in ("account_id", "frontend_key", "origin", "host"))


def _account_matches_selector(account: dict, selector: dict[str, str]) -> bool:
    selector_data = selector if isinstance(selector, dict) else {}
    account_id = _clean_text(selector_data.get("account_id"))
    frontend_key = _clean_text(selector_data.get("frontend_key")).lower()
    origin = _normalize_origin(selector_data.get("origin"))
    host = _normalize_host(selector_data.get("host"))

    if account_id and _clean_text(account.get("id")) != account_id:
        return False

    account_frontend_keys = [value.lower() for value in _normalize_string_list(account.get("frontend_key"), account.get("frontend_keys"))]
    if frontend_key and frontend_key not in account_frontend_keys and frontend_key != _clean_text(account.get("id")).lower():
        return False

    account_origins = [_normalize_origin(value) for value in _normalize_string_list(account.get("frontend_origin"), account.get("frontend_origins"))]
    account_hosts = [_normalize_host(value) for value in _normalize_string_list(account.get("frontend_host"), account.get("frontend_hosts"))]

    if origin:
        origin_host = _normalize_host(origin)
        if origin not in account_origins and origin_host not in account_hosts:
            return False

    if host and host not in account_hosts:
        if not any(_normalize_host(value) == host for value in account_origins):
            return False

    return True


def _find_account_by_id(accounts: list[dict], account_id: str) -> dict | None:
    target = _clean_text(account_id)
    if not target:
        return None
    return next((row for row in accounts if _clean_text(row.get("id")) == target), None)


def _resolve_saved_account(*, selector: dict[str, str] | None = None, payment_id: str | None = None) -> dict | None:
    cfg = _load_config_raw()
    accounts = cfg.get("accounts") or []
    active_id = str(cfg.get("active_account_id") or "").strip()
    target_payment_id = _clean_text(payment_id)
    if target_payment_id:
        payment_map = _load_payment_account_map_raw().get("payments") or {}
        mapped_account = _find_account_by_id(accounts, str(payment_map.get(target_payment_id) or ""))
        if mapped_account:
            return mapped_account

    if _selector_has_values(selector):
        matches = [row for row in accounts if _account_matches_selector(row, selector or {})]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise ValueError("Multiple FIB accounts match this frontend selector. Narrow the selector or fix the account config.")
        raise ValueError("No FIB account matches this frontend selector.")

    return _find_account_by_id(accounts, active_id)


def _get_active_account(*, selector: dict[str, str] | None = None, payment_id: str | None = None) -> dict:
    account = _resolve_saved_account(selector=selector, payment_id=payment_id)
    env_base = os.getenv("FIB_BASE_URL") or DEFAULT_FIB_BASE_URL
    env_client_id = os.getenv("FIB_CLIENT_ID") or ""
    env_client_secret = os.getenv("FIB_CLIENT_SECRET") or ""

    if not account and env_base and env_client_id and env_client_secret:
        account = {
            "id": "env",
            "label": "ENV",
            "client_id": env_client_id,
            "client_secret": env_client_secret,
            "base_url": env_base,
        }

    if not account:
        raise ValueError("No active FIB account configured.")

    if not (account.get("client_id") and account.get("client_secret")):
        raise ValueError("FIB credentials are missing.")

    return account


def _get_access_token(account: dict) -> str:
    base_url = (account.get("base_url") or "").strip()
    if not base_url:
        raise ValueError("FIB base URL is missing.")

    token_url = base_url.rstrip("/") + "/auth/realms/fib-online-shop/protocol/openid-connect/token"
    data = {
        "grant_type": "client_credentials",
        "client_id": account.get("client_id"),
        "client_secret": account.get("client_secret"),
    }

    try:
        resp = httpx.post(token_url, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=20)
    except httpx.RequestError as exc:
        target = _fib_target_label(base_url)
        raise ValueError(
            f"Cannot reach FIB token endpoint at {target}. Check Base URL and network access."
        ) from exc
    if resp.status_code != 200:
        raise ValueError(f"Token request failed ({resp.status_code}): {resp.text}")
    payload = resp.json()
    token = payload.get("access_token")
    if not token:
        raise ValueError("Missing access_token in FIB response.")
    return token


def _request_protected(
    *,
    account: dict,
    token: str,
    method: str,
    path: str,
    expected_statuses: tuple[int, ...],
) -> dict:
    base_url = (account.get("base_url") or "").strip()
    if not base_url:
        raise ValueError("FIB base URL is missing.")
    url = base_url.rstrip("/") + path
    try:
        resp = httpx.request(
            method.upper(),
            url,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
            timeout=20,
        )
    except httpx.RequestError as exc:
        target = _fib_target_label(base_url)
        raise ValueError(f"Cannot reach FIB endpoint at {target}. Check Base URL and network access.") from exc

    if resp.status_code not in expected_statuses:
        raise ValueError(f"FIB request failed ({resp.status_code}): {resp.text}")

    raw = (resp.text or "").strip()
    if not raw:
        return {}
    try:
        payload = resp.json()
        if isinstance(payload, dict):
            return payload
        return {"data": payload}
    except Exception:
        return {"raw": raw}


def create_payment(
    amount_iqd: int,
    description: str | None = None,
    *,
    options: dict[str, Any] | None = None,
    selector: dict[str, str] | None = None,
) -> dict:
    account = _get_active_account(selector=selector)
    token = _get_access_token(account)

    base_url = (account.get("base_url") or "").strip()
    pay_url = base_url.rstrip("/") + "/protected/v1/payments"

    create_options = options if isinstance(options, dict) else {}
    public_base = (os.getenv("PUBLIC_BASE_URL") or "http://127.0.0.1:8000").rstrip("/")
    desc = _clean_text(description or "Payment") or "Payment"
    if len(desc) > 50:
        raise ValueError("description must be 50 characters or fewer.")

    redirect_uri = _clean_url(
        create_options.get("redirect_uri") or (public_base + "/fib/return"),
        field_name="redirectUri",
    )
    status_callback = _clean_url(
        create_options.get("status_callback_url") or (public_base + "/fib/webhook"),
        field_name="statusCallbackUrl",
    )
    expires_in = _clean_text(create_options.get("expires_in"))
    refundable_for = _clean_text(create_options.get("refundable_for"))
    category = _clean_text(create_options.get("category")).upper()
    if category and category not in VALID_PAYMENT_CATEGORIES:
        allowed = ", ".join(sorted(VALID_PAYMENT_CATEGORIES))
        raise ValueError(f"category must be one of: {allowed}.")

    payload = {
        "monetaryValue": {
            "amount": str(int(amount_iqd or 0)),
            "currency": "IQD",
        },
        "statusCallbackUrl": status_callback,
        "description": desc,
        "redirectUri": redirect_uri,
    }
    if expires_in:
        payload["expiresIn"] = expires_in
    if category:
        payload["category"] = category
    if refundable_for:
        payload["refundableFor"] = refundable_for

    try:
        resp = httpx.post(
            pay_url,
            json=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
            timeout=20,
        )
    except httpx.RequestError as exc:
        target = _fib_target_label(base_url)
        raise ValueError(
            f"Cannot reach FIB payment endpoint at {target}. Check Base URL and network access."
        ) from exc
    if resp.status_code not in (200, 201):
        raise ValueError(f"Payment request failed ({resp.status_code}): {resp.text}")

    data = resp.json()
    ref = data.get("readableCode") or data.get("paymentId") or f"FIB-{int(time.time())}"
    payment_link = (
        data.get("personalAppLink")
        or data.get("businessAppLink")
        or data.get("corporateAppLink")
        or ""
    )
    qr_code = data.get("qrCode") or ""

    out = {
        "reference": ref,
        "amount": int(amount_iqd or 0),
        "currency": "IQD",
        "description": desc,
        "account_id": account.get("id"),
        "account_label": account.get("label") or "",
        "payment_link": payment_link,
        "qr_url": qr_code,
        "readable_code": data.get("readableCode"),
        "payment_id": data.get("paymentId"),
        "valid_until": data.get("validUntil"),
        "links": {
            "personal": data.get("personalAppLink"),
            "business": data.get("businessAppLink"),
            "corporate": data.get("corporateAppLink"),
        },
    }
    _remember_payment_account(str(out.get("payment_id") or ""), str(account.get("id") or ""))
    return out


def check_payment_status(payment_id: str, *, selector: dict[str, str] | None = None) -> dict:
    target = str(payment_id or "").strip()
    if not target:
        raise ValueError("payment_id is required.")
    account = _get_active_account(selector=selector, payment_id=target)
    token = _get_access_token(account)
    data = _request_protected(
        account=account,
        token=token,
        method="GET",
        path=f"/protected/v1/payments/{target}/status",
        expected_statuses=(200,),
    )
    if "paymentId" not in data:
        data["paymentId"] = target
    return data


def cancel_payment(payment_id: str, *, selector: dict[str, str] | None = None) -> dict:
    target = str(payment_id or "").strip()
    if not target:
        raise ValueError("payment_id is required.")
    account = _get_active_account(selector=selector, payment_id=target)
    token = _get_access_token(account)
    data = _request_protected(
        account=account,
        token=token,
        method="POST",
        path=f"/protected/v1/payments/{target}/cancel",
        expected_statuses=(200, 202, 204),
    )
    out = {"paymentId": target, "status": "cancel_requested"}
    if isinstance(data, dict):
        out.update(data)
    return out


def refund_payment(payment_id: str, *, selector: dict[str, str] | None = None) -> dict:
    target = str(payment_id or "").strip()
    if not target:
        raise ValueError("payment_id is required.")
    account = _get_active_account(selector=selector, payment_id=target)
    token = _get_access_token(account)
    data = _request_protected(
        account=account,
        token=token,
        method="POST",
        path=f"/protected/v1/payments/{target}/refund",
        expected_statuses=(200, 202, 204),
    )
    out = {"paymentId": target, "status": "refund_requested"}
    if isinstance(data, dict):
        out.update(data)
    return out
