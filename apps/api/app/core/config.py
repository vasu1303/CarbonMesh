import os
from decimal import Decimal
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

load_dotenv(Path(__file__).resolve().parents[2] / ".env")


class Settings(BaseModel):
    """Validated process configuration with secrets hidden from representations."""

    model_config = ConfigDict(extra="ignore", hide_input_in_errors=True)

    database_url: SecretStr | None = None
    electricity_maps_api_token: SecretStr | None = None
    electricity_maps_live_enabled: bool = False
    electricity_maps_timeout_seconds: float = 10.0
    demo_reset_token: SecretStr | None = None
    ai_provider: str | None = None
    ai_timeout_seconds: float = 15.0
    agent_recovery_interval_seconds: float = 30.0
    ai_energy_wh_per_1k_tokens: Decimal = Decimal("0.3")
    ai_grid_intensity_gco2e_per_kwh: Decimal = Decimal(475)
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
    embedding_provider: Literal["openai", "hash"] = "openai"
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: Literal[768] = 768
    embedding_timeout_seconds: float = 15.0
    source_storage_dir: Path = Path(__file__).resolve().parents[2] / ".data" / "sources"
    cors_origins: list[str] = Field(default_factory=list)
    telemetry_enabled: bool = False
    telemetry_otlp_endpoint: str | None = None
    telemetry_service_name: str = "carbonmesh-api"

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

    @field_validator(
        "electricity_maps_timeout_seconds", "ai_timeout_seconds", "embedding_timeout_seconds"
    )
    @classmethod
    def validate_external_timeout(cls, value: float) -> float:
        if not 1 <= value <= 30:
            raise ValueError("External API timeouts must be between 1 and 30 seconds.")
        return value

    @field_validator("agent_recovery_interval_seconds")
    @classmethod
    def validate_agent_recovery_interval(cls, value: float) -> float:
        if not 1 <= value <= 3600:
            raise ValueError("Agent recovery interval must be between 1 and 3600 seconds.")
        return value

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip().rstrip("/") for item in value.split(",") if item.strip()]
        return value

    @field_validator("cors_origins")
    @classmethod
    def validate_origins(cls, values: list[str]) -> list[str]:
        from urllib.parse import urlsplit

        for value in values:
            url = urlsplit(value)
            if (url.scheme not in {"http", "https"} or not url.hostname or "*" in url.netloc
                    or url.username or url.password or url.path or url.query or url.fragment):
                raise ValueError("CORS origins must be explicit HTTP(S) origins without paths.")
        return values

    @field_validator(
        "ai_energy_wh_per_1k_tokens",
        "ai_grid_intensity_gco2e_per_kwh",
    )
    @classmethod
    def validate_sustainability_proxy_assumption(cls, value: Decimal) -> Decimal:
        if not value.is_finite() or value < 0:
            raise ValueError("Sustainability proxy assumptions must be finite and nonnegative.")
        return value


def get_settings() -> Settings:
    """Read environment settings without retaining a plaintext credential."""
    return Settings(
        database_url=os.getenv("DATABASE_URL"),
        electricity_maps_api_token=os.getenv("ELECTRICITY_MAPS_API_TOKEN"),
        electricity_maps_live_enabled=os.getenv("ELECTRICITY_MAPS_LIVE_ENABLED", "false"),
        electricity_maps_timeout_seconds=os.getenv("ELECTRICITY_MAPS_TIMEOUT_SECONDS", "10"),
        demo_reset_token=os.getenv("DEMO_RESET_TOKEN"),
        ai_provider=os.getenv("AI_PROVIDER"),
        ai_timeout_seconds=os.getenv("AI_TIMEOUT_SECONDS", "15"),
        agent_recovery_interval_seconds=os.getenv(
            "AGENT_RECOVERY_INTERVAL_SECONDS", "30"
        ),
        ai_energy_wh_per_1k_tokens=os.getenv("AI_ENERGY_WH_PER_1K_TOKENS", "0.3"),
        ai_grid_intensity_gco2e_per_kwh=os.getenv(
            "AI_GRID_INTENSITY_GCO2E_PER_KWH", "475"
        ),
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
        embedding_provider=os.getenv("EMBEDDING_PROVIDER", "openai"),
        embedding_model=os.getenv("EMBEDDING_MODEL", "text-embedding-3-small"),
        embedding_timeout_seconds=os.getenv("EMBEDDING_TIMEOUT_SECONDS", "15"),
        source_storage_dir=os.getenv(
            "SOURCE_STORAGE_DIR", str(Path(__file__).resolve().parents[2] / ".data" / "sources")
        ),
        cors_origins=os.getenv("CORS_ORIGINS", ""),
        telemetry_enabled=os.getenv("TELEMETRY_ENABLED", "false"),
        telemetry_otlp_endpoint=os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"),
        telemetry_service_name=os.getenv("OTEL_SERVICE_NAME", "carbonmesh-api"),
    )


def get_database_url() -> SecretStr | None:
    """Return the configured database URL as a redacted Pydantic secret."""
    return get_settings().database_url
