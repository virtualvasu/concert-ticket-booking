from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    redis_url: str
    payment_url: str = "http://payment:8001"
    admin_token: str = "admin"
    worker_interval_ms: int = 200


@lru_cache
def get_settings() -> Settings:
    return Settings()
