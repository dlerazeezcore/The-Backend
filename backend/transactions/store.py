from __future__ import annotations

import json
import os
import tempfile
from typing import Any

from backend.core.paths import DATA_DIR
from backend.supabase.transactions import load_transactions_doc, save_transactions_doc

TRANSACTIONS_PATH = DATA_DIR / "transactions.json"


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


def load_transactions_items() -> list[dict[str, Any]]:
    payload = load_transactions_doc(local_loader=lambda: _load_local_doc(TRANSACTIONS_PATH, "transactions"))
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        items = payload.get("transactions") or payload.get("items") or []
        if isinstance(items, list):
            return [row for row in items if isinstance(row, dict)]
    return []


def save_transactions_items(items: list[dict[str, Any]]) -> None:
    out = {"transactions": items if isinstance(items, list) else []}
    save_transactions_doc(
        value=out,
        local_saver=lambda payload: _save_local_doc(
            TRANSACTIONS_PATH,
            "transactions",
            payload if isinstance(payload, dict) else out,
        ),
    )


def find_transaction_item(items: list[dict[str, Any]], transaction_id: str) -> dict[str, Any] | None:
    target = str(transaction_id or "").strip()
    for row in items:
        if isinstance(row, dict) and str(row.get("id") or "") == target:
            return row
    return None


def find_transaction_by_pending_id(items: list[dict[str, Any]], pending_id: str) -> dict[str, Any] | None:
    target = str(pending_id or "").strip()
    for row in items:
        if isinstance(row, dict) and str(row.get("pending_id") or "") == target:
            return row
    return None
