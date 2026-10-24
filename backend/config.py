"""
backend/config.py

Application settings loaded from environment variables / .env file.

Instantiate with no arguments for defaults:
    settings = Settings()

All defaults are safe for local development with mock AI and no external services.
No field uses float — all numeric types are int or bool.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Runtime configuration for EDIResolve AI backend.

    Field names map to environment variables (case-insensitive):
      database_path     ← DATABASE_PATH
      ai_provider       ← AI_PROVIDER
      ai_api_key        ← AI_API_KEY
      ai_model_id       ← AI_MODEL_ID
      three_way_enabled ← THREE_WAY_ENABLED
      max_retry_attempts← MAX_RETRY_ATTEMPTS
      watsonx_url       ← WATSONX_URL
      watsonx_project_id← WATSONX_PROJECT_ID
      ai_timeout_seconds← AI_TIMEOUT_SECONDS
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",        # Ignore unknown env vars rather than raising
        case_sensitive=False,  # DATABASE_PATH and database_path both work
    )

    database_path: str = "data/edi_resolve.db"
    ai_provider: str = "mock"         # "mock" | "mock_unavailable" | "watsonx"
    ai_api_key: str | None = None
    ai_model_id: str | None = None
    three_way_enabled: bool = True    # 850+810+861 matching (team rules doc); false = 2-way
    max_retry_attempts: int = 3       # Bounded retry limit before TECHNICAL_HOLD
    watsonx_url: str = "https://us-south.ml.cloud.ibm.com"
    watsonx_project_id: str | None = None
    ai_timeout_seconds: int = 20      # Provider call timeout before AI_UNAVAILABLE
    de_minimis_line_tolerance: Decimal = Decimal("1.00")   # rules doc 4.1: per line + header
    high_value_hold_threshold: Decimal = Decimal("500.00")  # rules doc 4.2 (per invoice)
    remittance_dir: str = "data/processed/820"  # Generated (never transmitted) EDI 820 files


def get_settings() -> Settings:
    """Fresh settings from the environment / .env on every call (cheap; test-friendly)."""
    return Settings()
