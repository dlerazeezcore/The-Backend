from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

import requests


@dataclass(frozen=True)
class SupabaseConfig:
    url: str
    key: str
    table: str
    required: bool
    timeout_seconds: float


def _read_config() -> SupabaseConfig:
    url = str(os.getenv("SUPABASE_URL") or "").strip().rstrip("/")
    key = str(
        os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        or os.getenv("SUPABASE_SERVICE_KEY")
        or os.getenv("SUPABASE_ANON_KEY")
        or ""
    ).strip()
    table = str(os.getenv("SUPABASE_STATE_TABLE") or "app_state").strip() or "app_state"
    required = str(os.getenv("SUPABASE_REQUIRED") or "false").strip().lower() in {"1", "true", "yes", "on"}
    timeout_seconds = float(str(os.getenv("SUPABASE_TIMEOUT_SECONDS") or "20").strip() or "20")
    return SupabaseConfig(
        url=url,
        key=key,
        table=table,
        required=required,
        timeout_seconds=timeout_seconds,
    )


def _clone(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value))
    except Exception:
        return value


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


def is_enabled() -> bool:
    cfg = _read_config()
    return bool(cfg.url and cfg.key)


def _headers(cfg: SupabaseConfig) -> dict[str, str]:
    return {
        "apikey": cfg.key,
        "Authorization": f"Bearer {cfg.key}",
        "Content-Type": "application/json",
    }


def _table_endpoint(cfg: SupabaseConfig) -> str:
    return f"{cfg.url}/rest/v1/{cfg.table}"


def _handle_error(action: str, exc: Exception, cfg: SupabaseConfig) -> None:
    if cfg.required:
        raise RuntimeError(f"Supabase {action} failed: {exc}") from exc
    print(f"WARNING: Supabase {action} failed, falling back to local JSON. Error: {exc}")


def _fetch_document(doc_key: str, cfg: SupabaseConfig) -> tuple[bool, Any]:
    if not (cfg.url and cfg.key):
        return False, None
    params = {
        "select": "content",
        "doc_key": f"eq.{str(doc_key or '').strip()}",
        "limit": "1",
    }
    resp = requests.get(_table_endpoint(cfg), headers=_headers(cfg), params=params, timeout=cfg.timeout_seconds)
    if resp.status_code >= 400:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text}")
    payload = resp.json()
    if not isinstance(payload, list) or len(payload) == 0:
        return False, None
    row = payload[0] if isinstance(payload[0], dict) else {}
    return True, row.get("content")


def _upsert_document(doc_key: str, value: Any, cfg: SupabaseConfig) -> None:
    if not (cfg.url and cfg.key):
        return
    body = [
        {
            "doc_key": str(doc_key or "").strip(),
            "content": value,
            "updated_at": _now_iso(),
        }
    ]
    headers = _headers(cfg)
    headers["Prefer"] = "resolution=merge-duplicates,return=minimal"
    resp = requests.post(_table_endpoint(cfg), headers=headers, json=body, timeout=cfg.timeout_seconds)
    if resp.status_code >= 400:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text}")


def load_or_seed(
    *,
    doc_key: str,
    default: Any,
    local_loader: Callable[[], Any] | None = None,
) -> Any:
    """Load from Supabase when enabled; seed from local/default if missing."""
    cfg = _read_config()
    if not (cfg.url and cfg.key):
        return local_loader() if callable(local_loader) else _clone(default)

    try:
        found, value = _fetch_document(doc_key, cfg)
        if found:
            return value if value is not None else _clone(default)
        seed = local_loader() if callable(local_loader) else _clone(default)
        _upsert_document(doc_key, seed, cfg)
        return seed
    except Exception as exc:
        _handle_error(f"load ({doc_key})", exc, cfg)
        return local_loader() if callable(local_loader) else _clone(default)


def save(
    *,
    doc_key: str,
    value: Any,
    local_saver: Callable[[Any], None] | None = None,
) -> None:
    cfg = _read_config()
    if cfg.url and cfg.key:
        try:
            _upsert_document(doc_key, value, cfg)
            return
        except Exception as exc:
            _handle_error(f"save ({doc_key})", exc, cfg)
    if callable(local_saver):
        local_saver(value)
