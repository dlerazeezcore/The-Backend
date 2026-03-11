from __future__ import annotations

import json
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from backend.supabase import load_or_seed as sb_load_or_seed
from backend.supabase import save as sb_save


PERMISSIONS_PATH = Path(__file__).with_name("permissions.json")


def _default_schedule() -> dict:
    return {
        "enabled": False,
        "timezone": "Asia/Baghdad",
        "rules": [{"days": [0, 1, 2, 3, 4, 5, 6], "start": "00:00", "end": "23:59"}],
    }


DEFAULT_PERMISSIONS = {
    "services": {
        "flights": True,
        "hotels": True,
        "esim": True,
        "airport_transportation": True,
        "car_rental": True,
        "cip_services": True,
        "passenger_database": True,
        # Backward compatibility for old single transportation toggle.
        "transportation": True,
        "visa": True,
        "pending": True,
        # Transaction pages are always available by business requirement.
        "transactions": True,
    },
    "apis": {
        "ota": {
            "enabled": True,
            "sellable_mode": "online",  # online | manual
            "schedule": _default_schedule(),
            "notify_whatsapp_enabled": False,
            "notify_whatsapp_numbers": [],
        },
        "esim_oasis": {
            "enabled": True,
            "sellable_mode": "online",  # online | manual
            "schedule": _default_schedule(),
            "notify_whatsapp_enabled": False,
            "notify_whatsapp_numbers": [],
        },
        "esim_access": {
            "enabled": True,
            "sellable_mode": "online",  # online | manual
            "schedule": _default_schedule(),
            "notify_whatsapp_enabled": False,
            "notify_whatsapp_numbers": [],
        },
        "fib": {
            "enabled": True,
            "sellable_mode": "online",  # online | manual
            "schedule": _default_schedule(),
        },
        "email": {
            "enabled": True,
            "sellable_mode": "online",
            "schedule": _default_schedule(),
        },
    },
    "providers": {
        "OTA": {
            "availability_enabled": True,
            "seats_estimation_enabled": True,
            "ticketing_mode": "full",  # full | availability_only
            "filters_enabled": True,
            "blocked_airlines": [],
            "blocked_suppliers": [],
            "allowed_suppliers": [],
            "allowed_airlines": [],
            "ticketing_schedule": _default_schedule(),
        }
    },
}


def _clone(data: dict) -> dict:
    try:
        return json.loads(json.dumps(data))
    except Exception:
        return {}


def _parse_hhmm(v: str) -> time | None:
    try:
        v = (v or "").strip()
        if not v:
            return None
        hh, mm = v.split(":", 1)
        hh_n = int(hh)
        mm_n = int(mm)
        if not (0 <= hh_n <= 23 and 0 <= mm_n <= 59):
            return None
        return time(hh_n, mm_n)
    except Exception:
        return None


def _normalize_schedule(schedule: dict | None) -> dict:
    out = _clone(_default_schedule())
    if not isinstance(schedule, dict):
        return out
    out["enabled"] = bool(schedule.get("enabled"))
    tzname = str(schedule.get("timezone") or "Asia/Baghdad").strip() or "Asia/Baghdad"
    out["timezone"] = tzname

    rules = schedule.get("rules")
    if not isinstance(rules, list):
        rules = []
    cleaned = []
    for r in rules:
        if not isinstance(r, dict):
            continue
        days = r.get("days") or []
        if isinstance(days, str):
            try:
                days = json.loads(days)
            except Exception:
                days = []
        if not isinstance(days, list):
            days = []
        day_vals = []
        for x in days:
            try:
                v = int(x)
            except Exception:
                continue
            if 0 <= v <= 6 and v not in day_vals:
                day_vals.append(v)
        st = str(r.get("start") or "").strip()
        en = str(r.get("end") or "").strip()
        if _parse_hhmm(st) is None or _parse_hhmm(en) is None:
            continue
        if not day_vals:
            continue
        cleaned.append({"days": day_vals, "start": st, "end": en})
    out["rules"] = cleaned or _clone(_default_schedule())["rules"]
    return out


def _normalize_permissions(raw: dict | None) -> dict:
    cfg = raw if isinstance(raw, dict) else {}

    services = cfg.get("services")
    if not isinstance(services, dict):
        services = {}
    norm_services = {}
    transport_seed = bool(services.get("transportation", True))
    for key, default_value in (DEFAULT_PERMISSIONS.get("services") or {}).items():
        if key == "transactions":
            norm_services[key] = True
        elif key in {"airport_transportation", "car_rental", "cip_services"}:
            if key in services:
                norm_services[key] = bool(services.get(key))
            else:
                norm_services[key] = transport_seed
        else:
            norm_services[key] = bool(services.get(key, default_value))
    norm_services["transportation"] = bool(
        norm_services.get("airport_transportation", True)
        or norm_services.get("car_rental", True)
        or norm_services.get("cip_services", True)
    )
    cfg["services"] = norm_services

    apis = cfg.get("apis")
    if not isinstance(apis, dict):
        apis = {}
    norm_apis: dict[str, dict] = {}
    for api_id, defaults in (DEFAULT_PERMISSIONS.get("apis") or {}).items():
        row = apis.get(api_id)
        if not isinstance(row, dict):
            row = {}
        mode = str(row.get("sellable_mode") or defaults.get("sellable_mode") or "online").strip().lower()
        if mode not in ("online", "manual"):
            mode = "online"
        norm_apis[api_id] = {
            "enabled": bool(row.get("enabled", defaults.get("enabled", True))),
            "sellable_mode": mode,
            "schedule": _normalize_schedule(row.get("schedule")),
            "notify_whatsapp_enabled": bool(row.get("notify_whatsapp_enabled", defaults.get("notify_whatsapp_enabled", False))),
            "notify_whatsapp_numbers": [
                str(x).strip()
                for x in (
                    row.get("notify_whatsapp_numbers")
                    if isinstance(row.get("notify_whatsapp_numbers"), list)
                    else []
                )
                if str(x).strip()
            ],
        }
    cfg["apis"] = norm_apis

    providers = cfg.get("providers")
    if not isinstance(providers, dict):
        providers = {}

    ota_provider = providers.get("OTA")
    ota_defaults = _clone((DEFAULT_PERMISSIONS.get("providers") or {}).get("OTA") or {})
    ota_api = cfg["apis"].get("ota") or {}

    if not isinstance(ota_provider, dict):
        ota_provider = _clone(ota_defaults)
    else:
        # Keep existing provider settings but apply API-level controls when explicitly present.
        if "enabled" in ota_api:
            ota_provider["availability_enabled"] = bool(ota_api.get("enabled"))
        if "sellable_mode" in ota_api:
            ota_provider["ticketing_mode"] = "full" if str(ota_api.get("sellable_mode")) == "online" else "availability_only"
        if "schedule" in ota_api:
            ota_provider["ticketing_schedule"] = ota_api.get("schedule")

    ota_provider["availability_enabled"] = bool(ota_provider.get("availability_enabled", ota_defaults.get("availability_enabled", True)))
    ota_provider["seats_estimation_enabled"] = bool(
        ota_provider.get("seats_estimation_enabled", ota_defaults.get("seats_estimation_enabled", True))
    )
    ota_provider["ticketing_mode"] = (
        "full" if str(ota_provider.get("ticketing_mode") or "full").strip().lower() == "full" else "availability_only"
    )
    ota_provider["filters_enabled"] = bool(ota_provider.get("filters_enabled", ota_defaults.get("filters_enabled", True)))
    ota_provider["blocked_airlines"] = [
        str(x).strip().upper()
        for x in (ota_provider.get("blocked_airlines") or [])
        if str(x).strip()
    ]
    ota_provider["blocked_suppliers"] = [
        str(x).strip()
        for x in (ota_provider.get("blocked_suppliers") or [])
        if str(x).strip()
    ]
    ota_provider["allowed_suppliers"] = [
        str(x).strip()
        for x in (ota_provider.get("allowed_suppliers") or [])
        if str(x).strip()
    ]
    ota_provider["allowed_airlines"] = [
        str(x).strip().upper()
        for x in (ota_provider.get("allowed_airlines") or [])
        if str(x).strip()
    ]
    ota_provider["ticketing_schedule"] = _normalize_schedule(ota_provider.get("ticketing_schedule"))

    providers["OTA"] = ota_provider
    cfg["providers"] = providers

    # Sync API -> provider derived view for OTA (single source of truth for flight logic).
    cfg["apis"]["ota"] = {
        "enabled": bool(ota_provider.get("availability_enabled", True)),
        "sellable_mode": "online" if ota_provider.get("ticketing_mode") == "full" else "manual",
        "schedule": _normalize_schedule(ota_provider.get("ticketing_schedule")),
        "notify_whatsapp_enabled": bool(ota_api.get("notify_whatsapp_enabled", False)),
        "notify_whatsapp_numbers": [
            str(x).strip()
            for x in (
                ota_api.get("notify_whatsapp_numbers")
                if isinstance(ota_api.get("notify_whatsapp_numbers"), list)
                else []
            )
            if str(x).strip()
        ],
    }

    return cfg


def _load_permissions() -> dict:
    def _load_local() -> dict:
        try:
            if PERMISSIONS_PATH.exists():
                data = json.loads(PERMISSIONS_PATH.read_text(encoding="utf-8") or "{}")
                return data if isinstance(data, dict) else {}
        except Exception:
            pass
        return _clone(DEFAULT_PERMISSIONS)

    data = sb_load_or_seed(doc_key="permissions", default=_clone(DEFAULT_PERMISSIONS), local_loader=_load_local)
    return _normalize_permissions(data if isinstance(data, dict) else _clone(DEFAULT_PERMISSIONS))


def _save_permissions(cfg: dict) -> dict:
    normalized = _normalize_permissions(cfg)
    def _save_local(value: dict) -> None:
        PERMISSIONS_PATH.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")

    sb_save(doc_key="permissions", value=normalized, local_saver=_save_local)
    return normalized


def _ticketing_schedule_allows(schedule: dict) -> bool:
    try:
        if not isinstance(schedule, dict):
            return True
        if not schedule.get("enabled"):
            return True

        tzname = (schedule.get("timezone") or "Asia/Baghdad").strip() or "Asia/Baghdad"
        try:
            tz = ZoneInfo(tzname)
        except Exception:
            tz = ZoneInfo("Asia/Baghdad")

        now = datetime.now(tz)
        wd = int(now.weekday())  # 0=Mon .. 6=Sun
        rules = schedule.get("rules") or []
        if not isinstance(rules, list) or not rules:
            return False

        for r in rules:
            if not isinstance(r, dict):
                continue
            days = r.get("days") or []
            if isinstance(days, str):
                try:
                    days = json.loads(days)
                except Exception:
                    days = []
            if wd not in set(int(x) for x in days if str(x).isdigit() or isinstance(x, int)):
                continue
            st = _parse_hhmm(str(r.get("start") or ""))
            en = _parse_hhmm(str(r.get("end") or ""))
            if not st or not en:
                continue

            tnow = now.time()
            if st <= en and st <= tnow <= en:
                return True
            if st > en and (tnow >= st or tnow <= en):
                return True

        return False
    except Exception:
        return True


def _compute_schedule_windows(schedule: dict) -> dict:
    """Return schedule context: now_local, current_window, next_window, timezone."""
    try:
        if not isinstance(schedule, dict):
            return {"enabled": False}
        enabled = bool(schedule.get("enabled"))
        tzname = (schedule.get("timezone") or "Asia/Baghdad").strip() or "Asia/Baghdad"
        try:
            tz = ZoneInfo(tzname)
        except Exception:
            tz = ZoneInfo("Asia/Baghdad")

        now = datetime.now(tz)
        rules = schedule.get("rules") or []
        if not isinstance(rules, list):
            rules = []

        windows = []
        for r in rules:
            if not isinstance(r, dict):
                continue
            days = r.get("days") or []
            if isinstance(days, str):
                try:
                    days = json.loads(days)
                except Exception:
                    days = []
            days = [int(x) for x in days if str(x).isdigit() or isinstance(x, int)]
            st = _parse_hhmm(str(r.get("start") or ""))
            en = _parse_hhmm(str(r.get("end") or ""))
            if not st or not en:
                continue

            for offset in range(0, 8):
                d = now.date() + timedelta(days=offset)
                if int(d.weekday()) not in set(days):
                    continue
                start_dt = datetime.combine(d, st, tzinfo=tz)
                if en >= st:
                    end_dt = datetime.combine(d, en, tzinfo=tz)
                else:
                    end_dt = datetime.combine(d + timedelta(days=1), en, tzinfo=tz)
                windows.append((start_dt, end_dt))

        windows.sort(key=lambda x: x[0])
        current = None
        next_win = None

        for w in windows:
            if w[0] <= now <= w[1]:
                current = w
                break

        if current:
            for w in windows:
                if w[0] > current[1]:
                    next_win = w
                    break
        else:
            for w in windows:
                if w[0] > now:
                    next_win = w
                    break

        def _fmt(w):
            if not w:
                return None
            return {"start": w[0].isoformat(), "end": w[1].isoformat()}

        return {
            "enabled": enabled,
            "timezone": tzname,
            "now": now.isoformat(),
            # Backward compatibility with older admin UI code.
            "now_local": now.isoformat(),
            "current_window": _fmt(current),
            "next_window": _fmt(next_win),
        }
    except Exception:
        return {"enabled": False}


def _service_enabled(service_key: str, cfg: dict | None = None) -> bool:
    data = cfg if isinstance(cfg, dict) else _load_permissions()
    services = data.get("services") if isinstance(data, dict) else {}
    if not isinstance(services, dict):
        services = {}
    key = str(service_key or "").strip().lower()
    if not key:
        return True
    if key == "transactions":
        return True
    return bool(services.get(key, True))


def _api_policy(api_id: str, cfg: dict | None = None) -> dict:
    data = cfg if isinstance(cfg, dict) else _load_permissions()
    apis = data.get("apis") if isinstance(data, dict) else {}
    if not isinstance(apis, dict):
        apis = {}
    row = apis.get(str(api_id or "").strip().lower()) or {}
    if not isinstance(row, dict):
        row = {}
    enabled = bool(row.get("enabled", True))
    sellable_mode = str(row.get("sellable_mode") or "online").strip().lower()
    if sellable_mode not in ("online", "manual"):
        sellable_mode = "online"
    schedule = _normalize_schedule(row.get("schedule"))
    schedule_ok = _ticketing_schedule_allows(schedule)
    return {
        "enabled": enabled,
        "sellable_mode": sellable_mode,
        "schedule": schedule,
        "schedule_ok": schedule_ok,
        "is_online_now": enabled and schedule_ok and sellable_mode == "online",
        "notify_whatsapp_enabled": bool(row.get("notify_whatsapp_enabled", False)),
        "notify_whatsapp_numbers": [
            str(x).strip()
            for x in (
                row.get("notify_whatsapp_numbers")
                if isinstance(row.get("notify_whatsapp_numbers"), list)
                else []
            )
            if str(x).strip()
        ],
    }


def _ota_policy() -> dict:
    cfg = _load_permissions()
    p = (cfg.get("providers") or {}).get("OTA")
    if not isinstance(p, dict):
        return {
            "availability": False,
            "ticketing_effective": False,
            "ticketing_mode": "availability_only",
            "ticketing_schedule_ok": False,
            "filters_enabled": True,
            "blocked_airlines": [],
            "service_enabled": False,
            "api_enabled": False,
        }

    service_ok = _service_enabled("flights", cfg)
    api = _api_policy("ota", cfg)

    availability = bool(p.get("availability_enabled", True)) and service_ok and bool(api.get("enabled"))
    blocked_suppliers = p.get("blocked_suppliers") or []
    if "OTA" in blocked_suppliers or "ota" in [str(x).strip().lower() for x in blocked_suppliers]:
        availability = False

    ticketing_mode = (p.get("ticketing_mode") or "full").strip().lower()
    if not bool(api.get("is_online_now")):
        ticketing_mode = "availability_only"

    ticketing_allowed_by_schedule = _ticketing_schedule_allows(p.get("ticketing_schedule") or {})
    if not bool(api.get("schedule_ok")):
        ticketing_allowed_by_schedule = False

    ticketing_effective = availability and (ticketing_mode == "full") and ticketing_allowed_by_schedule

    filters_enabled = bool(p.get("filters_enabled", True))
    blocked_airlines = [str(x).strip().upper() for x in (p.get("blocked_airlines") or []) if str(x).strip()]
    return {
        "availability": availability,
        "ticketing_effective": ticketing_effective,
        "ticketing_mode": "full" if ticketing_mode == "full" else "availability_only",
        "ticketing_schedule_ok": ticketing_allowed_by_schedule,
        "filters_enabled": filters_enabled,
        "blocked_airlines": blocked_airlines,
        "service_enabled": service_ok,
        "api_enabled": bool(api.get("enabled")),
    }
