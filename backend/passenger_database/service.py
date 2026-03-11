from __future__ import annotations

import json
import os
import tempfile
import uuid
from datetime import date, datetime
from threading import Lock
from typing import Any

from backend.core.paths import DATA_DIR
from backend.esim.esimaccess.store import list_esimaccess_orders_for_owner
from backend.supabase.passenger_database.passenger_db_repo import (
    load_passenger_history_doc,
    load_passenger_profiles_doc,
    save_passenger_history_doc,
    save_passenger_profiles_doc,
)

PASSENGER_DB_DIR = DATA_DIR / "passenger_db"
PROFILES_PATH = PASSENGER_DB_DIR / "profiles.json"
HISTORY_PATH = PASSENGER_DB_DIR / "history.json"
_JSON_LOCK = Lock()


def _ensure_files() -> None:
    PASSENGER_DB_DIR.mkdir(parents=True, exist_ok=True)
    if not PROFILES_PATH.exists():
        PROFILES_PATH.write_text("[]", encoding="utf-8")
    if not HISTORY_PATH.exists():
        HISTORY_PATH.write_text("[]", encoding="utf-8")


def _load_json_local(path: Path, default: Any) -> Any:
    with _JSON_LOCK:
        try:
            _ensure_files()
            raw = path.read_text(encoding="utf-8")
            return json.loads(raw) if raw.strip() else default
        except Exception:
            return default


def _save_json_local(path: Path, data: Any) -> None:
    with _JSON_LOCK:
        _ensure_files()
        tmp = tempfile.NamedTemporaryFile("w", delete=False, dir=path.parent, encoding="utf-8")
        try:
            tmp.write(json.dumps(data, ensure_ascii=False, indent=2))
            tmp.flush()
            os.fsync(tmp.fileno())
        finally:
            tmp.close()
        os.replace(tmp.name, path)


def _load_json(path: Path, default: Any) -> Any:
    if str(path) == str(PROFILES_PATH):
        return load_passenger_profiles_doc(local_loader=lambda: _load_json_local(path, default))
    if str(path) == str(HISTORY_PATH):
        return load_passenger_history_doc(local_loader=lambda: _load_json_local(path, default))
    return _load_json_local(path, default)


def _save_json(path: Path, data: Any) -> None:
    if str(path) == str(PROFILES_PATH):
        save_passenger_profiles_doc(value=data, local_saver=lambda payload: _save_json_local(path, payload))
        return
    if str(path) == str(HISTORY_PATH):
        save_passenger_history_doc(value=data, local_saver=lambda payload: _save_json_local(path, payload))
        return
    _save_json_local(path, data)


def load_profiles() -> list[dict[str, Any]]:
    data = _load_json(PROFILES_PATH, [])
    return data if isinstance(data, list) else []


def save_profiles(profiles: list[dict[str, Any]]) -> None:
    _save_json(PROFILES_PATH, profiles)


def load_history() -> list[dict[str, Any]]:
    data = _load_json(HISTORY_PATH, [])
    return data if isinstance(data, list) else []


def save_history(history: list[dict[str, Any]]) -> None:
    _save_json(HISTORY_PATH, history)


def _parse_iso_date(d: str) -> date | None:
    try:
        d = (d or "").strip()
        if not d:
            return None
        return datetime.strptime(d, "%Y-%m-%d").date()
    except Exception:
        return None


def age_category(dob_iso: str, on: date | None = None) -> str:
    on = on or date.today()
    dob = _parse_iso_date(dob_iso)
    if not dob:
        return "unknown"
    years = on.year - dob.year - ((on.month, on.day) < (dob.month, dob.day))
    if years < 2:
        return "infant"
    if years < 12:
        return "child"
    return "adult"


def normalize(s: str) -> str:
    return " ".join((s or "").strip().lower().split())


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def profile_has_user_access(profile: dict[str, Any], user_id: str) -> bool:
    owner = str(profile.get("owner_user_id") or "")
    allowed = profile.get("allowed_user_ids") or []
    allowed = [str(x) for x in allowed] if isinstance(allowed, list) else []
    return owner == str(user_id) or str(user_id) in allowed


def find_member_by_passport(
    profiles: list[dict[str, Any]],
    passport_number: str,
    owner_user_id: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    passport_number = normalize(passport_number)
    if not passport_number:
        return None
    for profile in profiles:
        if owner_user_id and str(profile.get("owner_user_id") or "") != str(owner_user_id):
            continue
        for member in profile.get("members") or []:
            for doc in member.get("passports") or []:
                if normalize(str(doc.get("number") or "")) == passport_number:
                    return profile, member
            if normalize(str(member.get("passport_number") or "")) == passport_number:
                return profile, member
    return None


def find_members_by_query(profiles: list[dict[str, Any]], q: str) -> list[dict[str, Any]]:
    qn = normalize(q)
    if not qn:
        return []

    out: list[dict[str, Any]] = []
    for profile in profiles:
        if qn in normalize(str(profile.get("phone") or "")) or qn in normalize(str(profile.get("label") or "")):
            out.append(profile)
            continue

        hit = False
        for member in profile.get("members") or []:
            if qn in normalize(str(member.get("first_name") or "")) or qn in normalize(str(member.get("last_name") or "")):
                hit = True
            if qn in normalize(str(member.get("nationality") or "")) or qn in normalize(str(member.get("national_id_number") or "")):
                hit = True
            for doc in member.get("passports") or []:
                if qn in normalize(str(doc.get("number") or "")):
                    hit = True
            if hit:
                break
        if hit:
            out.append(profile)
    return out


def create_profile(owner_user_id: str, label: str = "", phone: str = "", allowed_user_ids: list[str] | None = None) -> dict[str, Any]:
    now = datetime.utcnow().isoformat() + "Z"
    return {
        "id": _new_id("prof"),
        "owner_user_id": str(owner_user_id),
        "label": (label or "").strip(),
        "phone": (phone or "").strip(),
        "allowed_user_ids": [str(x) for x in (allowed_user_ids or [])],
        "members": [],
        "created_at": now,
        "updated_at": now,
    }


def create_member(data: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("member data must be an object")

    first_name = (data.get("first_name") or "").strip()
    last_name = (data.get("last_name") or "").strip()
    if not first_name and not last_name:
        raise ValueError("first_name or last_name is required")

    passports: list[dict[str, Any]] = []
    raw_passports = data.get("passports")
    if isinstance(raw_passports, list):
        passports.extend(doc for doc in raw_passports if isinstance(doc, dict))

    passport_number = (data.get("passport_number") or "").strip()
    if passport_number:
        passports.append(
            {
                "number": passport_number,
                "expiry_date": (data.get("passport_expiry_date") or "").strip(),
                "issue_place": (data.get("passport_issue_place") or "").strip(),
                "nationality": (data.get("passport_nationality") or data.get("nationality") or "").strip(),
            }
        )

    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for doc in passports:
        num = (doc.get("number") or "").strip()
        if not num:
            continue
        key = normalize(num)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(
            {
                "number": num,
                "expiry_date": (doc.get("expiry_date") or "").strip(),
                "issue_place": (doc.get("issue_place") or "").strip(),
                "nationality": str(doc.get("nationality") or doc.get("country") or doc.get("passport_nationality") or "").strip(),
            }
        )

    if not deduped:
        raise ValueError("at least one passport number is required")

    now = datetime.utcnow().isoformat() + "Z"
    nationality = deduped[0].get("nationality") or (data.get("nationality") or "").strip()
    return {
        "id": _new_id("mem"),
        "title": (data.get("title") or "").strip(),
        "first_name": first_name,
        "last_name": last_name,
        "dob": (data.get("dob") or "").strip(),
        "nationality": nationality,
        "national_id_number": (data.get("national_id_number") or "").strip(),
        "phone": (data.get("phone") or "").strip(),
        "passports": deduped,
        "notes": (data.get("notes") or "").strip(),
        "created_at": now,
        "updated_at": now,
    }


def upsert_member_passport(member: dict[str, Any], passport: dict[str, Any]) -> None:
    docs = member.get("passports") or []
    if not isinstance(docs, list):
        docs = []
    num = (passport.get("number") or "").strip()
    if not num:
        return
    num_n = normalize(num)
    updated = False
    for doc in docs:
        if normalize(str(doc.get("number") or "")) != num_n:
            continue
        doc["expiry_date"] = (passport.get("expiry_date") or doc.get("expiry_date") or "").strip()
        doc["issue_place"] = (passport.get("issue_place") or doc.get("issue_place") or "").strip()
        doc["nationality"] = (passport.get("nationality") or doc.get("nationality") or "").strip()
        updated = True
        break
    if not updated:
        docs.append(
            {
                "number": num,
                "expiry_date": (passport.get("expiry_date") or "").strip(),
                "issue_place": (passport.get("issue_place") or "").strip(),
                "nationality": (passport.get("nationality") or "").strip(),
            }
        )
    member["passports"] = docs
    member["nationality"] = member.get("nationality") or (passport.get("nationality") or "").strip()
    member["updated_at"] = datetime.utcnow().isoformat() + "Z"


def compute_view_profile(profile: dict[str, Any]) -> dict[str, Any]:
    out = json.loads(json.dumps(profile))
    out.pop("owner_user_id", None)
    out.pop("allowed_user_ids", None)
    for member in out.get("members") or []:
        member["age_category"] = age_category(str(member.get("dob") or ""))
        docs = member.get("passports") or []
        member["primary_passport_number"] = str(docs[0].get("number") or "") if isinstance(docs, list) and docs else ""
    return out


def add_history_event(
    owner_user_id: str,
    profile_id: str,
    member_id: str,
    kind: str,
    details: dict[str, Any],
) -> dict[str, Any]:
    history = load_history()
    event = {
        "id": _new_id("hist"),
        "owner_user_id": str(owner_user_id),
        "profile_id": str(profile_id),
        "member_id": str(member_id),
        "kind": str(kind),
        "details": details if isinstance(details, dict) else {},
        "created_at": datetime.utcnow().isoformat() + "Z",
    }
    history.append(event)
    save_history(history)
    return event


def history_for_member(owner_user_id: str, member_id: str) -> list[dict[str, Any]]:
    history = load_history()
    out = [
        event
        for event in history
        if str(event.get("owner_user_id") or "") == str(owner_user_id)
        and str(event.get("member_id") or "") == str(member_id)
    ]
    out.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
    return out


def _history_has_esim(owner_user_id: str, member_id: str, order_reference: str) -> bool:
    if not order_reference:
        return False
    history = load_history()
    for event in history:
        if str(event.get("owner_user_id") or "") != str(owner_user_id):
            continue
        if str(event.get("member_id") or "") != str(member_id):
            continue
        if str(event.get("kind") or "") != "esim":
            continue
        details = event.get("details") or {}
        if str(details.get("order_reference") or "") == str(order_reference):
            return True
    return False


def _member_passport_numbers(member: dict[str, Any]) -> set[str]:
    numbers: set[str] = set()
    for doc in member.get("passports") or []:
        num = normalize(str(doc.get("number") or ""))
        if num:
            numbers.add(num)
    return numbers


def backfill_esim_history_for_member(
    owner_user_id: str,
    profile: dict[str, Any],
    member: dict[str, Any],
    orders: list[dict[str, Any]],
) -> int:
    if not isinstance(orders, list):
        return 0

    fn = normalize(str(member.get("first_name") or ""))
    ln = normalize(str(member.get("last_name") or ""))
    mem_phone = normalize(str(member.get("phone") or ""))
    prof_phone = normalize(str(profile.get("phone") or ""))
    if not fn and not ln:
        return 0

    added = 0
    for order in orders:
        if not isinstance(order, dict):
            continue
        customer_name = str(order.get("customer_name") or "")
        customer_phone = normalize(str(order.get("customer_phone") or ""))
        ofn, oln = _split_name(customer_name)
        if normalize(ofn) != fn or normalize(oln) != ln:
            continue
        if customer_phone and customer_phone not in {mem_phone, prof_phone}:
            continue
        order_ref = str(order.get("order_reference") or "")
        if order_ref and _history_has_esim(owner_user_id, str(member.get("id")), order_ref):
            continue
        order_passport = normalize(str(order.get("passport_number") or order.get("passport") or ""))
        if order_passport and order_passport not in _member_passport_numbers(member):
            continue

        details = {
            "bundle_name": order.get("bundle_name") or "",
            "bundle_description": order.get("bundle_description") or "",
            "country_name": order.get("country_name") or "",
            "country_iso": order.get("country_iso") or "",
            "quantity": order.get("quantity") or 1,
            "total_iqd": order.get("total_iqd"),
            "currency": order.get("currency") or "IQD",
            "status": order.get("status") or "",
            "order_reference": order_ref,
            "company_name": order.get("company_name") or "",
            "agent_name": order.get("agent_name") or "",
            "customer_phone": order.get("customer_phone") or "",
        }
        add_history_event(
            owner_user_id=owner_user_id,
            profile_id=str(profile.get("id")),
            member_id=str(member.get("id")),
            kind="esim",
            details=details,
        )
        added += 1
    return added


def _split_name(full: str) -> tuple[str, str]:
    full = (full or "").strip()
    if not full:
        return "", ""
    parts = [part for part in full.split(" ") if part.strip()]
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def find_member_by_name_phone(
    profiles: list[dict[str, Any]],
    owner_user_id: str,
    first_name: str,
    last_name: str,
    phone: str = "",
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    fn = normalize(first_name)
    ln = normalize(last_name)
    ph = normalize(phone)
    if not fn and not ln:
        return None
    for profile in profiles:
        if str(profile.get("owner_user_id") or "") != str(owner_user_id):
            continue
        prof_phone = normalize(str(profile.get("phone") or ""))
        for member in profile.get("members") or []:
            if normalize(str(member.get("first_name") or "")) != fn:
                continue
            if normalize(str(member.get("last_name") or "")) != ln:
                continue
            if ph:
                mem_phone = normalize(str(member.get("phone") or ""))
                if mem_phone != ph and prof_phone != ph:
                    continue
            return profile, member
    return None


def find_member_by_name_only(
    profiles: list[dict[str, Any]],
    owner_user_id: str,
    first_name: str,
    last_name: str,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    fn = normalize(first_name)
    ln = normalize(last_name)
    if not fn and not ln:
        return None
    matches: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for profile in profiles:
        if str(profile.get("owner_user_id") or "") != str(owner_user_id):
            continue
        for member in profile.get("members") or []:
            if normalize(str(member.get("first_name") or "")) != fn:
                continue
            if normalize(str(member.get("last_name") or "")) != ln:
                continue
            matches.append((profile, member))
            if len(matches) > 1:
                return None
    return matches[0] if matches else None


def list_esim_orders_for_owner(owner_user_id: str) -> list[dict[str, Any]]:
    return list_esimaccess_orders_for_owner(owner_user_id)


__all__ = [
    "add_history_event",
    "age_category",
    "backfill_esim_history_for_member",
    "compute_view_profile",
    "create_member",
    "create_profile",
    "find_member_by_name_only",
    "find_member_by_name_phone",
    "find_member_by_passport",
    "find_members_by_query",
    "history_for_member",
    "list_esim_orders_for_owner",
    "load_history",
    "load_profiles",
    "normalize",
    "profile_has_user_access",
    "save_history",
    "save_profiles",
    "upsert_member_passport",
]
