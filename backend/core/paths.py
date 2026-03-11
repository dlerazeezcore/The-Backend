from __future__ import annotations

from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
DATA_DIR = BACKEND_DIR / "data"


__all__ = ["BACKEND_DIR", "REPO_ROOT", "DATA_DIR"]
