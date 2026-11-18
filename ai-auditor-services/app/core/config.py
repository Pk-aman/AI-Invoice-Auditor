from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-driven settings (12-factor). No .env is read inside the container image."""

    model_config = SettingsConfigDict(extra="ignore")

    database_url: str
    incoming_dir: Path = Path("/data/incoming")
    reports_dir: Path = Path("/data/reports")
    max_upload_mb: int = 15
    cors_origins: str = "http://localhost:8501"
    log_level: str = "INFO"
    app_env: str = "dev"
    db_wait_seconds: int = 60

    @field_validator("database_url")
    @classmethod
    def _driver(cls, v: str) -> str:
        # accept the plain postgresql:// form used by the other services
        if v.startswith("postgresql://"):
            return v.replace("postgresql://", "postgresql+psycopg://", 1)
        return v

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
