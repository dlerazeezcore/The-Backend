from __future__ import annotations

import json
import os
import smtplib
import uuid
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from pathlib import Path
from urllib.parse import urlparse

from backend.supabase.communications.email_config_repo import (
    load_email_config_doc,
    save_email_config_doc,
)


CONFIG_PATH = Path(__file__).resolve().parent / "config.json"


def _default_cfg() -> dict:
    return {"accounts": [], "active_account_id": ""}


def _normalize_account(src: dict | None) -> dict:
    row = src if isinstance(src, dict) else {}
    smtp_user = str(row.get("smtp_user") or "").strip()
    smtp_from = str(row.get("smtp_from") or "").strip() or smtp_user
    return {
        "id": str(row.get("id") or f"email_{uuid.uuid4().hex[:8]}").strip(),
        "label": str(row.get("label") or "").strip() or (smtp_user or "Email account"),
        "provider": "smtp",
        "smtp_url": str(row.get("smtp_url") or "smtps://smtppro.zoho.com:465").strip() or "smtps://smtppro.zoho.com:465",
        "smtp_user": smtp_user,
        "smtp_pass": str(row.get("smtp_pass") or "").strip(),
        "smtp_from": smtp_from,
        "smtp_from_name": str(row.get("smtp_from_name") or "Tulip Bookings").strip() or "Tulip Bookings",
        "smtp_reply_to": str(row.get("smtp_reply_to") or smtp_from).strip(),
        "smtp_timeout": float(row.get("smtp_timeout") or 25),
        "smtp_starttls": bool(row.get("smtp_starttls", True)),
    }


def _normalize_cfg(src: dict | None) -> dict:
    row = src if isinstance(src, dict) else {}
    accounts_raw = row.get("accounts")
    accounts: list[dict] = []
    if isinstance(accounts_raw, list):
        for item in accounts_raw:
            if isinstance(item, dict):
                accounts.append(_normalize_account(item))
    active_id = str(row.get("active_account_id") or "").strip()
    if accounts and not any(str(a.get("id") or "") == active_id for a in accounts):
        active_id = str(accounts[0].get("id") or "")
    return {"accounts": accounts, "active_account_id": active_id}


def _load_local() -> dict:
    try:
        if CONFIG_PATH.exists():
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8") or "{}")
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return _default_cfg()


def _save_local(cfg: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg if isinstance(cfg, dict) else {}, ensure_ascii=False, indent=2), encoding="utf-8")


def load_config() -> dict:
    data = load_email_config_doc(local_loader=_load_local)
    return _normalize_cfg(data if isinstance(data, dict) else _default_cfg())


def save_config(cfg: dict) -> dict:
    normalized = _normalize_cfg(cfg if isinstance(cfg, dict) else _default_cfg())
    save_email_config_doc(value=normalized, local_saver=_save_local)
    return normalized


def _active_account(cfg: dict) -> dict | None:
    accounts = cfg.get("accounts") if isinstance(cfg, dict) else []
    accounts = accounts if isinstance(accounts, list) else []
    active_id = str((cfg or {}).get("active_account_id") or "").strip()
    if active_id:
        for a in accounts:
            if isinstance(a, dict) and str(a.get("id") or "") == active_id:
                return a
    for a in accounts:
        if isinstance(a, dict):
            return a
    return None


def _env_account() -> dict:
    smtp_user = str(os.getenv("SMTP_USER") or "").strip()
    smtp_pass = str(os.getenv("SMTP_PASS") or "").strip()
    smtp_from = str(os.getenv("SMTP_FROM") or smtp_user).strip() or smtp_user
    return {
        "id": "env",
        "label": "ENV SMTP",
        "provider": "smtp",
        "smtp_url": str(os.getenv("SMTP_URL") or "smtps://smtppro.zoho.com:465").strip() or "smtps://smtppro.zoho.com:465",
        "smtp_user": smtp_user,
        "smtp_pass": smtp_pass,
        "smtp_from": smtp_from,
        "smtp_from_name": str(os.getenv("SMTP_FROM_NAME") or "Tulip Bookings").strip() or "Tulip Bookings",
        "smtp_reply_to": str(os.getenv("SMTP_REPLY_TO") or smtp_from).strip(),
        "smtp_timeout": float(str(os.getenv("SMTP_TIMEOUT") or "25").strip() or "25"),
        "smtp_starttls": str(os.getenv("SMTP_STARTTLS") or "true").strip().lower() in {"1", "true", "yes", "on"},
    }


def _smtp_target(acc: dict) -> tuple[str, int, bool, bool]:
    parsed = urlparse(str(acc.get("smtp_url") or "smtps://smtppro.zoho.com:465").strip())
    scheme = (parsed.scheme or "smtps").lower()
    host = parsed.hostname or "smtppro.zoho.com"
    if parsed.port is not None:
        port = int(parsed.port)
    elif scheme == "smtps":
        port = 465
    else:
        port = 587
    use_ssl = scheme == "smtps"
    use_starttls = (not use_ssl) and bool(acc.get("smtp_starttls", True))
    return host, port, use_ssl, use_starttls


def send_email(to_email: str, subject: str, body: str) -> tuple[bool, str]:
    to_email = str(to_email or "").strip()
    if not to_email:
        return False, "Missing recipient email"

    try:
        cfg = load_config()
    except Exception:
        cfg = _default_cfg()
    acc = _active_account(cfg) or _env_account()

    smtp_user = str(acc.get("smtp_user") or "").strip()
    smtp_pass = str(acc.get("smtp_pass") or "").strip()
    smtp_from = str(acc.get("smtp_from") or "").strip() or smtp_user
    smtp_from_name = str(acc.get("smtp_from_name") or "Tulip Bookings").strip() or "Tulip Bookings"
    smtp_reply_to = str(acc.get("smtp_reply_to") or smtp_from).strip()
    timeout_s = float(acc.get("smtp_timeout") or 25)

    if not smtp_user or not smtp_pass or not smtp_from:
        return False, "SMTP credentials are missing"

    host, port, use_ssl, use_starttls = _smtp_target(acc)
    msg = EmailMessage()
    msg["From"] = formataddr((smtp_from_name, smtp_from))
    msg["To"] = to_email
    msg["Subject"] = str(subject or "Notification").strip() or "Notification"
    if smtp_reply_to:
        msg["Reply-To"] = smtp_reply_to
    msg["Date"] = formatdate(localtime=False)
    msg["Message-ID"] = make_msgid(domain=(smtp_from.split("@", 1)[1] if "@" in smtp_from else None))
    text_body = str(body or "").strip() or " "
    msg.set_content(text_body)
    html_body = "<br>".join(line for line in text_body.splitlines()) or " "
    msg.add_alternative(f"<html><body><div>{html_body}</div></body></html>", subtype="html")

    try:
        if use_ssl:
            with smtplib.SMTP_SSL(host, port, timeout=timeout_s) as smtp:
                smtp.login(smtp_user, smtp_pass)
                smtp.send_message(msg, from_addr=smtp_from, to_addrs=[to_email])
        else:
            with smtplib.SMTP(host, port, timeout=timeout_s) as smtp:
                smtp.ehlo()
                if use_starttls:
                    smtp.starttls()
                    smtp.ehlo()
                smtp.login(smtp_user, smtp_pass)
                smtp.send_message(msg, from_addr=smtp_from, to_addrs=[to_email])
        return True, "sent"
    except Exception as exc:
        return False, str(exc)
