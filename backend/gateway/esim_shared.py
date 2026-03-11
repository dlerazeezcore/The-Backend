from __future__ import annotations

import copy
import json
import time
from pathlib import Path
from threading import RLock
from typing import Any, Callable, Dict, List

_CATALOG_CACHE_PATH = Path(__file__).resolve().parents[1] / "esim" / "oasis" / "catalog_cache.json"
_LAST_CATALOG_REFRESH_ATTEMPT = 0.0
_CACHE: Dict[str, Dict[str, Any]] = {}
_LOCK = RLock()


def cache_get(key: str) -> Any | None:
    with _LOCK:
        item = _CACHE.get(key)
        if not item:
            return None
        exp = float(item.get("exp") or 0.0)
        if exp <= time.time():
            _CACHE.pop(key, None)
            return None
        return copy.deepcopy(item.get("value"))


def cache_set(key: str, value: Any, ttl_sec: int) -> None:
    expires_at = time.time() + max(1, int(ttl_sec))
    with _LOCK:
        _CACHE[key] = {"exp": expires_at, "value": copy.deepcopy(value)}


def cache_delete_prefix(prefix: str) -> None:
    with _LOCK:
        for key in [k for k in _CACHE if k.startswith(prefix)]:
            _CACHE.pop(key, None)


def load_catalog_cache() -> Dict[str, Any]:
    try:
        if _CATALOG_CACHE_PATH.exists():
            data = json.loads(_CATALOG_CACHE_PATH.read_text(encoding="utf-8") or "{}")
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def save_catalog_cache(data: Dict[str, Any]) -> None:
    try:
        _CATALOG_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CATALOG_CACHE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def catalog_items(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    items = data.get("items") or data.get("bundles") or []
    if not isinstance(items, list):
        return []
    return [x for x in items if isinstance(x, dict)]


def load_catalog_cache_with_fallback(
    list_bundles_fn: Callable[..., Dict[str, Any]], cooldown_sec: int = 120
) -> Dict[str, Any]:
    global _LAST_CATALOG_REFRESH_ATTEMPT

    data = load_catalog_cache()
    if catalog_items(data):
        return data

    now = time.time()
    with _LOCK:
        if (now - float(_LAST_CATALOG_REFRESH_ATTEMPT or 0.0)) < max(1, int(cooldown_sec)):
            return data
        _LAST_CATALOG_REFRESH_ATTEMPT = now

    try:
        fresh = list_bundles_fn(params=None)
        if isinstance(fresh, dict) and catalog_items(fresh):
            save_catalog_cache(fresh)
            return fresh
    except Exception:
        pass

    return data
