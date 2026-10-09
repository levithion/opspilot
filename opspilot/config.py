"""Central configuration. All settings can be overridden with OPSPILOT_* env vars."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OPSPILOT_", env_file=".env", extra="ignore")

    env: Literal["dev", "prod", "test"] = "dev"
    data_dir: Path = ROOT_DIR / "data"
    kb_dir: Path = ROOT_DIR / "kb"
    template_dir: Path = ROOT_DIR / "agent_templates"
    db_path: str = ""  # empty -> <data_dir>/opspilot.db ; ":memory:" supported (tests)

    # LLM
    # Free by default: "mock" (offline) or "ollama" (local models). "anthropic"/"openai" bill per token and are
    # refused unless OPSPILOT_ALLOW_PAID_LLM=true is set explicitly.
    llm_provider: Literal["mock", "ollama", "anthropic", "openai"] = "mock"
    allow_paid_llm: bool = False
    ollama_base_url: str = "http://localhost:11434/v1"
    ollama_model: str = "llama3.2"
    anthropic_model: str = "claude-sonnet-5-5"
    openai_model: str = "gpt-4o-mini"
    llm_timeout_s: float = 30.0
    llm_max_retries: int = 2
    anthropic_api_key: str = Field(default="", validation_alias="ANTHROPIC_API_KEY")
    openai_api_key: str = Field(default="", validation_alias="OPENAI_API_KEY")

    # Auth
    auth_mode: Literal["dev", "oidc"] = "dev"
    dev_jwt_secret: str = "change-me-dev-only-change-me-dev-only"
    dev_jwt_ttl_s: int = 8 * 3600
    oidc_tenant_id: str = ""
    oidc_audience: str = ""
    oidc_issuer: str = ""  # derived from tenant when empty
    oidc_jwks_url: str = ""  # derived from tenant when empty
    # Entra app-role / group claim -> OpsPilot role
    oidc_role_claim: str = "roles"
    oidc_team_claim: str = "department"

    # RAG
    embedding_backend: Literal["hash", "sentence-transformers"] = "hash"
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    hash_dim: int = 2048
    qdrant_url: str = ""
    qdrant_api_key: str = ""
    qdrant_collection: str = "opspilot_kb"
    rag_top_k: int = 4
    rag_min_score: float = 0.12

    # Integrations
    slack_webhook_url: str = ""
    teams_webhook_url: str = ""
    ticket_api_url: str = ""
    ticket_api_token: str = ""
    http_timeout_s: float = 5.0
    http_retries: int = 3

    # Guardrails
    max_input_chars: int = 4000
    injection_block_threshold: float = 0.6
    rate_limit_per_minute: int = 30

    # Cost governance
    default_team_budget_usd: float = 50.0
    budget_warn_ratio: float = 0.8

    # Tracing
    langfuse_public_key: str = Field(default="", validation_alias="LANGFUSE_PUBLIC_KEY")
    langfuse_secret_key: str = Field(default="", validation_alias="LANGFUSE_SECRET_KEY")
    langfuse_host: str = Field(default="", validation_alias="LANGFUSE_HOST")

    log_level: str = "INFO"
    seed_demo_data: bool = True

    @property
    def resolved_db_path(self) -> str:
        if self.db_path:
            return self.db_path
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return str(self.data_dir / "opspilot.db")

    @property
    def oidc_issuer_url(self) -> str:
        return self.oidc_issuer or f"https://login.microsoftonline.com/{self.oidc_tenant_id}/v2.0"

    @property
    def oidc_jwks_uri(self) -> str:
        return self.oidc_jwks_url or (f"https://login.microsoftonline.com/{self.oidc_tenant_id}/discovery/v2.0/keys")


@lru_cache
def get_settings() -> Settings:
    return Settings()
