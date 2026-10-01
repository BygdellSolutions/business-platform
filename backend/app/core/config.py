from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# The .env file lives in the repository root, shared with Docker Compose.
ROOT_DIR = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT_DIR / ".env", extra="ignore")

    database_url: str
    cors_origins: list[str] = ["http://localhost:3000"]

    # Defaults fail closed: unless .env opts in, there is no dev identity.
    app_env: str = "production"
    auth_mode: str = "disabled"  # "dev" | "disabled"; real auth will add a mode here
    dev_user_email: str | None = None  # default dev identity when no X-Dev-User-Email header

    @model_validator(mode="after")
    def dev_auth_only_in_development(self) -> "Settings":
        if self.auth_mode == "dev" and self.app_env != "development":
            raise ValueError("AUTH_MODE=dev is only allowed when APP_ENV=development")
        return self


settings = Settings()
