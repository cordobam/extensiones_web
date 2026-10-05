"""Contrato común de la capa de fuentes.

Toda fuente (scraper, RSS, fixtures) devuelve objetos `Offer`. Si una fuente
cambia, sólo cambia su implementación: el normalizador, la base y el matching
no se enteran.
"""

from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class Offer(BaseModel):
    """Una oferta de empleo normalizada, independiente de su origen."""

    external_id: str = Field(
        min_length=1,
        description="Identificador estable en la fuente. Clave de dedupe.",
    )
    titulo: str
    empresa: str | None = None
    url: str | None = None
    ubicacion: str | None = None
    modalidad: str | None = Field(default=None, description="remoto, híbrido, presencial")
    salario: str | None = None
    fecha_publicacion: datetime | None = None
    descripcion: str | None = None
    skills: list[str] = Field(default_factory=list)
    fuente: str = Field(description="Identificador de la fuente, p.ej. computrabajo")
    fecha_ingesta: datetime | None = None

    @field_validator("skills", mode="before")
    @classmethod
    def _sin_skills_vacias(cls, valor: object) -> object:
        if not isinstance(valor, list):
            return valor
        return [s.strip() for s in valor if isinstance(s, str) and s.strip()]
