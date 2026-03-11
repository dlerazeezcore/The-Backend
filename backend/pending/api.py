from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from backend.auth.api import read_request_payload, require_authenticated_user
from backend.auth.service import display_name, effective_owner_user_id, find_user, is_super_admin, load_users
from backend.pending.store import (
    find_pending_item,
    load_pending_items,
    save_pending_items,
    update_transaction_by_pending_id,
)
from backend.transactions.store import load_transactions_items


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _pending_enabled_for_user(user: dict[str, Any]) -> bool:
    if is_super_admin(user):
        return True
    service_access = user.get("service_access")
    if not isinstance(service_access, dict):
        return True
    return bool(service_access.get("pending", True))


def _filter_items_for_user(items: list[dict[str, Any]], user: dict[str, Any]) -> list[dict[str, Any]]:
    if is_super_admin(user):
        return items
    owner_id = effective_owner_user_id(user)
    return [row for row in items if str(row.get("company_admin_id") or "") == owner_id]


def _find_transaction_for_pending_id(pending_id: str) -> dict[str, Any] | None:
    target = str(pending_id or "").strip()
    for tx in load_transactions_items():
        if isinstance(tx, dict) and str(tx.get("pending_id") or "") == target:
            return tx
    return None


def _transactions_by_pending_id(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for tx in items:
        if not isinstance(tx, dict):
            continue
        pending_id = str(tx.get("pending_id") or "").strip()
        if not pending_id:
            continue
        out[pending_id] = tx
    return out


def _service_from_pending_tx(pending: dict[str, Any], tx: dict[str, Any] | None) -> str:
    service = str((tx or {}).get("service") or "").strip().lower()
    if service in {"esim", "e-sim", "e_sim", "sim"}:
        return "sim"
    if service:
        return service
    kind = str((pending or {}).get("kind") or "").strip().lower()
    if "ticket" in kind or "flight" in kind:
        return "flight"
    if "esim" in kind or "sim" in kind:
        return "sim"
    if "visa" in kind:
        return "visa"
    if "hotel" in kind:
        return "hotel"
    if "transport" in kind:
        return "transportation"
    details = (tx or {}).get("details")
    if isinstance(details, dict):
        if (
            str(details.get("iccid") or "").strip()
            or str(details.get("activation_code") or "").strip()
            or str(details.get("bundle_name") or "").strip()
        ):
            return "sim"
        if str(details.get("airline") or "").strip():
            return "flight"
        if str(details.get("from") or "").strip() and str(details.get("to") or "").strip():
            return "flight"
    return "other"


def _response_kind_for_pending(pending: dict[str, Any], tx: dict[str, Any] | None) -> str:
    raw_kind = str((pending or {}).get("kind") or "").strip().lower()
    service = _service_from_pending_tx(pending, tx)
    if not raw_kind:
        if service == "sim":
            return "esim"
        return service or "other"
    if service == "other":
        return raw_kind

    expected_prefix = "esim" if service == "sim" else service
    if raw_kind.startswith(expected_prefix):
        return raw_kind

    return f"{expected_prefix}_{raw_kind}"


def _provider_from_pending_tx(pending: dict[str, Any], tx: dict[str, Any] | None) -> str:
    details = (tx or {}).get("details")
    details = details if isinstance(details, dict) else {}
    return str(
        (tx or {}).get("provider_id")
        or details.get("provider_id")
        or (tx or {}).get("airline")
        or details.get("airline")
        or (pending or {}).get("provider_id")
        or ""
    ).strip()


def _passenger_from_tx(tx: dict[str, Any] | None) -> str:
    details = (tx or {}).get("details")
    details = details if isinstance(details, dict) else {}
    first = str((tx or {}).get("first_name") or details.get("first_name") or "").strip()
    last = str((tx or {}).get("last_name") or details.get("last_name") or "").strip()
    return f"{first} {last}".strip()


def _route_from_tx(tx: dict[str, Any] | None) -> str:
    details = (tx or {}).get("details")
    details = details if isinstance(details, dict) else {}
    src = str((tx or {}).get("from") or details.get("from") or "").strip()
    dst = str((tx or {}).get("to") or details.get("to") or "").strip()
    if src and dst:
        return f"{src} → {dst}"
    return ""


def _company_name_for_pending(
    pending: dict[str, Any],
    tx: dict[str, Any] | None,
    users: list[dict[str, Any]],
    current_user: dict[str, Any],
) -> str:
    owner_id = str((pending or {}).get("company_admin_id") or "").strip()
    owner = find_user(users, owner_id) if owner_id else None
    if isinstance(owner, dict):
        name = str(owner.get("company_name") or owner.get("company") or "").strip()
        if name:
            return name
    details = (tx or {}).get("details")
    details = details if isinstance(details, dict) else {}
    from_tx = str((tx or {}).get("company_name") or details.get("company_name") or "").strip()
    if from_tx:
        return from_tx
    if owner_id and owner_id == effective_owner_user_id(current_user):
        current_name = str(current_user.get("company_name") or current_user.get("company") or "").strip()
        if current_name:
            return current_name
    return owner_id


def _requested_by_name_for_pending(
    pending: dict[str, Any],
    tx: dict[str, Any] | None,
    users: list[dict[str, Any]],
) -> str:
    requested_by_id = str((pending or {}).get("requested_by_user_id") or "").strip()
    requested_by = find_user(users, requested_by_id) if requested_by_id else None
    if isinstance(requested_by, dict):
        return display_name(requested_by)
    by_name = str((tx or {}).get("by") or "").strip()
    if by_name:
        return by_name
    return requested_by_id


def _ensure_visible_item(item: dict[str, Any], user: dict[str, Any]) -> None:
    if is_super_admin(user):
        return
    owner_id = effective_owner_user_id(user)
    if str(item.get("company_admin_id") or "") != owner_id:
        raise HTTPException(status_code=403, detail="Not authorized")


def create_router() -> APIRouter:
    router = APIRouter(tags=["pending"])

    @router.get("/api/pending")
    async def list_pending(request: Request) -> dict[str, Any]:
        user = require_authenticated_user(request)
        if not _pending_enabled_for_user(user):
            raise HTTPException(status_code=403, detail="Pending service is disabled for this account.")
        items = load_pending_items()
        visible = _filter_items_for_user(items, user)
        return {"status": "ok", "count": len(visible), "pending": visible}

    @router.get("/api/pending/enriched")
    async def list_pending_enriched(request: Request) -> dict[str, Any]:
        user = require_authenticated_user(request)
        if not _pending_enabled_for_user(user):
            raise HTTPException(status_code=403, detail="Pending service is disabled for this account.")
        pending_items = _filter_items_for_user(load_pending_items(), user)
        tx_items = _filter_items_for_user(load_transactions_items(), user)
        tx_by_pending = _transactions_by_pending_id(tx_items)
        users = load_users()

        out: list[dict[str, Any]] = []
        for pending in pending_items:
            pending_id = str((pending or {}).get("id") or "").strip()
            tx = tx_by_pending.get(pending_id)
            details = (tx or {}).get("details")
            details = details if isinstance(details, dict) else {}
            pending_response = dict(pending or {})
            pending_response["kind"] = _response_kind_for_pending(pending_response, tx)
            raw_kind = str((pending or {}).get("kind") or "").strip()
            if raw_kind:
                pending_response["raw_kind"] = raw_kind
            out.append(
                {
                    "pending": pending_response,
                    "transaction": tx,
                    "display": {
                        "service": _service_from_pending_tx(pending, tx),
                        "company_name": _company_name_for_pending(pending, tx, users, user),
                        "requested_by_name": _requested_by_name_for_pending(pending, tx, users),
                        "provider": _provider_from_pending_tx(pending, tx),
                        "passenger": _passenger_from_tx(tx),
                        "route": _route_from_tx(tx),
                        "created_at": str((pending or {}).get("created_at") or (tx or {}).get("ts") or "").strip(),
                        "status": str((tx or {}).get("status") or "pending").strip(),
                        "transaction_id": str((tx or {}).get("id") or "").strip(),
                        "price": (tx or {}).get("price"),
                        "currency": str((tx or {}).get("currency") or details.get("currency") or "").strip(),
                    },
                }
            )
        return {"status": "ok", "count": len(out), "items": out}

    @router.post("/api/pending/{pending_id}/complete")
    async def complete_pending(request: Request, pending_id: str) -> dict[str, Any]:
        user = require_authenticated_user(request)
        if not _pending_enabled_for_user(user):
            raise HTTPException(status_code=403, detail="Pending service is disabled for this account.")

        payload = await read_request_payload(request)
        ticket_number = str(payload.get("ticket_number") or "").strip()
        booking_code = str(payload.get("booking_code") or "").strip()
        activation_code = str(payload.get("activation_code") or "").strip()
        iccid = str(payload.get("iccid") or "").strip()
        order_reference = str(payload.get("order_reference") or "").strip()

        items = load_pending_items()
        found_idx, item = find_pending_item(items, pending_id)
        if found_idx < 0 or not isinstance(item, dict):
            raise HTTPException(status_code=404, detail="Pending item not found")
        _ensure_visible_item(item, user)

        kind = str(item.get("kind") or "").strip().lower()
        if kind.startswith("esim") and not activation_code:
            raise HTTPException(status_code=400, detail="activation_code is required for eSIM pending completion.")

        tx = _find_transaction_for_pending_id(pending_id)
        details = tx.get("details") if isinstance(tx, dict) and isinstance(tx.get("details"), dict) else {}
        merged_details = dict(details or {})

        if ticket_number:
            merged_details["pnr"] = ticket_number
            merged_details["ticket_number"] = ticket_number
        if booking_code:
            merged_details["booking_code"] = booking_code
        if order_reference:
            merged_details["order_reference"] = order_reference
        if activation_code:
            merged_details["activation_code"] = activation_code
            merged_details["activation_codes"] = [activation_code]
        if iccid:
            merged_details["iccid"] = iccid
            merged_details["iccids"] = [iccid]

        if kind.startswith("esim"):
            final_ref = (
                order_reference
                or str(merged_details.get("order_reference") or "").strip()
                or str((tx or {}).get("booking_code") or "").strip()
                or str(pending_id or "").strip()
            )
            update_transaction_by_pending_id(
                pending_id,
                {
                    "status": "successful",
                    "booking_code": final_ref,
                    "pnr": "",
                    "details": merged_details,
                },
            )
        else:
            update_transaction_by_pending_id(
                pending_id,
                {
                    "status": "successful",
                    "pnr": ticket_number,
                    "booking_code": booking_code,
                    "details": merged_details,
                },
            )

        try:
            items.pop(found_idx)
        except Exception:
            pass
        save_pending_items(items)
        return {"status": "ok", "message": "Pending request completed", "pending_id": str(pending_id)}

    @router.post("/api/pending/{pending_id}/reject")
    async def reject_pending(request: Request, pending_id: str) -> dict[str, Any]:
        user = require_authenticated_user(request)
        if not _pending_enabled_for_user(user):
            raise HTTPException(status_code=403, detail="Pending service is disabled for this account.")

        payload = await read_request_payload(request)
        rejection_note = str(payload.get("note") or payload.get("rejection_note") or "").strip()
        if not rejection_note:
            raise HTTPException(status_code=400, detail="rejection note is required")

        items = load_pending_items()
        found_idx, item = find_pending_item(items, pending_id)
        if found_idx < 0 or not isinstance(item, dict):
            raise HTTPException(status_code=404, detail="Pending item not found")
        _ensure_visible_item(item, user)

        update_transaction_by_pending_id(
            pending_id,
            {
                "status": "rejected",
                "note": rejection_note,
                "rejection_note": rejection_note,
                "rejected_at": _now_iso(),
                "rejected_by_user_id": str(user.get("id") or ""),
                "rejected_by_name": str(user.get("username") or user.get("email") or ""),
            },
        )

        try:
            items.pop(found_idx)
        except Exception:
            pass
        save_pending_items(items)
        return {"status": "ok", "message": "Pending request rejected", "pending_id": str(pending_id)}

    return router
