from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from backend.gateway.admin_auth import require_super_admin_request
from backend.gateway.permissions_store import _api_policy
from backend.payments.fib.service import (
    cancel_payment as fib_cancel_payment,
    check_payment_status as fib_check_payment_status,
    create_payment as fib_create_payment,
    load_config as load_fib_config,
    refund_payment as fib_refund_payment,
    save_config as save_fib_config,
)

router = APIRouter()


def _ensure_fib_online() -> None:
    pol = _api_policy("fib")
    if not bool(pol.get("enabled")):
        raise HTTPException(status_code=503, detail="FIB API is disabled by admin permissions.")
    if not bool(pol.get("is_online_now")):
        raise HTTPException(status_code=503, detail="FIB API is currently offline (manual/scheduled mode).")


def _ensure_fib_enabled() -> None:
    pol = _api_policy("fib")
    if not bool(pol.get("enabled")):
        raise HTTPException(status_code=503, detail="FIB API is disabled by admin permissions.")


@router.get("/api/other-apis/fib")
async def fib_config_get(request: Request):
    require_super_admin_request(request)
    return load_fib_config()


@router.post("/api/other-apis/fib")
async def fib_config_set(request: Request, payload: dict):
    require_super_admin_request(request)
    try:
        cfg = save_fib_config(payload or {})
        return cfg
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/other-apis/fib/create-payment")
async def fib_create_payment_endpoint(request: Request, payload: dict):
    require_super_admin_request(request)
    try:
        _ensure_fib_online()
        amount = int(payload.get("amount") or 0)
        if amount <= 0:
            raise ValueError("Amount must be greater than 0.")
        description = payload.get("description") or "Payment"
        data = fib_create_payment(amount, description)
        return data
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/other-apis/fib/payments/{payment_id}/status")
async def fib_payment_status_endpoint(request: Request, payment_id: str):
    require_super_admin_request(request)
    try:
        _ensure_fib_enabled()
        return fib_check_payment_status(payment_id)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/other-apis/fib/payments/{payment_id}/cancel")
async def fib_payment_cancel_endpoint(request: Request, payment_id: str):
    require_super_admin_request(request)
    try:
        _ensure_fib_enabled()
        return fib_cancel_payment(payment_id)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/other-apis/fib/payments/{payment_id}/refund")
async def fib_payment_refund_endpoint(request: Request, payment_id: str):
    require_super_admin_request(request)
    try:
        _ensure_fib_enabled()
        return fib_refund_payment(payment_id)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/fib/webhook")
async def fib_webhook_endpoint(request: Request):
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    body = payload if isinstance(payload, dict) else {}
    payment_id = str(body.get("id") or body.get("paymentId") or "").strip()
    payment_status = str(body.get("status") or "").strip()
    return JSONResponse(
        status_code=202,
        content={
            "status": "accepted",
            "payment_id": payment_id,
            "payment_status": payment_status,
        },
    )


@router.api_route("/fib/return", methods=["GET", "POST"])
async def fib_return_endpoint(request: Request):
    return {
        "status": "ok",
        "message": "FIB return received.",
        "query": dict(request.query_params),
    }
