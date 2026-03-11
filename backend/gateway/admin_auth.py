from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request

from backend.auth.api import require_authenticated_user
from backend.auth.service import is_super_admin


def require_super_admin_request(request: Request) -> dict[str, Any]:
    current_user = require_authenticated_user(request)
    if not is_super_admin(current_user):
        raise HTTPException(status_code=403, detail="Forbidden.")
    return current_user
