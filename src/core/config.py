from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Always resolve .env from the repo root so `uv run` works from apps/ or elsewhere.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_ENV_FILE = _REPO_ROOT / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE) if _ENV_FILE.is_file() else ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # --- Domain loading ---
    enabled_domains: str = Field(
        default="aviation,shipping",
        description=(
            "Comma-separated domain ids to load "
            "(e.g. 'aviation,shipping'). "
            "Controls which adapters (and domain solvers) are registered."
        ),
    )

    # --- Postgres (PostGIS live store + pgvector) ---
    postgres_dsn: str = Field(
        default="postgresql://sim:sim@localhost:5433/sim",
        description="asyncpg-compatible DSN for the Postgres instance (PostGIS + pgvector).",
    )

    # --- Startup dependency waits (OpenShift race: API vs Postgres/Neo4j) ---
    startup_db_wait_seconds: float = Field(
        default=300.0,
        description="How long lifespan retries Postgres/Neo4j before failing startup.",
    )
    startup_db_wait_interval_seconds: float = Field(
        default=2.0,
        description="Delay between Postgres/Neo4j readiness retries during startup.",
    )

    # --- Neo4j (property graph) ---
    neo4j_uri: str = Field(
        default="bolt://localhost:7687",
        description="Bolt URI for the Neo4j instance.",
    )
    neo4j_user: str = Field(
        default="neo4j",
        description="Neo4j username.",
    )
    neo4j_password: str = Field(
        default="",
        description="Neo4j password.",
    )

    # --- LLM backend selector ---
    llm_backend: str = Field(
        default="openai",
        description=(
            "Which LLM client to use: 'openai' (default, any OpenAI-compatible "
            "endpoint), 'llamastack' (Llama Stack SDK), or 'fake' (in-memory stub)."
        ),
    )

    # --- Inference endpoint (openai and llamastack backends) ---
    llm_base_url: str = Field(
        default="https://api.openai.com/v1",
        description=(
            "Base URL for the OpenAI-compatible inference endpoint. "
            "Override to point at Llama Stack: http://llamastack:8321/v1"
        ),
    )
    openai_api_key: str = Field(
        default="",
        description="API key for the inference endpoint. Set to any non-empty string for self-hosted endpoints.",
    )

    # --- Model identifiers ---
    generation_model_id: str = Field(
        default="gpt-4o-mini",
        description="Model ID for chat completion.",
    )
    embedding_model_id: str = Field(
        default="text-embedding-3-small",
        description="Model ID for text embeddings.",
    )
    embedding_dimension: int = Field(
        default=1536,
        description="Vector dimension of the chosen embedding model.",
    )

    @field_validator("enabled_domains", mode="before")
    @classmethod
    def _normalize_enabled_domains(cls, value: object) -> str:
        if value is None:
            return "aviation"
        if isinstance(value, (list, tuple)):
            return ",".join(str(v).strip() for v in value if str(v).strip())
        return str(value)

    @property
    def parsed_enabled_domains(self) -> list[str]:
        """Normalized list of enabled domain ids (lowercased, stripped)."""
        return [
            part.strip().lower()
            for part in self.enabled_domains.split(",")
            if part.strip()
        ]
