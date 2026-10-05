"""El embudo de postulaciones: qué ofertás seguir y en qué punto estás.

La tabla existía desde el esquema inicial y nada la escribía. Acá está lo que la
convierte en algo que se sigue: los estados y las transiciones válidas en
`estados`, y el CRUD en `servicio`.
"""

from radar.postulaciones import estados
from radar.postulaciones.estados import ESTADOS, TRANSICIONES, etiqueta
from radar.postulaciones.servicio import (
    EstadoInvalido,
    PerfilDesconocido,
    PostulacionDuplicada,
    PostulacionNoEncontrada,
    TransicionInvalida,
    borrar,
    cambiar_estado,
    contar_por_estado,
    crear,
    de_una_fila,
    listar,
    obtener,
    obtener_por_oferta,
    poner_notas,
)

__all__ = [
    "ESTADOS",
    "TRANSICIONES",
    "EstadoInvalido",
    "PerfilDesconocido",
    "PostulacionDuplicada",
    "PostulacionNoEncontrada",
    "TransicionInvalida",
    "borrar",
    "cambiar_estado",
    "contar_por_estado",
    "crear",
    "de_una_fila",
    "estados",
    "etiqueta",
    "listar",
    "obtener",
    "obtener_por_oferta",
    "poner_notas",
]