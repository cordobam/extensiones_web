"""Tests de la ingesta: del listado a la tabla `ofertas`.

No se toca la red. El `Cliente` va con un `httpx.MockTransport` que devuelve los
fixtures del portal, así que estos tests miden la lógica (filtro de fecha,
dedupe, upsert) y no el portal.

Tocan PostgreSQL de verdad, como `test_db.py`.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from sqlalchemy import delete

from radar.db import SessionLocal
from radar.fuentes.base import OfertaCruda
from radar.fuentes.computrabajo import Cliente
from radar.fuentes.zonajobs import Cliente as ClienteZonaJobs
from radar.ingesta import _actualizar_skills, ingestar, ingestar_perfil
from radar.models import Match, Oferta, OfertaSkill, Perfil
from radar.config import get_settings
from radar.perfiles import servicio
from radar.perfiles.validacion import PerfilCompleto

FIXTURES = Path(__file__).parent / "fixtures" / "computrabajo"
EXTERNAL_ID = "EAD844F035268B1B61373E686DCF3405"
# La ingesta guarda el id del portal con el prefijo de la fuente, para que la
# unicidad de `ofertas.external_id` sea global y no por portal.
CLAVE = f"computrabajo:{EXTERNAL_ID}"
LISTADO_P1 = "listado_p1.html"
LISTADO_P2 = "listado_p2.html"
DETALLE = f"detalle_{EXTERNAL_ID}.html"

# El fixture tiene "Hace 19 horas" y "Ayer". Las fechas son relativas, así que
# los tests de ventana necesitan un reloj fijo: sin esto, uno que pasa a las
# 23:50 falla solo por el reloj, no por el código.
# Segundos en cero: el reloj real haría que este archivo dependiera del minuto
# en que corre. Con AHORA_FIJO + 20s se puede cruzar el minuto, y entonces la
# fecha truncada sí cambia y el test falla una de cada tres veces.
AHORA_FIJO = datetime.now(timezone.utc).replace(second=0, microsecond=0)


@pytest.fixture(autouse=True)
def _yaml_a_temporal(tmp_path, monkeypatch):
    """Manda los YAML que escriben los tests a un directorio temporal.

    `servicio.guardar()` usa `perfiles/` por defecto. Los tests que crean
    perfiles lo llaman sin `directorio`, así que sin esto escriben archivos de
    verdad en el repo, con los datos ficticios de `_perfil()` adentro.
    """
    destino = tmp_path / "perfiles"
    destino.mkdir()
    # `get_settings()` está cacheado, así que hay que pisar el valor en la
    # instancia viva: cambiar el atributo de la clase no alcanza.
    monkeypatch.setattr(get_settings(), "perfiles_dir", destino)


@pytest.fixture
def sesion() -> Iterator:
    s = SessionLocal()
    s.execute(delete(Match))
    s.execute(delete(Oferta))
    s.execute(delete(Perfil))
    s.commit()
    try:
        yield s
    finally:
        s.rollback()
        s.execute(delete(Match))
        s.execute(delete(Oferta))
        s.execute(delete(Perfil))
        s.commit()
        s.close()


def _html(nombre: str) -> str:
    return FIXTURES.joinpath(nombre).read_text(encoding="utf-8")


LISTADO_VACIO = "<html><body><main></main></body></html>"


class PortalFalso:
    """Responde como Computrabajo, pero con los fixtures y un log de requests.

    Las páginas se devuelven por número: `?p=1` da la primera, `?p=2` la segunda
    y `?p=3` un listado vacío, que es lo que el portal hace cuando se terminó.
    """

    def __init__(
        self,
        paginas: list[str] | None = None,
        detalle: str | None = DETALLE,
        fallar_detalle: bool = False,
    ) -> None:
        self.paginas = paginas if paginas is not None else [LISTADO_P1]
        self.detalle = detalle
        self.fallar_detalle = fallar_detalle
        self.peticiones: list[str] = []

    def _handler(self, peticion: httpx.Request) -> httpx.Response:
        url = str(peticion.url)
        self.peticiones.append(url)

        if "/ofertas-de-trabajo/" in url:
            if self.fallar_detalle:
                return httpx.Response(503, text="no disponible")
            return httpx.Response(200, text=_html(self.detalle or ""))

        pagina = 1
        if "?p=" in url:
            pagina = int(url.split("?p=")[1].split("&")[0])
        if pagina <= len(self.paginas):
            return httpx.Response(200, text=_html(self.paginas[pagina - 1]))
        return httpx.Response(200, text=LISTADO_VACIO)

    def cliente(self, delay: float = 0.0) -> tuple[Cliente, httpx.Client]:
        http = httpx.Client(transport=httpx.MockTransport(self._handler))
        return Cliente(delay=delay, cliente=http), http


def _perfil(keywords: list[str]) -> PerfilCompleto:
    return PerfilCompleto.model_validate(
        {
            "id": "soporte-it",
            "nombre": "Soporte IT",
            "busqueda": {"keywords": keywords},
            "cv": {
                "contacto": {
                    "nombre": "Ada",
                    "apellido": "Lovelace",
                    "email": "ada@example.com",
                },
                "skills": [{"nombre": "Soporte Técnico"}],
            },
        }
    )


# ------------------------------------------------------------------ guardado


def test_guarda_las_ofertas_del_listado(sesion) -> None:
    portal = PortalFalso()
    cliente, http = portal.cliente()
    with http:
        resumen = ingestar_perfil(
            sesion, _perfil(["mesa de ayuda"]), fuentes=[cliente], ahora=AHORA_FIJO
        )

    guardadas = sesion.query(Oferta).all()
    assert resumen.nuevas == len(guardadas)
    assert len(guardadas) > 0


def test_una_oferta_tiene_los_campos_del_listado(sesion) -> None:
    portal = PortalFalso()
    cliente, http = portal.cliente()
    with http:
        ingestar_perfil(
            sesion, _perfil(["mesa de ayuda"]), fuentes=[cliente], ahora=AHORA_FIJO
        )

    fila = sesion.query(Oferta).filter(Oferta.external_id == CLAVE).one()
    assert fila.fuente == "computrabajo"
    assert fila.titulo == "Soporte Tecnico IT"
    assert fila.empresa == "Universidad Siglo 21"
    assert fila.ubicacion == "Villa María, Córdoba"
    assert fila.salario == "$ 111.111,00 (Mensual)"
    assert fila.url.startswith("https://ar.computrabajo.com/ofertas-de-trabajo/")
    assert "#" not in fila.url
    assert fila.fecha_publicacion is not None


def test_el_detalle_carga_descripcion_y_skills(sesion) -> None:
    portal = PortalFalso()
    cliente, http = portal.cliente()
    with http:
        ingestar_perfil(
            sesion, _perfil(["mesa de ayuda"]), fuentes=[cliente], ahora=AHORA_FIJO
        )

    fila = sesion.query(Oferta).filter(Oferta.external_id == CLAVE).one()
    assert fila.descripcion
    assert "SOPORTE TÉCNICO IT" in fila.descripcion.upper()
    assert {s.skill for s in fila.skills} >= {"Soporte Técnico"}


def test_sin_detalle_no_baja_ninguna_pagina_de_oferta(sesion) -> None:
    portal = PortalFalso()
    cliente, http = portal.cliente()
    with http:
        ingestar_perfil(
            sesion,
            _perfil(["mesa de ayuda"]),
            fuentes=[cliente],
            con_detalle=False,
            ahora=AHORA_FIJO,
        )

    assert not any("/ofertas-de-trabajo/" in u for u in portal.peticiones)
    fila = sesion.query(Oferta).filter(Oferta.external_id == CLAVE).one()
    assert fila.descripcion is None
    # Sin detalle tampoco hay descripción, así que las skills salen sólo del
    # título. Antes esta lista era vacía porque el título no se miraba; ahora
    # aporta justo lo que el matching necesita para no descartar la oferta.
    assert sorted(s.skill for s in fila.skills) == ["Soporte Técnico"]


def test_sin_detalle_las_skills_salen_del_titulo(sesion) -> None:
    """El título es la mitad de la detección, y funciona sin bajar el detalle.

    Es lo que permite usar `ingest --listados` para probar el pipeline rápido y
    todavía tener skills con las que puntuar.
    """
    portal = PortalFalso()
    cliente, http = portal.cliente()
    with http:
        ingestar_perfil(
            sesion,
            _perfil(["mesa de ayuda"]),
            fuentes=[cliente],
            con_detalle=False,
            ahora=AHORA_FIJO,
        )

    con_skills = sesion.query(Oferta).join(OfertaSkill).distinct().count()
    assert con_skills > 0


# ------------------------------------------------------------- fechas y dedupe


def test_el_filtro_de_ventana_descarta_lo_viejo(sesion) -> None:
    """El fixture trae "Ayer", "Hace 19 horas" y "Hace 2/3 días".

    Con ventana de 1 día entran las dos primeras y se descartan las demás.
    """
    portal = PortalFalso()
    cliente, http = portal.cliente()
    with http:
        resumen = ingestar_perfil(
            sesion, _perfil(["mesa de ayuda"]), fuentes=[cliente], dias=1, ahora=AHORA_FIJO
        )

    assert resumen.fuera_de_ventana > 0
    assert resumen.nuevas > 0
    assert resumen.nuevas + resumen.fuera_de_ventana == 20

    # El límite también va truncado a minutos, porque así es como se guardan
    # las fechas: si no, el borde puede caer 8 segundos y la comparación es falsa.
    limite = (AHORA_FIJO - timedelta(days=1)).replace(second=0, microsecond=0)
    for fila in sesion.query(Oferta).all():
        assert fila.fecha_publicacion >= limite


def test_una_ventana_de_365_dias_entra_todo(sesion) -> None:
    portal = PortalFalso()
    cliente, http = portal.cliente()
    with http:
        resumen = ingestar_perfil(
            sesion, _perfil(["mesa de ayuda"]), fuentes=[cliente], dias=365, ahora=AHORA_FIJO
        )

    assert resumen.fuera_de_ventana == 0
    assert resumen.nuevas == 20


def test_toda_oferta_guardada_tiene_fecha(sesion) -> None:
    portal = PortalFalso()
    cliente, http = portal.cliente()
    with http:
        ingestar_perfil(
            sesion, _perfil(["mesa de ayuda"]), fuentes=[cliente], ahora=AHORA_FIJO
        )

    sin_fecha = sesion.query(Oferta).filter(Oferta.fecha_publicacion.is_(None)).count()
    assert sin_fecha == 0


def test_correr_dos_veces_no_duplica(sesion) -> None:
    portal = PortalFalso()
    cliente, http = portal.cliente()
    with http:
        primera = ingestar_perfil(
            sesion, _perfil(["mesa de ayuda"]), fuentes=[cliente], ahora=AHORA_FIJO
        )
        count_tras_primera = sesion.query(Oferta).count()

        portal.peticiones.clear()
        segunda = ingestar_perfil(
            sesion, _perfil(["mesa de ayuda"]), fuentes=[cliente], ahora=AHORA_FIJO
        )

    assert primera.nuevas > 0
    assert segunda.nuevas == 0
    assert sesion.query(Oferta).count() == count_tras_primera


def test_la_segunda_corrida_no_vuelve_a_bajar_detalles(sesion) -> None:
    """Es lo que hace que la corrida repetida sea barata."""
    portal = PortalFalso()
    cliente, http = portal.cliente()
    with http:
        ingestar_perfil(
            sesion, _perfil(["mesa de ayuda"]), fuentes=[cliente], ahora=AHORA_FIJO
        )
        portal.peticiones.clear()
        ingestar_perfil(
            sesion, _perfil(["mesa de ayuda"]), fuentes=[cliente], ahora=AHORA_FIJO
        )

    detalles = [u for u in portal.peticiones if "/ofertas-de-trabajo/" in u]
    assert detalles == []


def test_varias_keywords_deduplican(sesion) -> None:
    """La misma oferta sale en varias keywords y se guarda una sola vez."""
    portal = PortalFalso()
    cliente, http = portal.cliente()
    with http:
        resumen = ingestar_perfil(
            sesion,
            _perfil(["mesa de ayuda", "soporte técnico", "helpdesk"]),
            fuentes=[cliente],
            ahora=AHORA_FIJO,
        )

    assert resumen.duplicadas > 0
    ids = [f.external_id for f in sesion.query(Oferta).all()]
    assert len(ids) == len(set(ids))


def test_correr_dos_veces_ve_las_mismas_ofertas(sesion) -> None:
    """La segunda corrida ve las mismas 20 y no reporta ninguna como nueva."""
    portal = PortalFalso()
    cliente, http = portal.cliente()
    with http:
        primera = ingestar_perfil(
            sesion, _perfil(["mesa de ayuda"]), fuentes=[cliente], ahora=AHORA_FIJO
        )

    otro = PortalFalso()
    cliente2, http2 = otro.cliente()
    with http2:
        segunda = ingestar_perfil(
            sesion, _perfil(["mesa de ayuda"]), fuentes=[cliente2], ahora=AHORA_FIJO
        )

    assert primera.ofertas_vistas == segunda.ofertas_vistas == 20
    assert primera.nuevas == 17
    assert segunda.nuevas == 0
    assert segunda.actualizadas == 0


def test_recorre_todas_las_paginas_hasta_que_se_acaban(sesion) -> None:
    """Con dos páginas distintas las pide las dos, y para al listado vacío."""
    portal = PortalFalso(paginas=[LISTADO_P1, LISTADO_P2])
    cliente, http = portal.cliente()
    with http:
        ingestar_perfil(
            sesion, _perfil(["mesa de ayuda"]), fuentes=[cliente], ahora=AHORA_FIJO
        )

    listados = [u for u in portal.peticiones if "/trabajo-de-" in u]
    assert len(listados) == 3  # p=1, p=2, y la p=3 que viene vacía
    assert listados[-1].endswith("?p=3")


def test_una_pagina_sin_nuevas_corta_el_recorrido(sesion) -> None:
    """Si la página 2 repite lo de la 1, para: ya la vio todas.

    Es el corte que hace barata la corrida repetida. El listado real viene
    ordenado por fecha, así que una página sin nada nuevo es la última.
    """
    portal = PortalFalso(paginas=[LISTADO_P1, LISTADO_P1])
    cliente, http = portal.cliente()
    with http:
        ingestar_perfil(
            sesion, _perfil(["mesa de ayuda"]), fuentes=[cliente], ahora=AHORA_FIJO
        )

    listados = [u for u in portal.peticiones if "/trabajo-de-" in u]
    assert len(listados) == 2  # p=1 y p=2, pero no la p=3


def test_una_pagina_vacia_cierra_el_recorrido(sesion) -> None:
    """El fixture de la p1 anuncia una p2, así que se pide y viene vacía: y ahí para.

    No pide la 3: un listado vacío significa que se terminó.
    """
    portal = PortalFalso(paginas=[LISTADO_P1])
    cliente, http = portal.cliente()
    with http:
        ingestar_perfil(
            sesion, _perfil(["mesa de ayuda"]), fuentes=[cliente], ahora=AHORA_FIJO
        )

    listados = [u for u in portal.peticiones if "/trabajo-de-" in u]
    assert len(listados) == 2
    assert listados[-1].endswith("?p=2")


def test_un_listado_vacio_corta_el_recorrido(sesion) -> None:
    portal = PortalFalso(paginas=[])
    cliente, http = portal.cliente()
    with http:
        resumen = ingestar_perfil(
            sesion, _perfil(["mesa de ayuda"]), fuentes=[cliente], ahora=AHORA_FIJO
        )

    assert resumen.nuevas == 0
    assert resumen.ofertas_vistas == 0


def test_un_detalle_que_falla_no_tira_la_corrida(sesion) -> None:
    """Una oferta caída se anota como fallida y el resto se sigue guardando."""
    portal = PortalFalso(fallar_detalle=True)
    cliente, http = portal.cliente()
    with http:
        resumen = ingestar_perfil(
            sesion, _perfil(["mesa de ayuda"]), fuentes=[cliente], ahora=AHORA_FIJO
        )

    assert resumen.fallidas
    assert resumen.nuevas > 0
    for fila in sesion.query(Oferta).all():
        assert fila.descripcion is None


# ----------------------------------------------------------- varios perfiles


def test_ingestar_sin_perfil_recorre_los_activos(sesion) -> None:
    """La Etapa 0 dejó que convivan varios perfiles: la ingesta los ve a todos."""
    for pid in ("soporte-it", "data-engineer"):
        servicio.guardar(sesion, _perfil(["mesa de ayuda"]).model_copy(update={"id": pid}))
    servicio.activar(sesion, "soporte-it")
    servicio.activar(sesion, "data-engineer")

    portal = PortalFalso()
    cliente, http = portal.cliente()
    with http:
        resumen = ingestar(sesion, fuentes=[cliente], ahora=AHORA_FIJO)

    assert sorted(resumen.perfiles) == ["data-engineer", "soporte-it"]


def test_ingestar_sin_activos_no_hace_nada(sesion) -> None:
    servicio.guardar(sesion, _perfil(["mesa de ayuda"]))
    portal = PortalFalso()
    cliente, http = portal.cliente()
    with http:
        resumen = ingestar(sesion, fuentes=[cliente], ahora=AHORA_FIJO)

    assert resumen.perfiles == []
    assert resumen.ofertas_vistas == 0
    assert portal.peticiones == []


def test_ingestar_un_perfil_concreto(sesion) -> None:
    servicio.guardar(sesion, _perfil(["mesa de ayuda"]))
    portal = PortalFalso()
    cliente, http = portal.cliente()
    with http:
        resumen = ingestar(sesion, "soporte-it", fuentes=[cliente], ahora=AHORA_FIJO)

    assert resumen.perfiles == ["soporte-it"]


def test_ingestar_un_perfil_inexistente_falla(sesion) -> None:
    from radar.perfiles.servicio import PerfilNoEncontrado

    with pytest.raises(PerfilNoEncontrado):
        ingestar(sesion, "no-existe")


# ------------------------------------------------------------------- resumen


def test_el_resumen_se_lee_bien(sesion) -> None:
    portal = PortalFalso()
    cliente, http = portal.cliente()
    with http:
        resumen = ingestar_perfil(
            sesion, _perfil(["mesa de ayuda"]), fuentes=[cliente], ahora=AHORA_FIJO
        )

    linea = resumen.linea()
    assert "vistas" in linea
    assert "nuevas" in linea


def test_dos_corridas_en_el_mismo_minuto_no_cuentan_como_cambio(sesion) -> None:
    """La fecha se calcula restándole horas al reloj de cada corrida.

    Sin truncar, dos corridas separadas por un segundo dan fechas distintas y
    todo se marca "actualizado". Truncando al minuto, dos corridas dentro del
    mismo minuto coinciden y la segunda no toca nada.
    """
    portal = PortalFalso()
    cliente, http = portal.cliente()
    with http:
        ingestar_perfil(
            sesion, _perfil(["mesa de ayuda"]), fuentes=[cliente], ahora=AHORA_FIJO
        )

    otro = PortalFalso()
    cliente2, http2 = otro.cliente()
    with http2:
        despues = ingestar_perfil(
            sesion,
            _perfil(["mesa de ayuda"]),
            fuentes=[cliente2],
            ahora=AHORA_FIJO + timedelta(seconds=20),
        )

    assert despues.nuevas == 0
    assert despues.actualizadas == 0
    assert despues.duplicadas == 17


def test_dos_corridas_en_minutos_distintos_mueven_la_fecha(sesion) -> None:
    """La truncación evita escrituras inútiles, no los cambios de verdad.

    "Hace 19 horas" va desplazándose con el reloj, así que una corrida 40
    minutos más tarde tiene otra fecha. Actualizarla en ese caso es lo correcto:
    refleja que la fecha estimada se corrió.
    """
    portal = PortalFalso()
    cliente, http = portal.cliente()
    with http:
        ingestar_perfil(
            sesion, _perfil(["mesa de ayuda"]), fuentes=[cliente], ahora=AHORA_FIJO
        )

    otro = PortalFalso()
    cliente2, http2 = otro.cliente()
    with http2:
        despues = ingestar_perfil(
            sesion,
            _perfil(["mesa de ayuda"]),
            fuentes=[cliente2],
            ahora=AHORA_FIJO + timedelta(minutes=40),
        )

    assert despues.nuevas == 0
    assert despues.actualizadas == 17


def test_re_correr_no_duplica_las_skills(sesion) -> None:
    """Regresión de un UniqueViolation sobre `uq_oferta_skill`.

    Cuando la fecha se mueve, la oferta se marca "actualizada" y sus skills se
    vuelven a escribir. Si eso se hace reemplazando la colección entera,
    SQLAlchemy borra las filas viejas e inserta las nuevas, y en un mismo flush
    el INSERT puede ejecutarse antes que el DELETE: `llave duplicada viola
    restricción de unicidad`. Pasaba en la segunda corrida de cualquier oferta
    cuya fecha se hubiera corrido.
    """
    portal = PortalFalso()
    cliente, http = portal.cliente()
    with http:
        ingestar_perfil(
            sesion, _perfil(["mesa de ayuda"]), fuentes=[cliente], ahora=AHORA_FIJO
        )

    antes = {
        f.external_id: sorted(s.skill for s in f.skills)
        for f in sesion.query(Oferta).all()
    }
    total_antes = sesion.query(OfertaSkill).count()

    otro = PortalFalso()
    cliente2, http2 = otro.cliente()
    with http2:
        ingestar_perfil(
            sesion,
            _perfil(["mesa de ayuda"]),
            fuentes=[cliente2],
            ahora=AHORA_FIJO + timedelta(minutes=40),
        )

    # Las mismas skills, la misma cantidad de filas, ni una de más.
    despues = {
        f.external_id: sorted(s.skill for s in f.skills)
        for f in sesion.query(Oferta).all()
    }
    assert despues == antes
    assert sesion.query(OfertaSkill).count() == total_antes

    # Y ninguna oferta tiene la misma skill repetida.
    for skills in despues.values():
        assert len(skills) == len(set(skills))


def test_una_skill_que_desaparece_se_borra(sesion) -> None:
    """El diff también tiene que saber borrar, no sólo agregar.

    Si el aviso se edita y ya no menciona una skill, la fila vieja tiene que
    irse: si no, el matching seguiría diciendo que la persona tiene esa skill.
    """
    portal = PortalFalso()
    cliente, http = portal.cliente()
    with http:
        ingestar_perfil(
            sesion, _perfil(["mesa de ayuda"]), fuentes=[cliente], ahora=AHORA_FIJO
        )

    oferta = sesion.query(Oferta).filter(Oferta.external_id == CLAVE).one()
    # Se simula que la detección cambió: la fila guardada tenía
    # "Soporte Técnico" y la nueva pasada sólo ve "Windows".
    _actualizar_skills(oferta, ["Windows"])
    sesion.commit()

    # Sólo se mira esta oferta: la corrida dejó 17 ofertas y 83 filas de skills.
    assert sorted(s.skill for s in oferta.skills) == ["Windows"]
    assert sesion.query(OfertaSkill).filter_by(oferta_id=oferta.id).count() == 1


def test_las_fechas_no_tienen_segundos(sesion) -> None:
    portal = PortalFalso()
    cliente, http = portal.cliente()
    with http:
        ingestar_perfil(
            sesion, _perfil(["mesa de ayuda"]), fuentes=[cliente], ahora=AHORA_FIJO
        )

    for fila in sesion.query(Oferta).all():
        assert fila.fecha_publicacion.second == 0
        assert fila.fecha_publicacion.microsecond == 0


def test_las_skills_no_se_repiten_dentro_de_una_oferta(sesion) -> None:
    portal = PortalFalso()
    cliente, http = portal.cliente()
    with http:
        ingestar_perfil(
            sesion, _perfil(["mesa de ayuda"]), fuentes=[cliente], ahora=AHORA_FIJO
        )

    for fila in sesion.query(Oferta).all():
        skills = [s.skill for s in fila.skills]
        assert len(skills) == len(set(skills))


# ------------------------------------------------------------- dos fuentes


ZONAJOBS = Path(__file__).parent / "fixtures" / "zonajobs"

# Reloj fijo para la corrida con los dos portales: las fechas de Computrabajo
# son relativas (viven con el reloj) y las de ZonaJobs son absolutas (octubre
# de 2026), así que hace falta una referencia compartida para que las dos caigan
# dentro de la ventana.
AHORA_DOS_PORTALES = datetime(2026, 10, 7, 15, 0, tzinfo=timezone.utc)


def _zonajobs(nombre: str) -> dict:
    return json.loads(ZONAJOBS.joinpath(nombre).read_text(encoding="utf-8"))


class PortalZonaJobsFalso:
    """Responde como ZonaJobs con el JSON guardado, por número de página.

    El portal pagina desde 0, igual que el cliente real: `page=0` es la
    primera. Las demás páginas devuelven el listado vacío, que es lo que hace
    el portal cuando se acabaron los resultados.
    """

    def __init__(self, paginas: list[dict] | None = None) -> None:
        self.paginas = paginas if paginas is not None else [_zonajobs("listado_p1.json")]
        self.peticiones: list[str] = []

    def _handler(self, peticion: httpx.Request) -> httpx.Response:
        self.peticiones.append(str(peticion.url))
        pagina = int(peticion.url.params.get("page", "0"))
        datos = self.paginas[pagina] if pagina < len(self.paginas) else _zonajobs("vacio.json")
        return httpx.Response(200, json=datos, headers={"x-session-jwt": "falso"})

    def cliente(self, delay: float = 0.0) -> tuple[ClienteZonaJobs, httpx.Client]:
        http = httpx.Client(transport=httpx.MockTransport(self._handler))
        return ClienteZonaJobs(delay=delay, cliente=http), http


def test_dos_portales_en_la_misma_corrida(sesion) -> None:
    """Computrabajo y ZonaJobs guardados juntos, cada uno con su prefijo.

    Es la razón de la migración 0005: `ofertas.external_id` es única en toda la
    tabla, así que con dos portales el id tiene que decir de quién es.
    """
    servicio.guardar(sesion, _perfil(["mesa de ayuda"]))
    servicio.activar(sesion, "soporte-it")

    computrabajo = PortalFalso()
    zonajobs = PortalZonaJobsFalso()
    cliente_compu, http1 = computrabajo.cliente()
    cliente_zona, http2 = zonajobs.cliente()

    with http1, http2:
        resumen = ingestar(
            sesion,
            fuentes=[cliente_compu, cliente_zona],
            dias=365,
            ahora=AHORA_DOS_PORTALES,
        )

    assert resumen.fuentes == ["computrabajo", "zonajobs"]
    assert resumen.nuevas == 40
    assert resumen.sin_fecha == 0

    guardadas = sesion.query(Oferta).all()
    por_fuente = {f.fuente for f in guardadas}
    assert por_fuente == {"computrabajo", "zonajobs"}

    for fila in guardadas:
        # prefijo, no casualidad: la fila dice de dónde es sin mirar `fuente`.
        assert fila.external_id.startswith(f"{fila.fuente}:")

    # Veinte de cada portal: ninguno de los dos se comió al otro.
    assert sum(1 for f in guardadas if f.fuente == "computrabajo") == 20
    assert sum(1 for f in guardadas if f.fuente == "zonajobs") == 20


def test_el_mismo_id_en_dos_fuentes_no_choca(sesion) -> None:
    """El caso que la migración 0005 cubre: ids idénticos en dos portales.

    Sin prefijo, el segundo `_guardar` habría encontrado la fila del primero y
    la habría pisado en vez de crear la suya.
    """
    cruda = OfertaCruda(
        external_id="123",
        titulo="Mesa de Ayuda",
        empresa=None,
        url="https://ejemplo.com/aviso-123",
        ubicacion=None,
        modalidad=None,
        salario=None,
        fecha_texto="01-10-2026",
    )

    class FuenteFalsa:
        def __init__(self, id_: str) -> None:
            self.id = id_

        def listar(self, keyword, pagina):
            return ([cruda], False) if pagina == 1 else ([], False)

        def describir(self, cruda_):
            return ""

        def fecha(self, cruda_, ahora):
            return ahora

        def close(self) -> None:
            pass

    perfil = _perfil(["mesa de ayuda"])
    resumen = ingestar_perfil(
        sesion,
        perfil,
        fuentes=[FuenteFalsa("portal-a"), FuenteFalsa("portal-b")],
        ahora=AHORA_DOS_PORTALES,
    )

    guardadas = {f.external_id for f in sesion.query(Oferta).all()}
    assert guardadas == {"portal-a:123", "portal-b:123"}
    assert sorted(resumen.fuentes) == ["portal-a", "portal-b"]
    assert resumen.nuevas == 2
