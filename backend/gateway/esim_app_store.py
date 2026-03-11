from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Dict, List
from uuid import uuid4

from backend.core.paths import DATA_DIR
from backend.supabase import load_or_seed as sb_load_or_seed
from backend.supabase import save as sb_save

ROOT_ADMIN_PHONE = "+9647507343635"
STORE_PATH = DATA_DIR / "esim_app_store.json"


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _normalize_phone(phone: str) -> str:
    raw = (phone or "").strip().replace(" ", "")
    if raw.startswith("00"):
        raw = "+" + raw[2:]
    # Canonicalize known root-admin aliases to one stable E.164 value.
    root_e164 = ROOT_ADMIN_PHONE
    root_digits = re.sub(r"\D+", "", root_e164)
    local_digits = root_digits[3:] if root_digits.startswith("964") and len(root_digits) > 3 else root_digits
    digits = re.sub(r"\D+", "", raw)
    if digits in {root_digits, local_digits, f"0{local_digits}"}:
        return root_e164
    return raw


def normalize_phone(phone: str) -> str:
    return _normalize_phone(phone)


def _default_store() -> Dict[str, Any]:
    return {
        "users": [],
        "super_admins": [ROOT_ADMIN_PHONE],
        "settings": {
            "currency": {"enableIQD": False, "exchangeRate": "1320", "markupPercent": "0"},
            "whitelist": {"enabled": False, "codes": []},
            "popular": [],
        },
        "esims": [],
    }


def _load_local_store() -> dict:
    try:
        if STORE_PATH.exists():
            data = json.loads(STORE_PATH.read_text(encoding="utf-8") or "{}")
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


def _save_local_store(store: dict) -> None:
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STORE_PATH.write_text(json.dumps(store if isinstance(store, dict) else _default_store(), ensure_ascii=False, indent=2), encoding="utf-8")


def load_store() -> Dict[str, Any]:
    data = sb_load_or_seed(doc_key="esim_app_store", default=_default_store(), local_loader=_load_local_store)
    if not isinstance(data, dict):
        data = _default_store()
    data.setdefault("users", [])
    data.setdefault("super_admins", [ROOT_ADMIN_PHONE])
    data.setdefault("settings", {})
    data.setdefault("esims", [])
    if ROOT_ADMIN_PHONE not in data.get("super_admins", []):
        data["super_admins"].append(ROOT_ADMIN_PHONE)
    if "currency" not in data["settings"]:
        data["settings"]["currency"] = {"enableIQD": False, "exchangeRate": "1320", "markupPercent": "0"}
    if "whitelist" not in data["settings"]:
        data["settings"]["whitelist"] = {"enabled": False, "codes": []}
    if "popular" not in data["settings"]:
        data["settings"]["popular"] = []
    return data


def save_store(store: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(store, dict):
        store = _default_store()
    if ROOT_ADMIN_PHONE not in store.get("super_admins", []):
        store.setdefault("super_admins", []).append(ROOT_ADMIN_PHONE)
    sb_save(doc_key="esim_app_store", value=store, local_saver=_save_local_store)
    return store


def list_users() -> List[Dict[str, Any]]:
    store = load_store()
    return list(store.get("users") or [])


def get_user_by_phone(phone: str) -> Dict[str, Any] | None:
    normalized = _normalize_phone(phone)
    for user in list_users():
        if _normalize_phone(user.get("phone", "")) == normalized:
            return user
    return None


def get_user_by_id(user_id: str) -> Dict[str, Any] | None:
    for user in list_users():
        if str(user.get("id")) == str(user_id):
            return user
    return None


def create_user(phone: str, name: str) -> Dict[str, Any]:
    store = load_store()
    users = list(store.get("users") or [])
    normalized = _normalize_phone(phone)
    user = {
        "id": str(uuid4()),
        "name": name,
        "phone": normalized,
        "createdAt": _now_iso(),
        "loyalty": False,
    }
    users.append(user)
    store["users"] = users
    save_store(store)
    return user


def update_user(user: Dict[str, Any]) -> Dict[str, Any]:
    store = load_store()
    users = list(store.get("users") or [])
    updated = []
    for u in users:
        if str(u.get("id")) == str(user.get("id")):
            updated.append(user)
        else:
            updated.append(u)
    store["users"] = updated
    save_store(store)
    return user


def delete_user(user_id: str) -> bool:
    store = load_store()
    users = list(store.get("users") or [])
    target = get_user_by_id(user_id)
    if not target:
        return False
    if _normalize_phone(target.get("phone", "")) == ROOT_ADMIN_PHONE:
        return False
    store["users"] = [u for u in users if str(u.get("id")) != str(user_id)]
    save_store(store)
    return True


def list_super_admins() -> List[str]:
    store = load_store()
    admins = [str(x) for x in (store.get("super_admins") or []) if str(x).strip()]
    if ROOT_ADMIN_PHONE not in admins:
        admins.append(ROOT_ADMIN_PHONE)
    return admins


def add_super_admin(phone: str) -> List[str]:
    store = load_store()
    admins = list_super_admins()
    normalized = _normalize_phone(phone)
    if normalized and normalized not in admins:
        admins.append(normalized)
    store["super_admins"] = admins
    save_store(store)
    return admins


def remove_super_admin(phone: str) -> List[str]:
    store = load_store()
    normalized = _normalize_phone(phone)
    admins = [a for a in list_super_admins() if a != normalized and a != ROOT_ADMIN_PHONE]
    admins.append(ROOT_ADMIN_PHONE)
    store["super_admins"] = admins
    save_store(store)
    return admins


def is_super_admin(phone: str) -> bool:
    normalized = _normalize_phone(phone)
    return normalized in list_super_admins()


def get_settings() -> Dict[str, Any]:
    store = load_store()
    return store.get("settings") or {}


def update_settings(section: str, value: Any) -> Dict[str, Any]:
    store = load_store()
    settings = store.get("settings") or {}
    settings[section] = value
    store["settings"] = settings
    save_store(store)
    return settings


def list_esims() -> List[Dict[str, Any]]:
    store = load_store()
    return list(store.get("esims") or [])


def update_esim(esim_id: str, updates: Dict[str, Any]) -> Dict[str, Any] | None:
    store = load_store()
    esims = list(store.get("esims") or [])
    updated_item = None
    out = []
    for item in esims:
        if str(item.get("id")) == str(esim_id):
            merged = dict(item)
            merged.update(updates or {})
            updated_item = merged
            out.append(merged)
        else:
            out.append(item)
    if updated_item is None:
        return None
    store["esims"] = out
    save_store(store)
    return updated_item


def create_esim(
    user_id: str,
    name: str,
    country: str,
    flag: str,
    data_total: float,
    days_left: int,
    activation_code: str | None = None,
    iccid: str | None = None,
    order_reference: str | None = None,
    install_url: str | None = None,
    status: str = "active",
    activated_date: str | None = None,
) -> Dict[str, Any]:
    store = load_store()
    esims = list(store.get("esims") or [])

    matching_id = str(uuid4()).replace("-", "").upper()[:12]
    generated_code = activation_code if activation_code is not None else f"LPA:1$rsp-3104.idemia.io${matching_id}"
    generated_iccid = iccid if iccid is not None else ("89" + str(uuid4().int)[:17])
    state = str(status or "active").strip().lower()
    if state not in {"active", "pending", "expired"}:
        state = "active"
    if activated_date is None:
        activated_date = _now_iso() if state == "active" else ""
    record = {
        "id": str(uuid4()),
        "userId": str(user_id or ""),
        "name": name,
        "country": country,
        "flag": flag or "🌍",
        "status": state,
        "installed": False,
        "dataUsed": 0,
        "dataTotal": max(0.0, float(data_total or 0)),
        "daysLeft": max(0, int(days_left or 0)),
        "validityDays": max(0, int(days_left or 0)),
        "iccid": str(generated_iccid or ""),
        "activatedDate": str(activated_date or ""),
        "activationCode": generated_code,
        "installUrl": str(install_url or ""),
        "orderReference": str(order_reference or ""),
        "createdAt": _now_iso(),
    }
    esims.insert(0, record)
    store["esims"] = esims
    save_store(store)
    return record
