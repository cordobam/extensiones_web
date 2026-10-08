"""Tests del scraper de ZonaJobs, contra el JSON real del portal.

Los fixtures de `tests/fixtures/zonajobs/` se bajaron del API con el mismo
juego de headers que usa el cliente (x-site-id, x-pre-session-token, Chrome) y
se guardaron tal cual, salvo por recortar la descripción de los avisos que no
se leen en los tests. Si algún día un test falla, el motivo es que cambió el
portal, no que el fixture mintía.

Lo que estos tests cubren que Computrabajo no: la sesión del portal (el
uuid de un solo uso que devuelve un jwt), el 403 que hay cuando ese jwt vence,
y las fechas absolutas en hora argentina, que no dependen de cuándo corre la
corrida.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from radar.fuentes.base import Fuente
from radar.fuentes.zonajobs import (
    BASE_URL,
    SITE_ID,
    Cliente,
    a_cruda,
    modalidad,
    nombre_a_id,
    parsear_fecha,
    url_aviso,
)

FIXTURES = Path(__file__).parent / "fixtures" / "zonajobs"

ID_PRIMERO = "2191494"
TITULO_PRIMERO = "Desarrollador Mobile"
# "06-10-2026 18:56:15" es hora argentina: 3 horas más.
FECHA_PRIMERO = datetime(2026, 10, 6, 21, 56, 15, tzinfo=timezone.utc)


def _json(nombre: str) -> dict:
    return json.loads(FIXTURES.joinpath(nombre).read_text(encoding="utf-8"))


@pytest.fixture
def listado_p1() -> dict:
    return _json("listado_p1.json")


@pytest.fixture
def ultima() -> dict:
    return _json("ultima.json")


@pytest.fixture
def vacio() -> dict:
    return _json("vacio.json")


def _cliente(handler, **kwargs) -> tuple[Cliente, httpx.Client]:
    http = httpx.Client(transport=httpx.MockTransport(handler))
    return Cliente(delay=0, cliente=http, **kwargs), http


# ------------------------------------------------------------ url y slug


@pytest.mark.parametrize(
    ("keyword", "esperado"),
    [
        ("Soporte Técnico IT", "soporte-tecnico-it"),
        ("Mesa / Ayuda", "mesa-ayuda"),
        # La barra es la que separa secciones en el portal, por eso se va sola;
        # el `|`, que el JavaScript original deja adentro de la URL, acá se saca
        # (ver el docstring de `nombre_a_id`).
        ("Analista|QA", "analistaqa"),
        ("Data Engineer", "data-engineer"),
        ("  Senior  Dev  ", "senior-dev"),
        ("Entrenador/a IA", "entrenador-a-ia"),
    ],
)
def test_nombre_a_id(keyword: str, esperado: str) -> None:
    assert nombre_a_id(keyword) == esperado


def test_url_aviso_usa_el_slug_de_la_empresa() -> None:
    item = {
        "id": 123,
        "titulo": "Soporte Técnico",
        "empresa": "ACME S.A.",
        "confidencial": False,
    }
    assert url_aviso(item) == (
        f"{BASE_URL}/empleos/soporte-tecnico-acme-s.a.-123.html"
    )


def test_url_aviso_de_un_aviso_confidencial_no_inventa_empresa() -> None:
    item = {"id": 456, "titulo": "Mesa de Ayuda", "confidencial": True}
    assert url_aviso(item) == f"{BASE_URL}/empleos/mesa-de-ayuda-456.html"


# --------------------------------------------------------------- listado


def test_el_listado_trae_20_ofertas(listado_p1: dict) -> None:
    crudas = [a_cruda(item) for item in listado_p1["content"]]
    assert len(crudas) == 20
    assert all(c is not None for c in crudas)


def test_todos_los_ids_son_unicos(listado_p1: dict) -> None:
    ids = [str(item["id"]) for item in listado_p1["content"]]
    assert len(set(ids)) == len(ids)
    for id_ in ids:
        assert id_.isdigit()


def test_los_campos_de_la_primera_oferta(listado_p1: dict) -> None:
    primera = a_cruda(listado_p1["content"][0])
    assert primera is not None
    assert primera.external_id == ID_PRIMERO
    assert primera.titulo == TITULO_PRIMERO
    assert primera.empresa == "Italentitalentconnection Consultora"
    assert primera.ubicacion == "Capital Federal, Buenos Aires"
    assert primera.modalidad == "híbrido"
    assert primera.fecha_texto == "06-10-2026 18:56:15"
    assert primera.url == (
        f"{BASE_URL}/empleos/"
        "desarrollador-mobile-italentitalentconnection-consultora-2191494.html"
    )


def test_el_listado_trae_la_descripcion_completa(listado_p1: dict) -> None:
    """Es la razón por la que ZonaJobs no necesita bajar el detalle.

    El fixture recorta la descripción de los avisos que no se leen (queda un
    `[...]` al final), así que el assert es sobre los tres primeros.
    """
    crudas = [a_cruda(item) for item in listado_p1["content"]]
    assert all(c and c.descripcion for c in crudas)
    for cruda in crudas[:3]:
        assert len(cruda.descripcion or "") > 500


def test_los_modos_del_listado_son_los_del_contrato(listado_p1: dict) -> None:
    modalidades = {a_cruda(item).modalidad for item in listado_p1["content"]}
    assert modalidades == {"remoto", "híbrido", "presencial"}


def test_zonajobs_no_trae_salario(listado_p1: dict) -> None:
    assert all(a_cruda(item).salario is None for item in listado_p1["content"])


@pytest.mark.parametrize(
    ("item", "esperado"),
    [
        ({"id": 1, "titulo": "x", "modalidadTrabajo": "Remoto"}, "remoto"),
        ({"id": 1, "titulo": "x", "modalidadTrabajo": "Híbrido"}, "híbrido"),
        ({"id": 1, "titulo": "x", "modalidadTrabajo": "Presencial"}, "presencial"),
        ({"id": 1, "titulo": "x", "modalidadTrabajo": "Teletrabajo"}, None),
        ({"id": 1, "titulo": "x", "modalidadTrabajo": None}, None),
        ({"id": 1, "titulo": "x"}, None),
    ],
)
def test_modalidad(item: dict, esperado: str | None) -> None:
    assert modalidad(item.get("modalidadTrabajo")) == esperado


def test_un_aviso_incompleto_se_descarta() -> None:
    assert a_cruda({"titulo": "sin id"}) is None
    assert a_cruda({"id": 1, "titulo": ""}) is None
    assert a_cruda({"id": 1}) is None


# ------------------------------------------------------------ paginación


def test_la_primera_pagina_dice_que_hay_mas(listado_p1: dict) -> None:
    crudas, hay_siguiente = _listar_con(listado_p1)
    assert len(crudas) == 20
    assert hay_siguiente is True


def test_la_ultima_pagina_no_dice_que_hay_mas(ultima: dict) -> None:
    """`number=4, size=20, total=83` -> 5 páginas, ésta es la quinta."""
    crudas, hay_siguiente = _listar_con(ultima)
    assert len(crudas) == 3
    assert hay_siguiente is False


def test_un_listado_vacio_no_tiene_siguiente(vacio: dict) -> None:
    crudas, hay_siguiente = _listar_con(vacio)
    assert crudas == []
    assert hay_siguiente is False


def _listar_con(datos: dict) -> tuple[list, bool]:
    """`Cliente.listar` sobre un JSON cualquiera, sin tocar la red."""

    def handler(peticion: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=datos)

    cliente, http = _cliente(handler)
    with http:
        return cliente.listar("cualquiera", 1)


# --------------------------------------------------------------- cliente


def test_el_cliente_pega_al_endpoint_de_busqueda() -> None:
    llamadas: list[tuple[str, str, dict]] = []

    def handler(peticion: httpx.Request) -> httpx.Response:
        llamadas.append(
            (
                peticion.method,
                str(peticion.url),
                json.loads(peticion.content.decode("utf-8")),
            )
        )
        return httpx.Response(200, json=_json("listado_p1.json"))

    cliente, http = _cliente(handler)
    with http:
        cliente.listar("mesa de ayuda", 2)

    metodo, url, cuerpo = llamadas[0]
    assert metodo == "POST"
    assert url == (
        f"{BASE_URL}/api/avisos/searchV2?pageSize=20&page=1&sort=RECIENTES"
    )
    assert cuerpo["query"] == "mesa de ayuda"
    assert cuerpo["tipoDetalle"] == "full"


def test_el_cliente_manda_los_headers_de_un_navegador() -> None:
    """Sin UA de navegador ni Referer, Cloudflare contesta 403."""
    vistas: list[httpx.Headers] = []

    def handler(peticion: httpx.Request) -> httpx.Response:
        vistas.append(peticion.headers)
        return httpx.Response(200, json=_json("listado_p1.json"))

    cliente, http = _cliente(handler)
    with http:
        cliente.listar("mesa de ayuda", 1)

    assert "Chrome" in vistas[0]["user-agent"]
    assert vistas[0]["referer"] == f"{BASE_URL}/empleos.html"
    assert vistas[0]["origin"] == BASE_URL


def test_el_primer_request_trae_el_uuid_y_no_el_jwt() -> None:
    """El uuid es de un solo uso: manda uno por request hasta tener jwt."""
    vistas: list[httpx.Headers] = []

    def handler(peticion: httpx.Request) -> httpx.Response:
        vistas.append(peticion.headers)
        return httpx.Response(
            200, json=_json("listado_p1.json"), headers={"x-session-jwt": "jwt-1"}
        )

    cliente, http = _cliente(handler)
    with http:
        cliente.listar("mesa de ayuda", 1)
        cliente.listar("mesa de ayuda", 1)

    assert vistas[0]["x-site-id"] == SITE_ID
    assert "x-pre-session-token" in vistas[0]
    assert "x-session-jwt" not in vistas[0]

    # La segunda vuelta ya va con el jwt que devolvió la primera, y sin uuid.
    assert vistas[1]["x-site-id"] == SITE_ID
    assert vistas[1]["x-session-jwt"] == "jwt-1"
    assert "x-pre-session-token" not in vistas[1]


def test_el_site_id_va_tambien_con_el_jwt_puesto() -> None:
    """Regresión del smoke real: sin `x-site-id` el backend responde 400.

    El jwt no reemplaza al site-id, se le suma. Sin este test, la sesión se
    "arregla" para la primera petición y las cinco siguientes fallan con
    `400 No se incluyo el header "x-site-id"`, que es exactamente lo que pasó
    la primera vez que corrió `radar ingest --fuente zonajobs` contra el
    portal de verdad.
    """
    vistas: list[httpx.Headers] = []

    def handler(peticion: httpx.Request) -> httpx.Response:
        vistas.append(peticion.headers)
        return httpx.Response(
            200, json=_json("listado_p1.json"), headers={"x-session-jwt": "jwt-1"}
        )

    cliente, http = _cliente(handler)
    with http:
        cliente.listar("mesa de ayuda", 1)
        cliente.listar("mesa de ayuda", 1)

    assert len(vistas) == 2
    for encabezados in vistas:
        assert encabezados["x-site-id"] == SITE_ID
    assert "x-session-jwt" in vistas[1]


def test_si_rechazan_el_jwt_se_pide_una_sesion_nueva() -> None:
    """El caso real: el jwt vence y el portal responde 403.

    El sitio, ante eso, manda un uuid fresco. El cliente hace lo mismo y sigue.
    """
    vistas: list[httpx.Headers] = []

    def handler(peticion: httpx.Request) -> httpx.Response:
        vistas.append(peticion.headers)
        if "x-session-jwt" in peticion.headers:
            return httpx.Response(403, text="sesion vencida")
        return httpx.Response(
            200, json=_json("listado_p1.json"), headers={"x-session-jwt": "jwt-2"}
        )

    cliente, http = _cliente(handler)
    with http:
        cliente.listar("mesa de ayuda", 1)
        crudas, _ = cliente.listar("mesa de ayuda", 1)

    assert len(vistas) == 3
    assert len(crudas) == 20
    # La tercera es la repetida, con uuid y sin jwt.
    assert "x-session-jwt" not in vistas[2]
    assert "x-pre-session-token" in vistas[2]
    assert cliente._jwt == "jwt-2"


def test_un_403_sin_sesion_se_propaga() -> None:
    """Si ni el uuid convence a Cloudflare, no hay segunda vuelta que valga."""

    def handler(peticion: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="cloudflare")

    cliente, http = _cliente(handler)
    with http:
        with pytest.raises(httpx.HTTPStatusError):
            cliente.listar("mesa de ayuda", 1)


def test_el_cliente_reintenta_un_error_de_red() -> None:
    intentos = {"n": 0}

    def handler(peticion: httpx.Request) -> httpx.Response:
        intentos["n"] += 1
        if intentos["n"] < 3:
            raise httpx.ConnectError("se cayó", request=peticion)
        return httpx.Response(200, json=_json("listado_p1.json"))

    cliente, http = _cliente(handler)
    with http:
        crudas, _ = cliente.listar("mesa de ayuda", 1)

    assert intentos["n"] == 3
    assert len(crudas) == 20


def test_entre_peticiones_hace_pausa() -> None:
    """El mismo delay que Computrabajo: es lo que respeta robots.txt."""
    import time as reloj

    with Cliente(delay=0.3) as cliente:
        cliente._esperar()
        inicio = reloj.monotonic()
        cliente._esperar()
        cliente._esperar()
        transcurrido = reloj.monotonic() - inicio

    assert transcurrido >= 0.25


def test_el_cliente_es_una_fuente() -> None:
    """El contrato es lo que permite meterlo en la ingesta genérica."""
    with Cliente(delay=0) as cliente:
        assert isinstance(cliente, Fuente)
        assert cliente.id == "zonajobs"


# ------------------------------------------------------------------ fecha


def test_la_fecha_es_hora_argentina_pasada_a_utc() -> None:
    assert parsear_fecha("06-10-2026 18:56:15") == FECHA_PRIMERO


def test_tambien_acepta_la_fecha_corta() -> None:
    assert parsear_fecha("06-10-2026") == datetime(
        2026, 10, 6, 3, 0, tzinfo=timezone.utc
    )


def test_una_fecha_desconocida_da_none() -> None:
    assert parsear_fecha(None) is None
    assert parsear_fecha("") is None
    assert parsear_fecha("ayer") is None
    assert parsear_fecha("2026-10-06 18:56:15") is None


def test_la_fuente_usa_el_parser_de_fechas() -> None:
    cliente = Cliente(delay=0)
    cruda = a_cruda({"id": 1, "titulo": "x", "fechaHoraPublicacion": "06-10-2026 18:56:15"})
    assert cliente.fecha(cruda, datetime.now(timezone.utc)) == FECHA_PRIMERO


def test_sin_fecha_la_fuente_devuelve_none() -> None:
    cliente = Cliente(delay=0)
    cruda = a_cruda({"id": 1, "titulo": "x"})
    assert cliente.fecha(cruda, datetime.now(timezone.utc)) is None
