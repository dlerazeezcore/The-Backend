from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from typing import Any

from backend.core.paths import DATA_DIR
from backend.supabase.pending import load_pending_doc, save_pending_doc
from backend.transactions.store import load_transactions_items, save_transactions_items

PENDING_PATH = DATA_DIR / "pending.json"


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


def load_pending_items() -> list[dict[str, Any]]:
    payload = load_pending_doc(local_loader=lambda: _load_local_doc(PENDING_PATH, "pending"))
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        items = payload.get("pending") or payload.get("items") or []
        if isinstance(items, list):
            return [row for row in items if isinstance(row, dict)]
    return []


def save_pending_items(items: list[dict[str, Any]]) -> None:
    out = {"pending": items if isinstance(items, list) else []}
    save_pending_doc(
        value=out,
        local_saver=lambda payload: _save_local_doc(PENDING_PATH, "pending", payload if isinstance(payload, dict) else out),
    )


def find_pending_item(items: list[dict[str, Any]], pending_id: str) -> tuple[int, dict[str, Any] | None]:
    target = str(pending_id or "").strip()
    for idx, row in enumerate(items):
        if isinstance(row, dict) and str(row.get("id") or "") == target:
            return idx, row
    return -1, None


def update_transaction_by_pending_id(pending_id: str, updates: dict[str, Any]) -> bool:
    target = str(pending_id or "").strip()
    if not target:
        return False
    txs = load_transactions_items()
    changed = False
    for tx in txs:
        if not isinstance(tx, dict):
            continue
        if str(tx.get("pending_id") or "") != target:
            continue
        for key, value in (updates or {}).items():
            tx[key] = value
        details = tx.get("details")
        if isinstance(details, dict):
            for key, value in (updates or {}).items():
                if key == "details":
                    continue
                details[key] = value
        tx["updated_at"] = _now_iso()
        changed = True
        break
    if changed:
        save_transactions_items(txs)
    return changed
