from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    thestatsapi_key: str = Field(alias="THESTATSAPI_KEY")
    thestatsapi_base_url: str = Field(
        default="https://api.thestatsapi.com/api", alias="THESTATSAPI_BASE_URL"
    )
    thestatsapi_rate_limit_per_min: int = Field(
        default=120, alias="THESTATSAPI_RATE_LIMIT_PER_MIN"
    )

    database_url: str = Field(alias="DATABASE_URL")

    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # Backfill-management page (webapp/routers/backfill.py). All optional:
    # the feature only makes sense once actually deployed on Render, and
    # must degrade to a clear error rather than fail app startup when unset
    # (e.g. local dev, or before the Render services/secrets exist yet).
    render_api_key: str | None = Field(default=None, alias="RENDER_API_KEY")
    render_service_id: str | None = Field(default=None, alias="RENDER_SERVICE_ID")
    backfill_admin_token: str | None = Field(default=None, alias="BACKFILL_ADMIN_TOKEN")


@lru_cache
def get_settings() -> Settings:
    return Settings()
