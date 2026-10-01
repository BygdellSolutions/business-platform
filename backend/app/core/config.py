from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# The .env file lives in the repository root, shared with Docker Compose.
ROOT_DIR = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT_DIR / ".env", extra="ignore")

    database_url: str
    cors_origins: list[str] = ["http://localhost:3000"]


settings = Settings()
