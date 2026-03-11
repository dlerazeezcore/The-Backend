from __future__ import annotations

import os
import re

import requests


def _normalize_number(v: str) -> str:
    raw = str(v or "").strip()
    if not raw:
        return ""
    if raw.startswith("+"):
        digits = "".join(ch for ch in raw if ch.isdigit())
        return f"+{digits}" if digits else ""
    digits = "".join(ch for ch in raw if ch.isdigit())
    return f"+{digits}" if digits else ""


def parse_recipients(v: list[str] | str | None) -> list[str]:
    if isinstance(v, list):
        src = v
    else:
        src = re.split(r"[,\n;]+", str(v or ""))
    out: list[str] = []
    seen: set[str] = set()
    for item in src:
        n = _normalize_number(str(item or ""))
        if not n or n in seen:
            continue
        seen.add(n)
        out.append(n)
    return out


def send_whatsapp(to_number: str, body: str) -> tuple[bool, str]:
    to = _normalize_number(to_number)
    text = str(body or "").strip()
    if not to:
        return False, "Missing recipient number."
    if not text:
        return False, "Missing message body."

    webhook_url = str(os.getenv("WHATSAPP_WEBHOOK_URL") or "").strip()
    if webhook_url:
        try:
            resp = requests.post(
                webhook_url,
                json={"to": to, "text": text},
                timeout=20,
            )
            if 200 <= int(resp.status_code) < 300:
                return True, "sent"
            return False, f"Webhook send failed ({resp.status_code}): {resp.text[:200]}"
        except Exception as exc:
            return False, f"Webhook send failed: {exc}"

    token = str(os.getenv("WHATSAPP_ACCESS_TOKEN") or "").strip()
    phone_number_id = str(os.getenv("WHATSAPP_PHONE_NUMBER_ID") or "").strip()
    api_version = str(os.getenv("WHATSAPP_API_VERSION") or "v20.0").strip()
    if (not token) or (not phone_number_id):
        return False, "WhatsApp not configured (set WHATSAPP_ACCESS_TOKEN and WHATSAPP_PHONE_NUMBER_ID)."

    url = f"https://graph.facebook.com/{api_version}/{phone_number_id}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": text, "preview_url": False},
    }
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=20)
        if 200 <= int(resp.status_code) < 300:
            return True, "sent"
        return False, f"Meta send failed ({resp.status_code}): {resp.text[:200]}"
    except Exception as exc:
        return False, f"Meta send failed: {exc}"


def send_whatsapp_many(recipients: list[str] | str | None, body: str) -> dict:
    numbers = parse_recipients(recipients)
    sent = 0
    failed: list[dict[str, str]] = []
    for number in numbers:
        ok, msg = send_whatsapp(number, body)
        if ok:
            sent += 1
        else:
            failed.append({"to": number, "error": msg})
    return {
        "attempted": len(numbers),
        "sent": sent,
        "failed": failed,
    }
