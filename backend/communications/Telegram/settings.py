from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any


CONFIG_PATH = Path(__file__).resolve().parent / "config.json"
PLACEHOLDER_PREFIXES = ("PUT_", "<")
ENV_KEY_ALIASES = {
    "supabase_service_role_key": ("SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_SERVICE_KEY"),
}


def _load_raw() -> dict[str, Any]:
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"Failed to load Telegram config file: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError("Telegram config file must contain a JSON object.")
    return data


@lru_cache(maxsize=1)
def get_settings() -> dict[str, Any]:
    return _load_raw()


def _looks_unset(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return True
        upper = stripped.upper()
        if upper in {"NONE", "NULL"}:
            return True
        if any(upper.startswith(prefix) for prefix in PLACEHOLDER_PREFIXES):
            return True
    return False


def _env_candidates(key: str) -> tuple[str, ...]:
    explicit = ENV_KEY_ALIASES.get(key, ())
    derived = (key.upper(),)
    return explicit + tuple(name for name in derived if name not in explicit)


def read_setting(key: str, default: Any = None) -> Any:
    for env_key in _env_candidates(key):
        env_value = os.getenv(env_key)
        if not _looks_unset(env_value):
            return env_value
    value = get_settings().get(key, None)
    if not _looks_unset(value):
        return value
    return default if value is None else value


def read_text(key: str, default: str = "") -> str:
    return str(read_setting(key, default) or "").strip()


def read_int(key: str) -> int | None:
    value = read_setting(key, None)
    if value in (None, ""):
        return None
    try:
        return int(value)
    except Exception:
        return None


def read_float(key: str, default: float) -> float:
    value = read_setting(key, default)
    try:
        return float(value)
    except Exception:
        return float(default)
