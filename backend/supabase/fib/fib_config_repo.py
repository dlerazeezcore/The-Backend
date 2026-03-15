from __future__ import annotations

from typing import Any, Callable

from backend.supabase import load_or_seed, save


def load_fib_accounts_doc(*, local_loader: Callable[[], Any] | None = None) -> Any:
    return load_or_seed(
        doc_key="fib_accounts",
        default={"accounts": []},
        local_loader=local_loader,
    )


def save_fib_accounts_doc(*, value: Any, local_saver: Callable[[Any], None] | None = None) -> None:
    save(doc_key="fib_accounts", value=value, local_saver=local_saver)


def load_fib_frontend_routes_doc(*, local_loader: Callable[[], Any] | None = None) -> Any:
    return load_or_seed(
        doc_key="fib_frontend_routes",
        default={"routes": []},
        local_loader=local_loader,
    )


def save_fib_frontend_routes_doc(*, value: Any, local_saver: Callable[[Any], None] | None = None) -> None:
    save(doc_key="fib_frontend_routes", value=value, local_saver=local_saver)


def load_fib_settings_doc(*, local_loader: Callable[[], Any] | None = None) -> Any:
    return load_or_seed(
        doc_key="fib_settings",
        default={"active_account_id": ""},
        local_loader=local_loader,
    )


def save_fib_settings_doc(*, value: Any, local_saver: Callable[[Any], None] | None = None) -> None:
    save(doc_key="fib_settings", value=value, local_saver=local_saver)


def load_fib_payment_accounts_doc(*, local_loader: Callable[[], Any] | None = None) -> Any:
    return load_or_seed(
        doc_key="fib_payment_accounts",
        default={"payments": {}},
        local_loader=local_loader,
    )


def save_fib_payment_accounts_doc(*, value: Any, local_saver: Callable[[Any], None] | None = None) -> None:
    save(doc_key="fib_payment_accounts", value=value, local_saver=local_saver)
