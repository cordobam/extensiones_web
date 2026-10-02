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

    # Tope de páginas por keyword. Es una red de seguridad, no la regla: la
    # ingesta para antes cuando una página no trae ofertas nuevas. Con 5
    # keywords × 20 ofertas × 2 s de pausa, un tope muy alto puede tardar
    # minutos sin encontrar nada.
    paginas_max_por_keyword: int = 5

    perfiles_dir: Path = RAIZ / "perfiles"


@lru_cache
def get_settings() -> Settings:
    return Settings()
