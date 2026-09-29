import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")


def get_database_url() -> str | None:
    """Return the Neon connection string configured for the API process."""
    return os.getenv("DATABASE_URL")
