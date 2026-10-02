"""Tests del parser de fechas de Computrabajo.

Cada formato que se vio en el portal tiene un caso. El `ahora` es fijo para que
los tests no dependan del reloj: si el parser dice "hace 19 horas" y el test lo
evalúa a las 23:50, un día va a fallar solo.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from radar.fuentes import fechas

AHORA = datetime(2026, 3, 15, 12, 0, tzinfo=timezone.utc)


def test_hace_minutos() -> None:
    assert fechas.parsear("Hace 35 minutos", AHORA) == AHORA - timedelta(minutes=35)


def test_hace_horas() -> None:
    assert fechas.parsear("Hace 20 horas", AHORA) == AHORA - timedelta(hours=20)


def test_ayer() -> None:
    assert fechas.parsear("Ayer", AHORA) == AHORA - timedelta(days=1)


def test_hace_dias() -> None:
    assert fechas.parsear("Hace 5 días", AHORA) == AHORA - timedelta(days=5)


def test_hace_meses() -> None:
    assert fechas.parsear("Hace 2 meses", AHORA) == AHORA - timedelta(days=60)


def test_mas_de_dias_usa_el_borde_mas_viejo() -> None:
    """"Más de 30 días" no dice el día exacto, así que se toma el peor caso.

    Con una ventana de 30 días, una oferta que dice "más de 30" no entra: si se
    asumiera que tiene exactamente 30, entraría siendo más vieja.
    """
    assert fechas.parsear("Más de 30 días", AHORA) == AHORA - timedelta(days=31)


def test_fecha_absoluta_del_ano_actual() -> None:
    assert fechas.parsear("16 de septiembre", datetime(2026, 10, 1, tzinfo=timezone.utc)) == (
        datetime(2026, 9, 16, tzinfo=timezone.utc)
    )


def test_fecha_absoluta_del_ano_anterior() -> None:
    """El 1° de enero, un puesto de diciembre es del año pasado."""
    referencia = datetime(2026, 1, 5, tzinfo=timezone.utc)
    assert fechas.parsear("16 de diciembre", referencia) == (
        datetime(2025, 12, 16, tzinfo=timezone.utc)
    )


def test_el_html_trae_espacios_dobles() -> None:
    """El listado real trae "Hace  2  días" con dos espacios."""
    assert fechas.parsear("Hace  2  días", AHORA) == AHORA - timedelta(days=2)
    assert fechas.parsear("Hace  19  horas", AHORA) == AHORA - timedelta(hours=19)


@pytest.mark.parametrize("texto", ["", "   ", None])
def test_sin_texto_no_hay_fecha(texto: str | None) -> None:
    assert fechas.parsear(texto, AHORA) is None


@pytest.mark.parametrize(
    "texto",
    [
        "Hace un tiempo",
        "Publicado hace poco",
        " Hace",
        "Hace 5 bananas",
        "32 de febrero",
        "16 de blablabla",
    ],
)
def test_lo_que_no_se_entiende_no_se_inventa(texto: str) -> None:
    """Si no se puede parsear, se devuelve None y la oferta se descarta.

    Es la decisión más importante del módulo: devolver una fecha aproximada
    haría que ofertas viejas entraran como si fueran nuevas.
    """
    assert fechas.parsear(texto, AHORA) is None


def test_sin_referencia_usa_el_reloj() -> None:
    resultado = fechas.parsear("Hace 2 horas")
    assert resultado is not None
    assert resultado <= datetime.now(timezone.utc)


def test_acepta_un_ahora_naive() -> None:
    """Si le pasan un datetime sin zona, se lo asumimos UTC antes de restar."""
    naive = datetime(2026, 3, 15, 12, 0)
    assert fechas.parsear("Hace 2 horas", naive) == AHORA - timedelta(hours=2)
