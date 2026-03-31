from __future__ import annotations

import mimetypes
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List
from uuid import uuid4

import requests
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

from backend.gateway.esim_app_store import (
    ROOT_ADMIN_PHONE,
    add_super_admin,
    create_esim,
    create_user,
    delete_user,
    get_settings,
    get_user_by_id,
    get_user_by_phone,
    is_super_admin,
    list_esims,
    list_super_admins,
    list_users,
    normalize_phone,
    remove_super_admin,
    update_esim,
    update_settings,
    update_user,
)
from backend.gateway.esim_shared import (
    cache_get,
    cache_set,
)
from backend.gateway.permissions_store import _api_policy, _service_enabled
from backend.communications.twilio_whatsapp.service import send_whatsapp_many
from backend.esim.esimaccess.service import (
    is_configured as esim_access_is_configured,
    list_packages as esim_access_list_packages,
    order_profiles as esim_access_order_profiles,
    query_profiles as esim_access_query_profiles,
)
from backend.payments.fib.service import create_payment as fib_create_payment

router = APIRouter()
ROOT_ADMIN_PASSWORD = os.getenv("ESIM_ROOT_ADMIN_PASSWORD", "StrongPass123")
DEFAULT_SMDP_ADDRESS = os.getenv("ESIMACCESS_DEFAULT_SMDP", "rsp-eu.simlessly.com")

_DESTINATIONS_CACHE_TTL_SEC = 180
_COUNTRY_PLANS_CACHE_TTL_SEC = 180
_ESIM_APP_TUTORIALS_BUCKET = (
    str(os.getenv("ESIM_APP_TUTORIALS_BUCKET") or "esim-app-home-tutorials").strip() or "esim-app-home-tutorials"
)

_COUNTRY_NAME_BY_ISO: Dict[str, str] = {
    "US": "United States",
    "GB": "United Kingdom",
    "CA": "Canada",
    "AU": "Australia",
    "DE": "Germany",
    "FR": "France",
    "IT": "Italy",
    "ES": "Spain",
    "NL": "Netherlands",
    "BE": "Belgium",
    "CH": "Switzerland",
    "AT": "Austria",
    "SE": "Sweden",
    "NO": "Norway",
    "DK": "Denmark",
    "FI": "Finland",
    "PL": "Poland",
    "CZ": "Czech Republic",
    "PT": "Portugal",
    "GR": "Greece",
    "TR": "Turkey",
    "RU": "Russia",
    "UA": "Ukraine",
    "JP": "Japan",
    "CN": "China",
    "KR": "South Korea",
    "IN": "India",
    "PK": "Pakistan",
    "BD": "Bangladesh",
    "ID": "Indonesia",
    "TH": "Thailand",
    "VN": "Vietnam",
    "PH": "Philippines",
    "MY": "Malaysia",
    "SG": "Singapore",
    "NZ": "New Zealand",
    "ZA": "South Africa",
    "EG": "Egypt",
    "SA": "Saudi Arabia",
    "AE": "United Arab Emirates",
    "QA": "Qatar",
    "KW": "Kuwait",
    "BH": "Bahrain",
    "OM": "Oman",
    "JO": "Jordan",
    "LB": "Lebanon",
    "IL": "Israel",
    "BR": "Brazil",
    "MX": "Mexico",
    "AR": "Argentina",
    "CL": "Chile",
    "CO": "Colombia",
    "IQ": "Iraq",
}
_ISO_BY_COUNTRY_NAME: Dict[str, str] = {
    str(name or "").strip().lower(): code for code, name in _COUNTRY_NAME_BY_ISO.items()
}


def _supabase_storage_config() -> tuple[str, str, float]:
    url = str(os.getenv("SUPABASE_URL") or "").strip().rstrip("/")
    key = str(
        os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        or os.getenv("SUPABASE_SERVICE_KEY")
        or os.getenv("SUPABASE_ANON_KEY")
        or ""
    ).strip()
    timeout_seconds = float(str(os.getenv("SUPABASE_TIMEOUT_SECONDS") or "20").strip() or "20")
    return url, key, timeout_seconds


def _storage_object_endpoint(base_url: str, bucket: str, path: str) -> str:
    return f"{base_url}/storage/v1/object/{bucket}/{path.lstrip('/')}"


def _storage_public_url(base_url: str, bucket: str, path: str) -> str:
    return f"{base_url}/storage/v1/object/public/{bucket}/{path.lstrip('/')}"


def _storage_bucket_endpoint(base_url: str, bucket: str = "") -> str:
    suffix = f"/{bucket}" if bucket else ""
    return f"{base_url}/storage/v1/bucket{suffix}"


def _ensure_public_bucket(base_url: str, key: str, bucket: str, timeout_seconds: float) -> None:
    response = requests.post(
        _storage_bucket_endpoint(base_url),
        headers={
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        json={"id": bucket, "name": bucket, "public": True},
        timeout=timeout_seconds,
    )
    if response.status_code in {200, 201, 409}:
        return
    raise RuntimeError(f"Supabase storage bucket create failed ({response.status_code}): {response.text[:300]}")


def _tutorial_upload_path(platform: str, asset_type: str, filename: str, content_type: str) -> str:
    safe_name = (filename or "upload").strip().replace("\\", "_").replace("/", "_")
    ext = ""
    if "." in safe_name:
        ext = "." + safe_name.rsplit(".", 1)[-1].lower()
    if not ext:
        guessed = mimetypes.guess_extension(content_type or "") or ""
        ext = guessed if isinstance(guessed, str) else ""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"esim-app/home-tutorial/{platform}/{asset_type}/{stamp}-{uuid4().hex}{ext}"


def _upload_home_tutorial_asset(*, platform: str, asset_type: str, filename: str, content: bytes, content_type: str) -> str:
    base_url, key, timeout_seconds = _supabase_storage_config()
    if not base_url or not key:
        raise RuntimeError("Supabase storage is not configured. Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY.")

    path = _tutorial_upload_path(platform, asset_type, filename, content_type)
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": content_type or "application/octet-stream",
        "x-upsert": "false",
    }
    response = requests.post(
        _storage_object_endpoint(base_url, _ESIM_APP_TUTORIALS_BUCKET, path),
        headers=headers,
        data=content,
        timeout=timeout_seconds,
    )
    if response.status_code == 404 or "bucket not found" in str(response.text or "").lower():
        _ensure_public_bucket(base_url, key, _ESIM_APP_TUTORIALS_BUCKET, timeout_seconds)
        response = requests.post(
            _storage_object_endpoint(base_url, _ESIM_APP_TUTORIALS_BUCKET, path),
            headers=headers,
            data=content,
            timeout=timeout_seconds,
        )
    if response.status_code >= 400:
        raise RuntimeError(f"Supabase storage upload failed ({response.status_code}): {response.text[:300]}")
    return _storage_public_url(base_url, _ESIM_APP_TUTORIALS_BUCKET, path)


def _default_home_tutorial_settings() -> Dict[str, Any]:
    return {
        "enabled": False,
        "cardTitle": "",
        "cardSubtitle": "",
        "modalTitle": "",
        "iphone": {
            "videoUrl": "",
            "thumbnailUrl": "",
            "description": "",
            "durationLabel": "",
        },
        "android": {
            "videoUrl": "",
            "thumbnailUrl": "",
            "description": "",
            "durationLabel": "",
        },
    }


def _normalize_home_tutorial_platform_settings(value: Any) -> Dict[str, str]:
    row = value if isinstance(value, dict) else {}
    return {
        "videoUrl": str(row.get("videoUrl") or "").strip(),
        "thumbnailUrl": str(row.get("thumbnailUrl") or "").strip(),
        "description": str(row.get("description") or "").strip(),
        "durationLabel": str(row.get("durationLabel") or "").strip(),
    }


def _normalize_home_tutorial_settings(value: Any) -> Dict[str, Any]:
    row = value if isinstance(value, dict) else {}
    return {
        "enabled": bool(row.get("enabled")),
        "cardTitle": str(row.get("cardTitle") or "").strip(),
        "cardSubtitle": str(row.get("cardSubtitle") or "").strip(),
        "modalTitle": str(row.get("modalTitle") or "").strip(),
        "iphone": _normalize_home_tutorial_platform_settings(row.get("iphone")),
        "android": _normalize_home_tutorial_platform_settings(row.get("android")),
    }


def _get_home_tutorial_settings() -> Dict[str, Any]:
    settings = get_settings()
    current = _normalize_home_tutorial_settings(settings.get("homeTutorial"))
    default = _default_home_tutorial_settings()
    if current == default and settings.get("homeTutorial") != default:
        update_settings("homeTutorial", current)
    return current


def _country_name_from_iso(iso: str, fallback: str = "") -> str:
    code = str(iso or "").strip().upper()
    if code and code in _COUNTRY_NAME_BY_ISO:
        return _COUNTRY_NAME_BY_ISO[code]
    fb = str(fallback or "").strip()
    return fb or code or "Unknown"


def _country_code_from_name(name: str) -> str:
    key = str(name or "").strip().lower()
    if not key:
        return ""
    return str(_ISO_BY_COUNTRY_NAME.get(key) or "").strip().upper()


def _notify_manual_mode_whatsapp(policy: dict, message: str) -> None:
    try:
        if not isinstance(policy, dict):
            return
        if str(policy.get("sellable_mode") or "").strip().lower() != "manual":
            return
        if not bool(policy.get("notify_whatsapp_enabled")):
            return
        recipients = policy.get("notify_whatsapp_numbers") or []
        if not isinstance(recipients, list) or not recipients:
            return
        send_whatsapp_many(recipients, message)
    except Exception:
        return


def _load_catalog_cache_with_fallback() -> Dict[str, Any]:
    cache_key = "esim:app:access:catalog"
    cached = cache_get(cache_key)
    if isinstance(cached, dict):
        return cached
    items = _load_access_catalog()
    data = {"items": items, "bundles": items}
    cache_set(cache_key, data, ttl_sec=120)
    return data


def _to_int(value: object, default: int = 0) -> int:
    try:
        if value is None:
            return default
        if isinstance(value, bool):
            return int(value)
        text = str(value).strip()
        if not text:
            return default
        return int(float(text))
    except Exception:
        return default


def _access_price_divisor() -> int:
    div = _to_int(os.getenv("ESIMACCESS_PRICE_DIVISOR"), default=100)
    return max(1, div)


def _access_price_to_usd_minor(raw_value: object) -> int:
    raw = _to_int(raw_value, default=0)
    if raw <= 0:
        return 0
    return int(round(float(raw) / float(_access_price_divisor())))


def _parse_location_codes(raw: object) -> list[str]:
    text = str(raw or "").strip()
    if not text:
        return []
    out: list[str] = []
    for part in text.split(","):
        code = str(part or "").strip().upper()
        if len(code) == 2 and code.isalpha() and code not in out:
            out.append(code)
    return out


def _normalize_access_item(item: dict) -> dict | None:
    if not isinstance(item, dict):
        return None
    package_code = str(item.get("packageCode") or "").strip()
    plan_slug = str(item.get("slug") or "").strip()
    if not package_code and not plan_slug:
        return None
    plan_name = str(item.get("name") or package_code or plan_slug).strip()
    iso_list = _parse_location_codes(item.get("location"))
    country_name_hint = ""
    if len(iso_list) == 1:
        country_name_hint = _country_name_from_iso(iso_list[0], "")

    countries = [{"iso": iso, "name": country_name_hint or iso, "region": ""} for iso in iso_list]
    volume_bytes = _to_int(item.get("volume"), default=0)
    data_amount_mb = int(round(volume_bytes / (1024.0 * 1024.0))) if volume_bytes > 0 else 0
    duration = _to_int(item.get("duration"), default=0) or _to_int(item.get("unusedValidTime"), default=0)
    data_type = _to_int(item.get("dataType"), default=1)
    daily_plan = data_type == 2 or "/day" in plan_name.lower() or "daily" in plan_slug.lower()
    unlimited = data_type == 4
    provider_price_raw = _to_int(item.get("price"), default=0)
    price_minor = _access_price_to_usd_minor(provider_price_raw)
    bundle_ref = plan_slug if daily_plan and plan_slug else package_code or plan_slug

    return {
        "bundleName": f"ea::{bundle_ref}",
        "description": plan_name,
        "dataAmountMb": data_amount_mb,
        "durationDays": duration,
        "unlimited": unlimited,
        "allowanceMode": "per_day" if daily_plan else "total",
        "price": {
            "finalMinor": price_minor,
            "currency": str(item.get("currencyCode") or "USD").upper(),
        },
        "countries": countries,
        "provider": "esim_access",
        "providerBundleCode": package_code,
        "providerSlug": plan_slug,
        "providerDataType": data_type,
        "provider_price_raw": provider_price_raw,
        "provider_price_minor": price_minor,
    }


def _load_access_catalog() -> list[dict]:
    if not esim_access_is_configured():
        return []
    out: list[dict] = []
    seen_keys: set[str] = set()
    payload = {"locationCode": "", "type": "BASE", "packageCode": "", "slug": "", "iccid": ""}
    for body in (payload, {**payload, "dataType": 2}):
        response = esim_access_list_packages(body)
        if not bool(response.get("success")):
            raise ValueError(response.get("errorMsg") or response.get("errorCode") or "eSIMAccess package query failed.")
        obj = response.get("obj") if isinstance(response.get("obj"), dict) else {}
        package_list = obj.get("packageList") if isinstance(obj, dict) else []
        if not isinstance(package_list, list):
            continue
        for package in package_list:
            normalized = _normalize_access_item(package)
            if not normalized:
                continue
            key = "|".join(
                [
                    str(normalized.get("providerBundleCode") or "").strip(),
                    str(normalized.get("providerSlug") or "").strip(),
                    str(normalized.get("allowanceMode") or "").strip(),
                ]
            )
            if key in seen_keys:
                continue
            seen_keys.add(key)
            out.append(normalized)
    return out


def _flag_from_iso(iso: str) -> str:
    if not iso or len(iso) != 2 or not iso.isalpha():
        return "🌍"
    iso = iso.upper()
    return chr(127397 + ord(iso[0])) + chr(127397 + ord(iso[1]))


def _flag_for_region(name: str) -> str:
    label = (name or "").lower()
    if "europe" in label:
        return "🇪🇺"
    if "asia" in label:
        return "🌏"
    if "africa" in label:
        return "🌍"
    if "america" in label:
        return "🌎"
    if "global" in label:
        return "🌍"
    return "🌍"


def _price_from_item(item: Dict[str, Any]) -> float:
    price = item.get("price") or {}
    try:
        usd_minor = float(price.get("finalMinor") or 0)
        return max(0.0, usd_minor / 100.0)
    except Exception:
        return 0.0


def _region_name_from_item(item: Dict[str, Any]) -> str:
    raw = str(item.get("description") or item.get("name") or "").strip()
    if not raw:
        return "Regional"
    # Keep the leading region label; remove trailing plan metrics.
    # Example: "Global (120+ areas) 1GB 7Days" -> "Global (120+ areas)".
    cleaned = re.sub(r"\s+\d+\s*(gb|mb)(\s+\d+\s*days?)?.*$", "", raw, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or raw


def _region_code_from_name(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", str(name or "").strip().lower()).strip("-")
    if not base:
        base = "regional"
    return f"region-{base[:60]}"


def _build_destinations() -> List[Dict[str, Any]]:
    cached = cache_get("esim:app:destinations")
    if isinstance(cached, list):
        return cached

    data = _load_catalog_cache_with_fallback()
    items = data.get("items") or data.get("bundles") or []
    out: Dict[str, Dict[str, Any]] = {}
    region_out: Dict[str, Dict[str, Any]] = {}
    counts: Dict[str, int] = {}
    region_counts: Dict[str, int] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        price_from = _price_from_item(item)
        countries = item.get("countries") or []
        if not isinstance(countries, list):
            continue
        valid_countries = [c for c in countries if isinstance(c, dict)]
        if len(valid_countries) > 1:
            region_name = _region_name_from_item(item)
            region_code = _region_code_from_name(region_name)
            region_counts[region_code] = region_counts.get(region_code, 0) + 1
            existing_region = region_out.get(region_code)
            if existing_region:
                existing_price = float(existing_region.get("priceFrom") or 0)
                if price_from and (existing_price == 0 or price_from < existing_price):
                    existing_region["priceFrom"] = price_from
                    existing_region["price_from"] = price_from
            else:
                region_out[region_code] = {
                    "id": f"r-{len(region_out) + 1}",
                    "name": region_name,
                    "flag": _flag_for_region(region_name),
                    "priceFrom": price_from,
                    "price_from": price_from,
                    "code": region_code,
                    "iso": region_code,
                    "plansCount": 0,
                    "plans": 0,
                    "type": "regional",
                }
        for c in valid_countries:
            iso = str(c.get("iso") or "").strip().upper()
            name = _country_name_from_iso(iso, str(c.get("name") or "").strip())
            key = iso or name
            if not key:
                continue
            counts[key] = counts.get(key, 0) + 1
            existing = out.get(key)
            if existing:
                existing_price = float(existing.get("priceFrom") or 0)
                if price_from and (existing_price == 0 or price_from < existing_price):
                    existing["priceFrom"] = price_from
                continue
            is_country = len(iso) == 2 and iso.isalpha()
            out[key] = {
                "id": len(out) + 1,
                "name": name or iso,
                "flag": _flag_from_iso(iso) if is_country else _flag_for_region(name or iso),
                "priceFrom": price_from,
                "price_from": price_from,
                "code": iso if is_country else iso,
                "iso": iso if is_country else iso,
                "plansCount": 0,
                "plans": 0,
                "type": "country" if is_country else "regional",
            }
    for key, row in out.items():
        plan_count = int(counts.get(key) or 0)
        row["plansCount"] = plan_count
        row["plans"] = plan_count
        row["price_from"] = float(row.get("priceFrom") or 0)
    for key, row in region_out.items():
        plan_count = int(region_counts.get(key) or 0)
        row["plansCount"] = plan_count
        row["plans"] = plan_count
        row["price_from"] = float(row.get("priceFrom") or 0)

    result = sorted([*out.values(), *region_out.values()], key=lambda x: x.get("name") or "")
    cache_set("esim:app:destinations", result, ttl_sec=_DESTINATIONS_CACHE_TTL_SEC)
    return result


def _enrich_destination_item(raw: Any, code_map: Dict[str, Dict[str, Any]]) -> Dict[str, Any] | None:
    if isinstance(raw, str):
        key = raw.strip().upper()
        if not key:
            return None
        match = code_map.get(key)
        if match:
            return dict(match)
        is_country = len(key) == 2 and key.isalpha()
        return {
            "name": key,
            "code": key,
            "iso": key,
            "flag": _flag_from_iso(key) if is_country else _flag_for_region(key),
            "priceFrom": 0.0,
            "price_from": 0.0,
            "plansCount": 0,
            "plans": 0,
            "type": "country" if is_country else "regional",
        }

    if not isinstance(raw, dict):
        return None

    key = str(raw.get("code") or raw.get("iso") or raw.get("name") or "").strip().upper()
    if key:
        match = code_map.get(key)
        if match:
            return dict(match)

    code = str(raw.get("code") or raw.get("iso") or "").strip().upper()
    name = str(raw.get("name") or code or "Unknown").strip()
    is_country = len(code) == 2 and code.isalpha()
    price_from = float(raw.get("priceFrom") or raw.get("price_from") or 0)
    plans_count = int(raw.get("plansCount") or raw.get("plans") or 0)
    row_type = str(raw.get("type") or "").strip().lower()
    if row_type not in {"country", "regional"}:
        row_type = "country" if is_country else "regional"
    return {
        "name": name,
        "code": code or key,
        "iso": code or key,
        "flag": str(raw.get("flag") or (_flag_from_iso(code) if is_country else _flag_for_region(name)) or "🌍"),
        "priceFrom": price_from,
        "price_from": price_from,
        "plansCount": plans_count,
        "plans": plans_count,
        "type": row_type,
    }


def _build_country_plans(country_code: str) -> List[Dict[str, Any]]:
    code = str(country_code or "").strip().upper()
    if not code:
        return []
    cache_key = f"esim:app:country-plans:{code}"
    cached = cache_get(cache_key)
    if isinstance(cached, list):
        return cached

    data = _load_catalog_cache_with_fallback()
    items = data.get("items") or data.get("bundles") or []
    out = []
    for item in items:
        if not isinstance(item, dict):
            continue
        countries = item.get("countries") or []
        if not isinstance(countries, list):
            continue
        valid_countries = [c for c in countries if isinstance(c, dict)]
        # Country offers should be country-specific only (exclude regional/global bundles).
        if len(valid_countries) != 1:
            continue
        match = False
        for c in valid_countries:
            iso = str(c.get("iso") or "").strip().upper()
            if iso == code:
                match = True
                break
        if not match:
            continue

        try:
            data_mb = float(item.get("dataAmountMb") or 0)
        except Exception:
            data_mb = 0
        data_gb = round(data_mb / 1024.0, 2) if data_mb else 0
        price = _price_from_item(item)
        coverage_countries = [
            _country_name_from_iso(str(c.get("iso") or ""), str(c.get("name") or ""))
            for c in valid_countries
        ]
        out.append(
            {
                "id": item.get("bundleName") or item.get("id") or item.get("name"),
                "data": data_gb,
                "validity": int(item.get("durationDays") or 0),
                "price": price,
                "unlimited": bool(item.get("unlimited")),
                "coverageCountries": coverage_countries,
            }
        )
    cache_set(cache_key, out, ttl_sec=_COUNTRY_PLANS_CACHE_TTL_SEC)
    return out


def _build_region_plans(region_code: str) -> List[Dict[str, Any]]:
    code = str(region_code or "").strip().lower()
    if not code:
        return []
    cache_key = f"esim:app:region-plans:{code}"
    cached = cache_get(cache_key)
    if isinstance(cached, list):
        return cached

    data = _load_catalog_cache_with_fallback()
    items = data.get("items") or data.get("bundles") or []
    out: List[Dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        countries = item.get("countries") or []
        if not isinstance(countries, list):
            continue
        valid_countries = [c for c in countries if isinstance(c, dict)]
        if len(valid_countries) <= 1:
            continue
        item_region = _region_code_from_name(_region_name_from_item(item)).lower()
        if item_region != code:
            continue

        try:
            data_mb = float(item.get("dataAmountMb") or 0)
        except Exception:
            data_mb = 0
        data_gb = round(data_mb / 1024.0, 2) if data_mb else 0
        price = _price_from_item(item)
        coverage_countries = []
        seen = set()
        for c in valid_countries:
            iso = str(c.get("iso") or "").strip().upper()
            name = _country_name_from_iso(str(c.get("iso") or ""), str(c.get("name") or ""))
            if name and name not in seen:
                seen.add(name)
                coverage_countries.append(
                    {
                        "name": name,
                        "iso": iso,
                        "flag": _flag_from_iso(iso) if len(iso) == 2 and iso.isalpha() else "🌍",
                    }
                )
        out.append(
            {
                "id": item.get("bundleName") or item.get("id") or item.get("name"),
                "data": data_gb,
                "validity": int(item.get("durationDays") or 0),
                "price": price,
                "unlimited": bool(item.get("unlimited")),
                "coverageCountries": coverage_countries,
            }
        )

    cache_set(cache_key, out, ttl_sec=_COUNTRY_PLANS_CACHE_TTL_SEC)
    return out


def _extract_user_id_from_request(request: Request | None) -> str:
    if request is None:
        return ""
    auth_header = str(request.headers.get("Authorization") or "").strip()
    if not auth_header.lower().startswith("bearer "):
        return ""
    token = auth_header.split(" ", 1)[1].strip()
    if token.startswith("local-") and len(token) > 6:
        return token[6:]
    return ""


def _ensure_fib_checkout_online() -> None:
    pol = _api_policy("fib")
    if not bool(pol.get("enabled")):
        raise HTTPException(status_code=503, detail="FIB API is disabled by admin permissions.")
    if not bool(pol.get("is_online_now")):
        raise HTTPException(status_code=503, detail="FIB API is currently offline (manual/scheduled mode).")


def _find_bundle(bundle_name: str) -> Dict[str, Any] | None:
    target = str(bundle_name or "").strip()
    if not target:
        return None
    data = _load_catalog_cache_with_fallback()
    items = data.get("items") or data.get("bundles") or []
    for item in items:
        if not isinstance(item, dict):
            continue
        if str(item.get("bundleName") or "").strip() == target:
            return item
    return None


def _pick_country_from_bundle(bundle: Dict[str, Any], preferred_code: str = "") -> Dict[str, str]:
    countries = bundle.get("countries") or []
    if not isinstance(countries, list) or len(countries) == 0:
        return {"name": "Unknown", "flag": "🌍"}

    preferred_code = str(preferred_code or "").strip().upper()
    picked = None
    if preferred_code:
        for country in countries:
            if not isinstance(country, dict):
                continue
            iso = str(country.get("iso") or "").strip().upper()
            if iso == preferred_code:
                picked = country
                break
    if picked is None:
        picked = countries[0] if isinstance(countries[0], dict) else {}

    iso = str(picked.get("iso") or "").strip().upper()
    name = _country_name_from_iso(iso, str(picked.get("name") or "").strip())
    return {"name": name, "flag": _flag_from_iso(iso) if len(iso) == 2 else "🌍"}


def _activation_lpa_from_access_row(row: Dict[str, Any] | None) -> str:
    data = row if isinstance(row, dict) else {}
    raw_code = str(data.get("ac") or data.get("activationCode") or "").strip()
    if not raw_code:
        return ""
    if raw_code.upper().startswith("LPA:"):
        return raw_code
    if raw_code.startswith("1$"):
        return f"LPA:{raw_code}"

    smdp_address = str(
        data.get("smdpAddress")
        or data.get("smdp")
        or data.get("smdpServerAddress")
        or data.get("smdpServer")
        or DEFAULT_SMDP_ADDRESS
    ).strip()
    if smdp_address:
        return f"LPA:1${smdp_address}${raw_code}"
    return raw_code


def _extract_install_url_from_access_row(row: Dict[str, Any] | None) -> str:
    data = row if isinstance(row, dict) else {}
    # Prefer explicit quick-install fields over QR image URLs.
    explicit = [
        str(data.get("shortUrl") or "").strip(),
        str(data.get("quickInstallUrl") or "").strip(),
        str(data.get("installUrl") or "").strip(),
    ]
    for url in explicit:
        if url.startswith("http://") or url.startswith("https://"):
            return url

    seen: set[int] = set()
    candidates: List[str] = []

    def _walk(value: Any) -> None:
        if id(value) in seen:
            return
        seen.add(id(value))
        if isinstance(value, str):
            text = value.strip()
            if text.startswith("http://") or text.startswith("https://"):
                candidates.append(text)
            return
        if isinstance(value, dict):
            for v in value.values():
                _walk(v)
            return
        if isinstance(value, list):
            for v in value:
                _walk(v)

    _walk(data)
    for url in candidates:
        lower = url.lower()
        if ("p.qrsim.net/" in lower and not lower.endswith(".png")) or "esimsetup.apple.com/" in lower:
            return url
    for url in candidates:
        lower = url.lower()
        # If only qrsim PNG is available, convert it to page URL.
        if "p.qrsim.net/" in lower and lower.endswith(".png"):
            return url[:-4]
    return candidates[0] if candidates else ""


def _map_access_query_row(order_no: str, row: Dict[str, Any] | None) -> Dict[str, Any]:
    data = row if isinstance(row, dict) else {}
    activation_code = _activation_lpa_from_access_row(data)
    install_url = _extract_install_url_from_access_row(data)
    iccid = str(data.get("iccid") or "").strip()
    esim_status = str(data.get("esimStatus") or "").strip().upper()
    smdp_status = str(data.get("smdpStatus") or "").strip().upper()

    status = "processing"
    if esim_status in {"CANCEL", "REVOKED"}:
        status = "revoked"
    elif activation_code:
        status = "completed"

    status_msg = " ".join(part for part in [esim_status, smdp_status] if part).strip()
    return {
        "provider": "esim_access",
        "status": status,
        "statusMessage": status_msg,
        "orderReference": order_no,
        "orderNo": order_no,
        "activationCodes": [activation_code] if activation_code else [],
        "iccidList": [iccid] if iccid else [],
        "quickInstallUrl": install_url,
        "raw": data,
    }


def _poll_access_order_until_complete(order_no: str, attempts: int = 6, sleep_seconds: float = 1.0) -> Dict[str, Any]:
    target_order = str(order_no or "").strip()
    if not target_order:
        return _map_access_query_row("", {})
    for _ in range(max(0, attempts)):
        query_payload = {"orderNo": target_order, "iccid": "", "pager": {"pageNum": 1, "pageSize": 20}}
        response = esim_access_query_profiles(query_payload)
        if bool(response.get("success")):
            obj = response.get("obj") if isinstance(response.get("obj"), dict) else {}
            esim_list = obj.get("esimList") if isinstance(obj, dict) else []
            if isinstance(esim_list, list):
                for row in esim_list:
                    if not isinstance(row, dict):
                        continue
                    row_order = str(row.get("orderNo") or "").strip()
                    if not row_order or row_order != target_order:
                        continue
                    mapped = _map_access_query_row(target_order, row)
                    if mapped.get("status") == "completed":
                        return mapped
        time.sleep(max(0.0, sleep_seconds))
    return _map_access_query_row(target_order, {})


def _parse_iso_datetime(raw: str | None) -> datetime | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _apply_esim_lifecycle(esim: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(esim)
    validity_days = int(out.get("validityDays") or out.get("daysLeft") or 0)
    activated_at = _parse_iso_datetime(out.get("activatedDate"))
    if validity_days <= 0 or activated_at is None:
        return out

    now_utc = datetime.now(timezone.utc)
    elapsed_seconds = max(0.0, (now_utc - activated_at).total_seconds())
    elapsed_days = int(elapsed_seconds // 86400)
    remaining_days = max(0, validity_days - elapsed_days)

    out["validityDays"] = validity_days
    out["daysLeft"] = remaining_days
    out["status"] = "expired" if remaining_days == 0 else "active"
    return out


async def _complete_purchase(payload: Dict[str, Any], request: Request | None = None) -> Dict[str, Any]:
    body = dict(payload or {})
    user_id = str(body.get("userId") or "").strip() or _extract_user_id_from_request(request)
    if not user_id:
        raise HTTPException(status_code=400, detail="userId is required")

    user = get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    plan = body.get("plan") or {}
    if not isinstance(plan, dict):
        plan = {}
    country = body.get("country") or {}
    if not isinstance(country, dict):
        country = {}

    bundle_name = (
        str(body.get("bundleName") or "").strip()
        or str(body.get("planId") or "").strip()
        or str(plan.get("id") or "").strip()
    )
    if not bundle_name:
        raise HTTPException(status_code=400, detail="bundleName is required")

    user_name = str(user.get("name") or "").strip() or "User"
    user_phone = str(user.get("phone") or "").strip()
    if not user_phone:
        raise HTTPException(status_code=400, detail="User phone is missing")

    idempotency_key = str(body.get("idempotencyKey") or "").strip() or f"esim-app-{user_id[:8]}-{uuid4().hex[:12]}"
    if not _service_enabled("esim"):
        raise HTTPException(status_code=503, detail="eSIM service is disabled by admin permissions.")
    policy = _api_policy("esim_access")
    if not bool(policy.get("enabled")):
        raise HTTPException(status_code=503, detail="eSIMAccess API is disabled by admin permissions.")
    if not esim_access_is_configured():
        raise HTTPException(status_code=503, detail="eSIMAccess is not configured.")

    bundle = _find_bundle(bundle_name) or {}
    preferred_code = str(country.get("code") or "").strip().upper()
    bundle_country = _pick_country_from_bundle(bundle, preferred_code)
    country_name = str(country.get("name") or "").strip() or bundle_country["name"]
    country_flag = str(country.get("flag") or "").strip() or bundle_country["flag"]

    try:
        bundle_data = float(bundle.get("dataAmountMb") or 0) / 1024.0 if bundle else 0.0
    except Exception:
        bundle_data = 0.0
    try:
        bundle_validity = int(bundle.get("durationDays") or 0) if bundle else 0
    except Exception:
        bundle_validity = 0

    try:
        plan_data = float(plan.get("data") or bundle_data or 0)
    except Exception:
        plan_data = 0.0
    try:
        plan_validity = int(plan.get("validity") or bundle_validity or 0)
    except Exception:
        plan_validity = 0
    is_unlimited = bool(plan.get("unlimited")) or bool(bundle.get("unlimited"))

    if (not bool(policy.get("schedule_ok"))) or str(policy.get("sellable_mode") or "online").strip().lower() != "online":
        pending_ref = f"PND-{uuid4().hex[:10].upper()}"
        pending_msg = "eSIM selling is set to manual/offline mode by admin permissions."
        esim_name = f"{country_name} {'Unlimited' if is_unlimited else 'Travel'} Plan"
        pending_esim = create_esim(
            user_id=user_id,
            name=esim_name,
            country=country_name,
            flag=country_flag,
            data_total=0 if is_unlimited else plan_data,
            days_left=plan_validity,
            activation_code="",
            iccid="",
            order_reference=pending_ref,
            status="pending",
            activated_date="",
        )
        _notify_manual_mode_whatsapp(
            policy,
            "\n".join(
                [
                    "Tulip Bookings - eSIM app pending request",
                    f"Pending ID: {pending_ref}",
                    f"User: {user_name or '-'}",
                    f"Phone: {user_phone or '-'}",
                    f"Country: {country_name or '-'}",
                    f"Bundle: {bundle_name or '-'}",
                    f"Reason: {pending_msg}",
                ]
            ),
        )
        return {
            "pending": True,
            "message": pending_msg,
            "esim": pending_esim,
            "order": {
                "status": "pending",
                "pending": True,
                "provider": "esim_access",
                "pending_kind": "esim_manual_fulfillment",
                "reason": pending_msg,
                "orderReference": pending_ref,
            },
            "orderReference": pending_ref,
        }

    quantity = 1
    package_code = str(bundle.get("providerBundleCode") or "").strip()
    plan_slug = str(bundle.get("providerSlug") or "").strip()
    allowance_mode = str(bundle.get("allowanceMode") or "total").strip().lower()
    if not package_code and bundle_name.startswith("ea::") and allowance_mode != "per_day":
        package_code = bundle_name.split("ea::", 1)[1]
    if allowance_mode != "per_day" and not package_code:
        raise HTTPException(status_code=400, detail="Invalid eSIMAccess package code.")
    if allowance_mode == "per_day" and not plan_slug:
        raise HTTPException(status_code=400, detail="Invalid eSIMAccess day-pass slug.")

    unit_price = _to_int(bundle.get("provider_price_raw"), default=0)
    if unit_price <= 0:
        unit_price = _to_int(bundle.get("provider_price_minor"), default=0)
    if unit_price <= 0:
        unit_price = _to_int((bundle.get("price") or {}).get("finalMinor"), default=0)
    if unit_price <= 0:
        raise HTTPException(status_code=400, detail="Unable to resolve eSIMAccess package price.")

    period_num = 1
    if allowance_mode == "per_day":
        period_num = max(
            1,
            _to_int(
                body.get("periodNum") or body.get("period_num") or body.get("durationDays") or bundle.get("durationDays"),
                default=1,
            ),
        )
    order_item = {"count": quantity, "price": unit_price}
    if allowance_mode == "per_day":
        order_item["slug"] = plan_slug
        order_item["periodNum"] = period_num
    else:
        order_item["packageCode"] = package_code
    order_payload = {
        "transactionId": idempotency_key,
        "amount": unit_price * quantity * period_num,
        "packageInfoList": [order_item],
    }
    try:
        order_resp = esim_access_order_profiles(order_payload)
        if not bool(order_resp.get("success")):
            raise HTTPException(status_code=400, detail=f"Order creation failed: {order_resp}")
        order_obj = order_resp.get("obj") if isinstance(order_resp.get("obj"), dict) else {}
        order_no = str(order_obj.get("orderNo") or "").strip()
        if not order_no:
            raise HTTPException(status_code=400, detail="eSIMAccess orderNo missing.")
        order = _poll_access_order_until_complete(order_no)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Order creation failed: {exc}") from exc

    order_status = str(order.get("status") or "").strip().lower()
    if order_status != "completed":
        raise HTTPException(status_code=400, detail=f"Order not completed ({order_status or 'unknown'})")

    activation_codes = order.get("activationCodes") if isinstance(order.get("activationCodes"), list) else []
    activation_code = str(activation_codes[0] or "").strip() if activation_codes else ""
    if not activation_code:
        raise HTTPException(status_code=400, detail="Activation code missing from completed order")

    iccid_list = order.get("iccidList") if isinstance(order.get("iccidList"), list) else []
    iccid = str(iccid_list[0] or "").strip() if iccid_list else None
    order_reference = str(order.get("orderReference") or order.get("orderNo") or "").strip()

    esim_name = f"{country_name} {'Unlimited' if is_unlimited else 'Travel'} Plan"
    esim = create_esim(
        user_id=user_id,
        name=esim_name,
        country=country_name,
        flag=country_flag,
        data_total=0 if is_unlimited else plan_data,
        days_left=plan_validity,
        activation_code=activation_code,
        iccid=iccid,
        order_reference=order_reference,
        install_url=str(order.get("quickInstallUrl") or "").strip(),
    )

    return {"esim": esim, "order": order, "orderReference": order_reference}


@router.get("/api/esim-app/test-api")
async def test_api():
    endpoint = "/api/v1/open/package/list"
    if not esim_access_is_configured():
        return {
            "success": False,
            "error": "eSIMAccess is not configured.",
            "results": [
                {
                    "endpoint": endpoint,
                    "status": 503,
                    "ok": False,
                    "isJson": True,
                    "contentType": "application/json",
                    "dataPreview": {"hasItems": False, "itemCount": 0, "firstItemKeys": []},
                }
            ],
        }
    try:
        payload = esim_access_list_packages(
            {"locationCode": "", "type": "BASE", "packageCode": "", "slug": "", "iccid": ""}
        )
        obj = payload.get("obj") if isinstance(payload.get("obj"), dict) else {}
        package_list = obj.get("packageList") if isinstance(obj, dict) else []
        first = package_list[0] if isinstance(package_list, list) and package_list else {}
        return {
            "success": bool(payload.get("success")),
            "results": [
                {
                    "endpoint": endpoint,
                    "status": 200 if bool(payload.get("success")) else 400,
                    "ok": bool(payload.get("success")),
                    "isJson": True,
                    "contentType": "application/json",
                    "dataPreview": {
                        "hasItems": bool(isinstance(package_list, list) and len(package_list) > 0),
                        "itemCount": len(package_list) if isinstance(package_list, list) else 0,
                        "firstItemKeys": list(first.keys())[:10] if isinstance(first, dict) else [],
                    },
                }
            ],
        }
    except Exception as exc:
        return {
            "success": False,
            "error": str(exc),
            "results": [
                {
                    "endpoint": endpoint,
                    "status": 400,
                    "ok": False,
                    "isJson": True,
                    "contentType": "application/json",
                    "dataPreview": {"hasItems": False, "itemCount": 0, "firstItemKeys": []},
                }
            ],
        }


@router.post("/api/esim-app/signup")
async def signup(payload: Dict[str, Any]):
    phone = str(payload.get("phone") or "").strip()
    name = str(payload.get("name") or "").strip() or "User"
    if not phone:
        raise HTTPException(status_code=400, detail="Phone is required")

    existing = get_user_by_phone(phone)
    if existing:
        return {"success": False, "error": "User already exists"}

    user = create_user(phone, name)
    return {"success": True, "data": {"userId": user["id"], "phone": user["phone"], "name": user["name"], "token": f"local-{user['id']}"}}


@router.post("/api/esim-app/login")
async def login(payload: Dict[str, Any]):
    phone = str(payload.get("phone") or "").strip()
    password = str(payload.get("password") or "")
    normalized_phone = normalize_phone(phone)
    if not phone:
        raise HTTPException(status_code=400, detail="Phone is required")

    user = get_user_by_phone(phone)
    if not user:
        if normalized_phone == ROOT_ADMIN_PHONE:
            if password != ROOT_ADMIN_PASSWORD:
                return {"success": False, "error": "Invalid admin password"}
            user = create_user(normalized_phone, "Admin")
        else:
            return {"success": False, "error": "User not found"}
    elif normalized_phone == ROOT_ADMIN_PHONE and password != ROOT_ADMIN_PASSWORD:
        return {"success": False, "error": "Invalid admin password"}
    return {"success": True, "data": {"userId": user["id"], "phone": user["phone"], "name": user.get("name") or "User", "token": f"local-{user['id']}"}}


@router.post("/api/esim-app/super-admin/check")
async def super_admin_check(payload: Dict[str, Any]):
    phone = str(payload.get("phoneNumber") or "").strip()
    return {"success": True, "data": {"isSuperAdmin": is_super_admin(phone)}}


@router.get("/api/esim-app/super-admin/list")
async def super_admin_list(adminPhone: str):
    if not is_super_admin(adminPhone):
        raise HTTPException(status_code=401, detail="Unauthorized")
    return {"success": True, "data": list_super_admins()}


@router.post("/api/esim-app/super-admin/add")
async def super_admin_add(payload: Dict[str, Any]):
    admin_phone = str(payload.get("adminPhone") or "").strip()
    if not is_super_admin(admin_phone):
        raise HTTPException(status_code=401, detail="Unauthorized")
    phone = str(payload.get("phoneNumber") or "").strip()
    admins = add_super_admin(phone)
    return {"success": True, "data": admins}


@router.delete("/api/esim-app/super-admin/remove")
async def super_admin_remove(payload: Dict[str, Any]):
    admin_phone = str(payload.get("adminPhone") or "").strip()
    if not is_super_admin(admin_phone):
        raise HTTPException(status_code=401, detail="Unauthorized")
    phone = str(payload.get("phoneNumber") or "").strip()
    admins = remove_super_admin(phone)
    return {"success": True, "data": admins}


@router.get("/api/esim-app/users")
async def users_list(adminPhone: str):
    if not is_super_admin(adminPhone):
        raise HTTPException(status_code=401, detail="Unauthorized")
    admins = set(list_super_admins())
    users = []
    for user in list_users():
        u = dict(user)
        u["isAdmin"] = u.get("phone") in admins
        users.append(u)
    return {"success": True, "data": users}


@router.delete("/api/esim-app/users/{user_id}")
async def users_delete(user_id: str, adminPhone: str):
    if not is_super_admin(adminPhone):
        raise HTTPException(status_code=401, detail="Unauthorized")
    ok = delete_user(user_id)
    if not ok:
        raise HTTPException(status_code=400, detail="User not found or cannot delete root admin")
    return {"success": True, "data": {"deleted": True}}


@router.post("/api/esim-app/loyalty/grant")
async def loyalty_grant(payload: Dict[str, Any]):
    admin_phone = str(payload.get("adminPhone") or "").strip()
    if not is_super_admin(admin_phone):
        raise HTTPException(status_code=401, detail="Unauthorized")
    user_id = str(payload.get("userId") or "").strip()
    granted = bool(payload.get("granted"))
    user = get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user["loyalty"] = granted
    update_user(user)
    return {"success": True, "data": {"loyalty": bool(user.get("loyalty"))}}


@router.get("/api/esim-app/loyalty/status")
async def loyalty_status(userId: str | None = None):
    if not userId:
        return {"success": True, "data": {"hasAccess": False}}
    user = get_user_by_id(userId)
    return {"success": True, "data": {"hasAccess": bool(user and user.get("loyalty"))}}


@router.get("/api/esim-app/destinations")
async def destinations_all():
    return {"success": True, "data": _build_destinations()}


@router.get("/api/esim-app/countries")
async def countries_all():
    # Backward-compatible alias for older eSIM app builds.
    return {"success": True, "data": _build_destinations()}


@router.get("/api/esim-app/destinations/popular")
async def destinations_popular():
    settings = get_settings()
    saved = settings.get("popular") or []
    all_destinations = _build_destinations()
    code_map = {str(d.get("code") or "").upper(): d for d in all_destinations}
    code_map.update({str(d.get("iso") or "").upper(): d for d in all_destinations if str(d.get("iso") or "").strip()})
    enriched = []
    for item in saved:
        row = _enrich_destination_item(item, code_map)
        if row:
            enriched.append(row)
    if enriched != saved:
        update_settings("popular", enriched)
    return {"success": True, "data": enriched}


@router.post("/api/esim-app/destinations/popular")
async def destinations_popular_set(payload: Dict[str, Any]):
    codes = payload.get("countryCodes") or []
    if not isinstance(codes, list):
        raise HTTPException(status_code=400, detail="countryCodes must be a list")
    all_destinations = _build_destinations()
    code_map = {str(d.get("code") or "").upper(): d for d in all_destinations}
    code_map.update({str(d.get("iso") or "").upper(): d for d in all_destinations if str(d.get("iso") or "").strip()})
    popular = []
    for code in codes:
        key = str(code or "").strip().upper()
        if not key:
            continue
        match = code_map.get(key)
        if match:
            popular.append(match)
        else:
            is_country = len(key) == 2 and key.isalpha()
            popular.append(
                {
                    "name": key,
                    "code": key,
                    "iso": key,
                    "flag": _flag_from_iso(key) if is_country else _flag_for_region(key),
                    "priceFrom": 0.0,
                    "price_from": 0.0,
                    "plansCount": 0,
                    "plans": 0,
                    "type": "country" if is_country else "regional",
                }
            )
    settings = get_settings()
    settings["popular"] = popular
    update_settings("popular", popular)
    return {"success": True, "data": popular}


@router.delete("/api/esim-app/destinations/popular")
async def destinations_popular_clear():
    update_settings("popular", [])
    return {"success": True, "data": []}


@router.post("/api/esim-app/home-tutorial/upload")
async def home_tutorial_upload(
    platform: str = Form(...),
    assetType: str = Form(...),
    file: UploadFile = File(...),
):
    platform_value = str(platform or "").strip().lower()
    if platform_value not in {"iphone", "android"}:
        raise HTTPException(status_code=400, detail="platform must be one of: iphone, android")

    asset_type_value = str(assetType or "").strip().lower()
    if asset_type_value not in {"video", "thumbnail"}:
        raise HTTPException(status_code=400, detail="assetType must be one of: video, thumbnail")

    blob = await file.read()
    if not blob:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    try:
        public_url = _upload_home_tutorial_asset(
            platform=platform_value,
            asset_type=asset_type_value,
            filename=str(file.filename or "upload"),
            content=blob,
            content_type=str(file.content_type or "application/octet-stream"),
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {"success": True, "data": {"url": public_url}}


@router.get("/api/esim-app/home-tutorial/current")
async def home_tutorial_current():
    return {"success": True, "data": _get_home_tutorial_settings()}


@router.get("/api/esim-app/home-tutorial")
async def home_tutorial_get():
    return {"success": True, "data": _get_home_tutorial_settings()}


@router.post("/api/esim-app/home-tutorial")
async def home_tutorial_set(payload: Dict[str, Any]):
    data = _normalize_home_tutorial_settings(payload)
    update_settings("homeTutorial", data)
    return {"success": True, "data": data}


async def _create_fib_payment(payload: Dict[str, Any]):
    try:
        _ensure_fib_checkout_online()
        amount = int(payload.get("amount") or 0)
        if amount <= 0:
            raise HTTPException(status_code=400, detail="amount must be greater than zero.")
        description = str(payload.get("description") or "Payment").strip() or "Payment"
        return fib_create_payment(amount, description)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/esim-app/fib/create-payment")
async def create_fib_payment(payload: Dict[str, Any]):
    return await _create_fib_payment(payload)


@router.post("/other-apis-data/fib/create-payment")
async def create_fib_payment_legacy(payload: Dict[str, Any]):
    return await _create_fib_payment(payload)


@router.get("/api/esim-app/countries/{country_code}/plans")
async def country_plans(country_code: str):
    code = str(country_code or "").strip().upper()
    if not code:
        raise HTTPException(status_code=400, detail="Country code required")
    return {"success": True, "data": _build_country_plans(code)}


@router.get("/api/esim-app/regions/{region_code}/plans")
async def region_plans(region_code: str):
    code = str(region_code or "").strip().lower()
    if not code:
        raise HTTPException(status_code=400, detail="Region code required")
    return {"success": True, "data": _build_region_plans(code)}


@router.get("/api/esim-app/currency-settings/current")
async def currency_settings_current():
    settings = get_settings()
    return {"success": True, "data": settings.get("currency") or {"enableIQD": False, "exchangeRate": "1320", "markupPercent": "0"}}


@router.post("/api/esim-app/currency-settings")
async def currency_settings_update(payload: Dict[str, Any]):
    enable_iqd = bool(payload.get("enableIQD"))
    exchange_rate = str(payload.get("exchangeRate") or "1320")
    markup_percent = str(payload.get("markupPercent") or "0")
    new_settings = {"enableIQD": enable_iqd, "exchangeRate": exchange_rate, "markupPercent": markup_percent}
    update_settings("currency", new_settings)
    return {"success": True, "data": new_settings}


@router.get("/api/esim-app/whitelist-settings/current")
async def whitelist_current():
    settings = get_settings()
    return {"success": True, "data": settings.get("whitelist") or {"enabled": False, "codes": []}}


@router.post("/api/esim-app/whitelist-settings")
async def whitelist_update(payload: Dict[str, Any]):
    enabled = bool(payload.get("enabled"))
    codes = payload.get("codes") or []
    if not isinstance(codes, list):
        raise HTTPException(status_code=400, detail="codes must be a list")
    codes = [str(code).strip().upper() for code in codes if str(code).strip()]
    data = {"enabled": enabled, "codes": codes}
    update_settings("whitelist", data)
    return {"success": True, "data": data}


@router.delete("/api/esim-app/whitelist-settings")
async def whitelist_clear():
    data = {"enabled": False, "codes": []}
    update_settings("whitelist", data)
    return {"success": True, "data": data}


@router.get("/api/esim-app/my-esims")
async def my_esims(request: Request, userId: str | None = None):
    target_user_id = str(userId or "").strip() or _extract_user_id_from_request(request)
    if not target_user_id:
        return {"success": True, "data": []}

    esims = [e for e in list_esims() if str(e.get("userId") or "") == target_user_id]
    normalized = []
    for esim in esims:
        enriched = _apply_esim_lifecycle(esim)
        normalized.append(enriched)
        changes: Dict[str, Any] = {}
        if esim.get("status") != enriched.get("status"):
            changes["status"] = enriched.get("status")
        if int(esim.get("daysLeft") or 0) != int(enriched.get("daysLeft") or 0):
            changes["daysLeft"] = int(enriched.get("daysLeft") or 0)
        if int(esim.get("validityDays") or 0) != int(enriched.get("validityDays") or 0):
            changes["validityDays"] = int(enriched.get("validityDays") or 0)
        if changes and esim.get("id"):
            update_esim(str(esim.get("id")), changes)
    return {"success": True, "data": normalized}


@router.post("/api/esim-app/esims/{esim_id}/activate")
async def activate_esim(esim_id: str, payload: Dict[str, Any], request: Request):
    body = dict(payload or {})
    user_id = str(body.get("userId") or "").strip() or _extract_user_id_from_request(request)
    if not user_id:
        raise HTTPException(status_code=400, detail="userId is required")

    target = next((item for item in list_esims() if str(item.get("id") or "") == str(esim_id)), None)
    if not target:
        raise HTTPException(status_code=404, detail="eSIM not found")
    if str(target.get("userId") or "") != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")

    updates: Dict[str, Any] = {}
    order_reference = str(target.get("orderReference") or "").strip()
    current_install_url = str(target.get("installUrl") or "").strip()
    current_activation = str(target.get("activationCode") or "").strip()

    # Backfill provider install URL/LPA for older records that were stored without it.
    if order_reference and (not current_install_url or not current_activation or not current_activation.upper().startswith("LPA:")):
        try:
            order = _poll_access_order_until_complete(order_reference, attempts=2, sleep_seconds=0.5)
            quick_url = str(order.get("quickInstallUrl") or "").strip()
            activation_codes = order.get("activationCodes") if isinstance(order.get("activationCodes"), list) else []
            latest_activation = str(activation_codes[0] or "").strip() if activation_codes else ""
            if quick_url:
                updates["installUrl"] = quick_url
            if latest_activation:
                updates["activationCode"] = latest_activation
        except Exception:
            # Do not fail activation on enrichment problems.
            pass

    now_iso = datetime.utcnow().isoformat() + "Z"
    updates["installed"] = True
    updates["installedAt"] = now_iso
    updated = update_esim(esim_id, updates)
    if not updated:
        raise HTTPException(status_code=404, detail="eSIM not found")
    return {"success": True, "data": _apply_esim_lifecycle(updated)}


@router.post("/api/esim-app/esims/{esim_id}/topup")
async def topup_esim(esim_id: str, payload: Dict[str, Any], request: Request):
    body = dict(payload or {})
    user_id = str(body.get("userId") or "").strip() or _extract_user_id_from_request(request)
    if not user_id:
        raise HTTPException(status_code=400, detail="userId is required")

    target = next((item for item in list_esims() if str(item.get("id") or "") == str(esim_id)), None)
    if not target:
        raise HTTPException(status_code=404, detail="eSIM not found")
    if str(target.get("userId") or "") != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")

    plan_id = str(body.get("planId") or "").strip()
    plan_data_gb = 0.0
    plan_validity_days = 0
    plan_unlimited = False

    if plan_id:
        bundle = _find_bundle(plan_id) or {}
        if bundle:
            try:
                plan_data_gb = float(bundle.get("dataAmountMb") or 0) / 1024.0
            except Exception:
                plan_data_gb = 0.0
            try:
                plan_validity_days = int(bundle.get("durationDays") or 0)
            except Exception:
                plan_validity_days = 0
            plan_unlimited = bool(bundle.get("unlimited"))

    if plan_data_gb <= 0 and plan_validity_days <= 0 and not plan_unlimited:
        country_name = str(target.get("country") or "").strip()
        code = _country_code_from_name(country_name)
        if not code and len(country_name) == 2 and country_name.isalpha():
            code = country_name.upper()
        plans = _build_country_plans(code) if code else []
        if isinstance(plans, list) and plans:
            candidate = sorted(
                [p for p in plans if isinstance(p, dict)],
                key=lambda p: float(p.get("price") or 0) if float(p.get("price") or 0) > 0 else 10**9,
            )[0]
            try:
                plan_data_gb = float(candidate.get("data") or 0)
            except Exception:
                plan_data_gb = 0.0
            try:
                plan_validity_days = int(candidate.get("validity") or 0)
            except Exception:
                plan_validity_days = 0
            plan_unlimited = bool(candidate.get("unlimited"))

    if plan_data_gb <= 0 and plan_validity_days <= 0 and not plan_unlimited:
        raise HTTPException(status_code=400, detail="No valid top-up package available.")

    current_total = float(target.get("dataTotal") or 0)
    current_days = int(target.get("daysLeft") or 0)
    current_validity = int(target.get("validityDays") or current_days or 0)

    if plan_unlimited:
        next_total = 0.0
    elif current_total <= 0:
        next_total = max(0.0, plan_data_gb)
    else:
        next_total = current_total + max(0.0, plan_data_gb)

    next_days = current_days + max(0, plan_validity_days)
    next_validity = current_validity + max(0, plan_validity_days)

    updates: Dict[str, Any] = {
        "status": "active",
        "dataTotal": next_total,
        "daysLeft": next_days,
        "validityDays": next_validity,
    }
    if not str(target.get("activatedDate") or "").strip():
        updates["activatedDate"] = datetime.utcnow().isoformat() + "Z"

    updated = update_esim(esim_id, updates)
    if not updated:
        raise HTTPException(status_code=404, detail="eSIM not found")
    return {"success": True, "data": _apply_esim_lifecycle(updated)}


@router.post("/api/esim-app/purchase/complete")
async def purchase_complete(payload: Dict[str, Any], request: Request):
    result = await _complete_purchase(payload, request)
    data = dict(result["esim"])
    data["orderReference"] = result.get("orderReference") or ""
    if result.get("pending") is True:
        data["pending"] = True
        data["status"] = "pending"
        data["message"] = str(result.get("message") or "Pending manual fulfillment.")
    return {"success": True, "data": data}


@router.post("/api/esim-app/purchase/loyalty")
async def purchase_loyalty(payload: Dict[str, Any], request: Request):
    body = dict(payload or {})
    if not body.get("bundleName") and body.get("planId"):
        body["bundleName"] = body.get("planId")
    user_id = str(body.get("userId") or "").strip() or _extract_user_id_from_request(request)
    user = get_user_by_id(user_id) if user_id else None
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    if not bool(user.get("loyalty")):
        raise HTTPException(status_code=403, detail="Loyalty Program is not available for this account")
    body["userId"] = user_id
    result = await _complete_purchase(body, request)
    pending = bool(result.get("pending") is True)
    return {
        "success": True,
        "data": {
            "status": "pending" if pending else "ok",
            "pending": pending,
            "message": str(result.get("message") or ""),
            "orderReference": result.get("orderReference") or "",
            "esim": result.get("esim"),
        },
    }
