from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import tempfile
import uuid
from typing import Any

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from backend.core.paths import DATA_DIR
from backend.supabase.auth.users_repo import load_users_doc, save_users_doc
from backend.communications.corevia_email.service import send_email


USERS_PATH = DATA_DIR / "users.json"
AUTH_SALT = "the-book-auth-token"
DEFAULT_TOKEN_MAX_AGE_SECONDS = int(str(os.getenv("AUTH_TOKEN_MAX_AGE_SECONDS") or "604800").strip() or "604800")
STORE_LEGACY_PASSWORD = str(os.getenv("AUTH_STORE_LEGACY_PASSWORD") or "true").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
EXPOSE_TEMP_PASSWORD = str(os.getenv("AUTH_INCLUDE_TEMP_PASSWORD_IN_RESPONSE") or "false").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}


def _default_company_services() -> dict[str, bool]:
    return {
        "flights": True,
        "hotels": True,
        "esim": True,
        "airport_transportation": True,
        "car_rental": True,
        "cip_services": True,
        "passenger_database": True,
        "transportation": True,
        "visa": True,
        "pending": True,
        "transactions": True,
    }


def _default_company_api_access() -> dict[str, bool]:
    return {
        "ota": True,
        "esim_oasis": True,
        "esim_access": True,
        "fib": True,
        "email": True,
    }


def _normalize_company_services(raw: dict[str, Any] | None) -> dict[str, bool]:
    src = raw if isinstance(raw, dict) else {}
    out = _default_company_services()
    transport_seed = bool(src.get("transportation", out.get("transportation", True)))
    for key in list(out.keys()):
        if key == "transactions":
            out[key] = True
        elif key in {"airport_transportation", "car_rental", "cip_services"}:
            out[key] = bool(src.get(key, transport_seed))
        elif key in src:
            out[key] = bool(src.get(key))
    out["transportation"] = bool(
        out.get("airport_transportation", True)
        or out.get("car_rental", True)
        or out.get("cip_services", True)
    )
    return out


def _normalize_company_api_access(raw: dict[str, Any] | None) -> dict[str, bool]:
    src = raw if isinstance(raw, dict) else {}
    out = _default_company_api_access()
    for key in list(out.keys()):
        if key in src:
            out[key] = bool(src.get(key))
    return out


def _users_path() -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return USERS_PATH


def _load_users_local_doc() -> dict[str, Any]:
    path = _users_path()
    if not path.exists():
        return {"users": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8") or "{}")
    except Exception:
        return {"users": []}
    if isinstance(payload, list):
        return {"users": payload}
    if isinstance(payload, dict):
        users = payload.get("users") or []
        return {"users": users if isinstance(users, list) else []}
    return {"users": []}


def _save_users_local_doc(payload: dict[str, Any]) -> None:
    path = _users_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = tempfile.NamedTemporaryFile("w", delete=False, dir=path.parent, encoding="utf-8")
    try:
        tmp.write(json.dumps(payload if isinstance(payload, dict) else {"users": []}, ensure_ascii=False, indent=2))
        tmp.flush()
        os.fsync(tmp.fileno())
    finally:
        tmp.close()
    os.replace(tmp.name, path)


def _auth_secret() -> str:
    return (
        str(os.getenv("AUTH_TOKEN_SECRET") or "").strip()
        or str(os.getenv("APP_SESSION_SECRET") or "").strip()
        or "development-auth-secret-change-me"
    )


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(_auth_secret())


def _pbkdf2(password: str, salt: bytes, iterations: int = 310000) -> str:
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return base64.b64encode(derived).decode("ascii")


def hash_password(password: str) -> str:
    password = str(password or "")
    salt = secrets.token_bytes(16)
    iterations = 310000
    digest = _pbkdf2(password, salt, iterations)
    return f"pbkdf2_sha256${iterations}${base64.b64encode(salt).decode('ascii')}${digest}"


def verify_password_record(user: dict[str, Any], password: str) -> bool:
    password = str(password or "")
    stored_hash = str(user.get("password_hash") or "").strip()
    if stored_hash:
        try:
            algorithm, iterations_s, salt_b64, expected = stored_hash.split("$", 3)
            if algorithm != "pbkdf2_sha256":
                return False
            salt = base64.b64decode(salt_b64.encode("ascii"))
            computed = _pbkdf2(password, salt, int(iterations_s))
            if hmac.compare_digest(computed, expected):
                return True
        except Exception:
            return False

    legacy = str(user.get("password") or "")
    return bool(legacy) and secrets.compare_digest(legacy, password)


def set_password_fields(user: dict[str, Any], password: str) -> None:
    user["password_hash"] = hash_password(password)
    if STORE_LEGACY_PASSWORD:
        user["password"] = password
    else:
        user.pop("password", None)


def maybe_upgrade_password_hash(users: list[dict[str, Any]], user: dict[str, Any], password: str) -> None:
    if str(user.get("password_hash") or "").strip():
        return
    if not str(user.get("password") or "").strip():
        return
    set_password_fields(user, password)
    save_users(users)


def generate_password(length: int = 12) -> str:
    length = max(10, int(length or 12))
    alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789@#$%&*_-+!"
    required = [
        secrets.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ"),
        secrets.choice("abcdefghijklmnopqrstuvwxyz"),
        secrets.choice("0123456789"),
        secrets.choice("@#$%&*_-+!"),
    ]
    while len(required) < length:
        required.append(secrets.choice(alphabet))
    secrets.SystemRandom().shuffle(required)
    return "".join(required)


def is_super_admin(user: dict[str, Any] | None) -> bool:
    if not isinstance(user, dict):
        return False
    return str(user.get("role") or "").strip().lower() == "super_admin"


def is_sub_user(user: dict[str, Any] | None) -> bool:
    if not isinstance(user, dict):
        return False
    if str(user.get("role") or "").strip().lower() == "sub_user":
        return True
    return bool(user.get("company_admin_id"))


def is_company_admin(user: dict[str, Any] | None) -> bool:
    return bool(isinstance(user, dict) and not is_super_admin(user) and not is_sub_user(user))


def effective_owner_user_id(user: dict[str, Any]) -> str:
    if is_sub_user(user):
        owner_id = str(user.get("company_admin_id") or "").strip()
        if owner_id:
            return owner_id
    return str(user.get("id") or "").strip()


def display_name(user: dict[str, Any]) -> str:
    first = str(user.get("first_name") or "").strip()
    last = str(user.get("last_name") or "").strip()
    full = f"{first} {last}".strip()
    if full:
        return full
    return str(user.get("username") or user.get("email") or user.get("company_name") or "User").strip() or "User"


def public_user(user: dict[str, Any]) -> dict[str, Any]:
    account_role = "super_admin" if is_super_admin(user) else "sub_user" if is_sub_user(user) else "company_admin"
    return {
        "id": str(user.get("id") or ""),
        "username": str(user.get("username") or ""),
        "email": str(user.get("email") or ""),
        "name": display_name(user),
        "company": str(user.get("company_name") or user.get("company") or ""),
        "company_name": str(user.get("company_name") or user.get("company") or ""),
        "phone": str(user.get("phone") or ""),
        "contact": str(user.get("contact") or ""),
        "role": "admin" if is_super_admin(user) else "user",
        "account_role": account_role,
        "is_admin": is_super_admin(user),
        "is_super_admin": is_super_admin(user),
        "is_company_admin": is_company_admin(user),
        "is_sub_user": is_sub_user(user),
        "active": bool(user.get("active", True)),
        "cash": user.get("cash", 0),
        "credit": user.get("credit", 0),
        "owner_user_id": effective_owner_user_id(user),
    }


def _normalize_user(user: dict[str, Any]) -> bool:
    changed = False
    if not user.get("id"):
        user["id"] = uuid.uuid4().hex
        changed = True
    if "active" not in user:
        user["active"] = True
        changed = True
    if "apis" not in user or not isinstance(user.get("apis"), list):
        user["apis"] = []
        changed = True
    if "service_access" not in user or not isinstance(user.get("service_access"), dict):
        user["service_access"] = _default_company_services()
        changed = True
    else:
        normalized = _normalize_company_services(user.get("service_access"))
        if normalized != user.get("service_access"):
            user["service_access"] = normalized
            changed = True
    if "api_access" not in user or not isinstance(user.get("api_access"), dict):
        user["api_access"] = _default_company_api_access()
        changed = True
    else:
        normalized_api = _normalize_company_api_access(user.get("api_access"))
        if normalized_api != user.get("api_access"):
            user["api_access"] = normalized_api
            changed = True
    if "credit" not in user:
        user["credit"] = 0
        changed = True
    if "cash" not in user:
        user["cash"] = 0
        changed = True
    if "preferred_payment" not in user:
        user["preferred_payment"] = "cash"
        changed = True
    if "email" not in user:
        user["email"] = ""
        changed = True
    if "phone" not in user:
        user["phone"] = ""
        changed = True
    if "contact" not in user:
        user["contact"] = user.get("phone") or ""
        changed = True
    if "role" not in user:
        user["role"] = "user"
        changed = True
    if "auth_token_version" not in user:
        user["auth_token_version"] = 0
        changed = True
    return changed


def load_users() -> list[dict[str, Any]]:
    payload = load_users_doc(local_loader=_load_users_local_doc)
    if isinstance(payload, list):
        payload = {"users": payload}
    if not isinstance(payload, dict):
        payload = {"users": []}
    users = payload.get("users") or []
    users = users if isinstance(users, list) else []

    changed = False
    out: list[dict[str, Any]] = []
    for item in users:
        if not isinstance(item, dict):
            continue
        row = dict(item)
        changed = _normalize_user(row) or changed
        out.append(row)
    if changed:
        save_users(out)
    return out


def save_users(users: list[dict[str, Any]]) -> None:
    save_users_doc(
        value={"users": users if isinstance(users, list) else []},
        local_saver=_save_users_local_doc,
    )


def find_user(users: list[dict[str, Any]], user_id: str) -> dict[str, Any] | None:
    for user in users:
        if isinstance(user, dict) and str(user.get("id") or "") == str(user_id):
            return user
    return None


def find_user_by_identifier(users: list[dict[str, Any]], identifier: str) -> dict[str, Any] | None:
    ident = str(identifier or "").strip().lower()
    if not ident:
        return None
    for user in users:
        if not isinstance(user, dict):
            continue
        if str(user.get("username") or "").strip().lower() == ident:
            return user
        if str(user.get("email") or "").strip().lower() == ident:
            return user
    return None


def authenticate_user(identifier: str, password: str) -> tuple[dict[str, Any] | None, str | None]:
    users = load_users()
    user = find_user_by_identifier(users, identifier)
    if not user:
        return None, "Invalid credentials."
    if not bool(user.get("active", True)):
        return None, "Your account is blocked."
    if not verify_password_record(user, password):
        return None, "Invalid credentials."
    maybe_upgrade_password_hash(users, user, password)
    return user, None


def issue_token(user: dict[str, Any]) -> str:
    payload = {
        "sub": str(user.get("id") or ""),
        "ver": int(user.get("auth_token_version") or 0),
    }
    return _serializer().dumps(payload, salt=AUTH_SALT)


def resolve_token(token: str, *, max_age_seconds: int | None = None) -> dict[str, Any] | None:
    token = str(token or "").strip()
    if not token:
        return None
    try:
        payload = _serializer().loads(
            token,
            salt=AUTH_SALT,
            max_age=max_age_seconds if max_age_seconds is not None else DEFAULT_TOKEN_MAX_AGE_SECONDS,
        )
    except (BadSignature, SignatureExpired):
        return None
    if not isinstance(payload, dict):
        return None
    user_id = str(payload.get("sub") or "").strip()
    if not user_id:
        return None
    users = load_users()
    user = find_user(users, user_id)
    if not user or not bool(user.get("active", True)):
        return None
    if int(user.get("auth_token_version") or 0) != int(payload.get("ver") or 0):
        return None
    return user


def revoke_user_tokens(user_id: str) -> dict[str, Any] | None:
    users = load_users()
    user = find_user(users, user_id)
    if not user:
        return None
    user["auth_token_version"] = int(user.get("auth_token_version") or 0) + 1
    save_users(users)
    return user


def create_signup_user(*, email: str, username: str, company_name: str, contact: str, password: str) -> dict[str, Any]:
    return {
        "id": uuid.uuid4().hex,
        "type": "user",
        "company_name": company_name or username,
        "username": username,
        "email": email,
        "phone": contact,
        "contact": contact,
        "role": "user",
        "active": True,
        "credit": 0,
        "cash": 0,
        "preferred_payment": "cash",
        "apis": [],
        "service_access": _default_company_services(),
        "api_access": _default_company_api_access(),
        "employees": [],
        "commission": [],
        "markup": [],
        "auth_token_version": 0,
    }


def create_user_and_notify(*, email: str, username: str, company_name: str, contact: str) -> tuple[dict[str, Any], str]:
    users = load_users()
    email_l = str(email or "").strip().lower()
    username_l = str(username or "").strip().lower()
    for user in users:
        if not isinstance(user, dict):
            continue
        if str(user.get("email") or "").strip().lower() == email_l:
            raise ValueError("This email is already registered.")
        if str(user.get("username") or "").strip().lower() == username_l:
            raise ValueError("This username is already taken.")

    temp_password = generate_password(12)
    user = create_signup_user(
        email=str(email or "").strip(),
        username=str(username or "").strip(),
        company_name=str(company_name or "").strip() or str(username or "").strip(),
        contact=str(contact or "").strip(),
        password=temp_password,
    )
    set_password_fields(user, temp_password)

    ok, message = send_email(
        str(email or "").strip(),
        "Welcome to Tulip Bookings",
        (
            f"Hello {username},\n\n"
            "Your company account is now active.\n\n"
            f"Username: {username}\n"
            f"Temporary password: {temp_password}\n\n"
            "For security, please sign in and change your password immediately.\n\n"
            "If you did not request this account, contact support."
        ),
    )
    if not ok and not EXPOSE_TEMP_PASSWORD:
        raise RuntimeError(f"Failed to send email: {message}")

    users.append(user)
    save_users(users)
    return user, temp_password


def reset_password_and_notify(identifier: str) -> tuple[dict[str, Any], str]:
    users = load_users()
    user = find_user_by_identifier(users, identifier)
    if not user:
        raise ValueError("No account found for that email/username.")
    email = str(user.get("email") or "").strip()
    if not email:
        raise ValueError("This account has no email address on file.")

    temp_password = generate_password(12)
    ok, message = send_email(
        email,
        "Tulip Bookings password reset",
        (
            f"Hello {user.get('username') or ''},\n\n"
            "Your password was reset successfully.\n\n"
            f"Temporary password: {temp_password}\n\n"
            "Please sign in and change your password immediately.\n\n"
            "If you did not request this, contact support right away."
        ),
    )
    if not ok and not EXPOSE_TEMP_PASSWORD:
        raise RuntimeError(f"Failed to send email: {message}")

    set_password_fields(user, temp_password)
    user["auth_token_version"] = int(user.get("auth_token_version") or 0) + 1
    save_users(users)
    return user, temp_password
