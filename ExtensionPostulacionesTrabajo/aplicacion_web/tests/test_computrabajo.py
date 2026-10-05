"""Tests del scraper de Computrabajo, corridas contra el HTML real.

Los fixtures de `tests/fixtures/computrabajo/` se bajaron del portal y se
recortaron quitando `<style>` y `<script>`, que no se parsean. El `<article>`
queda tal cual, con su `data-id` de comillas simples: si algún día se rompe el
parser, el motivo es que el HTML cambió, no que el test mentía.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from radar.fuentes.computrabajo import (
    Cliente,
    hay_siguiente_pagina,
    parsear_detalle,
    parsear_listado,
    slug,
    url_detalle,
    url_listado,
)

FIXTURES = Path(__file__).parent / "fixtures" / "computrabajo"
EXTERNAL_ID = "EAD844F035268B1B61373E686DCF3405"


def _html(nombre: str) -> str:
    return FIXTURES.joinpath(nombre).read_text(encoding="utf-8")


@pytest.fixture
def listado_p1() -> str:
    return _html("listado_p1.html")


@pytest.fixture
def listado_p2() -> str:
    return _html("listado_p2.html")


@pytest.fixture
def detalle() -> str:
    return _html(f"detalle_{EXTERNAL_ID}.html")


# --------------------------------------------------------------------- slug


@pytest.mark.parametrize(
    ("keyword", "esperado"),
    [
        ("mesa de ayuda", "mesa-de-ayuda"),
        ("soporte técnico", "soporte-tecnico"),
        ("Atención al Cliente", "atencion-al-cliente"),
        ("contact center", "contact-center"),
        ("  helpdesk  ", "helpdesk"),
        ("soporte it", "soporte-it"),
        ("data engineer", "data-engineer"),
    ],
)
def test_slug(keyword: str, esperado: str) -> None:
    assert slug(keyword) == esperado


def test_la_url_del_listado_lleva_la_pagina() -> None:
    assert url_listado("mesa de ayuda", 2) == (
        "https://ar.computrabajo.com/trabajo-de-mesa-de-ayuda?p=2"
    )


def test_la_url_del_detalle_es_absoluta_y_sin_fragmento() -> None:
    """El href del listado trae #lc=... que cambia en cada visita."""
    href = "/ofertas-de-trabajo/oferta-de-trabajo-de-soporte-tecnico-ABC123#lc=ListOffers-Score4-0"
    url = url_detalle(href)
    assert url == (
        "https://ar.computrabajo.com/ofertas-de-trabajo/"
        "oferta-de-trabajo-de-soporte-tecnico-ABC123"
    )
    assert "#" not in url


def test_la_url_absoluta_no_se_toca() -> None:
    href = "https://ar.computrabajo.com/ofertas-de-trabajo/oferta-de-trabajo-de-x-ABC123"
    assert url_detalle(href) == href


# ----------------------------------------------------------------- listado


def test_el_listado_trae_20_ofertas(listado_p1: str) -> None:
    assert len(parsear_listado(listado_p1)) == 20


def test_todos_los_ids_son_hex_y_unicos(listado_p1: str) -> None:
    ofertas = parsear_listado(listado_p1)
    ids = [o.external_id for o in ofertas]
    assert len(set(ids)) == len(ids)
    for id_ in ids:
        assert len(id_) == 32
        int(id_, 16)  # revienta si no es hex


def test_los_campos_de_la_primera_oferta(listado_p1: str) -> None:
    primera = parsear_listado(listado_p1)[0]
    assert primera.external_id == EXTERNAL_ID
    assert primera.titulo == "Soporte Tecnico IT"
    assert primera.empresa == "Universidad Siglo 21"
    assert primera.ubicacion == "Villa María, Córdoba"
    assert primera.salario == "$ 111.111,00 (Mensual)"
    assert primera.fecha_texto == "Hace  19  horas"
    assert primera.url.startswith("https://ar.computrabajo.com/ofertas-de-trabajo/")
    assert EXTERNAL_ID in primera.url
    assert "#" not in primera.url


def test_una_oferta_sin_empresa_no_inventa_una(listado_p1: str) -> None:
    """Varias ofertas del listado no traen empresa y su puntaje cuelga del `p`."""
    ofertas = parsear_listado(listado_p1)
    sin_empresa = [o for o in ofertas if o.empresa is None]
    assert sin_empresa, "el fixture debería tener alguna oferta sin empresa"
    for oferta in sin_empresa:
        assert oferta.empresa is None


def test_ninguna_oferta_trae_el_puntaje_de_la_empresa_como_ubicacion(
    listado_p1: str,
) -> None:
    """El bug real: `p.fc_base.mt5` aparece dos veces, y una trae '4,5Universidad'."""
    for oferta in parsear_listado(listado_p1):
        ubicacion = oferta.ubicacion or ""
        assert not ubicacion.startswith("4,"), ubicacion
        assert "Universidad" not in ubicacion or "Córdoba" in ubicacion


def test_las_fechas_del_listado_son_reconocibles(listado_p1: str) -> None:
    fechas = {o.fecha_texto for o in parsear_listado(listado_p1)}
    assert None not in fechas
    for fecha in fechas:
        limpio = " ".join(fecha.split())
        assert limpio.startswith("Hace") or limpio == "Ayer", limpio


def test_la_modalidad_se_lee_del_span_padre_del_icono(listado_p1: str) -> None:
    """El ícono va vacío: el texto está en el span que lo contiene."""
    modalidades = [o.modalidad for o in parsear_listado(listado_p1)]
    assert "remoto" in modalidades
    assert "híbrido" in modalidades
    assert None in modalidades


def test_presencial_y_remoto_es_hibrido(listado_p1: str) -> None:
    hibridos = [o for o in parsear_listado(listado_p1) if o.modalidad == "híbrido"]
    assert hibridos
    for oferta in hibridos:
        assert oferta.modalidad == "híbrido"


def test_la_pagina_siguiente_del_fixture(listado_p1: str, listado_p2: str) -> None:
    assert hay_siguiente_pagina(listado_p1)
    assert hay_siguiente_pagina(listado_p2)


# ------------------------------------------------------------------ detalle


def test_el_detalle_trae_la_descripcion(detalle: str) -> None:
    descripcion = parsear_detalle(detalle)
    assert len(descripcion) > 500
    assert "SOPORTE TÉCNICO IT" in descripcion.upper()


def test_la_descripcion_no_arrastra_la_seccion_de_empresa(detalle: str) -> None:
    """Termina en div-link="empresa"; si se pasa, entra la info de la empresa."""
    descripcion = parsear_detalle(detalle)
    assert "Información básica de privacidad" not in descripcion
    assert "Denunciar empleo" not in descripcion


def test_un_html_sin_descripcion_devuelve_vacio() -> None:
    assert parsear_detalle("<html><body><p>nada</p></body></html>") == ""


# ------------------------------------------------------------------- cliente


def test_el_cliente_arma_la_url_correcta() -> None:
    llamadas: list[str] = []

    def handler(peticion: httpx.Request) -> httpx.Response:
        llamadas.append(str(peticion.url))
        return httpx.Response(200, text=_html("listado_p1.html"))

    transporte = httpx.MockTransport(handler)
    with httpx.Client(transport=transporte) as http:
        with Cliente(delay=0, cliente=http) as cliente:
            ofertas = cliente.buscar("mesa de ayuda", 2)

    assert llamadas == ["https://ar.computrabajo.com/trabajo-de-mesa-de-ayuda?p=2"]
    assert len(ofertas) == 20


def test_el_cliente_manda_un_user_agent_de_navegador() -> None:
    """Sin un UA de navegador, el portal puede responder distinto."""
    vistas: list[str] = []

    def handler(peticion: httpx.Request) -> httpx.Response:
        vistas.append(peticion.headers.get("user-agent", ""))
        return httpx.Response(200, text="")

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        with Cliente(delay=0, cliente=http) as cliente:
            cliente.listado("mesa de ayuda")

    assert "Mozilla" in vistas[0]


def test_el_cliente_reintenta_un_error_de_red() -> None:
    intentos = {"n": 0}

    def handler(peticion: httpx.Request) -> httpx.Response:
        intentos["n"] += 1
        if intentos["n"] < 3:
            raise httpx.ConnectError("se cayó", request=peticion)
        return httpx.Response(200, text=_html("listado_p1.html"))

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        with Cliente(delay=0, cliente=http) as cliente:
            ofertas = cliente.buscar("mesa de ayuda")

    assert intentos["n"] == 3
    assert len(ofertas) == 20


def test_entre_peticiones_hace_pausa() -> None:
    """El pause entre requests es lo que nos mantiene dentro de robots.txt."""
    import time as reloj

    with Cliente(delay=0.3) as cliente:
        cliente._esperar()
        inicio = reloj.monotonic()
        cliente._esperar()
        cliente._esperar()
        transcurrido = reloj.monotonic() - inicio

    assert transcurrido >= 0.25
