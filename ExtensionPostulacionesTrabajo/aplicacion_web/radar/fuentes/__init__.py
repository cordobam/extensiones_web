"""La capa de fuentes: de dónde salen las ofertas.

Cada portal es una implementación de la misma idea: recibe un perfil, devuelve
objetos `Offer`. Todo lo que hay más abajo (filtro temporal, matching, base) no
sabe de dónde vino la oferta, y por eso mañana se puede sumar otro portal sin
tocarlos.

Hoy hay una sola fuente: Computrabajo Argentina.
"""

from radar.fuentes.computrabajo import Cliente, listar
from radar.fuentes.fechas import parsear

__all__ = ["Cliente", "listar", "parsear"]
