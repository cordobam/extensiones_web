"""Esquemas de validación de los perfiles.

Cada perfil tiene dos bloques con propósitos distintos:

- `busqueda`: qué le pidés al radar (keywords, ciudades, modalidades).
- `cv`: quién sos, para el matching y para la extensión en fases futuras.

El `id` es la clave primaria del perfil y también el nombre del archivo YAML.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Annotated, Any

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    EmailStr,
    Field,
    field_validator,
    model_validator,
)

SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
ANIOS_SKILL_RE = re.compile(r"\d+", re.IGNORECASE)


def _vacio_si_ausente(valor: Any) -> Any:
    """`ubicaciones:` sin nada en YAML es None, y el usuario quiere "sin filtro".

    Convertirlo a lista vacía evita un error de validación que no le dice nada
    útil: la lista vacía es exactamente lo que quiso escribir.
    """
    return [] if valor is None else valor


def _lista_de(tipo: type) -> Any:
    return Annotated[list[tipo], BeforeValidator(_vacio_si_ausente)]


ListaStr = _lista_de(str)


class Base(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Busqueda(Base):
    """Cómo y dónde quiero que el radar busque."""

    keywords: list[str] = Field(min_length=1)
    ubicaciones: ListaStr = Field(default_factory=list)
    modalidades: ListaStr = Field(default_factory=list)
    seniority: ListaStr = Field(default_factory=list)
    excluir: ListaStr = Field(default_factory=list)

    @field_validator("keywords")
    @classmethod
    def _keywords_no_vacias(cls, valor: list[str]) -> list[str]:
        sin_vacias = [k for k in valor if k.strip()]
        if not sin_vacias:
            raise ValueError("Necesitás al menos una keyword de búsqueda")
        return sin_vacias


class Contacto(Base):
    nombre: str = Field(min_length=1)
    apellido: str = Field(min_length=1)
    email: EmailStr
    telefono: str | None = None
    ciudad: str | None = None
    linkedin: str | None = None


class SkillCv(Base):
    """Una skill del CV, con la evidencia de que la tenés.

    `evidencia` importa: es lo que después va a alimentar las respuestas de la
    extensión, y obliga a que el dato sea real en vez de una keyword suelta.
    """

    nombre: str = Field(min_length=1)
    alias: ListaStr = Field(default_factory=list)
    obligatorio: bool = False
    evidencia: str | None = None
    anios: float | None = Field(default=None, ge=0, le=60)

    @field_validator("anios", mode="before")
    @classmethod
    def _anios_desde_texto(cls, valor: object) -> object:
        """Permite escribir '3 años' o '3' y guardar 3.0."""
        if isinstance(valor, str):
            if not valor.strip():
                return None
            coincide = ANIOS_SKILL_RE.search(valor)
            if not coincide:
                raise ValueError(f"No pude interpretar los años: {valor!r}")
            return float(coincide.group())
        return valor


class Experiencia(Base):
    puesto: str = Field(min_length=1)
    empresa: str | None = None
    desde: date | None = None
    hasta: date | None = None
    descripcion: str | None = None
    skills: ListaStr = Field(default_factory=list)

    @model_validator(mode="after")
    def _rango_coherente(self) -> Experiencia:
        if self.desde and self.hasta and self.hasta < self.desde:
            raise ValueError("El fin no puede ser anterior al inicio")
        return self


class Educacion(Base):
    estudio: str = Field(min_length=1)
    institucion: str | None = None
    anio: int | None = Field(default=None, ge=1900, le=2100)


class Idioma(Base):
    idioma: str = Field(min_length=1)
    nivel: str | None = None


class Certificacion(Base):
    nombre: str = Field(min_length=1)
    emisor: str | None = None
    fecha: date | None = None


ListaSkillsCv = _lista_de(SkillCv)
ListaExperiencias = _lista_de(Experiencia)
ListaEducaciones = _lista_de(Educacion)
ListaIdiomas = _lista_de(Idioma)
ListaCertificaciones = _lista_de(Certificacion)


class Cv(Base):
    """Los datos para comparar y, más adelante, para completar formularios."""

    contacto: Contacto

    #: Lo que la extensión escribe en el campo "presentación" del formulario.
    #:
    #: Es texto fijo, escrito a mano, y no lo genera un modelo. La idea de que
    #: la presentación se armara con lo que dice cada oferta es la fase 14, que
    #: depende de la 11, que está trabada: `SkillCv.evidencia` está vacía en los
    #: perfiles, y un modelo que invente la evidencia te manda a entrevistas por
    #: cosas que no hiciste. Mientras tanto, un texto que vos controlás es
    #: mejor que uno inventado.
    #:
    #: Sin límite de largo a propósito: lo que entre en el formulario lo decide
    #: el portal, no el modelo. Si Computrabajo corta a 3000 caracteres, es un
    #: tema del adaptador de la extensión.
    presentacion: str | None = None

    skills: ListaSkillsCv = Field(default_factory=list)
    experiencia: ListaExperiencias = Field(default_factory=list)
    educacion: ListaEducaciones = Field(default_factory=list)
    idiomas: ListaIdiomas = Field(default_factory=list)
    certificaciones: ListaCertificaciones = Field(default_factory=list)

    @field_validator("skills")
    @classmethod
    def _skills_unicas(cls, valor: list[SkillCv]) -> list[SkillCv]:
        vistas: set[str] = set()
        repetidas: list[str] = []
        for skill in valor:
            clave = skill.nombre.casefold()
            if clave in vistas:
                repetidas.append(skill.nombre)
            vistas.add(clave)
        if repetidas:
            raise ValueError(f"Skills repetidas: {', '.join(sorted(set(repetidas)))}")
        return valor

    def skill_por_nombre(self, nombre: str) -> SkillCv | None:
        objetivo = nombre.casefold()
        for skill in self.skills:
            if skill.nombre.casefold() == objetivo:
                return skill
            if any(alias.casefold() == objetivo for alias in skill.alias):
                return skill
        return None


class PerfilCompleto(Base):
    """El perfil entero, tal como viaja en el archivo YAML."""

    id: str
    nombre: str = Field(min_length=1)
    busqueda: Busqueda
    cv: Cv

    @field_validator("id")
    @classmethod
    def _id_es_slug(cls, valor: str) -> str:
        if not SLUG_RE.match(valor):
            raise ValueError(
                "El id sólo puede tener minúsculas, números y guiones. Ej: data-engineer"
            )
        return valor

    @property
    def skills_obligatorias(self) -> list[SkillCv]:
        return [s for s in self.cv.skills if s.obligatorio]

    @property
    def skills_deseables(self) -> list[SkillCv]:
        return [s for s in self.cv.skills if not s.obligatorio]
