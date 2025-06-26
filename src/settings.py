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

    # App settings
    host: str = Field(alias="HOST")
    port: int = Field(alias="PORT")
    admin_secret_token: str = Field(alias="ADMIN_SECRET_TOKEN")

    # Backend Server Settings
    backend_urls: list[str] = Field(
        alias="BACKEND_URLS", description="Comma-separated list of backend server URLs with ports"
    )
    allowed_hosts: list[str] = Field(
        alias="ALLOWED_HOSTS",
        default=[],
        description="Comma-separated list of allowed hosts. Defaults to allow all hosts. If empty, all hosts are allowed.",
    )

    # Load Balancing Settings
    load_balance_strategy: str = Field(
        alias="LOAD_BALANCE_STRATEGY",
        default="least_connections",
        description="Load balancing strategy: least_connections, fastest_avg_response, random",
    )

    # Connection Pool Settings
    max_connections: int = Field(alias="MAX_CONNECTIONS", description="Maximum number of connections to a single host")
    connection_timeout: int = Field(alias="CONNECTION_TIMEOUT", description="TCP connection timeout in seconds")
    keepalive_timeout: int = Field(alias="KEEPALIVE_TIMEOUT", description="TCP keepalive timeout in seconds")
    request_timeout: int = Field(alias="REQUEST_TIMEOUT", description="Full request lifecycle timeout in seconds")
    max_body_size: int = Field(alias="MAX_BODY_SIZE", description="Maximum body size in mb")

    # Health Checker Connection Pool (separate from main pool)
    health_check_interval: int = Field(alias="HEALTH_CHECK_INTERVAL", description="Health check interval in seconds")
    health_check_timeout: int = Field(alias="HEALTH_CHECK_TIMEOUT", description="Health check timeout in seconds")
    health_check_max_connections: int = Field(
        alias="HEALTH_CHECK_MAX_CONNECTIONS", description="Maximum number of connections to a single host"
    )

    # Logger Settings
    log_path: Path = Field(alias="LOG_PATH", default=ENV_PATH / "logs")
    stream_stdout: bool = Field(alias="STREAM_STDOUT", default=True)
    log_to_file: bool = Field(alias="LOG_TO_FILE", default=True)
    debug: bool = Field(alias="DEBUG")

    # Test Settings
    test_num_requests: int = Field(alias="TEST_NUM_REQUESTS")

    model_config = SettingsConfigDict(
        env_file=(f"{ENV_PATH}/.env", f"{ENV_PATH}/.secret"),
        case_sensitive=True,
        env_file_encoding="utf-8",
        frozen=True,
    )


settings = Settings()  # type: ignore
