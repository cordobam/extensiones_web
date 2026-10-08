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

    # Qué portales recorre `radar ingest`, en ese orden, separados por comas.
    # Los ids son los del registro (`radar/fuentes/__init__.py`); un id que no
    # existe hace fallar la corrida entera en vez de ignorarse en silencio.
    # Para una corrida puntual está `radar ingest --fuente <id>`, que no toca
    # esto.
    fuentes_activas: str = "computrabajo,zonajobs"

    # Tope de páginas por keyword. Es una red de seguridad, no la regla: la
    # ingesta para antes cuando una página no trae ofertas nuevas. Con 5
    # keywords × 20 ofertas × 2 s de pausa, un tope muy alto puede tardar
    # minutos sin encontrar nada.
    paginas_max_por_keyword: int = 5

    # ---------------------------------------------------------------- matching
    #
    # Los pesos son porcentaje: obligatorias + deseables = 100. El reparto es
    # 75/25 y no 50/50 a propósito. Con 50/50, una oferta a la que le falta una
    # obligatoria de tres pero cumple todas las deseables saca 66, igual que
    # una que cumple las tres y no menciona ninguna deseable. 75/25 hace que
    # perder una de tres obligatorias cueste 25 puntos, que es más de lo que
    # aportan todas las deseables juntas, así que el número nunca premia una
    # oferta que le falta un requisito.
    match_peso_obligatorias: int = 75
    match_peso_deseables: int = 25

    # Ratio de deseables cubiertas a partir del cual la oferta es "alta".
    # Abajo de eso pero mayor que cero es "media", y cero es "baja".
    # Con 0.5 alcanza con la mitad de tus deseables mencionadas en el aviso.
    match_umbral_alta: float = 0.5

    perfiles_dir: Path = RAIZ / "perfiles"


@lru_cache
def get_settings() -> Settings:
    return Settings()
