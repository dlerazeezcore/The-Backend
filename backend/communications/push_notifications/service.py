from __future__ import annotations

import json
import os
from functools import lru_cache
from typing import Any, Dict, Iterable, List

try:
    import firebase_admin
    from firebase_admin import credentials, messaging
except Exception:  # pragma: no cover - handled at runtime when dependency is missing
    firebase_admin = None
    credentials = None
    messaging = None


def _credentials_file() -> str:
    return str(
        os.getenv("FIREBASE_SERVICE_ACCOUNT_FILE")
        or os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        or ""
    ).strip()


def _credentials_json() -> str:
    return str(os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON") or "").strip()


def is_configured() -> bool:
    return bool(firebase_admin and (_credentials_file() or _credentials_json()))


def _load_credential_object() -> Any:
    file_path = _credentials_file()
    if file_path:
        return credentials.Certificate(file_path)

    raw_json = _credentials_json()
    if not raw_json:
        raise RuntimeError("Firebase service account credentials are not configured.")

    try:
        payload = json.loads(raw_json)
    except Exception as exc:  # pragma: no cover - runtime config issue
        raise RuntimeError("FIREBASE_SERVICE_ACCOUNT_JSON is not valid JSON.") from exc

    return credentials.Certificate(payload)


@lru_cache(maxsize=1)
def _get_firebase_app():
    if firebase_admin is None or credentials is None or messaging is None:
        raise RuntimeError("firebase-admin is not installed. Add it to the backend environment.")

    try:
        return firebase_admin.get_app()
    except ValueError:
        return firebase_admin.initialize_app(_load_credential_object())


def _normalize_data(payload: Dict[str, Any] | None) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for key, value in (payload or {}).items():
        name = str(key or "").strip()
        if not name or value is None:
            continue
        result[name] = str(value)
    return result


def _chunked(values: List[str], size: int) -> Iterable[List[str]]:
    for index in range(0, len(values), size):
        yield values[index:index + size]


def send_push_notification(
    *,
    tokens: Iterable[str],
    title: str,
    body: str,
    data: Dict[str, Any] | None = None,
    channel_id: str = "general",
    image: str = "",
) -> Dict[str, Any]:
    normalized_tokens = []
    seen = set()
    for token in tokens:
        value = str(token or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        normalized_tokens.append(value)

    if not normalized_tokens:
        return {
            "successCount": 0,
            "failureCount": 0,
            "invalidTokens": [],
        }

    app = _get_firebase_app()
    payload = _normalize_data(data)
    success_count = 0
    failure_count = 0
    invalid_tokens: List[str] = []

    for batch in _chunked(normalized_tokens, 500):
        message = messaging.MulticastMessage(
            tokens=batch,
            notification=messaging.Notification(
                title=str(title or "").strip(),
                body=str(body or "").strip(),
                image=str(image or "").strip() or None,
            ),
            data=payload,
            android=messaging.AndroidConfig(
                priority="high",
                notification=messaging.AndroidNotification(channel_id=str(channel_id or "general")),
            ),
            apns=messaging.APNSConfig(
                headers={"apns-priority": "10"},
                payload=messaging.APNSPayload(aps=messaging.Aps(sound="default")),
            ),
        )

        response = messaging.send_each_for_multicast(message, app=app)
        success_count += int(response.success_count)
        failure_count += int(response.failure_count)

        for index, item in enumerate(response.responses):
            if item.success:
                continue
            error_text = str(item.exception or "").lower()
            if "not registered" in error_text or "invalid registration token" in error_text:
                invalid_tokens.append(batch[index])

    return {
        "successCount": success_count,
        "failureCount": failure_count,
        "invalidTokens": invalid_tokens,
    }
