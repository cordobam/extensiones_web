"""La capa de fuentes: de dónde salen las ofertas.

Cada portal es una implementación de la misma idea (ver `base.Fuente`): recibe
una keyword, devuelve ofertas crudas; la ingesta filtra, normaliza y guarda.
Todo lo que hay más abajo (filtro temporal, matching, base) no sabe de dónde
vino la oferta, y por eso se puede sumar otro portal sin tocarlos.

Este módulo es el registro: `FABRICAS` mapea el id de cada fuente a la clase
que la materializa. Para sumar un portal nuevo hace falta un archivo en
`radar/fuentes/`, una entrada acá y nada más.

Hoy están registradas dos: Computrabajo Argentina (HTML con selectolax) y
ZonaJobs (JSON de una API interna). Cuáles corren en cada corrida decide
`Settings.fuentes_activas`.
"""

from __future__ import annotations

from collections.abc import Callable

from radar.config import get_settings
from radar.fuentes import computrabajo, zonajobs
from radar.fuentes.base import Fuente, OfertaCruda
from radar.fuentes.fechas import parsear

#: De id a constructor. El constructor no recibe argumentos: cada fuente se
#: configura con su propia config y con la inyección de `httpx.Client` que
#: usan los tests.
FABRICAS: dict[str, Callable[[], Fuente]] = {
    computrabajo.Cliente.id: computrabajo.Cliente,
    zonajobs.Cliente.id: zonajobs.Cliente,
}

# Comodidades históricas: `from radar.fuentes import Cliente, listar, parsear`
# era la forma de usar la única fuente que había. Sigue funcionando y sigue
# apuntando a Computrabajo.
Cliente = computrabajo.Cliente
listar = computrabajo.listar


def disponibles() -> list[str]:
    """Los ids registrados, en el orden del registro."""
    return list(FABRICAS)


def crear(fuente_id: str) -> Fuente:
    """Una fuente nueva, lista para usar.

    Un id desconocido es error de quien lo llamó, no una fuente que no existe:
    si se pasa `--fuente copiabobo` mejor fallar ahora que correr una ingesta
    vacía y no entender por qué.
    """
    fabrica = FABRICAS.get(fuente_id)
    if fabrica is None:
        raise KeyError(
            f"No existe la fuente {fuente_id!r}. Disponibles: {', '.join(disponibles())}."
        )
    return fabrica()


def activas(settings=None) -> list[Fuente]:
    """Las fuentes que van a corrir en esta ingesta, según la config.

    `fuentes_activas` es una lista separada por comas ("computrabajo,zonajobs").
    El orden es el del valor, no el del registro: el que escribe la config
    decide en qué orden le llegan las ofertas.
    """
    settings = settings if settings is not None else get_settings()
    elegidas = [f.strip() for f in settings.fuentes_activas.split(",") if f.strip()]
    if not elegidas:
        raise ValueError(
            "No hay fuentes activas: revisá FUENTES_ACTIVAS en .env "
            f"(disponibles: {', '.join(disponibles())})."
        )
    return [crear(fuente_id) for fuente_id in elegidas]


__all__ = [
    "Cliente",
    "FABRICAS",
    "Fuente",
    "OfertaCruda",
    "activas",
    "crear",
    "disponibles",
    "listar",
    "parsear",
]
