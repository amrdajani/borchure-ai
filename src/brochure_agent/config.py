"""Local configuration. Secrets stay in the environment."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel


class Settings(BaseModel):
    typesafe_api_key: str | None = None
    model: str = "jev-latest"
    copy_api_key: str | None = None
    copy_api_base: str = "https://api.openai.com/v1"
    copy_model: str | None = None
    vision_api_key: str | None = None
    vision_api_base: str = "https://api.openai.com/v1"
    vision_model: str | None = None
    max_actions: int = 60
    retry_limit: int = 2
    settle_seconds: float = 0.6
    screen_noul_min: float = 0.6
    verify_noul_min: float = 0.6
    safe_noul_low: float = 0.4
    safe_noul_medium: float = 0.6
    safe_noul_high: float = 0.75
    confidence_medium: float = 0.4
    confidence_high: float = 0.55
    task_complete_noul: float = 0.85
    support_noul: float = 0.75

    @classmethod
    def from_env(cls, env_file: Path | None = None) -> Settings:
        load_dotenv(env_file or Path.cwd() / ".env")
        copy_model = os.getenv("COPY_MODEL", "").strip() or None
        vision_model = os.getenv("VISION_MODEL", "").strip() or None
        key = os.getenv("TYPESAFE_API_KEY", "").strip() or None
        return cls(
            typesafe_api_key=key,
            model=os.getenv("TYPESAFE_DEFAULT_MODEL", "jev-latest").strip() or "jev-latest",
            copy_api_key=os.getenv("COPY_API_KEY", "").strip() or None,
            copy_api_base=os.getenv("COPY_API_BASE", "https://api.openai.com/v1").strip(),
            copy_model=copy_model,
            vision_api_key=os.getenv("VISION_API_KEY", "").strip() or None,
            vision_api_base=os.getenv("VISION_API_BASE", "https://api.openai.com/v1").strip(),
            vision_model=vision_model,
            max_actions=_int("BROCHURE_MAX_ACTIONS", 60),
            retry_limit=_int("BROCHURE_RETRY_LIMIT", 2),
            settle_seconds=_float("BROCHURE_SETTLE_SECONDS", 0.6),
        )

    @property
    def copy_enabled(self) -> bool:
        return bool(self.copy_api_key and self.copy_model)

    @property
    def vision_enabled(self) -> bool:
        return bool(self.vision_api_key and self.vision_model)


def _int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    return int(raw)


def _float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    return float(raw)
