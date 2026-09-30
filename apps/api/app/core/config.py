import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, SecretStr, field_validator

load_dotenv(Path(__file__).resolve().parents[2] / ".env")


class Settings(BaseModel):
    """Validated process configuration with secrets hidden from representations."""

    model_config = ConfigDict(extra="ignore", hide_input_in_errors=True)

    database_url: SecretStr | None = None

    @field_validator("database_url", mode="before")
    @classmethod
    def validate_database_url(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value


def get_settings() -> Settings:
    """Read environment settings without retaining a plaintext credential."""
    return Settings(database_url=os.getenv("DATABASE_URL"))


def get_database_url() -> SecretStr | None:
    """Return the configured database URL as a redacted Pydantic secret."""
    return get_settings().database_url
