"""Round-trip a YAML y detección de ediciones manuales del archivo."""

import os
import time
from datetime import datetime, timedelta, timezone

import pytest

from radar.perfiles import serializacion
from radar.perfiles.validacion import PerfilCompleto

DATOS = {
    "id": "data-engineer",
    "nombre": "Data Engineer",
    "busqueda": {"keywords": ["data engineer"], "ubicaciones": ["CABA"]},
    "cv": {
        "contacto": {"nombre": "Ada", "apellido": "Lovelace", "email": "ada@example.com"},
        "skills": [{"nombre": "Python", "alias": ["py"], "anios": 5, "obligatorio": True}],
        "experiencia": [
            {"puesto": "Data Engineer", "empresa": "Acme", "desde": "2022-01-01"}
        ],
    },
}


def _perfil() -> PerfilCompleto:
    return PerfilCompleto.model_validate(DATOS)


def test_round_trip() -> None:
    original = _perfil()
    recuperado = serializacion.desde_yaml(serializacion.a_yaml(original))
    assert recuperado == original


def test_yaml_conserva_acentos() -> None:
    datos = {**DATOS, "nombre": "Analista de Datos"}
    texto = serializacion.a_yaml(PerfilCompleto.model_validate(datos))
    assert "Analista de Datos" in texto
    assert "\\u" not in texto


def test_escribir_y_leer_disco(tmp_path) -> None:
    ruta = serializacion.escribir_disco(_perfil(), tmp_path)
    assert ruta == tmp_path / "data-engineer.yml"
    assert serializacion.desde_yaml(ruta.read_text(encoding="utf-8")) == _perfil()


def test_escribir_crea_el_directorio(tmp_path) -> None:
    destino = tmp_path / "nuevo" / "dir"
    serializacion.escribir_disco(_perfil(), destino)
    assert (destino / "data-engineer.yml").exists()


def test_borrar_disco(tmp_path) -> None:
    serializacion.escribir_disco(_perfil(), tmp_path)
    assert serializacion.borrar_disco("data-engineer", tmp_path) is True
    assert serializacion.borrar_disco("data-engineer", tmp_path) is False


def _tocar(ruta, segundos: float) -> None:
    """Fija el mtime del archivo a `segundos` después de ahora."""
    momento = time.time() + segundos
    os.utime(ruta, (momento, momento))


def test_sin_aviso_si_no_hay_archivo(tmp_path) -> None:
    ahora = datetime.now(timezone.utc)
    assert serializacion.archivo_editado_a_mano("data-engineer", ahora, tmp_path) is None


def test_sin_aviso_si_nunca_se_guardo(tmp_path) -> None:
    serializacion.escribir_disco(_perfil(), tmp_path)
    assert serializacion.archivo_editado_a_mano("data-engineer", None, tmp_path) is None


@pytest.mark.parametrize("delta", [0, 1, 2])
def test_sin_aviso_dentro_de_la_tolerancia(tmp_path, delta: float) -> None:
    ruta = serializacion.escribir_disco(_perfil(), tmp_path)
    _tocar(ruta, delta)
    guardado = datetime.now(timezone.utc) + timedelta(seconds=delta)
    assert serializacion.archivo_editado_a_mano("data-engineer", guardado, tmp_path) is None


@pytest.mark.parametrize("delta", [3, 60, 3600])
def test_aviso_si_el_archivo_es_mas_nuevo(tmp_path, delta: float) -> None:
    ruta = serializacion.escribir_disco(_perfil(), tmp_path)
    _tocar(ruta, delta)
    guardado = datetime.now(timezone.utc)

    aviso = serializacion.archivo_editado_a_mano("data-engineer", guardado, tmp_path)
    assert aviso is not None
    assert aviso.id_perfil == "data-engineer"
    assert "data-engineer.yml" in aviso.detalle


def test_sin_aviso_si_el_archivo_es_mas_viejo(tmp_path) -> None:
    """El caso normal: la app escribió el YAML después del commit."""
    ruta = serializacion.escribir_disco(_perfil(), tmp_path)
    _tocar(ruta, -60)
    guardado = datetime.now(timezone.utc)
    assert serializacion.archivo_editado_a_mano("data-engineer", guardado, tmp_path) is None


def test_guardado_naive_se_trata_como_utc(tmp_path) -> None:
    """Postgres puede devolver el timestamp sin tzinfo."""
    ruta = serializacion.escribir_disco(_perfil(), tmp_path)
    _tocar(ruta, -60)
    guardado = datetime.now(timezone.utc).replace(tzinfo=None)
    assert serializacion.archivo_editado_a_mano("data-engineer", guardado, tmp_path) is None


@pytest.mark.parametrize("nombre", ["data-engineer", "analytics-engineer"])
def test_los_perfiles_de_ejemplo_son_validos(nombre: str) -> None:
    """Guarda: si el ejemplo no valida, el primer uso del producto falla."""
    from radar.config import RAIZ

    ruta = RAIZ / "perfiles" / f"{nombre}.yml.example"
    assert ruta.exists(), f"falta el perfil de ejemplo {ruta.name}"

    perfil = serializacion.desde_yaml(ruta.read_text(encoding="utf-8"))
    assert perfil.id == nombre
    assert perfil.cv.skills
    assert perfil.cv.contacto.email