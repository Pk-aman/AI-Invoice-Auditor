"""Application settings (12-factor: environment variables only, validated at startup)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore", case_sensitive=False)

    # general
    app_env: str = "dev"
    log_level: str = "INFO"

    # backing services (docker service names by default; never localhost between containers)
    database_url: str = "postgresql://agent_rw:change-me@postgres:5432/auditor"
    erp_base_url: str = "http://mock-erp:8000"
    qdrant_url: str = "http://qdrant:6333"
    qdrant_collection: str = "invoices"
    redis_url: str = "redis://redis:6379/0"
    llm_cache_ttl_s: int = 86400

    # LLM (LiteLLM model strings). Credentials are read by LiteLLM from the environment
    # (AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_REGION_NAME) and never handled here.
    llm_reasoning_model: str = "bedrock/amazon.nova-lite-v1:0"
    llm_translation_model: str = "bedrock/amazon.nova-lite-v1:0"
    llm_generation_model: str = "bedrock/amazon.nova-lite-v1:0"
    embedding_model: str = "bedrock/amazon.titan-embed-text-v2:0"
    embedding_dim: int = 1024
    llm_temperature: float = 0.0
    llm_timeout_s: int = 60
    llm_probe: str = Field("config", description="config | off — startup check of LLM configuration")
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    aws_region_name: str = "us-east-1"
    ollama_base_url: str = "http://host.docker.internal:11434"

    # paths
    rules_path: Path = Path("/config/rules.yaml")
    incoming_dir: Path = Path("/data/incoming")
    reports_dir: Path = Path("/data/reports")

    # pipeline
    ocr_langs: str = "eng+spa+deu+fra+vie"  # Tesseract packs installed in the image (vie kept for the Vietnamese sample)
    min_chars_per_page: int = 30
    poll_interval_s: float = 5.0
    processing_concurrency: int = 1
    vendor_match_min_score: float = 0.5
    arith_tolerance: float = 0.01

    # human-in-the-loop: at most this many reject_and_revalidate rounds; the next one auto-finalizes SKIPPED_MAX_RETRIES
    max_human_checks: int = 3
    reviewers_allowed: str = ""  # optional comma-separated allow-list; empty = any valid reviewer name
    review_lock_timeout_s: float = 300.0

    # RAG
    rag_reflection: str = "on"  # on | off
    rag_cache_ttl_s: int = 3600
    ragas_min_context_relevance: float = 0.7
    ragas_min_faithfulness: float = 0.7
    ragas_min_answer_relevance: float = 0.7
    ragas_timeout_s: int = 180
    rag_top_k: int = 10            # RETRIEVAL_K: chunks fetched from Qdrant (+5 per retry)
    rag_final_context_k: int = 4   # FINAL_CONTEXT_K: chunks kept after reranking
    rag_max_retries: int = 1       # MAX_RETRIES: retries when a RAGAS score is below its threshold
    chunk_chars: int = 2000
    chunk_overlap_chars: int = 200

    # LangFuse observability (optional; everything works with it disabled). Keys come from the environment only.
    langfuse_enabled: bool = False
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"

    @property
    def reviewer_allow_list(self) -> set[str]:
        return {r.strip().lower() for r in self.reviewers_allowed.split(",") if r.strip()}

    @property
    def reflection_enabled(self) -> bool:
        return self.rag_reflection.lower() == "on"


@lru_cache
def get_settings() -> Settings:
    return Settings()
