from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from typing import Any

from backend.core.paths import DATA_DIR
from backend.supabase import load_or_seed as sb_load_or_seed
from backend.supabase import save as sb_save

SUBS_PATH = DATA_DIR / "subscriptions.json"
ADDONS_PATH = DATA_DIR / "addons.json"

ADDONS_DEFAULT = {
    "passenger_database": {
        "name": "Passenger Database",
        "description": "Profiles, family members, search, and automatic history linking.",
        "monthly_price": 10000,
        "yearly_price": 100000,
        "currency": "IQD",
        "visible": True,
    },
    "visa_vendor": {
        "name": "Visa Vendor",
        "description": "List visa prices, receive vendor submissions, and update status.",
        "monthly_price": 20000,
        "yearly_price": 200000,
        "currency": "IQD",
        "visible": True,
    },
    "support_chat_cliq": {
        "name": "Support Chat (Zoho Cliq)",
        "description": "Let users send transaction support messages directly to Zoho Cliq.",
        "monthly_price": 5000,
        "yearly_price": 50000,
        "currency": "IQD",
        "visible": True,
    },
}


def _normalize_addons(raw: Any) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    raw_dict = raw if isinstance(raw, dict) else {}
    for key, base in ADDONS_DEFAULT.items():
        if key == "esim":
            continue
        merged = dict(base)
        if isinstance(raw_dict.get(key), dict):
            merged.update(raw_dict.get(key))
        merged["visible"] = bool(merged.get("visible", True))
        out[key] = merged
    for key, val in raw_dict.items():
        if key == "esim":
            continue
        if key not in out and isinstance(val, dict):
            item = dict(val)
            item["visible"] = bool(item.get("visible", True))
            out[key] = item
    return out


def _load_addons_local_raw() -> dict[str, Any]:
    try:
        if not ADDONS_PATH.exists():
            return {}
        raw = ADDONS_PATH.read_text(encoding="utf-8")
        data = json.loads(raw) if raw.strip() else {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_addons_local_raw(addons: dict[str, Any]) -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        ADDONS_PATH.write_text(
            json.dumps(addons if isinstance(addons, dict) else {}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


def load_addons() -> dict[str, dict[str, Any]]:
    raw = sb_load_or_seed(doc_key="addons", default={}, local_loader=_load_addons_local_raw)
    return _normalize_addons(raw)


def save_addons(addons: dict[str, dict[str, Any]]) -> None:
    sb_save(doc_key="addons", value=addons if isinstance(addons, dict) else {}, local_saver=_save_addons_local_raw)


ADDONS = load_addons()


def _ensure_file() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not SUBS_PATH.exists():
        SUBS_PATH.write_text("[]", encoding="utf-8")


def _load_subscriptions_local_raw() -> list[dict[str, Any]]:
    try:
        _ensure_file()
        raw = SUBS_PATH.read_text(encoding="utf-8")
        data = json.loads(raw) if raw.strip() else []
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_subscriptions_local_raw(data: list[dict[str, Any]]) -> None:
    _ensure_file()
    SUBS_PATH.write_text(json.dumps(data if isinstance(data, list) else [], ensure_ascii=False, indent=2), encoding="utf-8")


def _load() -> list[dict[str, Any]]:
    data = sb_load_or_seed(doc_key="subscriptions", default=[], local_loader=_load_subscriptions_local_raw)
    return data if isinstance(data, list) else []


def _save(data: list[dict[str, Any]]) -> None:
    sb_save(doc_key="subscriptions", value=data if isinstance(data, list) else [], local_saver=_save_subscriptions_local_raw)


def _new_id() -> str:
    return f"sub_{uuid.uuid4().hex[:12]}"


def now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


def parse_iso(dt: str) -> datetime | None:
    try:
        dt = (dt or "").strip()
        if not dt:
            return None
        if dt.endswith("Z"):
            dt = dt[:-1]
        return datetime.fromisoformat(dt)
    except Exception:
        return None


def is_active(sub: dict[str, Any], at: datetime | None = None) -> bool:
    at = at or datetime.utcnow()
    if str(sub.get("status") or "").lower() != "active":
        return False
    end = parse_iso(str(sub.get("end_at") or ""))
    if not end:
        return False
    return at < end


def compute_period_dates(period: str, start_at: datetime | None = None) -> tuple[datetime, datetime]:
    period = str(period or "").strip().lower()
    start = start_at or datetime.utcnow()
    end = start + timedelta(days=365 if period == "yearly" else 30)
    return start, end


def update_addon_prices(
    addon: str,
    monthly_price: float | None = None,
    yearly_price: float | None = None,
    visible: bool | None = None,
) -> tuple[bool, str, dict[str, Any] | None]:
    addon = str(addon or "").strip()
    if addon not in ADDONS:
        return False, "Unknown add-on.", None
    try:
        if monthly_price is not None:
            ADDONS[addon]["monthly_price"] = float(monthly_price)
        if yearly_price is not None:
            ADDONS[addon]["yearly_price"] = float(yearly_price)
        if visible is not None:
            ADDONS[addon]["visible"] = bool(visible)
    except Exception:
        return False, "Invalid price.", None

    save_addons(ADDONS)
    return True, "Updated.", ADDONS.get(addon)


def grant_subscription_free(
    owner_user_id: str,
    addon: str,
    period: str,
    granted_by_user_id: str,
) -> tuple[bool, str, dict[str, Any] | None]:
    addon = str(addon or "").strip()
    period = str(period or "").strip().lower()
    if addon not in ADDONS:
        return False, "Unknown add-on.", None
    if period not in {"monthly", "yearly"}:
        return False, "Invalid period.", None

    subs = _load()
    active_sub = next(
        (
            sub
            for sub in subs
            if str(sub.get("owner_user_id") or "") == str(owner_user_id)
            and str(sub.get("addon") or "") == addon
            and is_active(sub)
        ),
        None,
    )
    if active_sub:
        return False, "User already has an active subscription.", None

    start, end = compute_period_dates(period)
    sub = {
        "id": _new_id(),
        "owner_user_id": str(owner_user_id),
        "addon": addon,
        "addon_name": ADDONS[addon]["name"],
        "period": period,
        "price": 0,
        "currency": ADDONS[addon]["currency"],
        "status": "active",
        "start_at": start.isoformat() + "Z",
        "end_at": end.isoformat() + "Z",
        "recurring": False,
        "renewal_period": "",
        "recurring_stopped_at": "",
        "assigned_user_ids": [],
        "purchased_by_user_id": str(granted_by_user_id),
        "payment_method": "admin_grant",
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    subs.append(sub)
    _save(subs)
    return True, "Granted.", sub


def list_subscriptions_for_owner(owner_user_id: str) -> list[dict[str, Any]]:
    subs = _load()
    out = [sub for sub in subs if str(sub.get("owner_user_id") or "") == str(owner_user_id)]
    out.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
    return out


def list_active_addons_for_user(user_id: str, owner_user_id: str | None = None) -> list[str]:
    subs = _load()
    active: list[str] = []
    for sub in subs:
        addon = str(sub.get("addon") or "")
        if not addon or not is_active(sub):
            continue
        owner = str(sub.get("owner_user_id") or "")
        assigned = sub.get("assigned_user_ids") or []
        assigned = [str(x) for x in assigned] if isinstance(assigned, list) else []
        if owner_user_id and owner != str(owner_user_id):
            continue
        if owner == str(user_id) or str(user_id) in assigned:
            if addon not in active:
                active.append(addon)
    return active


def purchase_subscription(
    owner_user_id: str,
    addon: str,
    period: str,
    purchased_by_user_id: str,
    recurring: bool = False,
) -> tuple[bool, str, dict[str, Any] | None]:
    addon = str(addon or "").strip()
    period = str(period or "").strip().lower()
    if addon not in ADDONS:
        return False, "Unknown add-on.", None
    if period not in {"monthly", "yearly"}:
        return False, "Invalid period.", None

    start, end = compute_period_dates(period)
    price = ADDONS[addon]["monthly_price"] if period == "monthly" else ADDONS[addon]["yearly_price"]
    sub = {
        "id": _new_id(),
        "owner_user_id": str(owner_user_id),
        "addon": addon,
        "addon_name": ADDONS[addon]["name"],
        "period": period,
        "price": price,
        "currency": ADDONS[addon]["currency"],
        "status": "active",
        "start_at": start.isoformat() + "Z",
        "end_at": end.isoformat() + "Z",
        "recurring": bool(recurring),
        "renewal_period": period if recurring else "",
        "recurring_stopped_at": "",
        "assigned_user_ids": [],
        "purchased_by_user_id": str(purchased_by_user_id),
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    subs = _load()
    subs.append(sub)
    _save(subs)
    return True, "Purchased.", sub


def admin_update_subscription(sub_id: str, fields: dict[str, Any]) -> tuple[bool, str, dict[str, Any] | None]:
    subs = _load()
    sub = next((row for row in subs if str(row.get("id")) == str(sub_id)), None)
    if not sub:
        return False, "Subscription not found.", None

    if "status" in fields:
        sub["status"] = str(fields.get("status") or "").strip().lower() or sub.get("status")
    if "end_at" in fields:
        sub["end_at"] = str(fields.get("end_at") or "").strip() or sub.get("end_at")
    if "assigned_user_ids" in fields and isinstance(fields.get("assigned_user_ids"), list):
        sub["assigned_user_ids"] = [str(x) for x in fields.get("assigned_user_ids")]
    if "recurring" in fields:
        sub["recurring"] = bool(fields.get("recurring"))
    if "renewal_period" in fields:
        sub["renewal_period"] = str(fields.get("renewal_period") or "").strip()
    if "recurring_stopped_at" in fields:
        sub["recurring_stopped_at"] = str(fields.get("recurring_stopped_at") or "").strip()

    sub["updated_at"] = now_iso()
    _save(subs)
    return True, "Updated.", sub


def admin_delete_subscription(sub_id: str) -> bool:
    subs = _load()
    before = len(subs)
    subs = [row for row in subs if str(row.get("id")) != str(sub_id)]
    _save(subs)
    return len(subs) != before


def list_all_subscriptions() -> list[dict[str, Any]]:
    subs = _load()
    subs.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
    return subs


__all__ = [
    "ADDONS",
    "admin_delete_subscription",
    "admin_update_subscription",
    "compute_period_dates",
    "grant_subscription_free",
    "is_active",
    "list_active_addons_for_user",
    "list_all_subscriptions",
    "list_subscriptions_for_owner",
    "load_addons",
    "parse_iso",
    "purchase_subscription",
    "save_addons",
    "update_addon_prices",
]
