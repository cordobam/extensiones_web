"""CRUD de perfiles sobre PostgreSQL.

Postgres es la fuente de verdad en runtime. El YAML en `perfiles/` es el
artefacto que se versiona y se comparte, y lo escribe esta capa cada vez que
se guarda un perfil desde la UI.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from radar.config import get_settings
from radar.db import SessionLocal
from radar.models import Match, Perfil, Postulacion
from radar.perfiles import serializacion
from radar.perfiles.validacion import PerfilCompleto


class PerfilNoEncontrado(Exception):
    pass


class PerfilDuplicado(Exception):
    pass


def _desde_fila(fila: Perfil) -> PerfilCompleto:
    return PerfilCompleto.model_validate(
        {
            "id": fila.id,
            "nombre": fila.nombre,
            "busqueda": fila.busqueda or {},
            "cv": {
                "contacto": fila.contacto or {},
                "presentacion": fila.presentacion,
                "skills": fila.skills or [],
                "experiencia": fila.experiencia or [],
                "educacion": fila.educacion or [],
                "idiomas": fila.idiomas or [],
                "certificaciones": fila.certificaciones or [],
            },
        }
    )


def _aplicar(fila: Perfil, perfil: PerfilCompleto) -> None:
    datos = serializacion.a_dict(perfil)
    cv = datos["cv"]
    fila.nombre = perfil.nombre
    fila.busqueda = datos["busqueda"]
    fila.contacto = cv["contacto"]
    fila.presentacion = cv["presentacion"]
    fila.skills = cv["skills"]
    fila.experiencia = cv["experiencia"]
    fila.educacion = cv["educacion"]
    fila.idiomas = cv["idiomas"]
    fila.certificaciones = cv["certificaciones"]


def listar(sesion: Session) -> list[PerfilCompleto]:
    filas = sesion.scalars(select(Perfil).order_by(Perfil.nombre)).all()
    return [_desde_fila(f) for f in filas]


def listar_con_activo(sesion: Session) -> tuple[list[PerfilCompleto], set[str]]:
    """Devuelve los perfiles ordenados y el conjunto de ids activos.

    Hay más de un activo a propósito: con varios perfiles cargados, la ingesta
    tiene que poder rastrear todos y no obligar a estar activando y desactivando
    a mano uno por vez.
    """
    filas = sesion.scalars(select(Perfil).order_by(Perfil.nombre)).all()
    activos = {f.id for f in filas if f.activo}
    return [_desde_fila(f) for f in filas], activos


def activos(sesion: Session) -> list[PerfilCompleto]:
    """Los perfiles activos, ordenados por nombre. Es lo que consume la ingesta."""
    filas = sesion.scalars(
        select(Perfil).where(Perfil.activo.is_(True)).order_by(Perfil.nombre)
    ).all()
    return [_desde_fila(f) for f in filas]


def obtener(sesion: Session, id_perfil: str) -> PerfilCompleto:
    fila = sesion.get(Perfil, id_perfil)
    if fila is None:
        raise PerfilNoEncontrado(f"No existe el perfil {id_perfil!r}")
    return _desde_fila(fila)


def existe(sesion: Session, id_perfil: str) -> bool:
    return sesion.get(Perfil, id_perfil) is not None


def guardar(
    sesion: Session,
    perfil: PerfilCompleto,
    directorio=None,
    escribir_yaml: bool = True,
) -> PerfilCompleto:
    """Crea o actualiza un perfil y escribe su YAML.

    `escribir_yaml=False` deja el archivo como está; útil en tests.
    """
    fila = sesion.get(Perfil, perfil.id)
    if fila is None:
        fila = Perfil(id=perfil.id, activo=False)
        sesion.add(fila)
    _aplicar(fila, perfil)
    fila.actualizado_en = datetime.now(timezone.utc)
    sesion.commit()

    if escribir_yaml:
        serializacion.escribir_disco(perfil, directorio)
    return perfil


def activar(sesion: Session, id_perfil: str) -> None:
    """Activa un perfil. No toca los demás: pueden convivir varios activos."""
    if not existe(sesion, id_perfil):
        raise PerfilNoEncontrado(f"No existe el perfil {id_perfil!r}")
    sesion.execute(update(Perfil).where(Perfil.id == id_perfil).values(activo=True))
    sesion.commit()


def desactivar(sesion: Session, id_perfil: str) -> None:
    """Desactiva un perfil. Es idempotente: desactivar uno ya inactivo no falla."""
    if not existe(sesion, id_perfil):
        raise PerfilNoEncontrado(f"No existe el perfil {id_perfil!r}")
    sesion.execute(update(Perfil).where(Perfil.id == id_perfil).values(activo=False))
    sesion.commit()


def borrar(sesion: Session, id_perfil: str, directorio=None) -> bool:
    """Borra el perfil, su YAML y todo lo que dependía de él."""
    if not existe(sesion, id_perfil):
        raise PerfilNoEncontrado(f"No existe el perfil {id_perfil!r}")

    # Las dependencias se borran explícitas: la cascada está declarada a nivel de
    # schema pero no de motor, así que no conviene depender de ella.
    sesion.query(Match).filter(Match.perfil_id == id_perfil).delete(synchronize_session="fetch")
    sesion.query(Postulacion).filter(Postulacion.perfil_id == id_perfil).delete(
        synchronize_session="fetch"
    )
    sesion.query(Perfil).filter(Perfil.id == id_perfil).delete(synchronize_session="fetch")
    sesion.commit()

    return serializacion.borrar_disco(id_perfil, directorio)


def obtener_de_sesion_nueva(id_perfil: str) -> PerfilCompleto:
    with SessionLocal() as sesion:
        return obtener(sesion, id_perfil)


@dataclass(frozen=True)
class ResultadoImportacion:
    """Qué pasó con un archivo al importar todo el directorio."""

    archivo: str
    estado: str  # "importado" | "actualizado" | "error"
    detalle: str = ""


def importar_desde_directorio(
    sesion: Session, directorio=None, escribir_yaml: bool = False
) -> list[ResultadoImportacion]:
    """Carga todos los `.yml` de `perfiles/` a PostgreSQL.

    Es el camino inverso a `guardar`: el YAML se comparte y se versiona, y en
    otra máquina se importa. Los archivos que no validen se saltean con el
    motivo, en vez de cortar la importación entera.

    Por defecto no reescribe los archivos: importarlos no debería reformatear
    un YAML que alguien editó a mano y piensa commitear.
    """
    base = directorio if directorio is not None else get_settings().perfiles_dir
    resultados: list[ResultadoImportacion] = []

    if not base.exists():
        return resultados

    for ruta in sorted(base.glob("*.yml")):
        try:
            perfil = serializacion.desde_yaml(ruta.read_text(encoding="utf-8"))
        except (OSError, ValidationError) as error:
            resultados.append(
                ResultadoImportacion(ruta.name, "error", _motivo(error))
            )
            continue

        estado = "actualizado" if existe(sesion, perfil.id) else "importado"
        guardar(sesion, perfil, directorio=base, escribir_yaml=escribir_yaml)
        resultados.append(ResultadoImportacion(ruta.name, estado, perfil.id))

    return resultados


def _motivo(error: Exception) -> str:
    """Mensaje corto y legible a partir de un error de validación."""
    if isinstance(error, ValidationError):
        partes = [
            f"{' → '.join(str(p) for p in e['loc']) or 'perfil'}: {e['msg']}"
            for e in error.errors()
        ]
        return "; ".join(dict.fromkeys(partes))
    return str(error)
