"""CRUD de postulaciones: el embudo sobre PostgreSQL.

La tabla `postulaciones` existía desde el esquema inicial y nada la escribía.
Acá vive lo que la vuelve un embudo: crear la postulación al marcar una oferta,
moverla de estado y anotarle notas.

Dos fechas y por qué son dos: `creado_en` es cuándo la marcaste, y
`fecha_postulacion` cuándo la mandaste. La segunda se llena sola al entrar en
`enviada` y no se toca más, así que una postulación con la fecha vacía está
esperando que la mandes, y una con fecha ya pasó por el embudo. Con una sola
fecha no se distinguirían.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from radar.models import Oferta, Perfil, Postulacion
from radar.postulaciones import estados


class PostulacionNoEncontrada(Exception):
    pass


class PostulacionDuplicada(Exception):
    pass


class EstadoInvalido(Exception):
    pass


class TransicionInvalida(Exception):
    pass


class PerfilDesconocido(Exception):
    """La postulación necesita un perfil, y el que vino no existe."""


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


def obtener(sesion: Session, id_postulacion: int) -> Postulacion:
    fila = sesion.get(Postulacion, id_postulacion)
    if fila is None:
        raise PostulacionNoEncontrada(f"No existe la postulación {id_postulacion}")
    return fila


def obtener_por_oferta(
    sesion: Session, oferta_id: int, perfil_id: str
) -> Postulacion | None:
    return sesion.scalar(
        select(Postulacion).where(
            Postulacion.oferta_id == oferta_id,
            Postulacion.perfil_id == perfil_id,
        )
    )


def crear(sesion: Session, oferta_id: int, perfil_id: str) -> Postulacion:
    """Marca una oferta como postulándose, en `pendiente`.

    No es idempotente a propósito: el botón se oculta cuando ya existe, y si
    vuelve a llegar es un doble clic o un submit repetido, dos cosas que conviene
    ver y no tragarse. El `uq_postulacion_oferta_perfil` es el que garantiza que
    no haya dos; la comprobación de acá es sólo para el mensaje.

    El perfil se valida a mano y no se deja que lo rechace la clave foránea: el
    `perfil_id` viene de un form, y un `IntegrityError` sin explicar es un 500
    feo donde debería haber un mensaje.
    """
    if sesion.get(Perfil, perfil_id) is None:
        raise PerfilDesconocido(f"No existe el perfil {perfil_id!r}")

    if obtener_por_oferta(sesion, oferta_id, perfil_id) is not None:
        raise PostulacionDuplicada(
            f"La oferta {oferta_id} ya está postulada para el perfil {perfil_id!r}"
        )

    fila = Postulacion(oferta_id=oferta_id, perfil_id=perfil_id, estado="pendiente")
    sesion.add(fila)
    sesion.commit()
    return fila


def listar(sesion: Session, perfil_id: str) -> list[Postulacion]:
    """Las postulaciones del perfil, primero las que están vivas.

    El `estado == "archivada"` ordena por falso/verdadero, y en PostgreSQL falso
    va antes que verdadero: las archivadas caen al final sin agregar una columna
    ni una segunda consulta. Dentro de cada grupo, lo último marcado primero.
    """
    return list(
        sesion.scalars(
            select(Postulacion)
            .where(Postulacion.perfil_id == perfil_id)
            .order_by(Postulacion.estado == "archivada", Postulacion.creado_en.desc())
        )
    )


def contar_por_estado(sesion: Session, perfil_id: str) -> dict[str, int]:
    """Cuántas hay en cada estado, para los contadores del embudo.

    Se devuelven todos los estados, aunque estén en cero: el embudo se ve entero
    desde el primer día y no aparece un estado recién cuando le llega la primera
    postulación.
    """
    conteo = {estado: 0 for estado in estados.ESTADOS}
    for estado, total in sesion.execute(
        select(Postulacion.estado, func.count(Postulacion.id))
        .where(Postulacion.perfil_id == perfil_id)
        .group_by(Postulacion.estado)
    ):
        conteo[estado] = total
    return conteo


def cambiar_estado(
    sesion: Session, id_postulacion: int, nuevo: str
) -> Postulacion:
    """Mueve la postulación a `nuevo`.

    Guarda la fecha de envío la primera vez que llega a `enviada`, y no la
    vuelve a tocar: `fecha_postulacion` es cuándo la mandaste, no cuándo la
    mandaste por última vez.
    """
    if not estados.es_valido(nuevo):
        raise EstadoInvalido(f"{nuevo!r} no es un estado del embudo")

    fila = obtener(sesion, id_postulacion)
    if not estados.puede_pasar(fila.estado, nuevo):
        raise TransicionInvalida(
            f"No se puede pasar de {fila.estado!r} a {nuevo!r}"
        )

    if nuevo != fila.estado:
        fila.estado = nuevo
        if nuevo == "enviada" and fila.fecha_postulacion is None:
            fila.fecha_postulacion = _ahora()
        sesion.commit()

    return fila


def poner_notas(
    sesion: Session, id_postulacion: int, notas: str | None
) -> Postulacion:
    """Guarda las notas. Vacío se guarda como NULL y no como cadena vacía."""
    fila = obtener(sesion, id_postulacion)
    fila.notas = notas.strip() if notas and notas.strip() else None
    sesion.commit()
    return fila


def borrar(sesion: Session, id_postulacion: int) -> None:
    """Desmarca la postulación.

    Es el deshacer del botón "Postular". No es lo mismo que archivar: archivar
    dice "la seguí y se terminó", borrar dice "esto nunca fue una postulación",
    que es lo que pasó si lo marcaste por error.
    """
    obtener(sesion, id_postulacion)
    sesion.execute(delete(Postulacion).where(Postulacion.id == id_postulacion))
    sesion.commit()


# ---------- el envío reportado por la extensión ----------


@dataclass(frozen=True)
class RegistroEnvio:
    """Qué pasó cuando se reportó un envío.

    El mensaje va en el dataclass y no se arma en la ruta porque los tres casos
    --recién creada, recién movida y ya avanzada-- son decisiones de negocio
    ("esto no es una postulación todavía" contra "esto ya se archive"), y la ruta
    sólo tiene que pasarlo a la extensión.
    """

    postulacion: Postulacion
    mensaje: str

    @property
    def enviada(self) -> bool:
        """Si el embudo quedó en un estado que dice que la mandaste."""
        return self.postulacion.estado == "enviada"


def registrar_envio(sesion: Session, oferta: Oferta, perfil_id: str) -> RegistroEnvio:
    """Registra que mandaste la postulación de una oferta.

    Lo llama la extensión de Firefox cuando confirmás el envío, y hace tres
    cosas según dónde esté la postulación:

    - Si no existe, la crea y la manda directo a `enviada`. No la deja en
      `pendiente` para que la veas y la muevas vos: ya la mandaste, y una
      postulación en `pendiente` con la fecha puesta mentiría sobre el embudo.
      La marca, eso sí, no la descarta nadie: queda en el historial como una
      postulación más.

    - Si estaba en `pendiente`, la mueve a `enviada`, que es lo normal.

    - Si ya estaba más adelante, **no la toca**. Si llegó a `entrevista` es que
      el portal ya la había contado antes de que reportaras, y rebajarla a `enviada`
      perdería la entrevista. Si está archivada o tiene resultado, el envío ya
      quedó registrado en su momento. En los dos casos se devuelve el estado
      real y un mensaje que lo dice, en vez de un error: el reporte no estaba
      mal, es que llegó tarde.
    """
    if sesion.get(Perfil, perfil_id) is None:
        raise PerfilDesconocido(f"No existe el perfil {perfil_id!r}")

    fila = obtener_por_oferta(sesion, oferta.id, perfil_id)

    if fila is None:
        nueva = crear(sesion, oferta.id, perfil_id)
        cambiar_estado(sesion, nueva.id, "enviada")
        return RegistroEnvio(
            postulacion=obtener(sesion, nueva.id),
            mensaje="Registrada como enviada. La creé porque esa oferta no "
            "estaba marcada en el embudo.",
        )

    if fila.estado == "pendiente":
        cambiar_estado(sesion, fila.id, "enviada")
        return RegistroEnvio(
            postulacion=obtener(sesion, fila.id),
            mensaje="Registrada como enviada.",
        )

    if fila.estado == "enviada":
        return RegistroEnvio(
            postulacion=fila,
            mensaje="Ya estaba registrada como enviada.",
        )

    return RegistroEnvio(
        postulacion=fila,
        mensaje=(
            f"No la toqué: ya estaba en {estados.etiqueta(fila.estado)}. "
            "La fecha de envío quedó como estaba."
        ),
    )


def de_una_fila(fila: Postulacion) -> dict:
    """La fila como la ve la plantilla: estado con nombre y fechas en texto.

    El formato de la fecha va acá y no en Jinja para que el test pueda compararlo
    sin depender de cómo esté configurado el filtro de plantillas.
    """
    return {
        "id": fila.id,
        "oferta_id": fila.oferta_id,
        "estado": fila.estado,
        "etiqueta": estados.etiqueta(fila.estado),
        "estado_css": fila.estado.replace("resultado_", "resultado-"),
        "titulo": fila.oferta.titulo,
        "empresa": fila.oferta.empresa,
        "url": fila.oferta.url,
        "external_id": fila.oferta.external_id,
        "creado_en": fila.creado_en,
        "fecha_postulacion": fila.fecha_postulacion,
        "notas": fila.notas,
        "abiertas": estados.permitidas(fila.estado),
    }