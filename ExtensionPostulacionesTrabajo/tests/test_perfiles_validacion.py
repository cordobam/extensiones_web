"""Validación de perfiles: lo que la UI y el servidor tienen que rechazar."""

import pytest
from pydantic import ValidationError

from radar.perfiles.validacion import PerfilCompleto

BASE = {
    "id": "data-engineer",
    "nombre": "Data Engineer",
    "busqueda": {"keywords": ["data engineer", "python"]},
    "cv": {
        "contacto": {"nombre": "Ada", "apellido": "Lovelace", "email": "ada@example.com"},
        "skills": [{"nombre": "Python", "anios": 5}],
    },
}


def perfil(**cambios) -> dict:
    datos = {
        "id": BASE["id"],
        "nombre": BASE["nombre"],
        "busqueda": dict(BASE["busqueda"]),
        "cv": {
            "contacto": dict(BASE["cv"]["contacto"]),
            "skills": [dict(s) for s in BASE["cv"]["skills"]],
        },
    }
    datos.update(cambios)
    return datos


def test_perfil_minimo_valido() -> None:
    resultado = PerfilCompleto.model_validate(perfil())
    assert resultado.id == "data-engineer"
    assert resultado.cv.skills[0].nombre == "Python"
    assert resultado.cv.skills[0].anios == 5


def test_keywords_vacias_se_rechazan() -> None:
    with pytest.raises(ValidationError):
        PerfilCompleto.model_validate(perfil(busqueda={"keywords": []}))


def test_keywords_solo_espacios_se_rechazan() -> None:
    with pytest.raises(ValidationError):
        PerfilCompleto.model_validate(perfil(busqueda={"keywords": ["  ", ""]}))


def test_campos_desconocidos_se_rechazan() -> None:
    datos = perfil()
    datos["busqueda"]["filtro_magico"] = "si"
    with pytest.raises(ValidationError):
        PerfilCompleto.model_validate(datos)


@pytest.mark.parametrize("id_invalido", ["Data Engineer", "data_engineer", "data--engineer", "-x", ""])
def test_id_invalido_se_rechaza(id_invalido: str) -> None:
    with pytest.raises(ValidationError):
        PerfilCompleto.model_validate(perfil(id=id_invalido))


@pytest.mark.parametrize("id_valido", ["data-engineer", "perfil2", "a", "data-analytics-2"])
def test_id_valido_se_acepta(id_valido: str) -> None:
    assert PerfilCompleto.model_validate(perfil(id=id_valido)).id == id_valido


def test_email_invalido_se_rechaza() -> None:
    datos = perfil()
    datos["cv"]["contacto"]["email"] = "no-es-un-email"
    with pytest.raises(ValidationError):
        PerfilCompleto.model_validate(datos)


def test_nombre_de_contacto_obligatorio() -> None:
    datos = perfil()
    datos["cv"]["contacto"]["nombre"] = ""
    with pytest.raises(ValidationError):
        PerfilCompleto.model_validate(datos)


def test_skills_duplicadas_se_rechazan() -> None:
    datos = perfil()
    datos["cv"]["skills"] = [{"nombre": "Python"}, {"nombre": "Python"}]
    with pytest.raises(ValidationError):
        PerfilCompleto.model_validate(datos)


def test_skills_unicas_por_nombre_sin_importar_mayusculas() -> None:
    datos = perfil()
    datos["cv"]["skills"] = [{"nombre": "Python"}, {"nombre": "python"}]
    with pytest.raises(ValidationError):
        PerfilCompleto.model_validate(datos)


def test_anios_acepta_texto() -> None:
    datos = perfil()
    datos["cv"]["skills"] = [{"nombre": "Python", "anios": "3 años"}]
    assert PerfilCompleto.model_validate(datos).cv.skills[0].anios == 3


def test_anios_invalido_se_rechaza() -> None:
    datos = perfil()
    datos["cv"]["skills"] = [{"nombre": "Python", "anios": "muchos"}]
    with pytest.raises(ValidationError):
        PerfilCompleto.model_validate(datos)


def test_experiencia_con_rango_invertido_se_rechaza() -> None:
    datos = perfil()
    datos["cv"]["experiencia"] = [
        {"puesto": "Data Engineer", "desde": "2023-01-01", "hasta": "2021-01-01"}
    ]
    with pytest.raises(ValidationError):
        PerfilCompleto.model_validate(datos)


def test_experiencia_sin_fecha_de_fin_se_acepta() -> None:
    datos = perfil()
    datos["cv"]["experiencia"] = [{"puesto": "Data Engineer", "desde": "2023-01-01"}]
    experiencia = PerfilCompleto.model_validate(datos).cv.experiencia[0]
    assert experiencia.hasta is None


def test_cv_solo_obligatorio_no_rompe() -> None:
    datos = perfil()
    datos["cv"] = {"contacto": dict(BASE["cv"]["contacto"])}
    assert PerfilCompleto.model_validate(datos).cv.skills == []


def test_listas_vacias_sin_guiones_valen() -> None:
    """`certificaciones:` a secas en YAML es None: el usuario quiere "ninguna"."""
    datos = perfil()
    datos["busqueda"] = {"keywords": ["python"], "ubicaciones": None, "excluir": None}
    datos["cv"]["certificaciones"] = None
    datos["cv"]["idiomas"] = None

    resultado = PerfilCompleto.model_validate(datos)
    assert resultado.busqueda.ubicaciones == []
    assert resultado.busqueda.excluir == []
    assert resultado.cv.certificaciones == []
    assert resultado.cv.idiomas == []


def test_lista_none_en_keywords_sigue_dando_error() -> None:
    datos = perfil()
    datos["busqueda"] = {"keywords": None}
    with pytest.raises(ValidationError):
        PerfilCompleto.model_validate(datos)