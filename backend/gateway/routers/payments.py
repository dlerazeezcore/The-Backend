from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from backend.gateway.admin_auth import require_super_admin_request
from backend.gateway.permissions_store import _api_policy
from backend.payments.fib.service import (
    create_payment as fib_create_payment,
    load_config as load_fib_config,
    save_config as save_fib_config,
)

router = APIRouter()


def _ensure_fib_online() -> None:
    pol = _api_policy("fib")
    if not bool(pol.get("enabled")):
        raise HTTPException(status_code=503, detail="FIB API is disabled by admin permissions.")
    if not bool(pol.get("is_online_now")):
        raise HTTPException(status_code=503, detail="FIB API is currently offline (manual/scheduled mode).")


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
