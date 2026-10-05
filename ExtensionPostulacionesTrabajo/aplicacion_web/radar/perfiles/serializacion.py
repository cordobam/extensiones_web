"""Serialización de perfiles a YAML y control de ediciones a mano.

La UI es la única que escribe los archivos YAML. Como los archivos también se
versionan y comparten, existe el riesgo de que alguien los edite a mano y la UI
los pise en el siguiente guardado.

Para detectarlo usamos el mtime del archivo contra `perfiles.actualizado_en`,
que ya existe en la base. Si el archivo es más nuevo que el último guardado
conocido, alguien lo tocó por fuera.

La tolerancia de 2 segundos absorbe la diferencia entre la precisión de
`st_mtime` en Windows y la del timestamptz de Postgres. Sin ella, cada guardado
daría un falso positivo.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml

from radar.config import get_settings
from radar.perfiles.validacion import PerfilCompleto

TOLERANCIA_SEGUNDOS = 2.0


def ruta_yaml(id_perfil: str, directorio: Path | None = None) -> Path:
    base = directorio if directorio is not None else get_settings().perfiles_dir
    return base / f"{id_perfil}.yml"


def a_dict(perfil: PerfilCompleto) -> dict:
    """Perfil → dict plano, listo para YAML o para las columnas JSON."""
    datos = perfil.model_dump(mode="json")
    datos["busqueda"] = datos.get("busqueda") or {}
    datos["cv"] = datos.get("cv") or {}
    return datos


def desde_dict(datos: dict) -> PerfilCompleto:
    """dict → Perfil validado. Lanza `ValidationError` si algo no cierra."""
    return PerfilCompleto.model_validate(datos)


def a_yaml(perfil: PerfilCompleto) -> str:
    """Dict → texto YAML con el orden de claves del esquema y acentos."""
    return yaml.safe_dump(
        a_dict(perfil),
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        width=100,
    )


def desde_yaml(texto: str) -> PerfilCompleto:
    return desde_dict(yaml.safe_load(texto) or {})


def escribir_disco(perfil: PerfilCompleto, directorio: Path | None = None) -> Path:
    """Escribe el YAML del perfil. Crea el directorio si no existe."""
    destino = ruta_yaml(perfil.id, directorio)
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(a_yaml(perfil), encoding="utf-8")
    return destino


def borrar_disco(id_perfil: str, directorio: Path | None = None) -> bool:
    """Borra el YAML. Devuelve False si el archivo no existía."""
    destino = ruta_yaml(id_perfil, directorio)
    if not destino.exists():
        return False
    destino.unlink()
    return True


@dataclass(frozen=True)
class AvisoSobreescritura:
    """El YAML fue editado a mano después del último guardado desde la UI."""

    id_perfil: str
    mtime_archivo: datetime
    ultimo_guardado: datetime | None
    detalle: str


def _a_aware(momento: datetime) -> datetime:
    if momento.tzinfo is None:
        return momento.replace(tzinfo=timezone.utc)
    return momento


def archivo_editado_a_mano(
    id_perfil: str,
    ultimo_guardado: datetime | None,
    directorio: Path | None = None,
) -> AvisoSobreescritura | None:
    """Detecta ediciones manuales del YAML comparando mtime contra la base.

    Devuelve None si el archivo no existe, si nunca se guardó desde la UI, o si
    el archivo no es más nuevo que el último guardado conocido.
    """
    destino = ruta_yaml(id_perfil, directorio)
    if not destino.exists():
        return None
    if ultimo_guardado is None:
        return None

    mtime = datetime.fromtimestamp(destino.stat().st_mtime, tz=timezone.utc)
    guardado = _a_aware(ultimo_guardado)

    if (mtime - guardado).total_seconds() <= TOLERANCIA_SEGUNDOS:
        return None

    return AvisoSobreescritura(
        id_perfil=id_perfil,
        mtime_archivo=mtime,
        ultimo_guardado=guardado,
        detalle=(
            f"perfiles/{destino.name} fue modificado el "
            f"{mtime.strftime('%d/%m/%Y %H:%M')} a mano, después del último "
            f"guardado desde la app ({guardado.strftime('%d/%m/%Y %H:%M')}). "
            "Si guardás ahora, esos cambios se pierden."
        ),
    )
