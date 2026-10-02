"""Tests de las rutas de perfiles.

Usan la base real (`radar_laboral`) con dependencia sobreescrita por una sesión
única por test, y un directorio de perfiles temporal para no tocar los YAML
versionados.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from urllib.parse import unquote_plus, urlparse

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from radar.api import app
from radar.db import SessionLocal
from radar.models import Match, Perfil, Postulacion
from radar.perfiles import serializacion

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


@pytest.fixture
def cliente(tmp_path, monkeypatch) -> Iterator[TestClient]:
    from radar import api

    sesion = SessionLocal()
    sesion.execute(delete(Match))
    sesion.execute(delete(Postulacion))
    sesion.execute(delete(Perfil))
    sesion.commit()

    class Ajustes:
        perfiles_dir = tmp_path

    monkeypatch.setattr(api, "get_settings", lambda: Ajustes())
    monkeypatch.setattr(
        api,
        "get_session",
        lambda: iter([sesion]),
    )
    app.dependency_overrides[api.get_session] = lambda: sesion

    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
        sesion.rollback()
        sesion.execute(delete(Match))
        sesion.execute(delete(Postulacion))
        sesion.execute(delete(Perfil))
        sesion.commit()
        sesion.close()


FORMULARIO = {
    "id": "data-engineer",
    "nombre": "Data Engineer",
    "keywords": "data engineer\npython",
    "ubicaciones": "CABA",
    "contacto__nombre": "Ada",
    "contacto__apellido": "Lovelace",
    "contacto__email": "ada@example.com",
    "skills__0__nombre": "Python",
    "skills__0__alias": "py",
    "skills__0__anios": "5",
    "skills__0__obligatorio": "1",
}


def _existe(cliente: TestClient, id_perfil: str) -> bool:
    with SessionLocal() as sesion:
        return sesion.get(Perfil, id_perfil) is not None


def test_raiz_redirige_a_perfiles(cliente: TestClient) -> None:
    respuesta = cliente.get("/", follow_redirects=False)
    assert respuesta.status_code == 307
    assert respuesta.headers["location"] == "/perfiles"


def test_health(cliente: TestClient) -> None:
    assert cliente.get("/health").json() == {"estado": "ok"}


def test_lista_vacia(cliente: TestClient) -> None:
    respuesta = cliente.get("/perfiles")
    assert respuesta.status_code == 200
    assert "Todavía no cargaste ningún perfil" in respuesta.text


def test_formulario_nuevo(cliente: TestClient) -> None:
    respuesta = cliente.get("/perfiles/nuevo")
    assert respuesta.status_code == 200
    assert "Nuevo perfil" in respuesta.text
    assert 'name="skills__IDX__nombre"' in respuesta.text


def test_crear_perfil_escribe_en_base_y_yaml(cliente: TestClient, tmp_path) -> None:
    respuesta = cliente.post("/perfiles", data=FORMULARIO, follow_redirects=False)
    assert respuesta.status_code == 303
    assert respuesta.headers["location"] == "/perfiles"

    assert _existe(cliente, "data-engineer")
    archivo = tmp_path / "data-engineer.yml"
    assert archivo.exists()

    perfil = serializacion.desde_yaml(archivo.read_text(encoding="utf-8"))
    assert perfil.id == "data-engineer"
    assert perfil.cv.skills[0].nombre == "Python"
    assert perfil.cv.skills[0].obligatorio is True


def test_crear_perfil_aparece_en_la_lista(cliente: TestClient) -> None:
    cliente.post("/perfiles", data=FORMULARIO, follow_redirects=False)
    texto = cliente.get("/perfiles").text
    assert "data-engineer" in texto
    assert "Data Engineer" in texto


def test_email_invalido_devuelve_422_y_no_guarda(cliente: TestClient) -> None:
    datos = {**FORMULARIO, "contacto__email": "no-es-un-email"}
    respuesta = cliente.post("/perfiles", data=datos)
    assert respuesta.status_code == 422
    assert "email" in respuesta.text.lower()
    assert not _existe(cliente, "data-engineer")


def test_id_invalido_devuelve_422(cliente: TestClient) -> None:
    respuesta = cliente.post("/perfiles", data={**FORMULARIO, "id": "Data Engineer"})
    assert respuesta.status_code == 422
    assert not _existe(cliente, "Data Engineer")


def test_id_duplicado_devuelve_422(cliente: TestClient) -> None:
    cliente.post("/perfiles", data=FORMULARIO, follow_redirects=False)
    datos = {**FORMULARIO, "nombre": "Otro nombre", "contacto__email": "otro@example.com"}
    respuesta = cliente.post("/perfiles", data=datos)
    assert respuesta.status_code == 422
    assert "Ya existe" in respuesta.text


def test_el_formulario_revierte_lo_que_el_usuario_escribio(cliente: TestClient) -> None:
    """Un 422 no debe tirar al usuario su trabajo a la basura."""
    datos = {**FORMULARIO, "contacto__email": "malo", "keywords": "data engineer\npyspark"}
    texto = cliente.post("/perfiles", data=datos).text
    assert "pyspark" in texto
    assert "Data Engineer" in texto
    assert "malo" in texto


def test_filas_vacias_se_descartan(cliente: TestClient, tmp_path) -> None:
    datos = {
        **FORMULARIO,
        "skills__1__nombre": "",
        "skills__1__alias": "",
        "experiencia__0__puesto": "",
    }
    respuesta = cliente.post("/perfiles", data=datos, follow_redirects=False)
    assert respuesta.status_code == 303
    perfil = serializacion.desde_yaml((tmp_path / "data-engineer.yml").read_text(encoding="utf-8"))
    assert [s.nombre for s in perfil.cv.skills] == ["Python"]
    assert perfil.cv.experiencia == []


def test_editar_perfil_precarga_el_formulario(cliente: TestClient) -> None:
    cliente.post("/perfiles", data=FORMULARIO, follow_redirects=False)
    respuesta = cliente.get("/perfiles/data-engineer")
    assert respuesta.status_code == 200
    assert "Editar perfil" in respuesta.text
    assert 'value="ada@example.com"' in respuesta.text
    assert "data engineer\npython" in respuesta.text.replace("&#10;", "\n")


def test_editar_perfil_inexistente(cliente: TestClient) -> None:
    respuesta = cliente.get("/perfiles/no-existe")
    assert respuesta.status_code == 404
    assert "No existe el perfil" in respuesta.text


def test_actualizar_perfil(cliente: TestClient, tmp_path) -> None:
    cliente.post("/perfiles", data=FORMULARIO, follow_redirects=False)
    datos = {**FORMULARIO, "nombre": "Data Engineer Senior", "skills__0__nombre": "Python"}
    respuesta = cliente.post("/perfiles/data-engineer", data=datos, follow_redirects=False)
    assert respuesta.status_code == 303

    perfil = serializacion.desde_yaml((tmp_path / "data-engineer.yml").read_text(encoding="utf-8"))
    assert perfil.nombre == "Data Engineer Senior"


def test_no_se_puede_cambiar_el_id_al_editar(cliente: TestClient) -> None:
    cliente.post("/perfiles", data=FORMULARIO, follow_redirects=False)
    datos = {**FORMULARIO, "id": "otro-perfil"}
    respuesta = cliente.post("/perfiles/data-engineer", data=datos)
    assert respuesta.status_code == 422
    assert "no se puede cambiar" in respuesta.text
    assert _existe(cliente, "data-engineer")
    assert not _existe(cliente, "otro-perfil")


def test_activar_perfil(cliente: TestClient) -> None:
    cliente.post("/perfiles", data=FORMULARIO, follow_redirects=False)
    cliente.post("/perfiles", data={**FORMULARIO, "id": "otro"}, follow_redirects=False)

    assert cliente.post("/perfiles/otro/activar", follow_redirects=False).status_code == 303

    with SessionLocal() as sesion:
        activos = [p.id for p in sesion.query(Perfil).all() if p.activo]
    assert activos == ["otro"]


def test_activar_no_desactiva_los_demas(cliente: TestClient) -> None:
    """La regla del proyecto: pueden convivir varios perfiles activos.

    Antes `activar` desactivaba todos los demás a propósito, así que con cuatro
    perfiles cargados la ingesta sólo podía rastrear uno.
    """
    cliente.post("/perfiles", data=FORMULARIO, follow_redirects=False)
    cliente.post("/perfiles", data={**FORMULARIO, "id": "otro"}, follow_redirects=False)

    cliente.post("/perfiles/data-engineer/activar", follow_redirects=False)
    cliente.post("/perfiles/otro/activar", follow_redirects=False)

    with SessionLocal() as sesion:
        activos = sorted(p.id for p in sesion.query(Perfil).all() if p.activo)
    assert activos == ["data-engineer", "otro"]


def test_desactivar_saca_solo_ese(cliente: TestClient) -> None:
    cliente.post("/perfiles", data=FORMULARIO, follow_redirects=False)
    cliente.post("/perfiles", data={**FORMULARIO, "id": "otro"}, follow_redirects=False)
    cliente.post("/perfiles/data-engineer/activar", follow_redirects=False)
    cliente.post("/perfiles/otro/activar", follow_redirects=False)

    assert cliente.post("/perfiles/otro/desactivar", follow_redirects=False).status_code == 303

    with SessionLocal() as sesion:
        activos = [p.id for p in sesion.query(Perfil).all() if p.activo]
    assert activos == ["data-engineer"]


def test_desactivar_es_idempotente(cliente: TestClient) -> None:
    """Desactivar dos veces no debe romper: la UI puede recibir doble clic."""
    cliente.post("/perfiles", data=FORMULARIO, follow_redirects=False)
    cliente.post("/perfiles/data-engineer/activar", follow_redirects=False)

    for _ in range(2):
        assert cliente.post(
            "/perfiles/data-engineer/desactivar", follow_redirects=False
        ).status_code == 303

    with SessionLocal() as sesion:
        assert [p for p in sesion.query(Perfil).all() if p.activo] == []


def test_activar_perfil_inexistente_no_rompe(cliente: TestClient) -> None:
    assert cliente.post("/perfiles/no-existe/activar", follow_redirects=False).status_code == 303


def test_desactivar_perfil_inexistente_no_rompe(cliente: TestClient) -> None:
    assert (
        cliente.post("/perfiles/no-existe/desactivar", follow_redirects=False).status_code
        == 303
    )


def test_activos_devuelve_los_perfiles_marcados(cliente: TestClient) -> None:
    """`servicio.activos()` es lo que va a consumir la ingesta.

    Lo cubre el caso de varios perfiles activos a la vez, que es el motivo por
    el que se cambió la regla de "un único activo".
    """
    from radar.perfiles import servicio

    cliente.post("/perfiles", data=FORMULARIO, follow_redirects=False)
    cliente.post("/perfiles", data={**FORMULARIO, "id": "otro"}, follow_redirects=False)

    with SessionLocal() as sesion:
        assert [p.id for p in servicio.activos(sesion)] == []

        servicio.activar(sesion, "data-engineer")
        servicio.activar(sesion, "otro")
        assert sorted(p.id for p in servicio.activos(sesion)) == ["data-engineer", "otro"]

        servicio.desactivar(sesion, "otro")
        assert [p.id for p in servicio.activos(sesion)] == ["data-engineer"]


def test_lista_muestra_cuantos_activos_hay(cliente: TestClient) -> None:
    cliente.post("/perfiles", data=FORMULARIO, follow_redirects=False)
    cliente.post("/perfiles", data={**FORMULARIO, "id": "otro"}, follow_redirects=False)
    cliente.post("/perfiles/data-engineer/activar", follow_redirects=False)
    cliente.post("/perfiles/otro/activar", follow_redirects=False)

    respuesta = cliente.get("/perfiles")
    assert respuesta.status_code == 200
    cuerpo = respuesta.text
    assert "2 perfiles activos" in cuerpo
    # Cada perfil ofrece la acción contraria a su estado.
    assert cuerpo.count("/desactivar") == 2
    assert "/perfiles/data-engineer/activar" not in cuerpo

    cliente.post("/perfiles/otro/desactivar", follow_redirects=False)
    cuerpo = cliente.get("/perfiles").text
    assert "1 perfil activo" in cuerpo
    assert "/perfiles/otro/activar" in cuerpo


def test_borrar_perfil_borra_base_y_yaml(cliente: TestClient, tmp_path) -> None:
    cliente.post("/perfiles", data=FORMULARIO, follow_redirects=False)
    assert (tmp_path / "data-engineer.yml").exists()

    assert cliente.post("/perfiles/data-engineer/borrar", follow_redirects=False).status_code == 303
    assert not _existe(cliente, "data-engineer")
    assert not (tmp_path / "data-engineer.yml").exists()


def test_borrar_perfil_inexistente_no_rompe(cliente: TestClient) -> None:
    assert cliente.post("/perfiles/no-existe/borrar", follow_redirects=False).status_code == 303


def test_confirmacion_de_borrado_muestra_el_id(cliente: TestClient) -> None:
    cliente.post("/perfiles", data=FORMULARIO, follow_redirects=False)
    respuesta = cliente.get("/perfiles/data-engineer/borrar")
    assert respuesta.status_code == 200
    assert "Borrar perfil" in respuesta.text
    assert "data-engineer.yml" in respuesta.text


def test_edicion_manual_dispara_aviso_y_no_pisa(cliente: TestClient, tmp_path) -> None:
    cliente.post("/perfiles", data=FORMULARIO, follow_redirects=False)
    archivo = tmp_path / "data-engineer.yml"

    # El usuario edita el archivo a mano, más tarde que el último guardado.
    momento = time.time() + 600
    os.utime(archivo, (momento, momento))

    datos = {**FORMULARIO, "nombre": "Editado desde la app"}
    respuesta = cliente.post("/perfiles/data-engineer", data=datos)

    assert respuesta.status_code == 200
    assert "modificado" in respuesta.text
    # Y el archivo con la edición manual quedó intacto.
    assert "Editado desde la app" not in archivo.read_text(encoding="utf-8")


def test_confirmacion_explicita_pisa_el_archivo(cliente: TestClient, tmp_path) -> None:
    cliente.post("/perfiles", data=FORMULARIO, follow_redirects=False)
    archivo = tmp_path / "data-engineer.yml"
    momento = time.time() + 600
    os.utime(archivo, (momento, momento))

    datos = {
        **FORMULARIO,
        "nombre": "Editado desde la app",
        "confirmar_sobrescritura": "1",
    }
    respuesta = cliente.post("/perfiles/data-engineer", data=datos, follow_redirects=False)
    assert respuesta.status_code == 303

    perfil = serializacion.desde_yaml(archivo.read_text(encoding="utf-8"))
    assert perfil.nombre == "Editado desde la app"


def test_guardar_dos_veces_no_avisa(cliente: TestClient) -> None:
    """El guardado normal no debe pedir confirmación: mtime va al revés."""
    cliente.post("/perfiles", data=FORMULARIO, follow_redirects=False)
    datos = {**FORMULARIO, "nombre": "Segundo guardado"}
    respuesta = cliente.post("/perfiles/data-engineer", data=datos, follow_redirects=False)
    assert respuesta.status_code == 303


def test_crear_otro_perfil_no_chuschea_el_primer_archivo(cliente: TestClient, tmp_path) -> None:
    cliente.post("/perfiles", data=FORMULARIO, follow_redirects=False)
    momento = time.time() + 600
    os.utime(tmp_path / "data-engineer.yml", (momento, momento))

    datos = {**FORMULARIO, "id": "analytics-engineer", "contacto__email": "b@example.com"}
    assert cliente.post("/perfiles", data=datos, follow_redirects=False).status_code == 303
    assert _existe(cliente, "analytics-engineer")


def test_archivos_estaticos_se_sirven(cliente: TestClient) -> None:
    assert cliente.get("/static/perfiles.js").status_code == 200
    assert cliente.get("/static/estilos.css").status_code == 200


YAML_VALIDO = """\
id: importado
nombre: Perfil importado
busqueda:
  keywords:
    - data analyst
cv:
  contacto:
    nombre: Grace
    apellido: Hopper
    email: grace@example.com
  skills:
    - nombre: SQL
"""


def test_la_lista_ofrece_importar_si_hay_yml_sin_cargar(cliente: TestClient, tmp_path) -> None:
    (tmp_path / "importado.yml").write_text(YAML_VALIDO, encoding="utf-8")
    texto = cliente.get("/perfiles").text
    assert "Importar desde perfiles/" in texto
    assert "importado" in texto


def _importacion(respuesta) -> str:
    """El resumen de la importación, ya desencriptado del query string."""
    location = respuesta.headers["location"]
    return unquote_plus(urlparse(location).query.partition("importacion=")[2])


def test_importar_yaml_carga_en_la_base(cliente: TestClient, tmp_path) -> None:
    (tmp_path / "importado.yml").write_text(YAML_VALIDO, encoding="utf-8")

    respuesta = cliente.post("/perfiles/importar", follow_redirects=False)
    assert respuesta.status_code == 303
    assert _importacion(respuesta) == "importado.yml: importado"

    assert _existe(cliente, "importado")
    with SessionLocal() as sesion:
        assert sesion.get(Perfil, "importado").nombre == "Perfil importado"

    # Y el archivo versionado queda tal cual, sin reformatear.
    assert (tmp_path / "importado.yml").read_text(encoding="utf-8") == YAML_VALIDO


def test_importar_revierte_un_perfil_ya_existente(cliente: TestClient, tmp_path) -> None:
    cliente.post("/perfiles", data=FORMULARIO, follow_redirects=False)

    # El YAML se escribe después: el alta desde la UI genera su propio archivo.
    otro = (
        YAML_VALIDO.replace("id: importado", "id: data-engineer")
        .replace("nombre: Perfil importado", "nombre: Versión del YAML")
        .replace("email: grace@example.com", "email: otra@example.com")
    )
    (tmp_path / "data-engineer.yml").write_text(otro, encoding="utf-8")

    respuesta = cliente.post("/perfiles/importar", follow_redirects=False)
    assert _importacion(respuesta) == "data-engineer.yml: actualizado"

    with SessionLocal() as sesion:
        assert sesion.get(Perfil, "data-engineer").nombre == "Versión del YAML"


def test_importar_saltea_un_yaml_invalido(cliente: TestClient, tmp_path) -> None:
    (tmp_path / "roto.yml").write_text(
        "id: roto\nnombre: X\nbusqueda:\n  keywords:\n    - ok\ncv:\n  contacto:\n"
        "    nombre: A\n    apellido: B\n    email: no-es-mail\n",
        encoding="utf-8",
    )
    (tmp_path / "bueno.yml").write_text(YAML_VALIDO, encoding="utf-8")

    respuesta = cliente.post("/perfiles/importar", follow_redirects=False)
    assert respuesta.status_code == 303
    assert "bueno.yml: importado" in _importacion(respuesta)
    assert "roto.yml: error" in _importacion(respuesta)
    assert "email" in _importacion(respuesta)

    # El bueno entra igual: uno roto no puede frenar la importación.
    assert _existe(cliente, "importado")
    assert not _existe(cliente, "roto")


def test_importar_no_ofrece_importar_de_nuevo(cliente: TestClient, tmp_path) -> None:
    (tmp_path / "importado.yml").write_text(YAML_VALIDO, encoding="utf-8")
    cliente.post("/perfiles/importar", follow_redirects=False)
    assert "Importar desde perfiles/" not in cliente.get("/perfiles").text