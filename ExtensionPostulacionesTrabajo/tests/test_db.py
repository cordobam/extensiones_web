"""Prueba de humo del esquema: inserta, lee y borra una oferta real en PostgreSQL."""

from datetime import datetime, timezone

from sqlalchemy import delete, select

from conftest import NOMBRE_TEST
from radar.config import RAIZ
from radar.db import SessionLocal
from radar.models import Match, Oferta, OfertaSkill, Perfil


EXTERNAL_ID = "F5A35EBD0F7AFE7B61373E686DCF3405"


def _limpiar() -> None:
    with SessionLocal() as sesion:
        sesion.execute(delete(Match))
        sesion.execute(delete(Oferta))
        sesion.execute(delete(Perfil))
        sesion.commit()


def test_oferta_y_match() -> None:
    _limpiar()

    with SessionLocal() as sesion:
        oferta = Oferta(
            external_id=EXTERNAL_ID,
            fuente="computrabajo",
            titulo="Senior Python Engineer",
            empresa="B&B Consultores",
            ubicacion="Retiro, Capital Federal",
            modalidad="remoto",
            fecha_publicacion=datetime.now(timezone.utc),
            descripcion="Buscamos Python, SQL y PySpark.",
            skills=[OfertaSkill(skill="Python"), OfertaSkill(skill="PySpark")],
        )
        perfil = Perfil(id="data-engineer", nombre="Data Engineer", activo=True)
        sesion.add_all([oferta, perfil])
        sesion.flush()

        sesion.add(
            Match(
                oferta_id=oferta.id,
                perfil_id=perfil.id,
                nivel="alta",
                obligatorios_ok=1,
                obligatorios_total=2,
                deseables_ok=0,
                deseables_total=1,
                detalle={"Python": "ok"},
            )
        )
        sesion.commit()

    with SessionLocal() as sesion:
        leida = sesion.scalar(
            select(Oferta).where(Oferta.external_id == "F5A35EBD0F7AFE7B61373E686DCF3405")
        )
        assert leida is not None
        assert leida.titulo == "Senior Python Engineer"
        assert sorted(s.skill for s in leida.skills) == ["PySpark", "Python"]

        match = sesion.scalar(select(Match))
        assert match is not None
        assert match.nivel == "alta"
        assert match.perfil_id == "data-engineer"

    _limpiar()

    with SessionLocal() as sesion:
        assert sesion.scalar(select(Oferta)) is None
        assert sesion.scalar(select(Perfil)) is None


# --------------------------------------------------- los tests no tocan la real


def test_los_tests_no_escriben_en_la_base_de_trabajo() -> None:
    """El engine de los tests tiene que estar en la base `_test`.

    Regresión de la Isolation: los tests usaban la base del `.env`, la misma
    que `radar ingest` llena, y sus fixtures borran ofertas, matches y perfiles
    antes y después de cada test. Cada corrida de `pytest` vaciaba la ingesta
    entera, y el síntoma hace pensar que la ingesta se rompió.

    El nombre sale del engine al que está bindeada la sesión, que es el mismo
    que usan todos los tests, y se compara contra la URL que tenía el entorno
    antes de que `conftest` la cambiara.
    """
    from sqlalchemy.engine import make_url

    from conftest import URL_DE_TRABAJO

    with SessionLocal() as sesion:
        base_de_los_tests = make_url(sesion.get_bind().url)

    assert base_de_los_tests.database == NOMBRE_TEST
    assert base_de_los_tests.database != make_url(URL_DE_TRABAJO).database


def test_la_base_de_tests_tiene_el_esquema() -> None:
    """La base de tests tiene que estar en el head de las migraciones.

    El fallo que esto tapa: `create_all` crea las tablas que faltan pero no
    agrega columnas a las que ya existen. Con una base de tests ya creada, un
    modelo nuevo dejaba el esquema viejo, y los tests morían con "no existe la
    columna postulaciones.creado_en" adentro de un test de ofertas, a nueve
    líneas del cambio que lo causó.

    Por eso el esquema sale de `alembic upgrade head` y no de `create_all`, y
    por eso esto compara el `alembic_version` de la base contra el head real de
    los archivos: si queda una migración sin correr, el `pytest` lo dice
    diciendo en `test_db`, no tres tests de ofertas más abajo.
    """
    from sqlalchemy import inspect, text

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    with SessionLocal() as sesion:
        motor = sesion.get_bind()
        inspector = inspect(motor)
        tablas = set(inspector.get_table_names())
        version = sesion.scalar(text("select version_num from alembic_version"))

    esperadas = {"ofertas", "ofertas_skills", "perfiles", "matches", "postulaciones"}
    assert esperadas <= tablas

    script = ScriptDirectory.from_config(Config(str(RAIZ / "alembic.ini")))
    assert version == script.get_current_head(), (
        f"la base de tests está en {version!r} y el head es "
        f"{script.get_current_head()!r}: falta correr una migración"
    )


def test_la_columna_nueva_llega_a_la_base_de_tests() -> None:
    """El chequeo a nivel columna, que es donde `create_all` no llegaba.

    Comparar tablas parece suficiente y no lo es: `postulaciones` existía de
    antes, así que una tabla nueva o faltante no la delata. Esto mira la columna
    que agregó la migración 0003.
    """
    from sqlalchemy import inspect

    with SessionLocal() as sesion:
        columnas = {
            c["name"] for c in inspect(sesion.get_bind()).get_columns("postulaciones")
        }

    assert {"estado", "fecha_postulacion", "notas", "creado_en"} <= columnas
