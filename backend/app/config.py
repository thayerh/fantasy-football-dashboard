"""Application settings, loaded from the environment or a .env file."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo root: backend/app/config.py -> backend/app -> backend -> <root>
REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FFD_",
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    sleeper_league_id: str = ""
    sleeper_base_url: str = "https://api.sleeper.app/v1"
    sleeper_timeout_seconds: float = 10.0
    sleeper_max_retries: int = 3

    db_path: Path = Path("data/ffd.db")
    backup_dir: Path = Path("data/backups")
    backup_keep: int = 14

    # FantasyPros. The free public tier allows 50 calls a day and caps every
    # rankings response at 10 players, which is why the sync is daily and asks
    # for one ranking set per position rather than paging through a big one.
    fantasypros_key: str = ""
    fantasypros_base_url: str = "https://api.fantasypros.com/public/v2/json"
    fantasypros_scoring: str = "PPR"
    fantasypros_timeout_seconds: float = 15.0
    # Rendering the real-time ADP page is slower than any JSON call: a cold
    # browser start plus the page's own data fetch. Generous on purpose.
    fantasypros_browser_timeout_seconds: float = 45.0

    stale_after_seconds: int = 900
    static_dir: Path = Path("frontend/dist")

    def _resolve(self, value: Path) -> Path:
        """Interpret relative paths against the repo root, not the cwd.

        systemd units and ad-hoc shell invocations run from different working
        directories; without this they would silently use different databases.
        """
        return value if value.is_absolute() else REPO_ROOT / value

    @property
    def resolved_db_path(self) -> Path:
        return self._resolve(self.db_path)

    @property
    def resolved_backup_dir(self) -> Path:
        return self._resolve(self.backup_dir)

    @property
    def resolved_static_dir(self) -> Path:
        return self._resolve(self.static_dir)

    @property
    def database_url(self) -> str:
        return f"sqlite+pysqlite:///{self.resolved_db_path}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
