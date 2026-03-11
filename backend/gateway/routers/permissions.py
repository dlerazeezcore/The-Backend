from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from backend.gateway.admin_auth import require_super_admin_request
from backend.gateway.permissions_store import (
    _api_policy,
    _compute_schedule_windows,
    _load_permissions,
    _save_permissions,
    _service_enabled,
    _ticketing_schedule_allows,
)

router = APIRouter()


@router.get("/api/permissions")
async def get_permissions(request: Request):
    require_super_admin_request(request)
    return _load_permissions()


@router.post("/api/permissions")
async def set_permissions(request: Request, payload: dict):
    require_super_admin_request(request)
    try:
        # Keep it simple: accept full object and write it.
        cfg = _save_permissions(payload or {})
        # Permissions can alter eSIM provider visibility/routing; clear cached catalogs.
        from backend.gateway.routers.esim import clear_esim_runtime_caches

        clear_esim_runtime_caches()
        return cfg
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/permissions/status")
async def permissions_status(request: Request):
    require_super_admin_request(request)
    cfg = _load_permissions()
    services_cfg = cfg.get("services") or {}
    services_out = {}
    if isinstance(services_cfg, dict):
        for key in sorted(services_cfg.keys()):
            services_out[str(key)] = {"enabled": _service_enabled(str(key), cfg)}

    apis_out = {}
    apis_cfg = cfg.get("apis") or {}
    if isinstance(apis_cfg, dict):
        for api_id in sorted(apis_cfg.keys()):
            pol = _api_policy(str(api_id), cfg)
            apis_out[str(api_id)] = {
                "enabled": bool(pol.get("enabled")),
                "sellable_mode": str(pol.get("sellable_mode") or "online"),
                "schedule_ok": bool(pol.get("schedule_ok")),
                "is_online_now": bool(pol.get("is_online_now")),
                "schedule": _compute_schedule_windows(pol.get("schedule") or {}),
            }

    providers = cfg.get("providers") or {}
    out = {}
    for code, p in providers.items():
        if not isinstance(p, dict):
            continue
        availability = bool(p.get("availability_enabled", True))
        blocked_suppliers = p.get("blocked_suppliers") or []
        if str(code) in [str(x).strip() for x in blocked_suppliers]:
            availability = False

        ticketing_mode = (p.get("ticketing_mode") or "full").strip().lower()
        schedule_cfg = p.get("ticketing_schedule") or {}
        schedule_ok = _ticketing_schedule_allows(schedule_cfg)
        ticketing_effective = availability and (ticketing_mode == "full") and schedule_ok
        schedule_info = _compute_schedule_windows(schedule_cfg)
        out[str(code)] = {
            "availability": availability,
            "ticketing_mode": "full" if ticketing_mode == "full" else "availability_only",
            "ticketing_schedule_ok": schedule_ok,
            "ticketing_effective": ticketing_effective,
            "schedule": schedule_info,
        }
    return {"services": services_out, "apis": apis_out, "providers": out}
