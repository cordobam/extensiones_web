"""Tests de la pantalla de ofertas.

Usan la base real (`radar_laboral`) con la dependencia sobreescrita por una
sesión única por test, como `test_api_perfiles.py`.

Las ofertas y los matches no se escriben a mano: se arma un perfil y se corre el
matching de verdad, así que los tests también quedan atados a las reglas del
matching. Es lo que se quiere: si `Match.puntaje` se deja de escribir, esta
pantalla deja de ordenar y tiene que fallar acá.
"""

from __future__ import annotations

import re
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from radar import matching
from radar.api import app
from radar.db import SessionLocal
from radar.models import Match, Oferta, OfertaSkill, Perfil
from radar.perfiles import servicio
from radar.perfiles.validacion import (
    Busqueda,
    Contacto,
    Cv,
    PerfilCompleto,
    SkillCv,
)

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


@pytest.fixture
def cliente() -> Iterator[TestClient]:
    from radar import api

    sesion = SessionLocal()
    sesion.execute(delete(Match))
    sesion.execute(delete(Oferta))
    sesion.execute(delete(Perfil))
    sesion.commit()

    app.dependency_overrides[api.get_session] = lambda: sesion
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
        sesion.rollback()
        sesion.execute(delete(Match))
        sesion.execute(delete(Oferta))
        sesion.execute(delete(Perfil))
        sesion.commit()
        sesion.close()


# --------------------------------------------------------------------- helpers


def _perfil(id_perfil: str = "soporte-it", *, excluir: list[str] | None = None) -> PerfilCompleto:
    return PerfilCompleto(
        id=id_perfil,
        nombre=f"Perfil {id_perfil}",
        busqueda=Busqueda(keywords=["soporte"], excluir=excluir or []),
        cv=Cv(
            contacto=Contacto(
                nombre="Ada", apellido="Lovelace", email="ada@example.com"
            ),
            skills=[
                SkillCv(nombre="Soporte Técnico", obligatorio=True),
                SkillCv(nombre="Atención al Cliente"),
                SkillCv(nombre="ServiceNow"),
            ],
        ),
    )


def _cargar_perfil(sesion, perfil: PerfilCompleto) -> None:
    """Guarda un perfil, lo activa, y lo puntúa contra lo que ya está en la base."""
    servicio.guardar(sesion, perfil, escribir_yaml=False)
    servicio.activar(sesion, perfil.id)
    sesion.commit()
    matching.evaluar_perfil(sesion, perfil)
    sesion.commit()


def _poblar(sesion, ofertas: list[tuple[str, str, list[str]]], perfil=None) -> None:
    """Guarda ofertas, un perfil activo, y los puntúa con el matching real.

    Las tuplas son (external_id, título, skills). Las que no tienen ninguna
    skill quedan `descartada`, que es justo el caso que hay que probar.
    """
    filas = [
        Oferta(
            external_id=external_id,
            fuente="computrabajo",
            titulo=titulo,
            empresa=f"Empresa {external_id[-1]}",
            skills=[OfertaSkill(skill=s) for s in skills],
        )
        for external_id, titulo, skills in ofertas
    ]
    sesion.add_all(filas)
    sesion.commit()
    _cargar_perfil(sesion, perfil if perfil is not None else _perfil())


# ----------------------------------------------------------------- la pantalla


def test_los_tests_no_escriben_yaml_en_perfiles(tmp_path, monkeypatch) -> None:
    """Regresión de la fase 7, que volvió por la puerta de atrás.

    `servicio.guardar` escribe el YAML salvo que se le pida lo contrario, y su
    `directorio` por defecto es el de `perfiles/`. Estos tests crean perfiles
    para poder puntuar ofertas, así que sin `escribir_yaml=False` dejaban
    `perfiles/uno.yml`, `perfiles/otro.yml` y compañía después de cada corrida.
    No rompía nada, pero ensuciaba el repo y hacía creer que faltaban perfiles
    por importar.
    """
    from radar import api

    directorio = tmp_path / "perfiles"
    directorio.mkdir()

    class Ajustes:
        perfiles_dir = directorio

    monkeypatch.setattr(api, "get_settings", lambda: Ajustes())

    sesion = SessionLocal()
    app.dependency_overrides[api.get_session] = lambda: sesion
    try:
        cliente = TestClient(app)
        cliente.get("/ofertas?perfil=soporte-it")
    finally:
        app.dependency_overrides.clear()
        sesion.execute(delete(Match))
        sesion.execute(delete(Oferta))
        sesion.execute(delete(Perfil))
        sesion.commit()
        sesion.close()

    assert list(directorio.glob("*.yml")) == []


def test_sin_perfiles_invita_a_crear_uno(cliente: TestClient) -> None:
    r = cliente.get("/ofertas")

    assert r.status_code == 200
    assert "Todavía no cargaste ningún perfil" in r.text
    assert "/perfiles" in r.text


def test_sin_ofertas_manda_correr_ingest(cliente: TestClient) -> None:
    with SessionLocal() as sesion:
        servicio.guardar(sesion, _perfil(), escribir_yaml=False)
        servicio.activar(sesion, "soporte-it")
        sesion.commit()

    r = cliente.get("/ofertas?perfil=soporte-it")

    assert "No hay ofertas en la base todavía" in r.text
    assert "radar ingest --perfil soporte-it" in r.text


def test_ofertas_sin_puntuar_manda_correr_match(cliente: TestClient) -> None:
    """Es el estado más fácil de confundir con "no tengo nada".

    Traer ofertas y puntuarlas son dos pasos separados, y después de correr el
    primero es normal mirar la pantalla. Si dijera "no hay ofertas" con 40 en la
    base, el usuario pensaría que la ingesta no funcionó.
    """
    with SessionLocal() as sesion:
        servicio.guardar(sesion, _perfil(), escribir_yaml=False)
        servicio.activar(sesion, "soporte-it")
        sesion.add(Oferta(external_id="SIN-MATCH", fuente="computrabajo", titulo="Algo"))
        sesion.commit()

    r = cliente.get("/ofertas?perfil=soporte-it")

    assert "40 ofertas" not in r.text
    assert "radar match --perfil soporte-it" in r.text
    assert "1 ofertas en la base" in r.text


def test_la_lista_ordena_por_puntaje(cliente: TestClient) -> None:
    with SessionLocal() as sesion:
        _poblar(
            sesion,
            [
                ("OF1", "Soporte Junior", ["Soporte Técnico"]),
                ("OF2", "Soporte Técnico Senior N2", ["Soporte Técnico", "ServiceNow"]),
                ("OF3", "Atención al Cliente", ["Atención al Cliente"]),
            ],
        )

    r = cliente.get("/ofertas?perfil=soporte-it")

    cuerpo = r.text
    # La que tiene las dos skills va primera; el orden importa más que nada
    # en una pantalla cuyo propósito es ordenar.
    assert cuerpo.index("Soporte Técnico Senior N2") < cuerpo.index("Soporte Junior")


def test_las_descartadas_no_aparecen_sin_pedirlas(cliente: TestClient) -> None:
    with SessionLocal() as sesion:
        _poblar(
            sesion,
            [
                ("OF1", "Puesto Bueno", ["Soporte Técnico"]),
                ("OF9", "Puesto Irrelevante", ["Linux"]),
            ],
        )

    r = cliente.get("/ofertas?perfil=soporte-it")

    assert "Puesto Bueno" in r.text
    assert "Puesto Irrelevante" not in r.text
    # Pero el contador las anuncia, para que se sepa que están ahí.
    assert "1</strong> descartada" in r.text


def test_mostrar_descartadas_las_trae(cliente: TestClient) -> None:
    with SessionLocal() as sesion:
        _poblar(
            sesion,
            [
                ("OF1", "Puesto Bueno", ["Soporte Técnico"]),
                ("OF9", "Puesto Irrelevante", ["Linux"]),
            ],
        )

    r = cliente.get("/ofertas?perfil=soporte-it&descartadas=1")

    assert "Puesto Irrelevante" in r.text


def test_filtro_por_nivel(cliente: TestClient) -> None:
    with SessionLocal() as sesion:
        _poblar(
            sesion,
            [
                ("OF1", "Con Una", ["Soporte Técnico"]),
                ("OF2", "Con Dos", ["Soporte Técnico", "ServiceNow"]),
                ("OF9", "Sin Nada", ["Linux"]),
            ],
        )

    # "Con Dos" es `alta`: cumple la obligatoria y 1 de 2 deseables, y el umbral
    # de 0.5 es inclusivo. "Con Una" es `baja` porque no matchea ninguna
    # deseable, y no `media`: ésa es la diferencia entre las dos.
    r = cliente.get("/ofertas?perfil=soporte-it&nivel=alta")

    assert "Con Dos" in r.text
    assert "Con Una" not in r.text


def test_filtro_por_texto_al_titulo_y_la_empresa(cliente: TestClient) -> None:
    with SessionLocal() as sesion:
        _poblar(
            sesion,
            [
                ("OF1", "Tecnico de Soporte", ["Soporte Técnico"]),
                ("OF2", "Analista de Datos", ["Soporte Técnico"]),
            ],
        )

    assert "Tecnico de Soporte" in cliente.get("/ofertas?perfil=soporte-it&texto=soporte").text
    # La empresa es "Empresa 1" / "Empresa 2".
    assert "Analisis" not in cliente.get("/ofertas?perfil=soporte-it&texto=empresa 2").text


def test_filtro_por_skill(cliente: TestClient) -> None:
    with SessionLocal() as sesion:
        _poblar(
            sesion,
            [
                ("OF1", "Con ServiceNow", ["Soporte Técnico", "ServiceNow"]),
                ("OF2", "Sin ServiceNow", ["Soporte Técnico"]),
            ],
        )

    r = cliente.get("/ofertas?perfil=soporte-it&skill=ServiceNow")

    assert "Con ServiceNow" in r.text
    assert "Sin ServiceNow" not in r.text


def test_filtro_imposible_no_rompe(cliente: TestClient) -> None:
    """Una URL escrita a mano no puede romper la consulta."""
    with SessionLocal() as sesion:
        _poblar(sesion, [("OF1", "Algo", ["Soporte Técnico"])])

    for query in (
        "nivel=inventado",
        "pagina=-3",
        "pagina=abc",
        "texto=" + "x" * 400,
        "descartadas=quizá",
    ):
        r = cliente.get(f"/ofertas?perfil=soporte-it&{query}")
        assert r.status_code == 200, query


def test_paginacion_no_repite_ni_pierde(cliente: TestClient) -> None:
    """Con puntajes empatados el orden tiene que ser estable.

    Sin un desempate por id, PostgreSQL puede devolver las dos filas en orden
    distinto en cada consulta y una oferta desaparece entre páginas.
    """
    from radar.ofertas import POR_PAGINA

    total = POR_PAGINA + 5
    with SessionLocal() as sesion:
        # Todas con el mismo puntaje a propósito: el caso donde el orden por
        # puntaje solo no alcanza.
        _poblar(
            sesion,
            [(f"EMP{i:03}", f"Oferta {i:03}", ["Soporte Técnico"]) for i in range(total)],
        )

    vistas: list[str] = []
    for pagina in (1, 2):
        r = cliente.get(f"/ofertas?perfil=soporte-it&pagina={pagina}")
        assert f"de {total}." in r.text, f"la página {pagina} no ve el total"
        for i in range(total):
            titulo = f"Oferta {i:03}"
            if titulo in r.text:
                vistas.append(titulo)

    assert len(vistas) == len(set(vistas)), "la paginación repitió ofertas"
    assert len(vistas) == total


# --------------------------------------------------------------- el "por qué"


def test_explica_por_que_una_oferta_media(cliente: TestClient) -> None:
    with SessionLocal() as sesion:
        _poblar(
            sesion,
            [("OF1", "Soporte Tecnico Junior", ["Soporte Técnico", "ServiceNow"])],
        )

    r = cliente.get("/ofertas?perfil=soporte-it")

    assert "Por qué" in r.text
    assert "Soporte Técnico" in r.text
    assert "1 de 2" in r.text  # deseables


def test_explica_por_que_una_descartada_por_excluir(cliente: TestClient) -> None:
    with SessionLocal() as sesion:
        perfil = _perfil(excluir=["trainee"])
        _poblar(
            sesion,
            [
                ("OF1", "Trainee Soporte Tecnico", ["Soporte Técnico"]),
                ("OF2", "Soporte Tecnicoreal", ["Soporte Técnico"]),
            ],
            perfil=perfil,
        )

    r = cliente.get("/ofertas?perfil=soporte-it&descartadas=1")

    assert "coincide con lo que pedís excluir" in r.text


def test_avisa_de_las_skills_fuera_del_catalogo(cliente: TestClient) -> None:
    """Una skill que el catálogo no conoce nunca va a matchear.

    Sin este aviso el usuario ve una lista corta y Cree que el matching anda
    mal, cuando el problema es una skill mal escrita en su CV.
    """
    from radar.perfiles.validacion import SkillCv

    perfil = _perfil()
    perfil.cv.skills = [
        *perfil.cv.skills,
        SkillCv(nombre="soldadura TIG al arco"),
    ]
    with SessionLocal() as sesion:
        _poblar(sesion, [("OF1", "Algo", ["Soporte Técnico"])], perfil=perfil)

    r = cliente.get("/ofertas?perfil=soporte-it")

    assert "no están en el catálogo" in r.text
    assert "soldadura TIG al arco" in r.text


# ------------------------------------------------------------- varios perfiles


def test_el_selector_muestra_contra_que_perfil_comparo(cliente: TestClient) -> None:
    """Dos perfiles ven las mismas ofertas, pero puntúan distinto.

    Por eso la pantalla tiene que decir contra cuál se comparó: sin eso, la
    misma lista es correcta e incorrecta según qué CV se mire.
    """
    with SessionLocal() as sesion:
        _poblar(sesion, [("OF1", "Algo", ["Soporte Técnico"])], perfil=_perfil("uno"))
        _cargar_perfil(sesion, _perfil("otro"))

    r = cliente.get("/ofertas?perfil=otro")

    assert "Comparando contra" in r.text
    assert "Perfil otro" in r.text


def test_sin_perfil_en_la_url_usa_el_activo(cliente: TestClient) -> None:
    with SessionLocal() as sesion:
        servicio.guardar(sesion, _perfil("inactivo"), escribir_yaml=False)
        _poblar(sesion, [("OF1", "Algo", ["Soporte Técnico"])], perfil=_perfil("activo"))

    r = cliente.get("/ofertas")

    assert "Perfil activo" in r.text


def test_perfil_inexistente_cae_al_activo(cliente: TestClient) -> None:
    """`?perfil=inventado` no puede ser un 500."""
    with SessionLocal() as sesion:
        _poblar(sesion, [("OF1", "Algo", ["Soporte Técnico"])], perfil=_perfil("soporte-it"))

    r = cliente.get("/ofertas?perfil=inventado")

    assert r.status_code == 200
    assert "Perfil soporte-it" in r.text

# ----------------------------------------------------------- filtro de provincia


def _poblar_ubicaciones(sesion, ubicaciones: list[str | None], perfil=None) -> None:
    """Una oferta por ubicación, con `Soporte Técnico` para que no sea descartada.

    A diferencia de `_poblar`, que arma las ofertas a mano con las skills en
    línea, acá cada oferta tiene su `ubicacion`, que es lo que se está probando.
    """
    filas = [
        Oferta(
            external_id=f"UB{indice:02}",
            fuente="computrabajo",
            titulo=f"Oferta {indice}",
            empresa=f"Empresa {indice}",
            ubicacion=ubicacion,
            skills=[OfertaSkill(skill="Soporte Técnico")],
        )
        for indice, ubicacion in enumerate(ubicaciones, start=1)
    ]
    sesion.add_all(filas)
    sesion.commit()
    _cargar_perfil(sesion, perfil if perfil is not None else _perfil())


def test_filtro_por_provincia_agrupa_las_tres_formas_de_buenos_aires(
    cliente: TestClient,
) -> None:
    """El motivo del filtro: Buenos Aires es el 66% de lo que trae el portal.

    Con la ubicación cruda, elegir "Buenos Aires" mostraría una sola de las tres
    ofertas de abajo.
    """
    with SessionLocal() as sesion:
        _poblar_ubicaciones(
            sesion,
            [
                "Belgrano, Capital Federal",
                "Avellaneda, Buenos Aires-GBA",
                "Pilar, Buenos Aires",
                "Córdoba, Córdoba",
            ],
        )

    r = cliente.get("/ofertas?perfil=soporte-it&provincia=Buenos+Aires")

    assert "Belgrano, Capital Federal" in r.text
    assert "Avellaneda, Buenos Aires-GBA" in r.text
    assert "Pilar, Buenos Aires" in r.text
    assert "Córdoba, Córdoba" not in r.text


def test_el_filtro_y_el_dropdown_usan_la_misma_cuenta(cliente: TestClient) -> None:
    """El número del `<select>` tiene que ser el número que trae el filtro.

    El `WHERE` del filtro y el `GROUP BY` del dropdown son dos expresiones
    separadas (`expr_provincia` en `ofertas._base` y en `ofertas.provincias_de`).
    Si se desincronizan, el menú promete una cantidad y el filtro entrega otra, y
    no hay ningún error: sólo una lista que no cierra.
    """
    with SessionLocal() as sesion:
        _poblar_ubicaciones(
            sesion,
            [
                "Belgrano, Capital Federal",
                "Recoleta, Capital Federal",
                "Avellaneda, Buenos Aires-GBA",
                "Pilar, Buenos Aires",
                "Córdoba, Córdoba",
                "Rosario, Santa Fe",
            ],
        )

    r = cliente.get("/ofertas?perfil=soporte-it&provincia=Buenos+Aires")

    # Cuatro ofertas de Buenos Aires, y el dropdown dice cuatro.
    assert r.text.count('class="oferta con-borde-') == 4
    assert '<option value="Buenos Aires" selected>' in r.text
    assert re.search(r'Buenos Aires \(4\)', r.text)
    # Las otras dos provincias con su propia cuenta.
    assert re.search(r'Córdoba \(1\)', r.text)
    assert re.search(r'Santa Fe \(1\)', r.text)


def test_el_dropdrop_no_ofrece_provincias_que_no_estan(cliente: TestClient) -> None:
    with SessionLocal() as sesion:
        _poblar_ubicaciones(sesion, ["Retiro, Capital Federal", "Córdoba, Córdoba"])

    r = cliente.get("/ofertas?perfil=soporte-it")

    assert '<option value="Buenos Aires"' in r.text
    assert '<option value="Córdoba"' in r.text
    assert '<option value="Salta"' not in r.text


def test_el_total_del_dropdown_es_la_suma_de_las_provincias(cliente: TestClient) -> None:
    """El "Todas" tiene que ser comparable con los números de al lado.

    Sale de la suma de las provincias y no de `listado.total`, porque los
    conteos de al lado no respetan los otros filtros: con un filtro de texto
    puesto, `listado.total` bajaría y el menú mezclaría dos reglas distintas.
    """
    with SessionLocal() as sesion:
        _poblar_ubicaciones(
            sesion,
            ["Retiro, Capital Federal", "Córdoba, Córdoba", "Salta, Salta"],
        )

    r = cliente.get("/ofertas?perfil=soporte-it")

    assert '<option value="">Todas (3)</option>' in r.text


def test_provincia_con_acentos_vuelve_bien_desde_la_url(cliente: TestClient) -> None:
    """Las provincias con acento y coma van y vuelven por el query string.

    Es un caso real y no hipotético: "Entre Ríos", "Río Negro", "Córdoba" y
    "Santiago del Estero" son cuatro de las trece provincias de las 248 ofertas.
    """
    with SessionLocal() as sesion:
        _poblar_ubicaciones(
            sesion,
            ["Paraná, Entre Ríos", "Viedma, Río Negro", "Córdoba, Córdoba"],
        )

    # `Córdoba` sin tilde tiene que ser la misma provincia, no una desconocida.
    for query in ("C%C3%B3rdoba", "cordoba", "CORDOBA"):
        r = cliente.get(f"/ofertas?perfil=soporte-it&provincia={query}")
        assert "Córdoba, Córdoba" in r.text, query
        assert '<option value="Córdoba" selected>' in r.text, query

    r = cliente.get("/ofertas?perfil=soporte-it&provincia=Entre+R%C3%ADos")
    assert "Paraná, Entre Ríos" in r.text
    assert "Viedma, Río Negro" not in r.text


def test_provincia_inexistente_se_ignora(cliente: TestClient) -> None:
    """Como `nivel=inventado`: se descarta y se ve la lista entera.

    La alternativa sería mostrar "ninguna oferta coincide con el filtro", pero
    el `<select>` quedaría sin ninguna opción elegida y el usuario vería la
    lista completa sin entender por qué el filtro que puso no hizo nada.
    """
    with SessionLocal() as sesion:
        _poblar_ubicaciones(sesion, ["Retiro, Capital Federal", "Córdoba, Córdoba"])

    r = cliente.get("/ofertas?perfil=soporte-it&provincia=Mar+del+Plata")

    assert r.status_code == 200
    assert "Retiro, Capital Federal" in r.text
    assert "Córdoba, Córdoba" in r.text
    assert "Ninguna oferta coincide con el filtro" not in r.text


def test_una_ciudad_no_es_una_provincia(cliente: TestClient) -> None:
    """`Mar del Plata` es una ciudad de Buenos Aires, no una provincia.

    Está en el `GROUP BY` sólo si alguna vez una ubicación llega sin la parte de
    la provincia, y en ese caso tiene que quedar como opción propia y no fundirse
    con Buenos Aires.
    """
    with SessionLocal() as sesion:
        _poblar_ubicaciones(sesion, ["Mar del Plata", "Argentina"])

    r = cliente.get("/ofertas?perfil=soporte-it&provincia=Argentina")

    assert '<option value="Mar del Plata"' in r.text
    assert "Argentina" in r.text


def test_el_filtro_de_provincia_se_combina_con_los_demas(cliente: TestClient) -> None:
    with SessionLocal() as sesion:
        _poblar_ubicaciones(
            sesion,
            [
                "Retiro, Capital Federal",
                "Recoleta, Capital Federal",
                "Córdoba, Córdoba",
            ],
        )

    r = cliente.get(
        "/ofertas?perfil=soporte-it&provincia=Buenos+Aires&texto=Oferta+1"
    )

    assert r.text.count('class="oferta con-borde-') == 1
    assert "Oferta 1" in r.text


def test_la_paginacion_conserva_la_provincia(cliente: TestClient) -> None:
    """Si el link de página no lleva el filtro, cambiar de página lo pierde.

    El orden es por puntaje y todas estas ofertas empatan, así que el corte de
    página cae en lugares distintos si el filtro no está puesto.
    """
    with SessionLocal() as sesion:
        _poblar_ubicaciones(
            sesion,
            ["Retiro, Capital Federal"] * 15 + ["Córdoba, Córdoba"] * 15,
        )

    base = cliente.get("/ofertas?perfil=soporte-it&provincia=Buenos+Aires")
    assert "26–30" not in base.text

    pagina2 = cliente.get(
        "/ofertas?perfil=soporte-it&provincia=Buenos+Aires&pagina=2"
    )

    assert pagina2.status_code == 200
    # Sin el filtro en la URL, la página 2 mostraría Córdoba.
    assert "Córdoba, Córdoba" not in pagina2.text
    assert "Mostrando 16–15" not in pagina2.text


def test_el_filtro_de_provincia_tolera_un_texto_largo(cliente: TestClient) -> None:
    """No es una URL rota, tiene que ser un 200 como cualquier otra.

    El recorte a 120 caracteres es el mismo que ya usan `texto` y `skill`.
    """
    with SessionLocal() as sesion:
        _poblar_ubicaciones(sesion, ["Retiro, Capital Federal"])

    r = cliente.get("/ofertas?perfil=soporte-it&provincia=" + "x" * 400)

    assert r.status_code == 200
    # No hay ninguna provincia de 400 caracteres, así que el filtro se descarta
    # y se ve la lista entera.
    assert "Retiro, Capital Federal" in r.text
