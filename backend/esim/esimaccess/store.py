from __future__ import annotations

import json
import os
import tempfile
import uuid
from datetime import datetime
from typing import Any

from backend.core.paths import DATA_DIR
from backend.supabase import load_or_seed
from backend.supabase.esimaccess import load_esimaccess_orders_doc, save_esimaccess_orders_doc

ESIMACCESS_ORDERS_PATH = DATA_DIR / "esimaccess_orders.json"
LEGACY_ESIM_ORDERS_PATH = DATA_DIR / "esim_orders.json"


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _load_local_doc(path: Path, key: str) -> dict[str, Any]:
    if not path.exists():
        return {key: []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8") or "{}")
        if isinstance(payload, list):
            return {key: payload}
        if isinstance(payload, dict):
            items = payload.get(key) or payload.get("items") or []
            return {key: items if isinstance(items, list) else []}
    except Exception:
        pass
    return {key: []}


def _save_local_doc(path: Path, key: str, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = tempfile.NamedTemporaryFile("w", delete=False, dir=str(path.parent), encoding="utf-8")
    try:
        json.dump(payload if isinstance(payload, dict) else {key: []}, tmp, ensure_ascii=False, indent=2)
        tmp.flush()
        os.fsync(tmp.fileno())
    finally:
        tmp.close()
    os.replace(tmp.name, path)


def _extract_items(payload: Any, preferred_key: str) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in (preferred_key, "orders", "items"):
            items = payload.get(key)
            if isinstance(items, list):
                return [row for row in items if isinstance(row, dict)]
    return []


def _load_legacy_items() -> list[dict[str, Any]]:
    payload = load_or_seed(
        doc_key="esim_orders",
        default={"orders": []},
        local_loader=lambda: _load_local_doc(LEGACY_ESIM_ORDERS_PATH, "orders"),
    )
    return _extract_items(payload, "orders")


def load_esimaccess_orders_items() -> list[dict[str, Any]]:
    payload = load_esimaccess_orders_doc(local_loader=lambda: _load_local_doc(ESIMACCESS_ORDERS_PATH, "orders"))
    items = _extract_items(payload, "orders")
    if items:
        return items

    legacy = _load_legacy_items()
    if legacy:
        save_esimaccess_orders_items(legacy)
        return legacy
    return items


def save_esimaccess_orders_items(items: list[dict[str, Any]]) -> None:
    out = {"orders": items if isinstance(items, list) else []}
    save_esimaccess_orders_doc(
        value=out,
        local_saver=lambda payload: _save_local_doc(
            ESIMACCESS_ORDERS_PATH,
            "orders",
            payload if isinstance(payload, dict) else out,
        ),
    )


def record_esimaccess_order(order: dict[str, Any]) -> dict[str, Any]:
    items = load_esimaccess_orders_items()
    row = dict(order if isinstance(order, dict) else {})
    if not str(row.get("id") or "").strip():
        row["id"] = "esimaccess_" + uuid.uuid4().hex[:12]
    if not str(row.get("created_at") or "").strip():
        row["created_at"] = _now_iso()
    row["updated_at"] = _now_iso()
    items.append(row)
    save_esimaccess_orders_items(items)
    return row


def update_esimaccess_order_by_reference(reference: str, fields: dict[str, Any]) -> dict[str, Any] | None:
    target = str(reference or "").strip()
    if not target:
        return None
    items = load_esimaccess_orders_items()
    updated: dict[str, Any] | None = None
    for row in items:
        if str(row.get("order_reference") or "").strip() != target:
            continue
        for key, value in (fields or {}).items():
            row[key] = value
        row["updated_at"] = _now_iso()
        updated = row
        break
    if updated:
        save_esimaccess_orders_items(items)
    return updated


def list_esimaccess_orders_for_owner(owner_user_id: str) -> list[dict[str, Any]]:
    items = load_esimaccess_orders_items()
    out = [x for x in items if str(x.get("owner_user_id") or "").strip() == str(owner_user_id or "").strip()]
    out.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
    return out


def list_esimaccess_orders_for_agent(owner_user_id: str, agent_user_id: str) -> list[dict[str, Any]]:
    items = load_esimaccess_orders_items()
    out = [
        x
        for x in items
        if str(x.get("owner_user_id") or "").strip() == str(owner_user_id or "").strip()
        and str(x.get("agent_user_id") or "").strip() == str(agent_user_id or "").strip()
    ]
    out.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
    return out
