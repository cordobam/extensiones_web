"""Parser de las fechas relativas que pone Computrabajo en el listado.

El portal escribe "Hace 19 horas" o "Ayer", no una fecha. Como su robots.txt
prohíbe los filtros de fecha por query (?pubdate=), no hay forma de pedirle
las ofertas recientes: hay que parsear este texto del lado nuestro.

La regla que guía todo el módulo: si no sabemos la fecha, no la inventamos.
"Hace 35 minutos" se puede parsear; "Hace un tiempo" no, y se devuelve None
para que el filtro temporal descarte la oferta en vez de dejar pasar algo viejo.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

# El HTML del listado trae entidades y espacios dobles: "Hace  2  días".
ESPACIOS = re.compile(r"\s+")

MINUTOS_RE = re.compile(r"hace\s+(\d+)\s+minutos?")
HORAS_RE = re.compile(r"hace\s+(\d+)\s+horas?")
DIAS_RE = re.compile(r"hace\s+(\d+)\s+d[íi]as?")
MESES_RE = re.compile(r"hace\s+(\d+)\s+mes(?:es)?")
MAS_DE_DIAS_RE = re.compile(r"m[áa]s\s+de\s+(\d+)\s+d[íi]as")
FECHA_ABSOLUTA_RE = re.compile(r"(\d{1,2})\s+de\s+([a-zñ]+)", re.IGNORECASE)

MESES = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "setiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}


def _normalizar(texto: str) -> str:
    """Baja acentos y espacios, para que los regex no dependan del HTML."""
    limpio = ESPACIOS.sub(" ", texto.strip().casefold())
    return limpio.replace("í", "i").replace("á", "a").replace("é", "e")


def parsear(texto: str | None, ahora: datetime | None = None) -> datetime | None:
    """Convierte la fecha de un listado en un datetime UTC.

    `ahora` se puede pasar para poder testear sin depender del reloj.
    Devuelve None si el texto no se reconoce: la oferta se descarta en vez de
    entrar con una fecha inventada.
    """
    if not texto:
        return None

    referencia = ahora or datetime.now(timezone.utc)
    if referencia.tzinfo is None:
        referencia = referencia.replace(tzinfo=timezone.utc)
    limpio = _normalizar(texto)

    # Las absolutas ("16 de septiembre") van primero: el regex de "hace" no las
    # tocaría igual, pero conviene resolverlas con su propio año.
    if (match := FECHA_ABSOLUTA_RE.search(limpio)) is not None:
        return _fecha_absoluta(match, referencia)

    if limpio in {"ayer", "hace 1 dia", "hace un dia"}:
        return referencia - timedelta(days=1)
    if limpio in {"hoy", "hace 0 dias", "recien publicada", "recien publicada."}:
        return referencia

    if (match := MINUTOS_RE.search(limpio)) is not None:
        return referencia - timedelta(minutes=int(match.group(1)))
    if (match := HORAS_RE.search(limpio)) is not None:
        return referencia - timedelta(hours=int(match.group(1)))
    if (match := DIAS_RE.search(limpio)) is not None:
        return referencia - timedelta(days=int(match.group(1)))
    if (match := MESES_RE.search(limpio)) is not None:
        return referencia - timedelta(days=30 * int(match.group(1)))

    if (match := MAS_DE_DIAS_RE.search(limpio)) is not None:
        # "Más de 30 días" no dice el día exacto: la oferta tiene 30 días o más.
        # Tomar 30 exactos sería asumir que tiene justo 30, y con una ventana
        # de 30 días entraría una oferta que en realidad es más vieja. Sumarle
        # un día hace que se descarte, que es el error que nos conviene
        # cometer: preferimos perder una oferta a mostrar una vieja como nueva.
        return referencia - timedelta(days=int(match.group(1)) + 1)

    return None


def _fecha_absoluta(match: re.Match[str], referencia: datetime) -> datetime | None:
    """Para "16 de septiembre": el año es el de la referencia.

    Si el mes quedara en el futuro respecto de la referencia, es del año
    anterior: el portal no pone fechas de December y abre al 1° de enero.
    """
    dia = int(match.group(1))
    mes = MESES.get(_normalizar(match.group(2)))
    if mes is None:
        return None

    anio = referencia.year
    if mes > referencia.month:
        anio -= 1
    try:
        return datetime(anio, mes, dia, tzinfo=timezone.utc)
    except ValueError:
        # "31 de febrero" y demás fechas inexistentes no son fechas.
        return None
