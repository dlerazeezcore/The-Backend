from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import uuid
from typing import Any

import httpx


DEFAULT_BASE_URL = "https://api.esimaccess.com"


def _env_str(*names: str, default: str = "") -> str:
    for name in names:
        value = str(os.getenv(name) or "").strip()
        if value:
            return value
    return default


def _env_bool(name: str, default: bool = False) -> bool:
    value = str(os.getenv(name) or "").strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    try:
        value = str(os.getenv(name) or "").strip()
        if not value:
            return default
        return float(value)
    except Exception:
        return default


def is_configured() -> bool:
    access_code = _env_str("ESIMACCESS_ACCESS_CODE", "ESIM_ACCESS_CODE")
    return bool(access_code)


def _build_headers(body_str: str, signed: bool | None = None) -> dict[str, str]:
    access_code = _env_str("ESIMACCESS_ACCESS_CODE", "ESIM_ACCESS_CODE")
    if not access_code:
        raise ValueError("Missing ESIMACCESS_ACCESS_CODE.")
    use_signature = _env_bool("ESIMACCESS_USE_SIGNATURE", default=False) if signed is None else bool(signed)

    headers: dict[str, str] = {
        "RT-AccessCode": access_code,
        "Content-Type": "application/json",
    }
    if not use_signature:
        return headers

    secret_key = _env_str("ESIMACCESS_SECRET_KEY", "ESIM_SECRET_KEY")
    if not secret_key:
        raise ValueError("Missing ESIMACCESS_SECRET_KEY for signed request.")

    timestamp = str(int(time.time() * 1000))
    request_id = uuid.uuid4().hex
    sign_data = f"{timestamp}{request_id}{access_code}{body_str}"
    signature = hmac.new(
        secret_key.encode("utf-8"),
        sign_data.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    headers["RT-Timestamp"] = timestamp
    headers["RT-RequestID"] = request_id
    headers["RT-Signature"] = signature
    return headers


def _request(path: str, payload: dict[str, Any] | None = None, *, signed: bool | None = None) -> dict[str, Any]:
    base_url = _env_str("ESIMACCESS_BASE_URL", default=DEFAULT_BASE_URL).rstrip("/")
    timeout = _env_float("ESIMACCESS_TIMEOUT_SEC", default=30.0)
    body = payload if payload is not None else {}
    body_str = json.dumps(body, separators=(",", ":"), ensure_ascii=False)
    headers = _build_headers(body_str, signed=signed)
    url = f"{base_url}/{path.lstrip('/')}"

    with httpx.Client(timeout=timeout) as client:
        response = client.post(url, headers=headers, content=body_str)
    try:
        data = response.json()
    except Exception:
        data = {"raw": response.text}

    if response.status_code >= 400:
        raise ValueError(f"eSIMAccess HTTP {response.status_code}: {data}")
    return data if isinstance(data, dict) else {"data": data}


def list_packages(payload: dict[str, Any] | None = None, *, signed: bool | None = None) -> dict[str, Any]:
    body = {
        "locationCode": "",
        "type": "BASE",
        "packageCode": "",
        "slug": "",
        "iccid": "",
    }
    if isinstance(payload, dict):
        body.update(payload)
    return _request("/api/v1/open/package/list", body, signed=signed)


def order_profiles(payload: dict[str, Any], *, signed: bool | None = None) -> dict[str, Any]:
    body = payload if isinstance(payload, dict) else {}
    return _request("/api/v1/open/esim/order", body, signed=signed)


def query_profiles(payload: dict[str, Any], *, signed: bool | None = None) -> dict[str, Any]:
    body = payload if isinstance(payload, dict) else {}
    return _request("/api/v1/open/esim/query", body, signed=signed)


def balance_query(*, signed: bool | None = None) -> dict[str, Any]:
    return _request("/api/v1/open/balance/query", {}, signed=signed)


def list_locations(payload: dict[str, Any] | None = None, *, signed: bool | None = None) -> dict[str, Any]:
    body = payload if isinstance(payload, dict) else {}
    return _request("/api/v1/open/location/list", body, signed=signed)


def topup_profiles(payload: dict[str, Any] | None = None, *, signed: bool | None = None) -> dict[str, Any]:
    body = payload if isinstance(payload, dict) else {}
    return _request("/api/v1/open/esim/topup", body, signed=signed)


def usage_query(payload: dict[str, Any] | None = None, *, signed: bool | None = None) -> dict[str, Any]:
    body = payload if isinstance(payload, dict) else {}
    return _request("/api/v1/open/esim/usage/query", body, signed=signed)


def cancel_profile(payload: dict[str, Any] | None = None, *, signed: bool | None = None) -> dict[str, Any]:
    body = payload if isinstance(payload, dict) else {}
    return _request("/api/v1/open/esim/cancel", body, signed=signed)


def suspend_profile(payload: dict[str, Any] | None = None, *, signed: bool | None = None) -> dict[str, Any]:
    body = payload if isinstance(payload, dict) else {}
    return _request("/api/v1/open/esim/suspend", body, signed=signed)


def unsuspend_profile(payload: dict[str, Any] | None = None, *, signed: bool | None = None) -> dict[str, Any]:
    body = payload if isinstance(payload, dict) else {}
    return _request("/api/v1/open/esim/unsuspend", body, signed=signed)


def revoke_profile(payload: dict[str, Any] | None = None, *, signed: bool | None = None) -> dict[str, Any]:
    body = payload if isinstance(payload, dict) else {}
    return _request("/api/v1/open/esim/revoke", body, signed=signed)


def send_sms(payload: dict[str, Any] | None = None, *, signed: bool | None = None) -> dict[str, Any]:
    body = payload if isinstance(payload, dict) else {}
    return _request("/api/v1/open/esim/sendSms", body, signed=signed)


def set_webhook(payload: dict[str, Any] | None = None, *, signed: bool | None = None) -> dict[str, Any]:
    body = payload if isinstance(payload, dict) else {}
    return _request("/api/v1/open/webhook/save", body, signed=signed)
