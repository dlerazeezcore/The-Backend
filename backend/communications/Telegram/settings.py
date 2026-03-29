from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any


CONFIG_PATH = Path(__file__).resolve().parent / "config.json"


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


def read_setting(key: str, default: Any = None) -> Any:
    value = get_settings().get(key, default)
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
