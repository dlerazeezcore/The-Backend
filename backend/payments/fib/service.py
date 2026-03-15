from __future__ import annotations

import json
import os
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

from backend.supabase import load_or_seed as sb_load_or_seed
from backend.supabase import save as sb_save


CONFIG_PATH = Path(__file__).resolve().parent / "config.json"


def _fib_target_label(base_url: str) -> str:
    host = (urlparse(base_url).hostname or "").strip()
    return host or "configured FIB host"


def _load_config_raw() -> dict:
    def _load_local() -> dict:
        try:
            if CONFIG_PATH.exists():
                data = json.loads(CONFIG_PATH.read_text(encoding="utf-8") or "{}")
                return data if isinstance(data, dict) else {}
        except Exception:
            pass
        return {"accounts": [], "active_account_id": ""}

    data = sb_load_or_seed(doc_key="fib_config", default={"accounts": [], "active_account_id": ""}, local_loader=_load_local)
    if not isinstance(data, dict):
        data = {"accounts": [], "active_account_id": ""}
    if not isinstance(data.get("accounts"), list):
        data["accounts"] = []
    if "active_account_id" not in data:
        data["active_account_id"] = ""
    return data


def _redact_account(account: dict) -> dict:
    row = account if isinstance(account, dict) else {}
    return {
        "id": str(row.get("id") or "").strip(),
        "label": str(row.get("label") or "").strip(),
        "client_id": str(row.get("client_id") or "").strip(),
        "client_secret": "",
        "has_client_secret": bool(str(row.get("client_secret") or "").strip()),
        "base_url": str(row.get("base_url") or "").strip(),
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
    if not isinstance(accounts, list):
        accounts = []
    norm_accounts = []
    for a in accounts:
        if not isinstance(a, dict):
            continue
        aid = str(a.get("id") or "").strip()
        if not aid:
            continue
        prev = existing_by_id.get(aid) if isinstance(existing_by_id.get(aid), dict) else {}
        incoming_secret = str(a.get("client_secret") or "").strip()
        merged_secret = incoming_secret or str(prev.get("client_secret") or "").strip()
        norm_accounts.append(
            {
                "id": aid,
                "label": str(a.get("label") or ""),
                "client_id": str(a.get("client_id") or ""),
                "client_secret": merged_secret,
                "base_url": str(a.get("base_url") or ""),
            }
        )
    out = {
        "accounts": norm_accounts,
        "active_account_id": "",
    }
    raw_active = cfg.get("active_account_id", None)
    if raw_active is None:
        active = norm_accounts[0].get("id") if norm_accounts else ""
    else:
        active = str(raw_active).strip()
        if active and not any(a.get("id") == active for a in norm_accounts):
            active = ""
    out["active_account_id"] = active

    def _save_local(value: dict) -> None:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")

    sb_save(doc_key="fib_config", value=out, local_saver=_save_local)
    return _redact_config(out)


def _get_active_account() -> dict:
    cfg = _load_config_raw()
    accounts = cfg.get("accounts") or []
    active_id = str(cfg.get("active_account_id") or "").strip()
    account = next((a for a in accounts if str(a.get("id")) == active_id), None)

    env_base = os.getenv("FIB_BASE_URL") or ""
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


def create_payment(amount_iqd: int, description: str | None = None) -> dict:
    account = _get_active_account()
    token = _get_access_token(account)

    base_url = (account.get("base_url") or "").strip()
    pay_url = base_url.rstrip("/") + "/protected/v1/payments"

    public_base = (os.getenv("PUBLIC_BASE_URL") or "http://127.0.0.1:8000").rstrip("/")
    redirect_uri = public_base + "/fib/return"
    status_callback = public_base + "/fib/webhook"
    desc = (description or "Payment").strip()

    payload = {
        "monetaryValue": {
            "amount": str(int(amount_iqd or 0)),
            "currency": "IQD",
        },
        "statusCallbackUrl": status_callback,
        "description": desc,
        "redirectUri": redirect_uri,
        "expiresIn": "PT1H",
        "category": "ECOMMERCE",
        "refundableFor": "PT48H",
    }

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

    return {
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


def check_payment_status(payment_id: str) -> dict:
    target = str(payment_id or "").strip()
    if not target:
        raise ValueError("payment_id is required.")
    account = _get_active_account()
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


def cancel_payment(payment_id: str) -> dict:
    target = str(payment_id or "").strip()
    if not target:
        raise ValueError("payment_id is required.")
    account = _get_active_account()
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


def refund_payment(payment_id: str) -> dict:
    target = str(payment_id or "").strip()
    if not target:
        raise ValueError("payment_id is required.")
    account = _get_active_account()
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
