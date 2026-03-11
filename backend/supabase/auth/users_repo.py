from __future__ import annotations

from typing import Any, Callable

from backend.supabase import load_or_seed, save


def load_users_doc(*, local_loader: Callable[[], Any] | None = None) -> Any:
    return load_or_seed(doc_key="users", default={"users": []}, local_loader=local_loader)


def save_users_doc(*, value: Any, local_saver: Callable[[Any], None] | None = None) -> None:
    save(doc_key="users", value=value, local_saver=local_saver)
