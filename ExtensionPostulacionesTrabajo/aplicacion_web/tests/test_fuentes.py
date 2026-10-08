"""Tests del registro de fuentes (`radar.fuentes`).

El registro es el pedazo que hace que sumar un portal sea agregar un archivo y
una entrada: la CLI y la ingesta no importan `computrabajo` ni `zonajobs`, le
preguntan a este módulo qué hay.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from radar import fuentes as registro
from radar.fuentes.base import Fuente


def test_estan_registradas_las_dos_portales() -> None:
    assert registro.disponibles() == ["computrabajo", "zonajobs"]


def test_crear_devuelve_un_objeto_que_cumple_el_contrato() -> None:
    for id_ in registro.disponibles():
        fuente = registro.crear(id_)
        try:
            assert isinstance(fuente, Fuente)
            assert fuente.id == id_
            assert callable(fuente.listar)
            assert callable(fuente.describir)
            assert callable(fuente.fecha)
            assert callable(fuente.close)
        finally:
            fuente.close()


def test_crear_un_id_desconocido_dice_los_que_hay() -> None:
    with pytest.raises(KeyError) as error:
        registro.crear("copiabobo")

    assert "computrabajo" in str(error.value)
    assert "zonajobs" in str(error.value)


def test_las_activas_salen_de_la_config_en_el_orden_que_dijo() -> None:
    settings = SimpleNamespace(fuentes_activas=" zonajobs , computrabajo ")
    try:
        activas = registro.activas(settings)
        assert [f.id for f in activas] == ["zonajobs", "computrabajo"]
    finally:
        for fuente in activas:
            fuente.close()


def test_sin_fuentes_activas_no_sigue_en_vacio() -> None:
    """Una corrida sin portales no falla: no hace nada, y eso es peor.

    Mejor un `ValueError` en el arranque que una ingesta que dice "0 ofertas"
    y deja creyendo que el portal no publicó nada.
    """
    with pytest.raises(ValueError, match="FUENTES_ACTIVAS"):
        registro.activas(SimpleNamespace(fuentes_activas="  ,  "))


def test_las_comodidades_historicas_siguen_apuntando_a_computrabajo() -> None:
    """`from radar.fuentes import Cliente, listar, parsear` era la API vieja."""
    assert registro.Cliente is not None
    assert registro.Cliente.id == "computrabajo"
    assert callable(registro.listar)
    assert callable(registro.parsear)
