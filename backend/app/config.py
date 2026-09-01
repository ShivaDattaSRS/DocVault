from functools import lru_cache
from pathlib import Path

try:
    from pydantic_settings import BaseSettings, SettingsConfigDict
except ImportError:  # Compatibility with environments using Pydantic v1.
    from pydantic import BaseSettings

    def SettingsConfigDict(**kwargs):
        return kwargs

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    app_name: str = "DocVault"
    secret_key: str = "dev-secret-change-me"
    access_token_expire_minutes: int = 720
    frontend_origins: str = "http://localhost:8000,http://127.0.0.1:8000"

    database_url: str = "postgresql+psycopg://docvault:docvault@localhost:5432/docvault"
    redis_url: str = "redis://localhost:6379/0"

    storage_dir: str = "../storage"
    max_file_size_mb: int = 50
    default_quota_mb: int = 512

    smtp_host: str = "localhost"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "no-reply@docvault.local"
    smtp_tls: bool = True
    smtp_console_fallback: bool = True

    otp_ttl_seconds: int = 300
    otp_max_attempts: int = 5

    admin_email: str = "admin@example.com"

    @property
    def storage_path(self) -> Path:
        p = Path(self.storage_dir)
        if not p.is_absolute():
            p = (BASE_DIR / p).resolve()
        return p

    @property
    def blob_path(self) -> Path:
        return self.storage_path / "blobs"

    @property
    def thumb_path(self) -> Path:
        return self.storage_path / "thumbnails"

    @property
    def max_file_size(self) -> int:
        return self.max_file_size_mb * 1024 * 1024

    @property
    def default_quota_bytes(self) -> int:
        return self.default_quota_mb * 1024 * 1024

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.frontend_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.blob_path.mkdir(parents=True, exist_ok=True)
    s.thumb_path.mkdir(parents=True, exist_ok=True)
    return s


settings = get_settings()
