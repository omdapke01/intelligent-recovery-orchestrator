"""Application configuration settings."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    APP_NAME: str = "Intelligent Recovery Orchestrator (IRO)"
    ENVIRONMENT: str = "development"
    DEBUG: bool = True

    # Database URLs
    DATABASE_URL: str = "sqlite+aiosqlite:///./iro_dev.db"
    # Postgres example: "postgresql+asyncpg://postgres:postgres@localhost:5432/iro_db"

    # Default thresholds
    DEFAULT_MAX_AUTO_RETRIES: int = 2
    DEFAULT_RECOVERY_WINDOW_SEC: int = 300

    # Redis & Distributed Locking
    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_LOCK_TTL_MS: int = 10000  # 10 seconds

    # Payment Execution Sandbox
    # Guardrail: provider execution timeout MUST be strictly less than REDIS_LOCK_TTL_MS
    PROVIDER_TIMEOUT_SEC: float = 5.0

    # Retry Policy
    RETRY_EXPONENTIAL_BASE_SEC: float = 1.0
    RETRY_MAX_BACKOFF_SEC: float = 60.0

    # Phase 5: AI Model Gateway & Router
    AI_PROVIDER: str = "mock"  # "mock", "gemini"
    AI_CONFIDENCE_THRESHOLD: float = 0.70  # 70% threshold below which recommendations escalate
    AI_GATEWAY_TIMEOUT_SEC: float = 3.0  # Strict timeout boundary on AI calls
    GEMINI_API_KEY: str | None = None
    GEMINI_MODEL_FAST: str = "gemini-2.5-flash"
    GEMINI_MODEL_REASONING: str = "gemini-2.5-pro"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
