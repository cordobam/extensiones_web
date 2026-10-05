"""Los estados del embudo y por dónde se puede pasar de uno a otro.

Vive aparte del servicio porque lo leen tres capas: el servicio que valida la
transición, la plantilla que arma el `<select>` y los tests. Si el servicio y la
plantilla tuvieran las listas por separado se desincronizarían en silencio, y
esa desincronización se vería en el sitio --un estado en el `<select>` que al
guardar rebota, que es la peor forma de aprender que algo está mal.
"""

from __future__ import annotations

#: El embudo, en el orden en que se avanza. Es el orden en que se arman los
#: `<select>` y los contadores, no el orden de un dict.
ESTADOS: tuple[str, ...] = (
    "pendiente",
    "enviada",
    "entrevista",
    "resultado_aceptada",
    "resultado_rechazada",
    "archivada",
)

#: A qué se puede pasar desde cada estado.
#:
#: No es una cadena lineal y hay una razón: te rechazan sin entrevista. Si
#: `enviada` sólo pudiera pasar a `entrevista`, para registrar un rechazo
#: tendrías que fingir una entrevista que no pasó, y el embudo quedaría mintiendo
#: justo en el dato que se usa para contar. Por eso `enviada` va directo a
#: `resultado_rechazada`.
#:
#: Tampoco se puede volver hacia atrás. Se llega a `pendiente` marcando la
#: oferta y se avanza; corregir un estado equivocado se hace con `archivada`, que
#: es el estado terminal, o borrando la postulación. Volver atrás dejaría
#: `fecha_postulacion` en un estado que ya no corresponde, y deshacer eso es más
#: código del que vale.
TRANSICIONES: dict[str, tuple[str, ...]] = {
    "pendiente": ("enviada", "archivada"),
    "enviada": ("entrevista", "resultado_rechazada", "archivada"),
    "entrevista": ("resultado_aceptada", "resultado_rechazada", "archivada"),
    "resultado_aceptada": ("archivada",),
    "resultado_rechazada": ("archivada",),
    "archivada": (),
}

#: Los que son un punto final. Sirve para no ofrecerlos como destino y para
#: saber si el embudo terminó de recorrer.
TERMINALES: frozenset[str] = frozenset({"archivada"})


def es_valido(estado: str) -> bool:
    return estado in ESTADOS


def permitidas(desde: str) -> tuple[str, ...]:
    """Los estados a los que se puede pasar desde `desde`."""
    return TRANSICIONES.get(desde, ())


def puede_pasar(desde: str, hacia: str) -> bool:
    """Si el salto es válido. Igual estado a igual estado vale True.

    El mismo estado no es una transición: es un no-op. Un `<select>` con el
    estado actual ya elegido reenvía el mismo valor si guardás sin cambiar nada,
    y eso no debería ser un error.
    """
    if hacia not in ESTADOS or desde not in ESTADOS:
        return False
    return hacia == desde or hacia in TRANSICIONES[desde]


def etiqueta(estado: str) -> str:
    """El nombre para mostrar, porque `resultado_aceptada` no se lee solo."""
    return {
        "pendiente": "Pendiente",
        "enviada": "Enviada",
        "entrevista": "Entrevista",
        "resultado_aceptada": "Aceptada",
        "resultado_rechazada": "Rechazada",
        "archivada": "Archivada",
    }.get(estado, estado)