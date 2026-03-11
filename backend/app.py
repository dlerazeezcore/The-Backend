from __future__ import annotations

"""
Compatibility entrypoint.

The canonical unified deployment app lives in backend.gateway.app.
Keep this module so existing uvicorn/Koyeb references to backend.app:app
continue to work without duplicating router wiring.
"""

from backend.gateway.app import app

__all__ = ["app"]
