"""Supabase pending repositories."""

from .pending_repo import (
    load_pending_doc,
    save_pending_doc,
)

__all__ = [
    "load_pending_doc",
    "save_pending_doc",
]
