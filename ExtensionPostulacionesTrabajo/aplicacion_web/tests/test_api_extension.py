"""Tests de la API que consume la extensión de Firefox.

Dos rutas: `/api/extension/datos` (el CV y el embudo, en un solo request) y
`/api/extension/enviada` (reportar que mandaste).

Lo que más importa acá no es que funcionen, sino que estén cerradas. La app
escucha en 127.0.0.1:8000, así que cualquier página que abras en el navegador
puede pegarle un POST. El candado es la cabecera `X-Radar-Extension`: una
cabecera propia obliga a un preflight que la app no contesta, así que la
petición nunca sale. Por eso hay tests que verifican que sin la cabecera la ruta
no sirve nada, y no es un detalle: es la diferencia entre que el endpoint sea
una herramienta local y que sea un endpoint abierto a internet.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from radar.api import CABECERA_EXTENSION, app
from radar.db import SessionLocal
from radar.models import Match, Oferta, OfertaSkill, Perfil, Postulacion
from radar.perfiles import servicio as perfiles
from radar.perfiles.validacion import (
    Busqueda,
    Contacto,
    Cv,
    PerfilCompleto,
    SkillCv,
)
from radar.postulaciones import servicio as postulaciones

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

ID_PERFIL = "soporte-it"
EXTERNAL_ID = "A" * 32
PRESENTACION = "Hola, soy Ada y hago soporte de escritorio desde 2018."


@pytest.fixture
def cliente() -> Iterator[TestClient]:
    """Una sesión por test, como en el resto de los tests de API."""
    from radar import api

    sesion = SessionLocal()
    sesion.execute(delete(Postulacion))
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
        sesion.execute(delete(Postulacion))
        sesion.execute(delete(Match))
        sesion.execute(delete(Oferta))
        sesion.execute(delete(Perfil))
        sesion.commit()
        sesion.close()


def _perfil(id_perfil: str = ID_PERFIL, presentacion: str | None = None):
    return PerfilCompleto(
        id=id_perfil,
        nombre=f"Perfil {id_perfil}",
        busqueda=Busqueda(keywords=["soporte"]),
        cv=Cv(
            contacto=Contacto(
                nombre="Ada", apellido="Lovelace", email="ada@example.com"
            ),
            presentacion=presentacion,
            skills=[SkillCv(nombre="Soporte Técnico", obligatorio=True)],
        ),
    )


def _guardar_perfil(id_perfil: str = ID_PERFIL, presentacion: str | None = None):
    with SessionLocal() as sesion:
        perfiles.guardar(sesion, _perfil(id_perfil, presentacion), escribir_yaml=False)
        perfiles.activar(sesion, id_perfil)


def _oferta(external_id: str = EXTERNAL_ID) -> int:
    with SessionLocal() as sesion:
        oferta = Oferta(
            external_id=external_id,
            fuente="computrabajo",
            titulo="Mesa de Ayuda",
            empresa="Acme",
            url=f"https://www.computrabajo.com/oferta/{external_id}",
            skills=[OfertaSkill(skill="Soporte Técnico")],
        )
        sesion.add(oferta)
        sesion.commit()
        return oferta.id


def _headers() -> dict[str, str]:
    return {CABECERA_EXTENSION: "1"}


# ------------------------------------------------------------------ el candado


class TestCandado:
    def test_sin_cabecera_no_devuelve_el_cv(self, cliente) -> None:
        _guardar_perfil()
        respuesta = cliente.get("/api/extension/datos")

        assert respuesta.status_code == 403
        assert "cv" not in respuesta.json()

    def test_con_la_cabecera_mal_no_devuelve_el_cv(self, cliente) -> None:
        _guardar_perfil()
        respuesta = cliente.get(
            "/api/extension/datos", headers={CABECERA_EXTENSION: "cualquiera"}
        )

        assert respuesta.status_code == 403

    def test_el_envio_tambien_exige_la_cabecera(self, cliente) -> None:
        _guardar_perfil()
        _oferta()

        respuesta = cliente.post(
            "/api/extension/enviada",
            json={"perfil": ID_PERFIL, "external_id": EXTERNAL_ID},
        )

        assert respuesta.status_code == 403

        with SessionLocal() as sesion:
            assert (
                postulaciones.obtener_por_oferta(sesion, 1, ID_PERFIL) is None
            ), "un POST sin cabecera no puede crear una postulación"


# -------------------------------------------------------------------- datos


class TestDatos:
    def test_trae_el_perfil_por_defecto(self, cliente) -> None:
        _guardar_perfil()

        datos = cliente.get("/api/extension/datos", headers=_headers()).json()

        assert datos["ok"]
        assert datos["perfil"] == ID_PERFIL
        assert datos["activo"]

    def test_trae_el_contacto_completo(self, cliente) -> None:
        _guardar_perfil()

        contacto = cliente.get("/api/extension/datos", headers=_headers()).json()["cv"][
            "contacto"
        ]

        assert contacto["nombre"] == "Ada"
        assert contacto["apellido"] == "Lovelace"
        assert contacto["email"] == "ada@example.com"

    def test_trae_el_texto_de_presentacion(self, cliente) -> None:
        _guardar_perfil(presentacion=PRESENTACION)

        cv = cliente.get("/api/extension/datos", headers=_headers()).json()["cv"]

        assert cv["presentacion"] == PRESENTACION

    def test_un_perfil_sin_presentacion_devuelve_null(self, cliente) -> None:
        """Es None y no cadena vacía, para que la extensión sepa que no hay."""
        _guardar_perfil()

        cv = cliente.get("/api/extension/datos", headers=_headers()).json()["cv"]

        assert cv["presentacion"] is None

    def test_trae_las_postulaciones_vivas(self, cliente) -> None:
        _guardar_perfil()
        id_oferta = _oferta()
        with SessionLocal() as sesion:
            postulaciones.crear(sesion, id_oferta, ID_PERFIL)

        vivas = cliente.get("/api/extension/datos", headers=_headers()).json()[
            "postulaciones"
        ]

        assert len(vivas) == 1
        assert vivas[0]["external_id"] == EXTERNAL_ID
        assert vivas[0]["titulo"] == "Mesa de Ayuda"
        assert vivas[0]["estado"] == "pendiente"
        assert vivas[0]["etiqueta"] == "Pendiente"
        assert vivas[0]["fecha_postulacion"] is None

    def test_las_archivadas_no_van(self, cliente) -> None:
        """La extensión no necesita saber de lo archivado: ya no se toca."""
        _guardar_perfil()
        id_oferta = _oferta()
        with SessionLocal() as sesion:
            creada = postulaciones.crear(sesion, id_oferta, ID_PERFIL)
            postulaciones.cambiar_estado(sesion, creada.id, "archivada")

        vivas = cliente.get("/api/extension/datos", headers=_headers()).json()[
            "postulaciones"
        ]

        assert vivas == []

    def test_trae_las_que_ya_avanzaron_con_su_etiqueta(self, cliente) -> None:
        _guardar_perfil()
        id_oferta = _oferta()
        with SessionLocal() as sesion:
            creada = postulaciones.crear(sesion, id_oferta, ID_PERFIL)
            postulaciones.cambiar_estado(sesion, creada.id, "enviada")
            postulaciones.cambiar_estado(sesion, creada.id, "entrevista")

        vivas = cliente.get("/api/extension/datos", headers=_headers()).json()[
            "postulaciones"
        ]

        assert vivas[0]["estado"] == "entrevista"
        assert vivas[0]["etiqueta"] == "Entrevista"

    def test_el_parametro_perfil_elige_otro(self, cliente) -> None:
        _guardar_perfil("soporte-it", presentacion="La de soporte")
        _guardar_perfil("data-engineer", presentacion="La de datos")

        datos = cliente.get(
            "/api/extension/datos", params={"perfil": "data-engineer"},
            headers=_headers(),
        ).json()

        assert datos["perfil"] == "data-engineer"
        assert datos["cv"]["presentacion"] == "La de datos"

    def test_un_perfil_inexistente_avisa_cuales_hay(self, cliente) -> None:
        _guardar_perfil()

        respuesta = cliente.get(
            "/api/extension/datos", params={"perfil": "no-existe"}, headers=_headers()
        )

        assert respuesta.status_code == 404
        cuerpo = respuesta.json()
        assert cuerpo["error"] == "perfil-desconocido"
        assert cuerpo["perfiles_disponibles"] == [ID_PERFIL]

    def test_sin_perfiles_avisa(self, cliente) -> None:
        respuesta = cliente.get("/api/extension/datos", headers=_headers())

        assert respuesta.status_code == 404
        assert respuesta.json()["error"] == "sin-perfiles"


# --------------------------------------------------------------- el envío


class TestEnviada:
    def test_registra_el_envio_de_una_pendiente(self, cliente) -> None:
        _guardar_perfil()
        id_oferta = _oferta()
        with SessionLocal() as sesion:
            postulaciones.crear(sesion, id_oferta, ID_PERFIL)

        respuesta = cliente.post(
            "/api/extension/enviada",
            json={"perfil": ID_PERFIL, "external_id": EXTERNAL_ID},
            headers=_headers(),
        )

        assert respuesta.status_code == 200
        cuerpo = respuesta.json()
        assert cuerpo["ok"]
        assert cuerpo["estado"] == "enviada"
        assert cuerpo["etiqueta"] == "Enviada"
        assert cuerpo["fecha_postulacion"], "mandarla tiene que fechar"

    def test_crea_la_postulacion_si_no_existia(self, cliente) -> None:
        _guardar_perfil()
        id_oferta = _oferta()

        cuerpo = cliente.post(
            "/api/extension/enviada",
            json={"perfil": ID_PERFIL, "external_id": EXTERNAL_ID},
            headers=_headers(),
        ).json()

        assert cuerpo["ok"]
        assert cuerpo["titulo"] == "Mesa de Ayuda"
        with SessionLocal() as sesion:
            fila = postulaciones.obtener_por_oferta(sesion, id_oferta, ID_PERFIL)
            assert fila is not None, "la extensión mandó el envío, tiene que existir"
            assert fila.estado == "enviada"

    def test_una_oferta_desconocida_explica_que_ingerir(self, cliente) -> None:
        """El caso real: el portal tiene una oferta y el radar todavía no.

        La respuesta tiene que decir qué hacer, no sólo que no encontró nada.
        """
        _guardar_perfil()

        respuesta = cliente.post(
            "/api/extension/enviada",
            json={"perfil": ID_PERFIL, "external_id": "B" * 32},
            headers=_headers(),
        )

        assert respuesta.status_code == 404
        cuerpo = respuesta.json()
        assert cuerpo["error"] == "oferta-desconocida"
        assert "radar ingest" in cuerpo["mensaje"]

    def test_una_que_ya_avanzo_no_es_error(self, cliente) -> None:
        """Un 4xx haría que la extensión lo pintara como fallo.

        El reporte llegó tarde, no está mal. Y el estado real tiene que volver,
        así la extensión puede avisar sin inventarse nada.
        """
        _guardar_perfil()
        id_oferta = _oferta()
        with SessionLocal() as sesion:
            creada = postulaciones.crear(sesion, id_oferta, ID_PERFIL)
            postulaciones.cambiar_estado(sesion, creada.id, "enviada")
            postulaciones.cambiar_estado(sesion, creada.id, "entrevista")

        respuesta = cliente.post(
            "/api/extension/enviada",
            json={"perfil": ID_PERFIL, "external_id": EXTERNAL_ID},
            headers=_headers(),
        )

        assert respuesta.status_code == 200
        cuerpo = respuesta.json()
        assert not cuerpo["ok"]
        assert cuerpo["estado"] == "entrevista"
        assert "No la toqué" in cuerpo["mensaje"]

    def test_reportar_dos_veces_no_rompe_nada(self, cliente) -> None:
        _guardar_perfil()
        _oferta()
        cuerpo = {"perfil": ID_PERFIL, "external_id": EXTERNAL_ID}

        primera = cliente.post(
            "/api/extension/enviada", json=cuerpo, headers=_headers()
        ).json()
        segunda = cliente.post(
            "/api/extension/enviada", json=cuerpo, headers=_headers()
        ).json()

        assert primera["fecha_postulacion"] == segunda["fecha_postulacion"]
        assert segunda["ok"]

    def test_la_fecha_siempre_viene_en_utc(self, cliente) -> None:
        """Una misma fecha tiene que volver siempre con la misma forma.

        Sin normalizar, la primera respuesta lee el valor que quedó en memoria
        después del INSERT y viene en UTC, y la segunda lo lee de PostgreSQL y
        viene en la zona de la conexión. Son el mismo instante, pero si el texto
        cambia la extensión no puede compararlos.
        """
        _guardar_perfil()
        _oferta()

        cuerpo = {"perfil": ID_PERFIL, "external_id": EXTERNAL_ID}
        primera = cliente.post(
            "/api/extension/enviada", json=cuerpo, headers=_headers()
        ).json()
        # Una lectura fresca, como la que hace el panel al abrirse.
        listado = cliente.get("/api/extension/datos", headers=_headers()).json()
        segunda = cliente.post(
            "/api/extension/enviada", json=cuerpo, headers=_headers()
        ).json()

        assert primera["fecha_postulacion"].endswith("+00:00")
        assert segunda["fecha_postulacion"] == primera["fecha_postulacion"]
        assert listado["postulaciones"][0]["fecha_postulacion"].endswith("+00:00")

    def test_el_mensaje_va_por_datos_y_no_por_url(self, cliente) -> None:
        """Como el resto de la app: los textos van en el JSON, no en la URL."""
        _guardar_perfil()
        _oferta()

        cuerpo = cliente.post(
            "/api/extension/enviada",
            json={"perfil": ID_PERFIL, "external_id": EXTERNAL_ID},
            headers=_headers(),
        ).json()

        assert "mensaje" in cuerpo
        assert cuerpo["ok"]