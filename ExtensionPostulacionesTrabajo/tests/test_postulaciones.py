"""Tests del embudo de postulaciones.

Van dos capas: `estados` es pura y se prueba sin base, y `servicio` necesita
filas de verdad porque lo que se está probando es el efecto de los commits --
que la fecha de envío se llene sola, que el orden de `listar` ponga las
archivadas al final.

Lo importante de esta capa es la matriz de transiciones. El embudo es la única
parte del proyecto donde un estado mal puesto corrompe el dato: no es que se vea
raro, es que el conteo del embudo queda mintiendo. Por eso los estados que no
se pueden alcanzar también tienen test, no sólo los que sí.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from radar.api import app
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
from radar.postulaciones import estados
from radar.postulaciones import servicio

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

ID_PERFIL = "soporte-it"


# ------------------------------------------------------------------- helpers


def _limpiar() -> None:
    with SessionLocal() as sesion:
        sesion.execute(delete(Postulacion))
        sesion.execute(delete(Match))
        sesion.execute(delete(Oferta))
        sesion.execute(delete(Perfil))
        sesion.commit()


def _perfil(id_perfil: str = ID_PERFIL) -> PerfilCompleto:
    """Un perfil completo y válido.

    Va por `servicio.guardar` y no con un `Perfil(...)` crudo porque `/postulaciones`
    deserializa los perfiles a `PerfilCompleto`, y una fila con los JSON vacíos
    revienta la validación. El perfil tiene que ser de verdad para poder probar
    la pantalla.
    """
    return PerfilCompleto(
        id=id_perfil,
        nombre=f"Perfil {id_perfil}",
        busqueda=Busqueda(keywords=["soporte"]),
        cv=Cv(
            contacto=Contacto(
                nombre="Ada", apellido="Lovelace", email="ada@example.com"
            ),
            skills=[SkillCv(nombre="Soporte Técnico", obligatorio=True)],
        ),
    )


def _base() -> tuple[int, str]:
    """Un perfil activo y una oferta puntuada. Devuelve (oferta_id, perfil_id)."""
    _limpiar()
    with SessionLocal() as sesion:
        perfiles.guardar(sesion, _perfil(), escribir_yaml=False)
        perfiles.activar(sesion, ID_PERFIL)
        oferta = Oferta(
            external_id="A" * 32,
            fuente="computrabajo",
            titulo="Mesa de Ayuda",
            empresa="Acme",
            skills=[OfertaSkill(skill="Soporte Técnico")],
        )
        sesion.add(oferta)
        sesion.flush()
        sesion.add(
            Match(
                oferta_id=oferta.id,
                perfil_id=ID_PERFIL,
                nivel="alta",
                obligatorios_ok=1,
                obligatorios_total=1,
                deseables_ok=0,
                deseables_total=1,
                detalle={},
                puntaje=90,
            )
        )
        sesion.commit()
        return oferta.id, ID_PERFIL


@pytest.fixture
def datos() -> Iterator[tuple[int, str]]:
    oferta_id, perfil_id = _base()
    try:
        yield oferta_id, perfil_id
    finally:
        _limpiar()


@pytest.fixture
def cliente() -> Iterator[TestClient]:
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


# -------------------------------------------------------------------- estados


def test_los_estados_del_embudo_no_se_pisan_con_los_del_matching() -> None:
    """`descartada` significa dos cosas distintas y es un error de lectura.

    `Match.nivel == "descartada"` es el veredicto del matching --le falta algo
    del CV-- y `Postulacion.estado` nunca es `descartada`. Si alguna vez una
    postulación llegara a ese estado, un contador que los junte mezclaría dos
    cosas que no se comparan.
    """
    assert "descartada" not in estados.ESTADOS


@pytest.mark.parametrize("estado", estados.ESTADOS)
def test_todo_estado_del_embudo_es_un_estado_valido(estado: str) -> None:
    assert estados.es_valido(estado)
    assert estados.etiqueta(estado) != estado, "el estado se muestra crudo"


def test_transiciones_permitidas() -> None:
    """La matriz, escrita entera para que se vea de un golpe.

    `enviada → resultado_rechazada` es la que hace que esto no sea una cadena:
    te rechazan sin entrevista, y para registrarlo con una cadena lineal
    tendrías que fingir una entrevista que no pasó.
    """
    assert estados.TRANSICIONES == {
        "pendiente": ("enviada", "archivada"),
        "enviada": ("entrevista", "resultado_rechazada", "archivada"),
        "entrevista": ("resultado_aceptada", "resultado_rechazada", "archivada"),
        "resultado_aceptada": ("archivada",),
        "resultado_rechazada": ("archivada",),
        "archivada": (),
    }


@pytest.mark.parametrize("origen", [e for e in estados.ESTADOS if e not in estados.TERMINALES])
def test_todo_estado_se_puede_archivar(origen: str) -> None:
    """Desde cualquier punto se puede cerrar.

    Si algún estado no llegara a `archivada`, una postulación rechazada en el
    primer filtro quedaría viva para siempre y el embudo nunca se leería
    terminado.
    """
    assert "archivada" in estados.permitidas(origen)


def test_se_puede_rechazar_sin_pasar_por_entrevista() -> None:
    assert estados.puede_pasar("enviada", "resultado_rechazada")
    assert not estados.puede_pasar("enviada", "resultado_aceptada")


def test_no_se_puede_volver_atras() -> None:
    """El embudo no tiene retroceso, y eso es una decisión.

    Volver atrás dejaría `fecha_postulacion` puesta en una postulación que
    vuelve a estar sin mandar, y no hay forma de distinguir "la mandé, me
    arrepentí" de "nunca la mandé" con los datos que se guardan.
    """
    assert not estados.puede_pasar("enviada", "pendiente")
    assert not estados.puede_pasar("entrevista", "enviada")
    assert not estados.puede_pasar("archivada", "pendiente")


def test_quedarse_en_el_mismo_estado_no_es_error() -> None:
    """Un `<select>` con el estado ya elegido reenvía ese mismo valor."""
    assert estados.puede_pasar("enviada", "enviada")


def test_archivada_es_terminal() -> None:
    assert estados.TERMINALES == frozenset({"archivada"})
    assert estados.permitidas("archivada") == ()


def test_un_estato_inventado_no_pasa() -> None:
    assert not estados.es_valido("contratado")
    assert not estados.puede_pasar("pendiente", "contratado")
    assert not estados.puede_pasar("inventado", "enviada")


# ------------------------------------------------------------------- servicio


def test_crear_deja_la_postulacion_en_pendiente_sin_fecha_de_envio(datos) -> None:
    """Marcar no es mandar, y la fecha lo demuestra.

    Si `crear` llenara `fecha_postulacion`, no habría forma de saber después si
    la persona llegó a enviarla o sólo la marcó y se olvidó.
    """
    oferta_id, perfil_id = datos
    with SessionLocal() as sesion:
        fila = servicio.crear(sesion, oferta_id, perfil_id)
        assert fila.estado == "pendiente"
        assert fila.fecha_postulacion is None
        assert fila.creado_en is not None


def test_crear_dos_veces_falla(datos) -> None:
    oferta_id, perfil_id = datos
    with SessionLocal() as sesion:
        servicio.crear(sesion, oferta_id, perfil_id)
        with pytest.raises(servicio.PostulacionDuplicada):
            servicio.crear(sesion, oferta_id, perfil_id)

    with SessionLocal() as sesion:
        assert len(sesion.query(Postulacion).all()) == 1


def test_crear_para_un_perfil_inexistente_falla(datos) -> None:
    """El `perfil_id` viene de un form, y la clave foránea sola daría un 500."""
    oferta_id, _ = datos
    with SessionLocal() as sesion:
        with pytest.raises(servicio.PerfilDesconocido):
            servicio.crear(sesion, oferta_id, "no-existe")


def test_misma_oferta_puede_postularse_en_perfiles_distintos(datos) -> None:
    """La única restricción es (oferta, perfil)."""
    oferta_id, _ = datos
    with SessionLocal() as sesion:
        sesion.add(Perfil(id="data-engineer", nombre="Data", activo=True))
        sesion.commit()

    with SessionLocal() as sesion:
        servicio.crear(sesion, oferta_id, ID_PERFIL)
        segunda = servicio.crear(sesion, oferta_id, "data-engineer")

    assert segunda.estado == "pendiente"


def test_al_pasar_a_enviada_se_guarda_la_fecha(datos) -> None:
    oferta_id, perfil_id = datos
    with SessionLocal() as sesion:
        fila = servicio.crear(sesion, oferta_id, perfil_id)
        id_postulacion = fila.id

    with SessionLocal() as sesion:
        fila = servicio.cambiar_estado(sesion, id_postulacion, "enviada")
        assert fila.estado == "enviada"
        assert fila.fecha_postulacion is not None


def test_la_fecha_de_envio_no_se_toca_al_avanzar(datos) -> None:
    """Es "cuándo la mandaste", no "cuándo la mandaste por última vez"."""
    oferta_id, perfil_id = datos
    with SessionLocal() as sesion:
        id_postulacion = servicio.crear(sesion, oferta_id, perfil_id).id
        servicio.cambiar_estado(sesion, id_postulacion, "enviada")

    with SessionLocal() as sesion:
        antes = servicio.obtener(sesion, id_postulacion).fecha_postulacion
        servicio.cambiar_estado(sesion, id_postulacion, "entrevista")
        servicio.cambiar_estado(sesion, id_postulacion, "resultado_rechazada")

    with SessionLocal() as sesion:
        assert servicio.obtener(sesion, id_postulacion).fecha_postulacion == antes


def test_rechazar_desde_enviada_no_pide_entrevista(datos) -> None:
    oferta_id, perfil_id = datos
    with SessionLocal() as sesion:
        id_postulacion = servicio.crear(sesion, oferta_id, perfil_id).id

    with SessionLocal() as sesion:
        servicio.cambiar_estado(sesion, id_postulacion, "enviada")
        fila = servicio.cambiar_estado(sesion, id_postulacion, "resultado_rechazada")

    assert fila.estado == "resultado_rechazada"


def test_aceptada_y_rechazada_cuentan_distinto(datos) -> None:
    """Si las dos se guardaran como `resultado`, el embudo no diría nada."""
    with SessionLocal() as sesion:
        perfiles.guardar(sesion, _perfil("data-engineer"), escribir_yaml=False)
        oferta = Oferta(
            external_id="B" * 32, fuente="computrabajo", titulo="Data Engineer"
        )
        sesion.add(oferta)
        sesion.commit()
        id_postulacion = servicio.crear(sesion, oferta.id, "data-engineer").id

    with SessionLocal() as sesion:
        servicio.cambiar_estado(sesion, id_postulacion, "enviada")
        servicio.cambiar_estado(sesion, id_postulacion, "entrevista")
        servicio.cambiar_estado(sesion, id_postulacion, "resultado_aceptada")

    with SessionLocal() as sesion:
        conteo = servicio.contar_por_estado(sesion, "data-engineer")

    assert conteo["resultado_aceptada"] == 1
    assert conteo["resultado_rechazada"] == 0


def test_una_transicion_invalida_no_escribe(datos) -> None:
    """El error tiene que ser un error: si se guarda igual, el embudo miente."""
    oferta_id, perfil_id = datos
    with SessionLocal() as sesion:
        id_postulacion = servicio.crear(sesion, oferta_id, perfil_id).id

    with SessionLocal() as sesion:
        with pytest.raises(servicio.TransicionInvalida):
            servicio.cambiar_estado(sesion, id_postulacion, "resultado_aceptada")

    with SessionLocal() as sesion:
        assert servicio.obtener(sesion, id_postulacion).estado == "pendiente"


def test_un_estado_que_no_existe_es_otro_error(datos) -> None:
    """`contratado` no está en el embudo, así que no es una transición inválida.

    Son dos fallos distintos porque vienen de lugares distintos: uno de la
    plantilla y otro del servicio. Un `ValueError` acá sería menos claro.
    """
    oferta_id, perfil_id = datos
    with SessionLocal() as sesion:
        id_postulacion = servicio.crear(sesion, oferta_id, perfil_id).id

    with SessionLocal() as sesion:
        with pytest.raises(servicio.EstadoInvalido):
            servicio.cambiar_estado(sesion, id_postulacion, "contratado")


def test_quedarse_en_el_mismo_estado_no_toca_la_fecha(datos) -> None:
    """Reenviar el `<select>` sin cambiar nada no debe inventarse una fecha."""
    oferta_id, perfil_id = datos
    with SessionLocal() as sesion:
        id_postulacion = servicio.crear(sesion, oferta_id, perfil_id).id

    with SessionLocal() as sesion:
        fila = servicio.cambiar_estado(sesion, id_postulacion, "pendiente")

    assert fila.estado == "pendiente"
    assert fila.fecha_postulacion is None


def test_las_archivadas_caen_al_final(datos) -> None:
    """Una postulación cerrada no debería tapar las que están vivas."""
    with SessionLocal() as sesion:
        ofertas = []
        for indice in range(3):
            perfiles.guardar(
                sesion, _perfil(f"p{indice}"), escribir_yaml=False
            )
            oferta = Oferta(
                external_id=f"C{indice}" + "0" * 31, fuente="computrabajo", titulo="X"
            )
            sesion.add(oferta)
            ofertas.append(oferta)
        sesion.commit()
        ids = [
            servicio.crear(sesion, o.id, f"p{i}").id for i, o in enumerate(ofertas)
        ]

    with SessionLocal() as sesion:
        servicio.cambiar_estado(sesion, ids[0], "enviada")
        servicio.cambiar_estado(sesion, ids[0], "archivada")
        servicio.cambiar_estado(sesion, ids[1], "enviada")

    with SessionLocal() as sesion:
        vivas = [f.perfil_id for f in servicio.listar(sesion, "p2")]
        vida_p0 = servicio.listar(sesion, "p0")
        vida_p1 = servicio.listar(sesion, "p1")

    assert vivas == ["p2"]
    assert vida_p0[0].estado == "archivada"
    assert vida_p1[0].estado == "enviada"


def test_contar_por_estado_devuelve_todos_los_estados(datos) -> None:
    """Aunque estén en cero, para que el embudo se lea entero desde el día uno."""
    with SessionLocal() as sesion:
        conteo = servicio.contar_por_estado(sesion, ID_PERFIL)

    assert list(conteo) == list(estados.ESTADOS)
    assert set(conteo.values()) == {0}


def test_notas_vacias_se_guardan_como_null(datos) -> None:
    """`''` y NULL tienen que ser lo mismo, si no el "tiene notas" no sirve."""
    oferta_id, perfil_id = datos
    with SessionLocal() as sesion:
        id_postulacion = servicio.crear(sesion, oferta_id, perfil_id).id

    with SessionLocal() as sesion:
        servicio.poner_notas(sesion, id_postulacion, "  ")
        assert servicio.obtener(sesion, id_postulacion).notas is None

    with SessionLocal() as sesion:
        servicio.poner_notas(sesion, id_postulacion, " called con RRHH  ")
        assert servicio.obtener(sesion, id_postulacion).notas == "called con RRHH"


def test_borrar_desmarca(datos) -> None:
    oferta_id, perfil_id = datos
    with SessionLocal() as sesion:
        id_postulacion = servicio.crear(sesion, oferta_id, perfil_id).id

    with SessionLocal() as sesion:
        servicio.borrar(sesion, id_postulacion)
        assert servicio.obtener_por_oferta(sesion, oferta_id, perfil_id) is None


def test_operar_sobre_una_postulacion_inexistente_falla(datos) -> None:
    for operacion in (
        lambda s: servicio.cambiar_estado(s, 999, "enviada"),
        lambda s: servicio.poner_notas(s, 999, "x"),
        lambda s: servicio.borrar(s, 999),
    ):
        with SessionLocal() as sesion:
            with pytest.raises(servicio.PostulacionNoEncontrada):
                operacion(sesion)


def test_de_una_fila_arma_el_css_del_estado(datos) -> None:
    """`resultado_aceptada` tiene que volverse `resultado-aceptada` para el CSS."""
    oferta_id, perfil_id = datos
    with SessionLocal() as sesion:
        id_postulacion = servicio.crear(sesion, oferta_id, perfil_id).id

    with SessionLocal() as sesion:
        fila = servicio.de_una_fila(servicio.obtener(sesion, id_postulacion))

    assert fila["estado_css"] == "pendiente"
    assert fila["titulo"] == "Mesa de Ayuda"
    assert fila["abiertas"] == ("enviada", "archivada")

    with SessionLocal() as sesion:
        servicio.cambiar_estado(sesion, id_postulacion, "enviada")
        servicio.cambiar_estado(sesion, id_postulacion, "entrevista")
        fila = servicio.de_una_fila(servicio.obtener(sesion, id_postulacion))
        servicio.cambiar_estado(sesion, id_postulacion, "resultado_aceptada")

    with SessionLocal() as sesion:
        fila = servicio.de_una_fila(servicio.obtener(sesion, id_postulacion))

    assert fila["estado_css"] == "resultado-aceptada"
    assert fila["etiqueta"] == "Aceptada"

# ------------------------------------------------------------------------ api


def test_el_boton_postular_marca_y_te_devuelve_a_los_filtros(cliente, datos) -> None:
    """El `volver` existe para no perder los filtros de la lista.

    Marcar y que salte a `/ofertas` pelado perdería el nivel, la provincia y la
    página, que es exactamente por lo que estabas mirando esa pantalla.
    """
    oferta_id, perfil_id = datos
    respuesta = cliente.post(
        "/postular",
        data={
            "perfil": perfil_id,
            "oferta_id": str(oferta_id),
            "volver": f"/ofertas?perfil={perfil_id}&nivel=alta&pagina=2",
        },
        follow_redirects=False,
    )

    assert respuesta.status_code == 303
    assert respuesta.headers["location"] == (
        f"/ofertas?perfil={perfil_id}&nivel=alta&pagina=2&aviso=postulada"
    )
    with SessionLocal() as sesion:
        fila = servicio.obtener_por_oferta(sesion, oferta_id, perfil_id)
        assert fila is not None and fila.estado == "pendiente"


def test_postular_dos_veces_no_crea_dos_filas(cliente, datos) -> None:
    """Doble clic o submit repetido. No es un 500, es "ya estaba"."""
    oferta_id, perfil_id = datos
    datos_form = {"perfil": perfil_id, "oferta_id": str(oferta_id)}

    cliente.post("/postular", data=datos_form, follow_redirects=False)
    respuesta = cliente.post("/postular", data=datos_form, follow_redirects=False)

    assert "aviso=ya-postulada" in respuesta.headers["location"]
    with SessionLocal() as sesion:
        assert len(sesion.query(Postulacion).all()) == 1


def test_postular_ignora_un_perfil_inventado(cliente, datos) -> None:
    """El `perfil_id` viene del form, así que se puede mandar cualquier cosa."""
    oferta_id, _ = datos
    respuesta = cliente.post(
        "/postular",
        data={"perfil": "no-existe", "oferta_id": str(oferta_id)},
        follow_redirects=False,
    )

    assert "error=perfil-desconocido" in respuesta.headers["location"]
    with SessionLocal() as sesion:
        assert sesion.query(Postulacion).count() == 0


def test_el_form_no_puede_mandar_a_otro_sitio(cliente, datos) -> None:
    """Open redirect: el `volver` vuelve a mandarse en un redirect.

    Sin la lista blanca, un link con `volver=https://ejemplo.com` se lleva a la
    persona a otro sitio desde un formulario de nuestro.
    """
    oferta_id, perfil_id = datos
    respuesta = cliente.post(
        "/postular",
        data={
            "perfil": perfil_id,
            "oferta_id": str(oferta_id),
            "volver": "https://ejemplo.com/ phishing",
        },
        follow_redirects=False,
    )

    assert respuesta.headers["location"].startswith("/ofertas")


@pytest.mark.parametrize(
    "volver,permitido",
    [
        ("//ejemplo.com", False),
        ("/\\ejemplo.com", False),
        ("/ofertas?p=1", True),
        ("/postulaciones", True),
        ("/otra-cosa", False),
    ],
)
def test_la_lista_blanca_de_destinos(volver: str, permitido: bool, cliente) -> None:
    from radar.api import _volver

    resultado = _volver(volver, "/ofertas")
    assert resultado.startswith("/ofertas") or resultado == "/postulaciones"
    assert permitido == resultado.startswith(volver.split("?")[0])


def test_la_pagina_del_embudo_lista_lo_marcado(cliente, datos) -> None:
    oferta_id, perfil_id = datos
    cliente.post(
        "/postular",
        data={"perfil": perfil_id, "oferta_id": str(oferta_id)},
        follow_redirects=False,
    )

    pagina = cliente.get(f"/postulaciones?perfil={perfil_id}")

    assert pagina.status_code == 200
    assert "Mesa de Ayuda" in pagina.text
    assert "Pendiente" in pagina.text


def test_el_embudo_vacio_explica_que_hacer(cliente, datos) -> None:
    _, perfil_id = datos
    pagina = cliente.get(f"/postulaciones?perfil={perfil_id}")

    assert pagina.status_code == 200
    assert "Todav" in pagina.text and "Postular" in pagina.text


def test_cambiar_el_estado_desde_la_web(cliente, datos) -> None:
    oferta_id, perfil_id = datos
    cliente.post(
        "/postular",
        data={"perfil": perfil_id, "oferta_id": str(oferta_id)},
        follow_redirects=False,
    )
    with SessionLocal() as sesion:
        id_postulacion = servicio.obtener_por_oferta(
            sesion, oferta_id, perfil_id
        ).id

    respuesta = cliente.post(
        f"/postulaciones/{id_postulacion}",
        data={"estado": "enviada", "notas": "mandé el CV", "volver": "/postulaciones"},
        follow_redirects=False,
    )

    assert "aviso=estado-guardado" in respuesta.headers["location"]
    with SessionLocal() as sesion:
        fila = servicio.obtener(sesion, id_postulacion)
        assert fila.estado == "enviada"
        assert fila.notas == "mandé el CV"
        assert fila.fecha_postulacion is not None


def test_un_estado_inventado_desde_el_form_no_se_guarda(cliente, datos) -> None:
    """El `<select>` no lo ofrece, pero el form se puede mandar a mano."""
    oferta_id, perfil_id = datos
    cliente.post(
        "/postular",
        data={"perfil": perfil_id, "oferta_id": str(oferta_id)},
        follow_redirects=False,
    )
    with SessionLocal() as sesion:
        id_postulacion = servicio.obtener_por_oferta(
            sesion, oferta_id, perfil_id
        ).id

    respuesta = cliente.post(
        f"/postulaciones/{id_postulacion}",
        data={"estado": "contratado", "volver": "/postulaciones"},
        follow_redirects=False,
    )

    assert "error=estado-invalido" in respuesta.headers["location"]
    with SessionLocal() as sesion:
        assert servicio.obtener(sesion, id_postulacion).estado == "pendiente"


def test_cambiar_a_un_estado_que_no_toca_no_se_guarda(cliente, datos) -> None:
    oferta_id, perfil_id = datos
    cliente.post(
        "/postular",
        data={"perfil": perfil_id, "oferta_id": str(oferta_id)},
        follow_redirects=False,
    )
    with SessionLocal() as sesion:
        id_postulacion = servicio.obtener_por_oferta(
            sesion, oferta_id, perfil_id
        ).id

    respuesta = cliente.post(
        f"/postulaciones/{id_postulacion}",
        data={"estado": "resultado_aceptada", "volver": "/postulaciones"},
        follow_redirects=False,
    )

    assert "error=transicion-invalido" in respuesta.headers["location"]


def test_un_form_solo_con_estado_no_borra_las_notas(cliente, datos) -> None:
    """Si faltara el campo `notas`, `datos.get` no lo distinguiría de vacío."""
    oferta_id, perfil_id = datos
    cliente.post(
        "/postular",
        data={"perfil": perfil_id, "oferta_id": str(oferta_id)},
        follow_redirects=False,
    )
    with SessionLocal() as sesion:
        id_postulacion = servicio.obtener_por_oferta(
            sesion, oferta_id, perfil_id
        ).id
    cliente.post(
        f"/postulaciones/{id_postulacion}",
        data={"estado": "enviada", "notas": "nota importante", "volver": "/postulaciones"},
        follow_redirects=False,
    )

    cliente.post(
        f"/postulaciones/{id_postulacion}",
        data={"estado": "entrevista", "volver": "/postulaciones"},
        follow_redirects=False,
    )

    with SessionLocal() as sesion:
        fila = servicio.obtener(sesion, id_postulacion)
        assert fila.estado == "entrevista"
        assert fila.notas == "nota importante"


def test_desmarcar_deshace(cliente, datos) -> None:
    oferta_id, perfil_id = datos
    cliente.post(
        "/postular",
        data={"perfil": perfil_id, "oferta_id": str(oferta_id)},
        follow_redirects=False,
    )
    with SessionLocal() as sesion:
        id_postulacion = servicio.obtener_por_oferta(
            sesion, oferta_id, perfil_id
        ).id

    cliente.post(f"/postulaciones/{id_postulacion}/borrar", follow_redirects=False)

    with SessionLocal() as sesion:
        assert servicio.obtener_por_oferta(sesion, oferta_id, perfil_id) is None


def test_la_fila_marca_su_oferta_como_ya_postulada(cliente, datos) -> None:
    """En `/ofertas`, una fila postulada muestra el estado y no el botón."""
    oferta_id, perfil_id = datos

    antes = cliente.get(f"/ofertas?perfil={perfil_id}").text
    assert antes.count(">Postular<") == 1

    cliente.post(
        "/postular",
        data={"perfil": perfil_id, "oferta_id": str(oferta_id)},
        follow_redirects=False,
    )

    despues = cliente.get(f"/ofertas?perfil={perfil_id}").text
    assert ">Postular<" not in despues
    assert "Pendiente" in despues


def test_el_embudo_separa_aceptadas_de_rechazadas(cliente, datos) -> None:
    """Dos postulaciones con distinto resultado, dos contadores distintos."""
    with SessionLocal() as sesion:
        perfiles.guardar(sesion, _perfil("data-engineer"), escribir_yaml=False)
        for indice in range(2):
            sesion.add(
                Oferta(
                    external_id=f"D{indice}" + "0" * 31,
                    fuente="computrabajo",
                    titulo=f"Oferta {indice}",
                )
            )
        sesion.commit()
        ids = [
            servicio.crear(sesion, o.id, "data-engineer").id
            for i, o in enumerate(sesion.query(Oferta).order_by(Oferta.id).all()[-2:])
        ]

    with SessionLocal() as sesion:
        servicio.cambiar_estado(sesion, ids[0], "enviada")
        servicio.cambiar_estado(sesion, ids[0], "entrevista")
        servicio.cambiar_estado(sesion, ids[0], "resultado_aceptada")
        servicio.cambiar_estado(sesion, ids[1], "enviada")
        servicio.cambiar_estado(sesion, ids[1], "resultado_rechazada")

    pagina = cliente.get("/postulaciones?perfil=data-engineer")

    assert "Aceptada" in pagina.text
    assert "Rechazada" in pagina.text
