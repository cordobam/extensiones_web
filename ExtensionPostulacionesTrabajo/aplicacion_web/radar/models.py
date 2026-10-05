from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Oferta(Base):
    __tablename__ = "ofertas"

    id: Mapped[int] = mapped_column(primary_key=True)
    external_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    fuente: Mapped[str] = mapped_column(String(64), index=True)

    titulo: Mapped[str] = mapped_column(String(500))
    empresa: Mapped[str | None] = mapped_column(String(300), index=True)
    url: Mapped[str | None] = mapped_column(Text)
    ubicacion: Mapped[str | None] = mapped_column(String(300), index=True)
    modalidad: Mapped[str | None] = mapped_column(String(50), index=True)
    salario: Mapped[str | None] = mapped_column(String(200))

    fecha_publicacion: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )
    fecha_ingesta: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True
    )
    descripcion: Mapped[str | None] = mapped_column(Text)

    skills: Mapped[list["OfertaSkill"]] = relationship(
        back_populates="oferta", cascade="all, delete-orphan"
    )
    matches: Mapped[list["Match"]] = relationship(
        back_populates="oferta", cascade="all, delete-orphan"
    )
    postulaciones: Mapped[list["Postulacion"]] = relationship(
        back_populates="oferta", cascade="all, delete-orphan"
    )


class OfertaSkill(Base):
    __tablename__ = "ofertas_skills"
    __table_args__ = (
        UniqueConstraint("oferta_id", "skill", name="uq_oferta_skill"),
        Index("ix_ofertas_skills_skill", "skill"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    oferta_id: Mapped[int] = mapped_column(
        ForeignKey("ofertas.id", ondelete="CASCADE"), index=True
    )
    skill: Mapped[str] = mapped_column(String(120))

    oferta: Mapped[Oferta] = relationship(back_populates="skills")


class Perfil(Base):
    __tablename__ = "perfiles"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    nombre: Mapped[str] = mapped_column(String(200))
    activo: Mapped[bool] = mapped_column(default=False, index=True)

    busqueda: Mapped[dict] = mapped_column(JSON, default=dict)
    contacto: Mapped[dict] = mapped_column(JSON, default=dict)
    presentacion: Mapped[str | None] = mapped_column(Text)
    skills: Mapped[list] = mapped_column(JSON, default=list)
    experiencia: Mapped[list] = mapped_column(JSON, default=list)
    educacion: Mapped[list] = mapped_column(JSON, default=list)
    idiomas: Mapped[list] = mapped_column(JSON, default=list)
    certificaciones: Mapped[list] = mapped_column(JSON, default=list)

    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    actualizado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    matches: Mapped[list["Match"]] = relationship(
        back_populates="perfil", cascade="all, delete-orphan"
    )
    postulaciones: Mapped[list["Postulacion"]] = relationship(
        back_populates="perfil", cascade="all, delete-orphan"
    )


class Match(Base):
    __tablename__ = "matches"
    __table_args__ = (
        UniqueConstraint("oferta_id", "perfil_id", name="uq_match_oferta_perfil"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    oferta_id: Mapped[int] = mapped_column(
        ForeignKey("ofertas.id", ondelete="CASCADE"), index=True
    )
    perfil_id: Mapped[str] = mapped_column(
        ForeignKey("perfiles.id", ondelete="CASCADE"), index=True
    )

    nivel: Mapped[str] = mapped_column(String(20), index=True)

    # El puntaje vive acá y no sólo dentro de `detalle` porque la lista del
    # dashboard ordena por él y paginatea. Ordenar por `detalle->>'puntaje'`
    # obliga a castear el JSON, que no se indexa y obliga a ordenar todas las
    # filas en cada página de resultados.
    puntaje: Mapped[int] = mapped_column(Integer, default=0, index=True)

    obligatorios_ok: Mapped[int] = mapped_column(Integer, default=0)
    obligatorios_total: Mapped[int] = mapped_column(Integer, default=0)
    deseables_ok: Mapped[int] = mapped_column(Integer, default=0)
    deseables_total: Mapped[int] = mapped_column(Integer, default=0)
    detalle: Mapped[dict] = mapped_column(JSON, default=dict)

    evaluado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )

    oferta: Mapped[Oferta] = relationship(back_populates="matches")
    perfil: Mapped[Perfil] = relationship(back_populates="matches")


class Postulacion(Base):
    """Estado de una postulación, y el embudo que la mueve.

    Los estados no son "descartada / posta": son los de `radar.postulaciones.
    estados`. Acá sólo queda el default, que es `pendiente` -- una postulación
    nace cuando la marcás, no cuando la mandás. `fecha_postulacion` se llena
    cuando pasa a `enviada`, y no antes: es la diferencia entre "la tengo en
    mira" y "la mandé", y son dos hechos que no salen de la misma fecha.
    """

    __tablename__ = "postulaciones"
    __table_args__ = (
        UniqueConstraint("oferta_id", "perfil_id", name="uq_postulacion_oferta_perfil"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    oferta_id: Mapped[int] = mapped_column(
        ForeignKey("ofertas.id", ondelete="CASCADE"), index=True
    )
    perfil_id: Mapped[str] = mapped_column(
        ForeignKey("perfiles.id", ondelete="CASCADE"), index=True
    )

    estado: Mapped[str] = mapped_column(String(40), default="pendiente", index=True)
    fecha_postulacion: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notas: Mapped[str | None] = mapped_column(Text)
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    oferta: Mapped[Oferta] = relationship(back_populates="postulaciones")
    perfil: Mapped[Perfil] = relationship(back_populates="postulaciones")
