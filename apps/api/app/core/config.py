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

    @field_validator("database_url", mode="before")
    @classmethod
    def validate_database_url(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value

    @field_validator("electricity_maps_api_token", mode="before")
    @classmethod
    def validate_electricity_maps_token(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value

    @field_validator("demo_reset_token", mode="before")
    @classmethod
    def validate_demo_reset_token(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value

    @field_validator("electricity_maps_timeout_seconds")
    @classmethod
    def validate_electricity_maps_timeout(cls, value: float) -> float:
        if not 1 <= value <= 30:
            raise ValueError("Electricity Maps timeout must be between 1 and 30 seconds.")
        return value


def get_settings() -> Settings:
    """Read environment settings without retaining a plaintext credential."""
    return Settings(
        database_url=os.getenv("DATABASE_URL"),
        electricity_maps_api_token=os.getenv("ELECTRICITY_MAPS_API_TOKEN"),
        electricity_maps_timeout_seconds=os.getenv("ELECTRICITY_MAPS_TIMEOUT_SECONDS", "10"),
        demo_reset_token=os.getenv("DEMO_RESET_TOKEN"),
    )


def get_database_url() -> SecretStr | None:
    """Return the configured database URL as a redacted Pydantic secret."""
    return get_settings().database_url
