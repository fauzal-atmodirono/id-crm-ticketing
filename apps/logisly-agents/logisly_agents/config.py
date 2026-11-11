"""Settings and Vertex environment defaults."""

from __future__ import annotations

import os
from datetime import date
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_PKG_DIR = Path(__file__).resolve().parent
# In the container the package sits at /app/logisly_agents, with no repo above it.
_REPO_ROOT = _PKG_DIR.parents[2] if len(_PKG_DIR.parents) > 2 else _PKG_DIR


def _default_data_dir() -> Path:
    bundled = _PKG_DIR / "data"
    if bundled.is_dir():
        return bundled
    return _REPO_ROOT / "demo-data" / "logisly"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LOGISLY_", env_file=".env", extra="ignore")

    data_dir: Path = Field(default_factory=_default_data_dir)
    model: str = "gemini-3.5-flash"
    as_of: date = date(2026, 11, 9)
    # UC3 only: the product-sync notes are dated 10 Nov; UC2 expected data is pinned to as_of above.
    uc3_as_of: date = date(2026, 11, 10)
    require_confirmation: bool = False
    prd_folder_id: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()


def configure_vertex_env() -> None:
    os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "true")
    os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "lv-playground-genai")
    os.environ.setdefault("GOOGLE_CLOUD_LOCATION", "global")
