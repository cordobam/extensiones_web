from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

RAIZ = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=RAIZ / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "postgresql+psycopg2://postgres:postgres@localhost:5432/radar_laboral"

    dias_por_defecto: int = 2
    request_delay: float = 2.0

    perfiles_dir: Path = RAIZ / "perfiles"


@lru_cache
def get_settings() -> Settings:
    return Settings()
