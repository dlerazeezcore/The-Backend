from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from backend.auth.api import require_authenticated_user
from backend.auth.service import effective_owner_user_id, find_user, is_super_admin, load_users
from backend.pending.store import load_pending_items
from backend.transactions.store import (
    find_transaction_by_pending_id,
    find_transaction_item,
    load_transactions_items,
)


def _transactions_enabled_for_user(user: dict[str, Any]) -> bool:
    if is_super_admin(user):
        return True
    service_access = user.get("service_access")
    if not isinstance(service_access, dict):
        return True
    return bool(service_access.get("transactions", True))


def _filter_items_for_user(items: list[dict[str, Any]], user: dict[str, Any]) -> list[dict[str, Any]]:
    if is_super_admin(user):
        return items
    owner_id = effective_owner_user_id(user)
    return [row for row in items if str(row.get("company_admin_id") or "") == owner_id]


def _pending_kind_by_id() -> dict[str, str]:
    out: dict[str, str] = {}
    for row in load_pending_items():
        if not isinstance(row, dict):
            continue
        pending_id = str(row.get("id") or "").strip()
        kind = str(row.get("kind") or "").strip().lower()
        if pending_id and kind:
            out[pending_id] = kind
    return out


def _service_from_transaction(item: dict[str, Any], pending_kind: str = "") -> str:
    pending_kind = str(pending_kind or "").strip().lower()
    service = str(item.get("service") or "").strip().lower()
    if service in {"esim", "e-sim", "e_sim", "sim"}:
        return "sim"
    if service:
        return service

    if "esim" in pending_kind or "sim" in pending_kind:
        return "sim"
    if "ticket" in pending_kind or "flight" in pending_kind:
        return "flight"

    details = item.get("details") if isinstance(item.get("details"), dict) else {}
    details_blob = str(details).lower()
    if (
        "esim" in details_blob
        or str(details.get("iccid") or "").strip()
        or str(details.get("activation_code") or "").strip()
        or str(details.get("bundle_name") or "").strip()
    ):
        return "sim"
    if str(details.get("airline") or item.get("airline") or "").strip():
        return "flight"
    src = str(item.get("from") or details.get("from") or "").strip()
    dst = str(item.get("to") or details.get("to") or "").strip()
    if src and dst:
        return "flight"
    if str(item.get("pnr") or details.get("pnr") or details.get("ticket_number") or "").strip():
        return "flight"
    return "other"


def _provider_from_transaction(item: dict[str, Any]) -> str:
    details = item.get("details") if isinstance(item.get("details"), dict) else {}
    return str(
        item.get("provider_id")
        or details.get("provider_id")
        or item.get("airline")
        or details.get("airline")
        or ""
    ).strip()


def _company_name_from_transaction(item: dict[str, Any], users: list[dict[str, Any]], current_user: dict[str, Any]) -> str:
    company_admin_id = str(item.get("company_admin_id") or "").strip()
    owner = find_user(users, company_admin_id) if company_admin_id else None
    if isinstance(owner, dict):
        company_name = str(owner.get("company_name") or owner.get("company") or "").strip()
        if company_name:
            return company_name
    details = item.get("details") if isinstance(item.get("details"), dict) else {}
    from_details = str(item.get("company_name") or details.get("company_name") or "").strip()
    if from_details:
        return from_details
    if company_admin_id and company_admin_id == effective_owner_user_id(current_user):
        current_name = str(current_user.get("company_name") or current_user.get("company") or "").strip()
        if current_name:
            return current_name
    return company_admin_id


def _passenger_name(item: dict[str, Any]) -> str:
    details = item.get("details") if isinstance(item.get("details"), dict) else {}
    first = str(item.get("first_name") or details.get("first_name") or "").strip()
    last = str(item.get("last_name") or details.get("last_name") or "").strip()
    return f"{first} {last}".strip()


def _route_text(item: dict[str, Any]) -> str:
    details = item.get("details") if isinstance(item.get("details"), dict) else {}
    src = str(item.get("from") or details.get("from") or "").strip()
    dst = str(item.get("to") or details.get("to") or "").strip()
    if src and dst:
        return f"{src} → {dst}"
    return ""


def _pnr_value(item: dict[str, Any]) -> str:
    details = item.get("details") if isinstance(item.get("details"), dict) else {}
    return str(item.get("pnr") or details.get("pnr") or details.get("ticket_number") or "").strip()


def _booking_code_value(item: dict[str, Any]) -> str:
    details = item.get("details") if isinstance(item.get("details"), dict) else {}
    return str(item.get("booking_code") or details.get("booking_code") or details.get("order_reference") or "").strip()


def _decorate_transaction(
    item: dict[str, Any],
    users: list[dict[str, Any]],
    current_user: dict[str, Any],
    pending_kind_map: dict[str, str] | None = None,
) -> dict[str, Any]:
    tx = dict(item or {})
    details = tx.get("details")
    details = details if isinstance(details, dict) else {}

    pending_id = str(tx.get("pending_id") or "").strip()
    pending_kind = str((pending_kind_map or {}).get(pending_id) or "").strip().lower()
    service = _service_from_transaction(tx, pending_kind=pending_kind)
    provider = _provider_from_transaction(tx)
    company_name = _company_name_from_transaction(tx, users, current_user)
    passenger = _passenger_name(tx)
    route = _route_text(tx)
    pnr = _pnr_value(tx)
    booking_code = _booking_code_value(tx)
    currency = str(tx.get("currency") or details.get("currency") or "").strip()
    created_at = str(tx.get("ts") or tx.get("created_at") or "").strip()

    if not str(tx.get("service") or "").strip():
        tx["service"] = service
    if not str(tx.get("provider_id") or "").strip():
        tx["provider_id"] = provider
    if not str(tx.get("company_name") or "").strip():
        tx["company_name"] = company_name
    if not str(tx.get("first_name") or "").strip() and str(details.get("first_name") or "").strip():
        tx["first_name"] = str(details.get("first_name") or "").strip()
    if not str(tx.get("last_name") or "").strip() and str(details.get("last_name") or "").strip():
        tx["last_name"] = str(details.get("last_name") or "").strip()
    if not str(tx.get("from") or "").strip() and str(details.get("from") or "").strip():
        tx["from"] = str(details.get("from") or "").strip()
    if not str(tx.get("to") or "").strip() and str(details.get("to") or "").strip():
        tx["to"] = str(details.get("to") or "").strip()
    if not str(tx.get("pnr") or "").strip() and pnr:
        tx["pnr"] = pnr
    if not str(tx.get("booking_code") or "").strip() and booking_code:
        tx["booking_code"] = booking_code
    if not str(tx.get("currency") or "").strip() and currency:
        tx["currency"] = currency

    # Flat aliases for less strict frontend bindings.
    tx["company"] = company_name
    tx["passenger"] = passenger
    tx["route"] = route
    tx["provider"] = provider
    tx["created"] = created_at
    tx["service_type"] = service

    tx["display"] = {
        "service": service,
        "company_name": company_name,
        "passenger": passenger,
        "route": route,
        "provider": provider,
        "pnr": pnr,
        "booking_code": booking_code,
        "price": tx.get("price"),
        "currency": currency,
        "created_at": created_at,
        "status": str(tx.get("status") or "").strip(),
    }
    return tx


def _matches_text(item: dict[str, Any], query: str) -> bool:
    q = str(query or "").strip().lower()
    if not q:
        return True
    details = item.get("details")
    blob = " ".join(
        [
            str(item.get("id") or ""),
            str(item.get("pending_id") or ""),
            str(item.get("provider_id") or ""),
            str(_service_from_transaction(item) or item.get("service") or ""),
            str(item.get("status") or ""),
            str(item.get("company_name") or ""),
            str(item.get("first_name") or ""),
            str(item.get("last_name") or ""),
            str(item.get("booking_code") or ""),
            str(item.get("pnr") or ""),
            str(details if isinstance(details, dict) else ""),
        ]
    ).lower()
    return q in blob


def _sort_key(item: dict[str, Any]) -> str:
    return str(item.get("updated_at") or item.get("ts") or item.get("created_at") or "")


def create_router() -> APIRouter:
    router = APIRouter(tags=["transactions"])

    @router.get("/api/transactions")
    async def list_transactions(
        request: Request,
        q: str = "",
        status: str = "",
        service: str = "",
        pending_id: str = "",
        limit: int = Query(default=100, ge=1, le=1000),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        user = require_authenticated_user(request)
        if not _transactions_enabled_for_user(user):
            raise HTTPException(status_code=403, detail="Transactions service is disabled for this account.")

        items = _filter_items_for_user(load_transactions_items(), user)
        users = load_users()
        pending_kind_map = _pending_kind_by_id()
        status_q = str(status or "").strip().lower()
        service_q = str(service or "").strip().lower()
        pending_q = str(pending_id or "").strip()
        if status_q:
            items = [row for row in items if str(row.get("status") or "").strip().lower() == status_q]
        if service_q:
            items = [row for row in items if _service_from_transaction(
                row,
                pending_kind=str((pending_kind_map or {}).get(str(row.get("pending_id") or "").strip()) or ""),
            ) == service_q]
        if pending_q:
            items = [row for row in items if str(row.get("pending_id") or "") == pending_q]
        if str(q or "").strip():
            items = [row for row in items if _matches_text(row, q)]

        items = sorted(items, key=_sort_key, reverse=True)
        items = [_decorate_transaction(row, users, user, pending_kind_map=pending_kind_map) for row in items]
        total = len(items)
        results = items[offset : offset + limit]
        return {
            "status": "ok",
            "count": len(results),
            "total": total,
            "offset": offset,
            "limit": limit,
            "results": results,
        }

    @router.get("/api/transactions/{transaction_id}")
    async def get_transaction(request: Request, transaction_id: str) -> dict[str, Any]:
        user = require_authenticated_user(request)
        if not _transactions_enabled_for_user(user):
            raise HTTPException(status_code=403, detail="Transactions service is disabled for this account.")

        users = load_users()
        pending_kind_map = _pending_kind_by_id()
        items = _filter_items_for_user(load_transactions_items(), user)
        tx = find_transaction_item(items, transaction_id)
        if not tx:
            raise HTTPException(status_code=404, detail="Transaction not found")
        decorated = _decorate_transaction(tx, users, user, pending_kind_map=pending_kind_map)
        return {
            "status": "ok",
            "transaction": decorated,
            "service": str(decorated.get("display", {}).get("service") or ""),
            "company": str(decorated.get("display", {}).get("company_name") or ""),
            "passenger": str(decorated.get("display", {}).get("passenger") or ""),
            "route": str(decorated.get("display", {}).get("route") or ""),
            "provider": str(decorated.get("display", {}).get("provider") or ""),
            "pnr": str(decorated.get("display", {}).get("pnr") or ""),
            "booking_code": str(decorated.get("display", {}).get("booking_code") or ""),
            "price": decorated.get("display", {}).get("price"),
            "created": str(decorated.get("display", {}).get("created_at") or ""),
        }

    @router.get("/api/transactions/by-pending/{pending_id}")
    async def get_transaction_by_pending(request: Request, pending_id: str) -> dict[str, Any]:
        user = require_authenticated_user(request)
        if not _transactions_enabled_for_user(user):
            raise HTTPException(status_code=403, detail="Transactions service is disabled for this account.")

        users = load_users()
        pending_kind_map = _pending_kind_by_id()
        items = _filter_items_for_user(load_transactions_items(), user)
        tx = find_transaction_by_pending_id(items, pending_id)
        if not tx:
            raise HTTPException(status_code=404, detail="Transaction not found")
        decorated = _decorate_transaction(tx, users, user, pending_kind_map=pending_kind_map)
        return {
            "status": "ok",
            "transaction": decorated,
            "service": str(decorated.get("display", {}).get("service") or ""),
            "company": str(decorated.get("display", {}).get("company_name") or ""),
            "passenger": str(decorated.get("display", {}).get("passenger") or ""),
            "route": str(decorated.get("display", {}).get("route") or ""),
            "provider": str(decorated.get("display", {}).get("provider") or ""),
            "pnr": str(decorated.get("display", {}).get("pnr") or ""),
            "booking_code": str(decorated.get("display", {}).get("booking_code") or ""),
            "price": decorated.get("display", {}).get("price"),
            "created": str(decorated.get("display", {}).get("created_at") or ""),
        }

    return router
