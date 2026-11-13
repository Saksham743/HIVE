
from pathlib import Path
from typing import ClassVar

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central configuration for HIVE."""

    PROJECT_ROOT: ClassVar[Path] = Path(__file__).resolve().parents[2]

    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Application
    app_name: str = "HIVE"
    environment: str = "development"
    debug: bool = True

    # Storage paths
    data_dir: Path = Field(
        default_factory=lambda: Settings.PROJECT_ROOT / "data"
    )
    logs_dir: Path = Field(
        default_factory=lambda: Settings.PROJECT_ROOT / "logs"
    )
    workspaces_dir: Path = Field(
        default_factory=lambda: Settings.PROJECT_ROOT / "workspaces"
    )

    # Database
    database_url: str = Field(
        default_factory=lambda: (
            f"sqlite:///{(Settings.PROJECT_ROOT / 'data' / 'hive.db').as_posix()}"
        )
    )

    # Logging
    log_level: str = "INFO"

    def ensure_directories(self) -> None:
        """Create required application directories if missing."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.workspaces_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()