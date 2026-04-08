"""Application configuration via environment variables or .env file."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All runtime settings — override any field with an env var of the same name."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Server
    host: str = Field(default="0.0.0.0", description="Uvicorn bind host.")
    port: int = Field(default=8000, ge=1, le=65535, description="Uvicorn bind port.")
    reload: bool = Field(default=False, description="Enable uvicorn hot-reload.")

    # Pipeline
    data_path: Path = Field(
        default=Path(__file__).parent.parent / "data" / "sales.csv",
        description="Default CSV/Parquet file validated on startup.",
    )

    # Search
    use_semantic_search: bool = Field(
        default=False,
        description=(
            "Set to true to use SentenceTransformers semantic embeddings "
            "instead of BM25 keyword search. Requires sentence-transformers."
        ),
    )
    semantic_model: str = Field(
        default="sentence-transformers/all-MiniLM-L6-v2",
        description="HuggingFace model ID used for semantic search embeddings.",
    )

    # Logging
    log_level: str = Field(
        default="INFO",
        description="Python logging level (DEBUG, INFO, WARNING, ERROR).",
    )
    log_json: bool = Field(
        default=False,
        description="Emit logs as JSON lines (structured logging).",
    )


# Singleton — imported everywhere
settings = Settings()
