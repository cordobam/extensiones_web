"""Los tests corren contra una base aparte, nunca contra la de trabajo.

Por qué existe: hasta ahora los tests usaban la base del `.env`, la misma que
`radar ingest` llena. Sus fixtures hacen `delete` de ofertas, matches y perfiles
antes y después de cada test, así que cada corrida de `pytest` borraba la
ingesta entera. Para probar la pantalla había que volver a esperar los 9 minutos
de la ingesta, y el síntoma --la base vacía después de correr tests-- hace pensar
que la ingesta falló cuando lo que se rompió fue el test suite.

Acá se deriva el nombre de la base: la del `.env` más `_test`. No es una
configuración que alguien pueda olvidar, se deriva del mismo lugar de donde sale
la URL de verdad, así que `DATABASE_URL` apuntando a la base equivocada en la
shell tampoco lleva los tests a la base real.

Se hace al importar este archivo, y no en un fixture, porque `radar/db.py`
crea el engine al importarse: si la variable se cambiara después, el engine ya
habría salido con la base de trabajo.

El esquema sale de las migraciones, no de `create_all`. Se empezó con
`create_all`, como hace `scripts/init_db.py`, y no sirve: `create_all` crea las
tablas que faltan pero no agrega columnas a las que ya existen, así que al
cambiar un modelo la base de tests queda con el esquema viejo y los tests
fallan con "no existe la columna" en un lugar que no tiene nada que ver con el
cambio. Peor: `alembic check` no lo detecta, porque compara el modelo contra las
migraciones, no contra la base de tests. Con las migraciones, la base de tests
sale igual que la de producción, y una migración rota revienta el `pytest`.
"""

from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from radar.config import get_settings

RAIZ = Path(__file__).resolve().parent.parent

#: Nombre de la base de tests. Fijo a propósito: derivarlo de la URL real y
#: sólo cambiarle el sufijo es lo que garantiza que los tests no puedan terminar
#: apuntando a la base de trabajo.
NOMBRE_TEST = "radar_laboral_test"

#: La URL de la base de trabajo, tal como estaba antes de que este archivo la
#: cambiara. La deja el propio `_preparar`; existe para que los tests puedan
#: comprobar que no se están escribiendo en la base real.
URL_DE_TRABAJO: str | None = None


def _url_de_tests() -> str:
    """La URL de la base del `.env`, con el nombre cambiado."""
    real = make_url(get_settings().database_url)
    return real.set(database=NOMBRE_TEST).render_as_string(hide_password=False)


def _asegurar_la_base(url_tests: str) -> None:
    """Crea la base de tests si no existe. Idempotente.

    Dos cosas que no son obvias:

    - `CREATE DATABASE` no puede ir dentro de una transacción, así que el motor
      de administración necesita `isolation_level="AUTOCOMMIT"`.
    - No se puede crear una base estando conectado a ella, así que el motor de
      administración se abre contra `postgres`, que siempre existe.
    """
    url = make_url(url_tests)
    admin = create_engine(
        url.set(database="postgres"),
        isolation_level="AUTOCOMMIT",
        future=True,
    )
    try:
        with admin.connect() as conexion:
            existe = conexion.execute(
                text("select 1 from pg_database where datname = :nombre"),
                {"nombre": NOMBRE_TEST},
            ).scalar()
            if not existe:
                conexion.execute(text(f'create database "{NOMBRE_TEST}"'))
    finally:
        admin.dispose()


def _migrar() -> None:
    """Deja la base de tests en el head de las migraciones.

    El `Config()` va sin archivo a propósito: `migrations/env.py` sólo corre
    `fileConfig` si hay un archivo de configuración, y `fileConfig` apaga los
    loggers que ya existen --incluidos los de pytest-- así que cargarlo desde
    adentro de la corrida rompería los tests que assert sobre logs.
    """
    from alembic import command
    from alembic.config import Config

    config = Config()
    config.set_main_option("script_location", str(RAIZ / "migrations"))
    config.set_main_option("prepend_sys_path", str(RAIZ))
    # Sin archivo no se hereda de `alembic.ini`, y sin esto Alembic parte el
    # `prepend_sys_path` por espacios, comas y dos puntos, y avisa que lo va a
    # dejar de hacer.
    config.set_main_option("path_separator", "os")
    command.upgrade(config, "head")


def _preparar() -> None:
    global URL_DE_TRABAJO

    # Se guarda antes de sobrescribir la variable, para que un test pueda
    # comprobar que el engine de los tests no quedó apuntando a esta.
    URL_DE_TRABAJO = get_settings().database_url

    url_tests = _url_de_tests()
    _asegurar_la_base(url_tests)

    # Antes de que cualquier test importe `radar.db`. `Settings` prioriza las
    # variables de entorno sobre el `.env`, así que esto gana; y el `cache_clear`
    # hace falta porque `get_settings` está cacheada y ya se consultó arriba para
    # derivar la URL. Las migraciones leen la URL de ahí, así que tienen que
    # verse después del cambio.
    os.environ["DATABASE_URL"] = url_tests
    get_settings.cache_clear()

    _migrar()


_preparar()
