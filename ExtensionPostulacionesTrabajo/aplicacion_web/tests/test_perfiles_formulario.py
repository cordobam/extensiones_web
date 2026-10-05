"""Parseo del formulario: nombres compuestos, filas vacías y conversión de tipos."""

from datetime import date

import pytest

from radar.perfiles.formulario import a_dict_formulario


def _v(**pares) -> dict[str, list[str]]:
    """Atajo para armar el dict de valores que devuelve `request.form()`."""
    return {clave: [valor] for clave, valor in pares.items()}


def _minimo() -> dict[str, list[str]]:
    return _v(
        id="data-engineer",
        nombre="Data Engineer",
        **{"keywords": "data engineer\npython"},
        **{"contacto__nombre": "Ada", "contacto__apellido": "Lovelace"},
        **{"contacto__email": "ada@example.com"},
    )


def test_campos_simples() -> None:
    datos = a_dict_formulario(_minimo())
    assert datos["id"] == "data-engineer"
    assert datos["nombre"] == "Data Engineer"
    assert datos["busqueda"]["keywords"] == ["data engineer", "python"]
    assert datos["cv"]["contacto"]["email"] == "ada@example.com"


def test_campos_vacios_quedan_en_none() -> None:
    datos = a_dict_formulario(_minimo())
    contacto = datos["cv"]["contacto"]
    assert contacto["telefono"] is None
    assert contacto["ciudad"] is None
    assert contacto["linkedin"] is None


def test_lista_se_parte_por_coma_y_por_salto_de_linea() -> None:
    valores = _minimo()
    valores.update(_v(**{"skills__0__nombre": "Python", "skills__0__alias": "py, python3\npython3.11"}))
    alias = a_dict_formulario(valores)["cv"]["skills"][0]["alias"]
    assert alias == ["py", "python3", "python3.11"]


def test_lista_de_ubicaciones_separte() -> None:
    datos = a_dict_formulario(_v(ubicaciones="CABA\nBuenos Aires"))
    assert datos["busqueda"]["ubicaciones"] == ["CABA", "Buenos Aires"]


def test_skills_con_indice_correcto() -> None:
    valores = _minimo()
    valores.update(
        _v(
            **{
                "skills__0__nombre": "Python",
                "skills__0__alias": "py, python3",
                "skills__0__anios": "5",
                "skills__0__evidencia": "3 años en el laburo anterior",
                "skills__1__nombre": "SQL",
                "skills__1__alias": "",
            }
        )
    )
    skills = a_dict_formulario(valores)["cv"]["skills"]

    assert len(skills) == 2
    assert skills[0]["nombre"] == "Python"
    assert skills[0]["alias"] == ["py", "python3"]
    assert skills[0]["anios"] == "5"
    assert skills[0]["evidencia"] == "3 años en el laburo anterior"
    assert skills[1]["nombre"] == "SQL"
    assert skills[1]["alias"] == []


def test_fila_vacia_se_descarta() -> None:
    valores = _minimo()
    valores.update(
        _v(
            **{
                "skills__0__nombre": "Python",
                "skills__0__alias": "",
                "skills__1__nombre": "",
                "skills__1__alias": "",
                "skills__1__evidencia": "",
            }
        )
    )
    skills = a_dict_formulario(valores)["cv"]["skills"]
    assert [s["nombre"] for s in skills] == ["Python"]


def test_fila_solo_con_checkbox_se_descarta() -> None:
    valores = _minimo()
    valores.update(_v(**{"skills__0__nombre": "", "skills__0__obligatorio": "1"}))
    assert a_dict_formulario(valores)["cv"]["skills"] == []


def test_indices_se_ordenan_numericamente() -> None:
    valores = _minimo()
    for indice in (0, 2, 10):
        valores.update(_v(**{f"skills__{indice}__nombre": f"Skill {indice}"}))
    skills = a_dict_formulario(valores)["cv"]["skills"]
    assert [s["nombre"] for s in skills] == ["Skill 0", "Skill 2", "Skill 10"]


def test_huecos_en_los_indices_no_rompen() -> None:
    valores = _minimo()
    valores.update(
        _v(**{"skills__0__nombre": "Python", "skills__3__nombre": "Spark"})
    )
    skills = a_dict_formulario(valores)["cv"]["skills"]
    assert [s["nombre"] for s in skills] == ["Python", "Spark"]


def test_checkbox_obligatorio() -> None:
    valores = _minimo()
    valores.update(
        _v(**{"skills__0__nombre": "Python", "skills__0__obligatorio": "1"})
    )
    assert a_dict_formulario(valores)["cv"]["skills"][0]["obligatorio"] is True


def test_sin_checkbox_obligatorio_es_false() -> None:
    valores = _minimo()
    valores.update(_v(**{"skills__0__nombre": "Python"}))
    assert a_dict_formulario(valores)["cv"]["skills"][0]["obligatorio"] is False


@pytest.mark.parametrize("crudo,esperado", [("1", True), ("on", True), ("sí", True), ("0", False), ("", False), ("no", False)])
def test_interpretacion_de_checkbox(crudo: str, esperado: bool) -> None:
    valores = _minimo()
    valores.update(_v(**{"skills__0__nombre": "Python", "skills__0__obligatorio": crudo}))
    assert a_dict_formulario(valores)["cv"]["skills"][0]["obligatorio"] is esperado


def test_experiencia_con_fechas() -> None:
    valores = _minimo()
    valores.update(
        _v(
            **{
                "experiencia__0__puesto": "Data Engineer",
                "experiencia__0__empresa": "Acme",
                "experiencia__0__desde": "2022-03-01",
                "experiencia__0__hasta": "2024-08-31",
                "experiencia__0__descripcion": "Leaderé el equipo de datos",
                "experiencia__0__skills": "Python, dbt",
            }
        )
    )
    experiencia = a_dict_formulario(valores)["cv"]["experiencia"][0]
    assert experiencia["puesto"] == "Data Engineer"
    assert experiencia["desde"] == date(2022, 3, 1)
    assert experiencia["hasta"] == date(2024, 8, 31)
    assert experiencia["skills"] == ["Python", "dbt"]


def test_experiencia_sin_hasta() -> None:
    valores = _minimo()
    valores.update(
        _v(**{"experiencia__0__puesto": "Data Engineer", "experiencia__0__desde": "2024-01-01"})
    )
    assert a_dict_formulario(valores)["cv"]["experiencia"][0]["hasta"] is None


def test_fecha_invalida_no_rompe_el_parseo() -> None:
    valores = _minimo()
    valores.update(_v(**{"experiencia__0__puesto": "Data Engineer", "experiencia__0__desde": "ayer"}))
    assert a_dict_formulario(valores)["cv"]["experiencia"][0]["desde"] is None


def test_educacion_anio_como_numero() -> None:
    valores = _minimo()
    valores.update(_v(**{"educacion__0__estudio": "Ingeniería", "educacion__0__anio": "2016"}))
    assert a_dict_formulario(valores)["cv"]["educacion"][0]["anio"] == 2016


def test_educacion_anio_vacio_o_invalido() -> None:
    valores = _minimo()
    valores.update(_v(**{"educacion__0__estudio": "Ingeniería", "educacion__0__anio": ""}))
    assert a_dict_formulario(valores)["cv"]["educacion"][0]["anio"] is None

    valores.update(_v(**{"educacion__0__anio": "hace rato"}))
    assert a_dict_formulario(valores)["cv"]["educacion"][0]["anio"] is None


def test_idiomas_y_certificaciones() -> None:
    valores = _minimo()
    valores.update(
        _v(
            **{
                "idiomas__0__idioma": "Inglés",
                "idiomas__0__nivel": "B2",
                "certificaciones__0__nombre": "AWS SAA",
                "certificaciones__0__emisor": "Amazon",
                "certificaciones__0__fecha": "2023-05-10",
            }
        )
    )
    datos = a_dict_formulario(valores)
    assert datos["cv"]["idiomas"][0] == {"idioma": "Inglés", "nivel": "B2"}
    assert datos["cv"]["certificaciones"][0]["fecha"] == date(2023, 5, 10)


def test_todas_las_listas_siempre_presentes() -> None:
    cv = a_dict_formulario(_minimo())["cv"]
    for lista in ("skills", "experiencia", "educacion", "idiomas", "certificaciones"):
        assert cv[lista] == []