"""Environment-driven settings. Backends are addressed by docker service name, never localhost."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    services_url: str = "http://ai-auditor-services:8000"
    agent_url: str = "http://agent-service:8000"
    log_level: str = "INFO"
    max_upload_mb: int = 15
    poll_upload_s: int = 3
    poll_dash_s: int = 10
    connect_timeout_s: float = 3.0
    services_timeout_s: float = 15.0
    agent_timeout_s: float = 120.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
