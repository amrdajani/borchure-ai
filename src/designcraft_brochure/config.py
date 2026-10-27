"""Local configuration. Secrets stay in the environment."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel

from designcraft_brochure.safety import require_localhost


class Settings(BaseModel):
    typesafe_api_key: str | None = None
    model: str = "jev-latest"
    copy_api_key: str | None = None
    copy_api_base: str = "https://api.openai.com/v1"
    copy_model: str | None = None
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-sonnet-5-5"
    vision_api_key: str | None = None
    vision_api_base: str = "https://api.openai.com/v1"
    vision_model: str | None = None
    max_actions: int = 60
    retry_limit: int = 2
    settle_seconds: float = 0.4
    screen_noul_min: float = 0.6
    verify_noul_min: float = 0.6
    safe_noul_low: float = 0.4
    safe_noul_medium: float = 0.6
    safe_noul_high: float = 0.75
    confidence_medium: float = 0.4
    confidence_high: float = 0.55
    task_complete_noul: float = 0.85
    support_noul: float = 0.75
    control_host: str = "127.0.0.1"
    control_port: int = 7979

    @classmethod
    def from_env(cls, env_file: Path | None = None) -> Settings:
        load_dotenv(env_file or Path.cwd() / ".env")
        copy_model = os.getenv("COPY_MODEL", "").strip() or None
        vision_model = os.getenv("VISION_MODEL", "").strip() or None
        key = os.getenv("TYPESAFE_API_KEY", "").strip() or None
        anthropic_key = os.getenv("ANTHROPIC_API_KEY", "").strip() or None
        host = os.getenv("DESIGNCRAFT_HOST", "127.0.0.1").strip() or "127.0.0.1"
        return cls(
            typesafe_api_key=key,
            model=os.getenv("TYPESAFE_DEFAULT_MODEL", "jev-latest").strip() or "jev-latest",
            copy_api_key=os.getenv("COPY_API_KEY", "").strip() or None,
            copy_api_base=os.getenv("COPY_API_BASE", "https://api.openai.com/v1").strip(),
            copy_model=copy_model,
            anthropic_api_key=anthropic_key,
            anthropic_model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5").strip() or "claude-sonnet-5-5",
            vision_api_key=os.getenv("VISION_API_KEY", "").strip() or None,
            vision_api_base=os.getenv("VISION_API_BASE", "https://api.openai.com/v1").strip(),
            vision_model=vision_model,
            max_actions=_int("BROCHURE_MAX_ACTIONS", 60),
            retry_limit=_int("BROCHURE_RETRY_LIMIT", 2),
            settle_seconds=_float("BROCHURE_SETTLE_SECONDS", 0.4),
            control_host=require_localhost(host),
            control_port=_int("DESIGNCRAFT_PORT", 7979),
        )

    @property
    def copy_enabled(self) -> bool:
        if self.anthropic_api_key:
            return True
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
