from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from src import ENV_PATH


class Settings(BaseSettings):
    """
    Settings for the application.
    Loads environment variables from the system environment first, then from the .env file for missing variables.
    All settings are frozen (immutable) after initialization via model_config.
    """

    # Backend Server Settings
    backend_urls: list[str] = Field(alias="BACKEND_URLS")

    # Load Balancing Settings
    load_balance_strategy: str = Field(
        alias="LOAD_BALANCE_STRATEGY",
        default="least_connections",
        description="Load balancing strategy: least_connections, fastest_avg_response, random",
    )

    # Health Check Settings

    # Connection Pool Settings
    max_connections: int = Field(alias="MAX_CONNECTIONS")
    connection_timeout: int = Field(alias="CONNECTION_TIMEOUT")
    keepalive_timeout: int = Field(alias="KEEPALIVE_TIMEOUT")

    # Health Checker Connection Pool (separate from main pool)
    health_check_interval: int = Field(alias="HEALTH_CHECK_INTERVAL")
    health_check_timeout: int = Field(alias="HEALTH_CHECK_TIMEOUT")
    health_check_max_connections: int = Field(alias="HEALTH_CHECK_MAX_CONNECTIONS")

    # Request Handling Settings
    request_timeout: int = Field(alias="REQUEST_TIMEOUT")

    # Logger Settings
    log_path: Path = Field(alias="LOG_PATH", default=Path.cwd().parent.parent / "etc" / "logs")
    stream_stdout: bool = Field(alias="STREAM_STDOUT", default=True)
    log_to_file: bool = Field(alias="LOG_TO_FILE", default=True)
    debug: bool = Field(alias="DEBUG")

    model_config = SettingsConfigDict(
        env_file=(f"{ENV_PATH}/.env",),
        case_sensitive=True,
        env_file_encoding="utf-8",
        frozen=True,
    )


settings = Settings()
