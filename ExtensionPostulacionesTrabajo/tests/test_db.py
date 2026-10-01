"""Prueba de humo del esquema: inserta, lee y borra una oferta real en PostgreSQL."""

from datetime import datetime, timezone

from sqlalchemy import delete, select

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
