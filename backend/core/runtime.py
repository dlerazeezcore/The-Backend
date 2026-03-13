from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


DEFAULT_CORS_ORIGINS = [
    "http://localhost:5173",
    "http://localhost:3000",
    "http://localhost:4173",
    "http://127.0.0.1:5173",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:4173",
]
DEFAULT_CORS_ORIGIN_REGEX = [
    r"https://.*\.figmaiframepreview\.figma\.site",
    r"https://.*\.figma\.site",
    r"https://.*\.makeproxy-m\.figma\.site",
]


def resolve_project_root(anchor: str | Path) -> Path:
    path = Path(anchor).resolve()
    current = path.parent if path.is_file() else path
    for candidate in [current, *current.parents]:
        if (candidate / ".git").exists() or (candidate / ".env").exists():
            return candidate
    return current


def load_project_env(anchor: str | Path) -> Path:
    project_root = resolve_project_root(anchor)
    load_dotenv(dotenv_path=project_root / ".env")
    return project_root


def configure_cors(app: FastAPI) -> None:
    raw = str(os.getenv("BACKEND_CORS_ALLOW_ORIGINS") or "").strip()
    origins = [item.strip() for item in raw.split(",") if item.strip()] if raw else list(DEFAULT_CORS_ORIGINS)
    raw_regex = str(os.getenv("BACKEND_CORS_ALLOW_ORIGIN_REGEX") or "").strip()
    origin_regex = raw_regex or "|".join(DEFAULT_CORS_ORIGIN_REGEX)
    allow_credentials = origins != ["*"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_origin_regex=origin_regex,
        allow_credentials=allow_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
    )
