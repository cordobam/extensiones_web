"""El contrato que cumple toda fuente de ofertas.

Una fuente sabe hacer cuatro cosas y nada más: traer una página de listado,
dar la descripción de una oferta, decir cuándo se publicó y cerrarse. Quien
llama (la ingesta) no sabe si eso salió de HTML con selectolax, de un JSON o de
una base local, y por eso sumar un portal no toca el filtro temporal, el
matching ni la base.

El contrato es un `Protocol` y no una clase abstracta: cada fuente implementa
los métodos donde le queda cómodo (Computrabajo los tiene en su `Cliente`,
Zonajobs en el suyo) y no hay que heredar de nada. Para testear, cualquier
objeto con esos cinco atributos sirve.

`OfertaCruda` vive acá y no en cada portal porque es literalmente el mismo
concepto: una fila del listado, todavía sin procesar. Lo que cambia entre
portales es de dónde sale cada campo, no qué campos se necesitan.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable


@dataclass
class OfertaCruda:
    """Lo que da el listado, antes de tener skills ni pasar por la base.

    Va separada de `Offer` (schema.py) a propósito: el listado no siempre trae
    descripción y no conviene bajar 20 detalles para poder decidir si una
    oferta está en la ventana temporal. `fecha_texto` sí viene acá, porque el
    filtro de tiempo corre antes de bajar los detalles.

    `descripcion` es la excepción: si la fuente la trae de entrada (Zonajobs
    manda el detalle en el mismo JSON del listado), la llena acá y la ingesta
    la usa sin pedir nada más. Si no, queda en None y la describe después.
    """

    external_id: str
    titulo: str
    empresa: str | None
    url: str
    ubicacion: str | None
    modalidad: str | None
    salario: str | None
    fecha_texto: str | None
    descripcion: str | None = None


@runtime_checkable
class Fuente(Protocol):
    """Un portal de ofertas, detrás de un interfaz común."""

    #: Identificador corto y estable: va en `Oferta.fuente`, en el prefijo del
    #: `external_id` y en el flag `--fuente` de la CLI. En minúsculas, sin
    #: espacios.
    id: str

    def listar(self, keyword: str, pagina: int) -> tuple[list[OfertaCruda], bool]:
        """Una página de listado para una keyword.

        Devuelve las ofertas crudas y si hay una página siguiente. La segunda
        posición importa: la ingesta corta cuando una página no trae nada nuevo
        *o* cuando no hay más páginas, y las dos señales vienen de lados
        distintos (una la da el contenido, la otra el portal).
        """

    def describir(self, cruda: OfertaCruda) -> str:
        """La descripción completa de una oferta.

        Puede no pegarle un request a nadie si la trajo el listado. Devuelve
        "" si no hay descripción, nunca None: quien llama no tiene que
        preguntar dos veces.
        """

    def fecha(self, cruda: OfertaCruda, ahora: datetime) -> datetime | None:
        """La fecha de publicación de una oferta, en UTC.

        None significa "no la sé": la ingesta descarta la oferta en vez de
        mostrar una vieja como si fuera nueva. Cada portal tiene su formato
        (Computrabajo escribe "Hace 19 horas", Zonajobs "06-10-2026 19:00:08"),
        y por eso el parser es de la fuente y no del pipeline.
        """

    def close(self) -> None:
        """Cierra lo que tenga abierto (una conexión HTTP, un navegador...)."""
