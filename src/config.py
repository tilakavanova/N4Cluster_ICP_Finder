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

    # Crawler API keys
    google_places_api_key: str = ""
    yelp_fusion_api_key: str = ""
    serpapi_api_key: str = ""
    google_places_max_pages: int = 5

    # Crawling
    proxy_list: str = ""
    crawl_concurrency: int = 3
    crawl_retry_attempts: int = 3
    crawl_retry_backoff: float = 2.0
    rate_limit_per_second: float = 1.0

    # Scoring weights v2 (must sum to 100)
    weight_independent: float = 15.0
    weight_platform_dependency: float = 20.0
    weight_pos: float = 12.0
    weight_density: float = 12.0
    weight_volume: float = 15.0
    weight_cuisine_fit: float = 10.0
    weight_price_point: float = 8.0
    weight_engagement: float = 8.0
    scoring_version: int = 2

    # Crawl execution mode
    use_celery: bool = False  # Set to true only if Celery workers are running

    # Cleanup
    crawl_job_retention_days: int = 30
    stale_job_timeout_minutes: int = 60

    # LLM cost controls
    llm_daily_token_limit: int = 1_000_000  # Daily token budget across all providers

    # Seed routes (data-import endpoints — disable in production)
    allow_seed_routes: bool = False  # Set ALLOW_SEED_ROUTES=true to enable /seed/* endpoints

    # HubSpot CRM
    hubspot_api_key: str = ""
    hubspot_pipeline_id: str = ""

    # Lead notifications
    slack_webhook_url: str = ""
    alert_email: str = ""
    hot_lead_threshold: float = 75.0   # ICP score >= this with multi-location = hot
    warm_lead_threshold: float = 55.0  # ICP score >= this = warm

    # App
    secret_key: str = ""
    log_level: str = "INFO"
    debug: bool = False
    allowed_origins: str = "https://n4cluster.com,https://www.n4cluster.com"
    api_key: str = ""

    # Dashboard auth
    dashboard_username: str = "admin"
    dashboard_password: str = ""  # Must be set via env var for dashboard access

    # Tracking service (NIF-223)
    tracking_base_url: str = "https://n4cluster.com"
    tracking_fallback_url: str = "https://n4cluster.com"

    # SendGrid (NIF-219)
    sendgrid_api_key: str = ""
    sendgrid_from_email: str = ""
    sendgrid_from_name: str = "N4Cluster"
    sendgrid_webhook_signing_key: str = ""

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
    def cors_origins(self) -> list[str]:
        if self.debug:
            return ["*"]
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    @property
    def proxy_pool(self) -> list[str]:
        if not self.proxy_list:
            return []
        return [p.strip() for p in self.proxy_list.split(",") if p.strip()]


settings = Settings()
