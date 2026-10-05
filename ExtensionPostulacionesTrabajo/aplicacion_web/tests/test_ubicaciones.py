"""Tests de la provincia: la cuenta en Python y la cuenta en SQL.

`provincia_de` (Python) y `expr_provincia` (SQL) tienen que dar lo mismo. Es la
invariante más importante del módulo: el `WHERE` del filtro usa una y el
`GROUP BY` del dropdown usa la otra, así que si difieren el filtro muestra un
número y filtra otro.

Las ubicaciones de acá son reales, sacadas de las 248 ofertas que trajo
Computrabajo, más los casos borde que el parser no garantiza.

Usa la base de tests (`radar_laboral_test`, la que arma `conftest.py`) como el
resto de los tests: la expresión SQL no se puede probar con SQLite.
"""

from __future__ import annotations

import pytest
from sqlalchemy import literal, select

from radar.db import SessionLocal
from radar.ubicaciones import ALIAS_PROVINCIA, expr_provincia, provincia_de

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def _sql(sesion, ubicacion: str | None) -> str | None:
    return sesion.scalar(select(expr_provincia(literal(ubicacion))))


#: Las tres formas de Buenos Aires, con las cuentas reales de las 248 ofertas.
UBICACIONES_REALES = [
    "Belgrano, Capital Federal",
    "Retiro, Capital Federal",
    "Monserrat, Capital Federal",
    "Avellaneda, Buenos Aires-GBA",
    "San Martín, Buenos Aires-GBA",
    "Escobar, Buenos Aires",
    "Pilar, Buenos Aires",
    "Villa Mercedes, San Luis",
    "Córdoba, Córdoba",
    "Rosario, Santa Fe",
    "San Juan, San Juan",
    "Bariloche, Neuquén",
    "Mendoza, Mendoza",
    "Resistencia, Chaco",
    "Posadas, Misiones",
    "Paraná, Entre Ríos",
    "Viedma, Río Negro",
    "Salta, Salta",
    "La Banda, Santiago del Estero",
]


# ------------------------------------------------------- la cuenta en Python


@pytest.mark.parametrize(
    ("ubicacion", "esperado"),
    [
        ("Belgrano, Capital Federal", "Buenos Aires"),
        ("Pilar, Buenos Aires", "Buenos Aires"),
        ("Avellaneda, Buenos Aires-GBA", "Buenos Aires"),
        # Sin alias: se devuelve como vino.
        ("Córdoba, Córdoba", "Córdoba"),
        ("Villa Mercedes, San Luis", "San Luis"),
        ("Bariloche, Neuquén", "Neuquén"),
        ("Paraná, Entre Ríos", "Entre Ríos"),
        # Sin coma: no hay ciudad/provincia que separar, se usa entero.
        ("Argentina", "Argentina"),
        ("Remoto (Argentina)", "Remoto (Argentina)"),
        # Espacios de más alrededor de la coma.
        ("  Retiro ,  Capital Federal  ", "Buenos Aires"),
        # Con coma de más: la provincia es todo lo que sigue a la primera.
        ("Villa María, Oberá, Misiones", "Oberá, Misiones"),
    ],
)
def test_provincia_de(ubicacion: str, esperado: str) -> None:
    assert provincia_de(ubicacion) == esperado


@pytest.mark.parametrize(
    "ubicacion",
    [
        None,
        "",
        "   ",
        "Retiro,",
        ",",
    ],
)
def test_provincia_de_sin_provincia(ubicacion: str | None) -> None:
    """Lo que no se puede interpretar es `None`, no una cadena rara.

    Importa porque un `""` en el `GROUP BY` abre una opción en el dropdown con
    nombre vacío, y una provincia inventada no: si `provincia_de` devolviera el
    texto crudo, el filtro y la cuenta seguirían cerrando pero el menú tendría
    basura.
    """
    assert provincia_de(ubicacion) is None


def test_una_coma_sin_ciudad_al_menos_da_la_provincia() -> None:
    """", Capital Federal" no es una ubicación válida, pero sí dice dónde.

    Se queda con la provincia en vez de devolver `None`: la ciudad es la parte
    que falta, y el filtro de provincia no la necesita.
    """
    assert provincia_de(", Capital Federal") == "Buenos Aires"


def test_las_tres_formas_de_buenos_aires_dan_lo_mismo() -> None:
    """El motivo de todo el módulo.

    Sin el alias, "Buenos Aires" en el filtro mostraría las 16 ofertas que
    dicen "Buenos Aires" a secas y escondería las 148 de "Capital Federal" y
    "Buenos Aires-GBA", que son el 66% de las que trae el portal.
    """
    assert provincia_de("Belgrano, Capital Federal") == "Buenos Aires"
    assert provincia_de("Avellaneda, Buenos Aires-GBA") == "Buenos Aires"
    assert provincia_de("Pilar, Buenos Aires") == "Buenos Aires"


def test_el_alias_sobrevive_a_como_se_criba() -> None:
    """La clave se compara sobre la forma normalizada.

    `catalogo.normalizar` saca acentos y pasa la puntuación a espacio, así que
    la misma provincia escrita de varias maneras tiene que llegar a la misma
    clave. Es lo que permite que el mapa de alias no dependa de cómo lascribió
    el portal.
    """
    for forma in (
        "Belgrano, Capital Federal",
        "belgrano, capital federal",
        "BELGRANO, CAPITAL FEDERAL",
    ):
        assert provincia_de(forma) == "Buenos Aires"


# ------------------------------------------------------------ paridad con SQL


def test_python_y_sql_coinciden() -> None:
    """La invariante del módulo, sobre las ubicaciones que aparecen de verdad."""
    with SessionLocal() as sesion:
        for ubicacion in UBICACIONES_REALES:
            assert _sql(sesion, ubicacion) == provincia_de(ubicacion), ubicacion


def test_python_y_sql_coinciden_en_los_casos_borde() -> None:
    """Los casos que el portal no manda pero el parser no descarta.

    Todos los que no tienen provincia tienen que dar NULL en los dos lados: si
    SQL devolviera "" y Python `None`, el `GROUP BY` agruparía la cadena vacía y
    la cuenta del dropdown no cerraría con el total.
    """
    borde = [None, "", "   ", "Retiro,", ",", "Argentina", ", Capital Federal"]
    with SessionLocal() as sesion:
        for ubicacion in borde:
            assert _sql(sesion, ubicacion) == provincia_de(ubicacion), ubicacion


def test_el_alias_no_tiene_claves_muertas() -> None:
    """Cada clave del mapa tiene que disparar sobre alguna ubicación real.

    Una clave que no matchea nada es código muerto, y es peor que inútil: deja
    la impresión de que cubre un caso, así que si alguna vez hay que corregir un
    conteo se agrega otra clave en vez de descubrir que la que estaba no hacía
    nada.
    """
    from radar.catalogo import normalizar

    claves_de_los_datos = {normalizar(u.split(",", 1)[1]) for u in UBICACIONES_REALES}

    for clave in ALIAS_PROVINCIA:
        assert clave in claves_de_los_datos, clave


def test_una_clave_con_guion_se_normaliza() -> None:
    """La clave "buenos aires gba" tiene que matchear "Buenos Aires-GBA".

    Regresión de la primera versión de `expr_provincia`: comparaba con `lower()`
    a secas, que deja el guion, así que de las tres formas de Buenos Aires sólo
    se agrupaban dos. Las 64 ofertas del área metropolitana quedaban aparte.
    """
    with SessionLocal() as sesion:
        assert _sql(sesion, "San Martín, Buenos Aires-GBA") == "Buenos Aires"