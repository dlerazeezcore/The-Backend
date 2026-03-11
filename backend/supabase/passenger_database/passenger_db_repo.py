from __future__ import annotations

from typing import Any, Callable

from backend.supabase import load_or_seed, save


def load_passenger_profiles_doc(*, local_loader: Callable[[], Any] | None = None) -> Any:
    return load_or_seed(doc_key="passenger_profiles", default=[], local_loader=local_loader)


def save_passenger_profiles_doc(*, value: Any, local_saver: Callable[[Any], None] | None = None) -> None:
    save(doc_key="passenger_profiles", value=value, local_saver=local_saver)


def load_passenger_history_doc(*, local_loader: Callable[[], Any] | None = None) -> Any:
    return load_or_seed(doc_key="passenger_history", default=[], local_loader=local_loader)


def save_passenger_history_doc(*, value: Any, local_saver: Callable[[Any], None] | None = None) -> None:
    save(doc_key="passenger_history", value=value, local_saver=local_saver)
