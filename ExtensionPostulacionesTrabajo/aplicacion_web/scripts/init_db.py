"""Crea el esquema en PostgreSQL. Idempotente: se puede correr las veces que haga falta."""

from sqlalchemy import create_engine, text

from radar.config import get_settings
from radar.models import Base


def init_db() -> None:
    url = get_settings().database_url
    motor = create_engine(url, future=True)
    try:
        with motor.connect() as conexion:
            conexion.execute(text("SELECT 1"))
        Base.metadata.create_all(motor)
        print("Esquema creado. Tablas:", ", ".join(sorted(Base.metadata.tables)))
    finally:
        motor.dispose()


if __name__ == "__main__":
    init_db()
