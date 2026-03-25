"""Application configuration via environment variables."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Central configuration — loaded from .env or environment."""

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    # Database
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/icp_finder"
    db_pool_size: int = 20
    db_max_overflow: int = 10

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # LLM
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    llm_max_tokens: int = 4000

    # Crawling
    proxy_list: str = ""
    crawl_concurrency: int = 3
    crawl_retry_attempts: int = 3
    crawl_retry_backoff: float = 2.0
    rate_limit_per_second: float = 1.0

    # Scoring weights (must sum to 100)
    weight_independent: float = 30.0
    weight_delivery: float = 25.0
    weight_pos: float = 20.0
    weight_density: float = 15.0
    weight_reviews: float = 10.0
    scoring_version: int = 1

    # App
    secret_key: str = "change-me"
    log_level: str = "INFO"
    debug: bool = False

    @property
    def async_database_url(self) -> str:
        """Convert Render's postgres:// URL to asyncpg-compatible format."""
        url = self.database_url
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql+asyncpg://", 1)
        elif url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        return url

    @property
    def proxy_pool(self) -> list[str]:
        if not self.proxy_list:
            return []
        return [p.strip() for p in self.proxy_list.split(",") if p.strip()]


settings = Settings()
