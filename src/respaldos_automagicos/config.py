"""Application settings loaded from environment variables."""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    """Runtime configuration for RespaldosAutomagicos."""

    app_name: str = Field(default="RespaldosAutomagicos")
    app_version: str = Field(default="1.0")
    database_url: str = Field(default="sqlite:///data/respaldos_automagicos.db")
    data_dir: Path = Field(default=Path("data"))
    logs_dir: Path = Field(default=Path("logs"))
    log_level: str = Field(default="INFO")
    scheduler_tick_seconds: float = Field(default=60.0)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="RESPALDOS_",
        extra="ignore",
    )
