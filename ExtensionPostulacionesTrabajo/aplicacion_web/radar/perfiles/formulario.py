"""Parseo del formulario de perfiles.

El formulario manda las filas repetibles con nombres compuestos:

    skills__0__nombre=Python
    skills__0__alias=py, python3
    skills__1__nombre=SQL

Se parsea a mano en vez de usar `list[Model] = Form()` de FastAPI porque ese
binding no maneja bien filas repetibles, y porque así podemos descartar las
filas que el usuario dejó vacías sin que rompan la validación.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from typing import Any

SEPARADOR = "__"

# Nombre del campo -> alias del esquema pydantic.
CAMPOS_SKILL = {
    "nombre": "nombre",
    "alias": "alias",
    "obligatorio": "obligatorio",
    "evidencia": "evidencia",
    "anios": "anios",
}

CAMPOS_EXPERIENCIA = {
    "puesto": "puesto",
    "empresa": "empresa",
    "desde": "desde",
    "hasta": "hasta",
    "descripcion": "descripcion",
    "skills": "skills",
}

CAMPOS_EDUCACION = {"estudio": "estudio", "institucion": "institucion", "anio": "anio"}
CAMPOS_IDIOMA = {"idioma": "idioma", "nivel": "nivel"}
CAMPOS_CERTIFICACION = {"nombre": "nombre", "emisor": "emisor", "fecha": "fecha"}

LISTAS = {
    "skills": CAMPOS_SKILL,
    "experiencia": CAMPOS_EXPERIENCIA,
    "educacion": CAMPOS_EDUCACION,
    "idiomas": CAMPOS_IDIOMA,
    "certificaciones": CAMPOS_CERTIFICACION,
}

CAMPOS_BUSQUEDA = {
    "keywords": "keywords",
    "ubicaciones": "ubicaciones",
    "modalidades": "modalidades",
    "seniority": "seniority",
    "excluir": "excluir",
}

CAMPOS_CONTACTO = {
    "contacto__nombre": "nombre",
    "contacto__apellido": "apellido",
    "contacto__email": "email",
    "contacto__telefono": "telefono",
    "contacto__ciudad": "ciudad",
    "contacto__linkedin": "linkedin",
}

VERDADEROS = {"1", "true", "on", "si", "sí"}


def agrupar(pares: Iterable[tuple[str, str]]) -> dict[str, list[str]]:
    """`request.form()` → dict de listas.

    Se agrupan los valores repetidos en una lista porque un mismo `name` puede
    aparecer varias veces (filas repetibles, checks). Un checkbox sin marcar
    simplemente no aparece.
    """
    valores: dict[str, list[str]] = {}
    for clave, valor in pares:
        valores.setdefault(clave, []).append(valor)
    return valores


def valor(valores: dict[str, list[str]], clave: str, defecto: str = "") -> str:
    """Primer valor de un campo, o `defecto` si no viene."""
    return valores.get(clave, [defecto])[0]


def _lista(valor: str) -> list[str]:
    """Parte un campo que acepta varios valores.

    Acepta coma o salto de línea como separador, porque en un textarea es más
    natural pegar uno por línea.
    """
    partes: list[str] = []
    for bruto in valor.replace("\r\n", "\n").replace(",", "\n").split("\n"):
        limpio = bruto.strip()
        if limpio:
            partes.append(limpio)
    return partes


def _fecha(valor: str) -> date | None:
    """Fecha ISO o None. No levanta error: un input mal tipeado se reporta
    después como error de validación, no como una excepción 500."""
    limpio = valor.strip()
    if not limpio:
        return None
    try:
        return date.fromisoformat(limpio)
    except ValueError:
        return None


def _anio(valor: str) -> int | None:
    limpio = valor.strip()
    if not limpio:
        return None
    try:
        return int(limpio)
    except ValueError:
        return None


def _fila(lista: str, indice: str, valores: dict[str, list[str]], mapeo: dict[str, str]) -> dict[str, Any]:
    """Lee una fila completa de una lista, ej. skills__0__nombre."""
    prefijo = f"{lista}{SEPARADOR}{indice}{SEPARADOR}"
    return {
        alias: valores.get(f"{prefijo}{campo}", [""])[0]
        for campo, alias in mapeo.items()
    }


def _indices(valores: dict[str, list[str]], lista: str) -> list[str]:
    """Índices presentes para una lista, en orden numérico (0, 1, 2, 10)."""
    prefijo = f"{lista}{SEPARADOR}"
    vistos = {
        clave[len(prefijo) :].split(SEPARADOR)[0]
        for clave in valores
        if clave.startswith(prefijo)
    }
    return sorted(vistos, key=lambda indice: (not indice.isdigit(), int(indice) if indice.isdigit() else 0, indice))


def _filas_vacias(fila: dict[str, Any], obligatorios: tuple[str, ...]) -> bool:
    """True si todos los campos relevantes de la fila están vacíos."""
    for campo in obligatorios:
        valor = fila.get(campo)
        if valor is None:
            continue
        if isinstance(valor, str) and not valor.strip():
            continue
        if valor in (False, None):
            continue
        return False
    return True


def a_dict_formulario(valores: dict[str, list[str]]) -> dict[str, Any]:
    """Formulario plano → dict con la forma del perfil.

    Las filas vacías se descartan: si el usuario agrega una fila y la deja en
    blanco, no debería romper el guardado.
    """
    def primero(clave: str) -> str:
        return valor(valores, clave)

    busqueda = {
        alias: _lista(primero(clave)) for clave, alias in CAMPOS_BUSQUEDA.items()
    }
    contacto = {alias: primero(clave).strip() or None for clave, alias in CAMPOS_CONTACTO.items()}

    cv: dict[str, Any] = {"contacto": contacto}
    obligatorios_por_lista = {
        "skills": ("nombre",),
        "experiencia": ("puesto",),
        "educacion": ("estudio",),
        "idiomas": ("idioma",),
        "certificaciones": ("nombre",),
    }

    for lista, mapeo in LISTAS.items():
        filas: list[dict[str, Any]] = []
        for indice in _indices(valores, lista):
            crudo = _fila(lista, indice, valores, mapeo)
            fila = {
                campo: (
                    _lista(valor)
                    if campo in ("alias", "skills")
                    else valor.strip() if isinstance(valor, str) else valor
                )
                for campo, valor in crudo.items()
            }
            if _filas_vacias(fila, obligatorios_por_lista[lista]):
                continue

            if lista == "skills":
                fila["obligatorio"] = fila["obligatorio"].strip().lower() in VERDADEROS
                fila["evidencia"] = fila["evidencia"] or None
                fila["anios"] = fila["anios"] or None
                fila["alias"] = fila["alias"] or []
            elif lista == "experiencia":
                fila["empresa"] = fila["empresa"] or None
                fila["descripcion"] = fila["descripcion"] or None
                fila["desde"] = _fecha(fila["desde"])
                fila["hasta"] = _fecha(fila["hasta"])
                fila["skills"] = fila["skills"] or []
            elif lista == "educacion":
                fila["institucion"] = fila["institucion"] or None
                fila["anio"] = _anio(fila["anio"]) if fila["anio"] else None
            elif lista == "certificaciones":
                fila["emisor"] = fila["emisor"] or None
                fila["fecha"] = _fecha(fila["fecha"])
            else:
                fila["nivel"] = fila["nivel"] or None

            filas.append(fila)
        cv[lista] = filas

    return {
        "id": primero("id").strip(),
        "nombre": primero("nombre").strip(),
        "busqueda": busqueda,
        "cv": cv,
    }
