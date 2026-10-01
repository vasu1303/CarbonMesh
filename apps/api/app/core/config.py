import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, SecretStr, field_validator

load_dotenv(Path(__file__).resolve().parents[2] / ".env")


class Settings(BaseModel):
    """Validated process configuration with secrets hidden from representations."""

    model_config = ConfigDict(extra="ignore", hide_input_in_errors=True)

    database_url: SecretStr | None = None
    electricity_maps_api_token: SecretStr | None = None
    electricity_maps_timeout_seconds: float = 10.0
    demo_reset_token: SecretStr | None = None
    ai_provider: str | None = None
    ai_timeout_seconds: float = 15.0
    openai_api_key: SecretStr | None = None
    openai_model: str | None = None
    gemini_api_key: SecretStr | None = None
    gemini_model: str | None = None
    anthropic_api_key: SecretStr | None = None
    anthropic_model: str | None = None
    openrouter_api_key: SecretStr | None = None
    openrouter_model: str | None = None
    openrouter_site_url: str | None = None
    openrouter_app_name: str | None = None

    @field_validator(
        "database_url",
        "electricity_maps_api_token",
        "demo_reset_token",
        "openai_api_key",
        "gemini_api_key",
        "anthropic_api_key",
        "openrouter_api_key",
        mode="before",
    )
    @classmethod
    def normalize_optional_secret(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value

    @field_validator(
        "ai_provider",
        "openai_model",
        "gemini_model",
        "anthropic_model",
        "openrouter_model",
        "openrouter_site_url",
        "openrouter_app_name",
        mode="before",
    )
    @classmethod
    def normalize_optional_text(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value

    @field_validator("openrouter_site_url", "openrouter_app_name")
    @classmethod
    def validate_header_value(cls, value: str | None) -> str | None:
        if value is not None and ("\r" in value or "\n" in value):
            raise ValueError("OpenRouter attribution values cannot contain newlines.")
        return value

    @field_validator("electricity_maps_timeout_seconds", "ai_timeout_seconds")
    @classmethod
    def validate_external_timeout(cls, value: float) -> float:
        if not 1 <= value <= 30:
            raise ValueError("External API timeouts must be between 1 and 30 seconds.")
        return value


def get_settings() -> Settings:
    """Read environment settings without retaining a plaintext credential."""
    return Settings(
        database_url=os.getenv("DATABASE_URL"),
        electricity_maps_api_token=os.getenv("ELECTRICITY_MAPS_API_TOKEN"),
        electricity_maps_timeout_seconds=os.getenv("ELECTRICITY_MAPS_TIMEOUT_SECONDS", "10"),
        demo_reset_token=os.getenv("DEMO_RESET_TOKEN"),
        ai_provider=os.getenv("AI_PROVIDER"),
        ai_timeout_seconds=os.getenv("AI_TIMEOUT_SECONDS", "15"),
        openai_api_key=os.getenv("OPENAI_API_KEY"),
        openai_model=os.getenv("OPENAI_MODEL"),
        gemini_api_key=os.getenv("GEMINI_API_KEY"),
        gemini_model=os.getenv("GEMINI_MODEL"),
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY"),
        anthropic_model=os.getenv("ANTHROPIC_MODEL"),
        openrouter_api_key=os.getenv("OPENROUTER_API_KEY"),
        openrouter_model=os.getenv("OPENROUTER_MODEL"),
        openrouter_site_url=os.getenv("OPENROUTER_SITE_URL"),
        openrouter_app_name=os.getenv("OPENROUTER_APP_NAME"),
    )


def get_database_url() -> SecretStr | None:
    """Return the configured database URL as a redacted Pydantic secret."""
    return get_settings().database_url
