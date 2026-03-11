from __future__ import annotations

from typing import Any, Callable

from backend.supabase import load_or_seed, save


def load_pending_doc(*, local_loader: Callable[[], Any] | None = None) -> Any:
    return load_or_seed(doc_key="pending", default={"pending": []}, local_loader=local_loader)


def save_pending_doc(*, value: Any, local_saver: Callable[[Any], None] | None = None) -> None:
    save(doc_key="pending", value=value, local_saver=local_saver)
