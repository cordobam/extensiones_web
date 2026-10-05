"""Tests del matching por reglas.

Los casos esperados están escritos a mano, no salen de ejecutar el código: si
el puntaje cambia, el test tiene que avisar. Por eso los números aparecen
explícitos en cada assert y no como una fórmula reimplementada.

`evaluar_oferta` es pura y no toca la base, así que casi todo el archivo no
necesita PostgreSQL. Sólo al final, para probar que se escribe en `matches`.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import delete

from radar.db import SessionLocal
from radar.matching import (
    NoHayPerfilesActivos,
    ResumenMatching,
    canonicalizar,
    conjuntos_perfil,
    evaluar,
    evaluar_oferta,
    evaluar_perfil,
)
from radar.models import Match, Oferta, OfertaSkill, Perfil
from radar.perfiles import servicio
from radar.perfiles.validacion import (
    Busqueda,
    Contacto,
    Cv,
    PerfilCompleto,
    SkillCv,
)

# --------------------------------------------------------------------- helpers


def _skill(nombre: str, obligatorio: bool = False, **extra) -> SkillCv:
    return SkillCv(nombre=nombre, obligatorio=obligatorio, **extra)


def _perfil(
    obligatorias: list[str],
    deseables: list[str] | None = None,
    *,
    excluir: list[str] | None = None,
    modalidades: list[str] | None = None,
    id_perfil: str = "soporte-it",
) -> PerfilCompleto:
    """Perfil mínimo, con las skills que el test necesita y nada más.

    Sin evidencia ni años: el matching no los mira, y ponerlos sólo haría ruido.
    """
    return PerfilCompleto(
        id=id_perfil,
        nombre="Soporte IT",
        busqueda=Busqueda(
            keywords=["mesa de ayuda"],
            excluir=excluir or [],
            modalidades=modalidades or [],
        ),
        cv=Cv(
            contacto=Contacto(nombre="Ada", apellido="Lovelace", email="ada@example.com"),
            skills=[*(_skill(s, True) for s in obligatorias), *(_skill(s) for s in deseables or [])],
        ),
    )


def _oferta(skills: list[str], titulo: str = "Mesa de Ayuda", descripcion: str = "") -> Oferta:
    """Una Oferta sin id ni external_id, para puntuar en memoria."""
    return Oferta(
        external_id="x" * 32,
        fuente="computrabajo",
        titulo=titulo,
        descripcion=descripcion or None,
        skills=[OfertaSkill(skill=s) for s in skills],
    )


# ------------------------------------------------------------- canonicalización


def test_una_skill_del_cv_se_traduce_al_nombre_del_catalogo() -> None:
    """El CV escribe "Help Desk"; el catálogo dice "Mesa de Ayuda"."""
    from radar.catalogo import catalogo

    perfil = _perfil(["Help Desk"])
    assert canonicalizar(perfil.cv.skills[0], catalogo()) == "Mesa de Ayuda"


def test_un_alias_tambien_canonico() -> None:
    """El nombre falla, pero uno de los alias acierta."""
    from radar.catalogo import catalogo

    skill = _skill("mesa de soporte en TI", alias=["mesa de soporte"])
    assert canonicalizar(skill, catalogo()) == "Mesa de Ayuda"


def test_una_skill_que_no_esta_en_el_catalogo_no_canoniza() -> None:
    """No se inventa: si el catálogo no la conoce, no hay con qué compararla."""
    from radar.catalogo import catalogo

    assert canonicalizar(_skill("soldadura TIG"), catalogo()) is None


def test_resolver_es_tolerante_y_encuentra_la_skill_dentro_del_texto() -> None:
    """Lookup tolerante a propósito, como el `detectar` de la fase 4.

    "kubernetes operator de sala" no es una skill del catálogo, pero contiene
    "Kubernetes" y esa es la que la persona quiere decir. Resolve a Kubernetes.
    """
    from radar.catalogo import catalogo

    assert canonicalizar(_skill("kubernetes operator de sala"), catalogo()) == "Kubernetes"


# --------------------------------------------------------------------- niveles


def test_falta_una_obligatoria_descarte_la_oferta() -> None:
    """El criterio de la fase 8, literal."""
    perfil = _perfil(["Mesa de Ayuda", "Soporte Técnico", "Windows"])
    resultado = evaluar_oferta(perfil, _oferta(["Mesa de Ayuda", "Soporte Técnico"]))

    assert resultado.nivel == "descartada"
    assert resultado.obligatorios_ok == 2
    assert resultado.obligatorios_total == 3
    assert resultado.detalle["obligatorias_faltantes"] == ["Windows"]


def test_todas_las_obligatorias_cubiertas_y_mitad_de_las_deseables_es_alta() -> None:
    perfil = _perfil(
        ["Mesa de Ayuda", "Windows"],
        ["Active Directory", "TCP/IP", "ServiceNow", "Linux"],
    )
    resultado = evaluar_oferta(
        perfil,
        _oferta(["Mesa de Ayuda", "Windows", "Active Directory", "TCP/IP"]),
    )

    assert resultado.nivel == "alta"
    assert resultado.deseables_ok == 2
    assert resultado.deseables_total == 4


def test_una_sola_deseable_es_media() -> None:
    """1 de 4 es 25%, menos del 50% del umbral, pero no es cero."""
    perfil = _perfil(
        ["Windows"],
        ["Active Directory", "TCP/IP", "ServiceNow", "Linux"],
    )
    resultado = evaluar_oferta(perfil, _oferta(["Windows", "ServiceNow"]))

    assert resultado.nivel == "media"


def test_ninguna_deseable_es_baja() -> None:
    """Cumple lo obligatorio y no menciona ni una deseable."""
    perfil = _perfil(["Windows"], ["Active Directory", "Linux"])
    resultado = evaluar_oferta(perfil, _oferta(["Windows"]))

    assert resultado.nivel == "baja"
    assert resultado.obligatorios_ok == 1


def test_sin_obligatorias_no_se_descarta_nunca() -> None:
    """Un perfil sin requisitos duros se juzga sólo por las deseables."""
    perfil = _perfil([], ["Windows", "Linux"])
    resultado = evaluar_oferta(perfil, _oferta(["Linux"]))

    assert resultado.nivel == "alta"
    assert resultado.obligatorios_total == 0


def test_un_perfil_sin_deseables_sale_alta() -> None:
    """No hay deseables que extrañar, así que el ratio es 1."""
    perfil = _perfil(["Windows"], [])
    resultado = evaluar_oferta(perfil, _oferta(["Windows"]))

    assert resultado.nivel == "alta"
    assert resultado.deseables_total == 0


def test_una_oferta_sin_skills_no_es_descartada_si_no_hay_obligatorias() -> None:
    """El borde: sin skills detectadas y sin obligatorias, queda en baja."""
    perfil = _perfil([], ["Windows"])
    resultado = evaluar_oferta(perfil, _oferta([]))

    assert resultado.nivel == "baja"
    assert resultado.obligatorios_ok == 0


# -------------------------------------------------------------------- puntajes


def test_todo_cubierto_saca_100() -> None:
    perfil = _perfil(["Windows"], ["Linux"])
    resultado = evaluar_oferta(perfil, _oferta(["Windows", "Linux"]))

    assert resultado.puntaje == 100


def test_las_obligatorias_pesan_mas_que_todas_las_deseables_juntas() -> None:
    """El motivo del reparto 75/25.

    Una oferta que cumple las dos obligatorias y no menciona ninguna deseable
    tiene que quedar por arriba de una a la que le falta una obligatoria y las
    menciona todas. Es lo que impide que el número premie una oferta que no
    cumple.
    """
    perfil = _perfil(
        ["Mesa de Ayuda", "Windows"],
        ["Active Directory", "TCP/IP", "ServiceNow", "Linux"],
    )
    cumple = evaluar_oferta(perfil, _oferta(["Mesa de Ayuda", "Windows"]))
    falla = evaluar_oferta(
        perfil, _oferta(["Mesa de Ayuda", "Active Directory", "TCP/IP", "ServiceNow", "Linux"])
    )

    assert cumple.puntaje > falla.puntaje
    assert cumple.nivel == "baja"
    assert falla.nivel == "descartada"


def test_una_descartada_no_supera_el_techo_de_49() -> None:
    """El tope existe para que ordenar por puntaje no contradiga el corte.

    Sin él, una oferta descartada con todas las deseables (25 puntos) puede
    pegarle a una "baja" que cumple las obligatorias y no menciona ninguna
    deseable, y aparecer arriba en un dashboard ordenado por puntaje.
    """
    perfil = _perfil(["Windows", "Linux", "Redes Inalámbricas"], ["Active Directory"])
    resultado = evaluar_oferta(perfil, _oferta(["Windows", "Active Directory"]))

    assert resultado.nivel == "descartada"
    assert resultado.puntaje <= 49


def test_el_tope_tambien_aparece_en_el_detalle() -> None:
    """El puntaje sólo existe en `detalle`: la tabla no tiene columna.

    Y va topeado también ahí. El dashboard va a ordenar por este número, así que
    si guardáramos el crudo, una descartada con todas las deseables (50)
    aparecería arriba de una "baja" que cumple las obligatorias (60)... o al
    revés. Lo que se guarda es lo que se ordena.
    """
    perfil = _perfil(["Windows", "Linux", "Redes Inalámbricas"], ["Active Directory"])
    resultado = evaluar_oferta(perfil, _oferta(["Windows", "Active Directory"]))

    # Crudo: 1 de 3 obligatorias = 25, más 25 de la deseable. Topeado a 49.
    assert resultado.detalle["puntaje"] == 49
    assert resultado.puntaje == 49
    assert resultado.detalle["obligatorias_faltantes"] == ["Linux", "Redes Inalámbricas"]


# ------------------------------------------------------------- reglas del perfil


def test_una_palabra_de_excluir_descarte_la_oferta() -> None:
    """`excluir` es instrucción explícita, a diferencia de modalidades."""
    perfil = _perfil(["Windows"], excluir=["seguro medico"])
    resultado = evaluar_oferta(
        perfil, _oferta(["Windows"], descripcion="Ofrecemos seguro medico y arroz con pollo.")
    )

    assert resultado.nivel == "descartada"
    assert resultado.detalle["excluidas"] == ["seguro medico"]


def test_excluir_no_empieza_a_filtrar_si_la_lista_esta_vacia() -> None:
    perfil = _perfil(["Windows"], excluir=[])
    resultado = evaluar_oferta(perfil, _oferta(["Windows"], descripcion="Con seguro medico."))

    assert resultado.nivel == "alta"


def test_excluir_ignora_mayusculas() -> None:
    perfil = _perfil(["Windows"], excluir=["SEGURO MEDICO"])
    resultado = evaluar_oferta(
        perfil, _oferta(["Windows"], descripcion="Incluye seguro medico.")
    )

    assert resultado.nivel == "descartada"


def test_una_modalidad_no_pedida_no_puntua_ni_descarta() -> None:
    """La decisión de la fase 8: sólo 62 de 245 ofertas traen modalidad.

    Un filtro duro taparía tres cuartas partes del mercado en silencio. Queda
    anotado, pero no cuenta. Este perfil pide sólo remoto y la oferta es
    presencial: igual sale "alta".
    """
    perfil = _perfil(["Windows"], modalidades=["remoto"])
    oferta = _oferta(["Windows"])
    oferta.modalidad = "presencial"

    resultado = evaluar_oferta(perfil, oferta)

    assert resultado.nivel == "alta"
    assert resultado.detalle["modalidad_no_pide"] is True


def test_la_modalidad_no_pedida_queda_anotada_solo_si_el_perfil_pide_algo() -> None:
    """Sin modalidades declaradas no hay con qué comparar, así que no se anota."""
    perfil = _perfil(["Windows"], modalidades=[])
    oferta = _oferta(["Windows"])
    oferta.modalidad = "presencial"

    resultado = evaluar_oferta(perfil, oferta)

    assert "modalidad_no_pide" not in resultado.detalle


# ------------------------------------------------------------------ duplicados


def test_dos_skills_del_cv_que_resuelven_a_la_misma_no_infla_el_ratio() -> None:
    """"Help Desk" y "Mesa de Ayuda" son una sola skill canónica.

    Si se contaran dos, el denominador de las obligatorias sería 2 y con una
    sola detectada la oferta quedaría con las obligatorias a medias en lugar de
    cumplidas.
    """
    perfil = _perfil(["Help Desk", "Mesa de Ayuda"], ["ServiceNow", "Linux"])
    resultado = evaluar_oferta(perfil, _oferta(["Mesa de Ayuda", "ServiceNow"]))

    # Una sola obligatoria, cubierta. El nivel sale del ratio de deseables:
    # 1 de 2 es 0.5, que es justo el umbral, y el umbral es inclusivo.
    assert resultado.obligatorios_total == 1
    assert resultado.obligatorios_ok == 1
    assert resultado.deseables_total == 2
    assert resultado.nivel == "alta"


def test_el_umbral_de_alta_es_inclusivo() -> None:
    """Medio exacto de deseables ya es "alta", no "media"."""
    perfil = _perfil(["Windows"], ["Linux", "ServiceNow"])
    resultado = evaluar_oferta(perfil, _oferta(["Windows", "Linux"]))

    assert resultado.deseables_ok == 1
    assert resultado.deseables_total == 2
    assert resultado.nivel == "alta"


# --------------------------------------------------------------- contra la base


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


def _guardar_perfil(sesion, perfil: PerfilCompleto, directorio) -> None:
    servicio.guardar(sesion, perfil, directorio=directorio)


def _guardar_oferta(sesion, skills: list[str]) -> Oferta:
    oferta = Oferta(
        external_id="o" * 31 + str(len(sesion.query(Oferta).all())),
        fuente="computrabajo",
        titulo="Mesa de Ayuda",
        skills=[OfertaSkill(skill=s) for s in skills],
    )
    sesion.add(oferta)
    sesion.commit()
    return oferta


def test_evaluar_perfil_escribe_una_fila_por_oferta(sesion, tmp_path) -> None:
    perfil = _perfil(["Mesa de Ayuda", "Windows"], ["Linux"])
    _guardar_perfil(sesion, perfil, tmp_path)
    _guardar_oferta(sesion, ["Mesa de Ayuda", "Windows", "Linux"])
    _guardar_oferta(sesion, ["Mesa de Ayuda"])

    resumen = evaluar_perfil(sesion, servicio.obtener(sesion, perfil.id))

    assert resumen.ofertas == 2
    assert resumen.por_nivel == {"alta": 1, "descartada": 1}
    assert sesion.query(Match).count() == 2


def test_re_puntar_no_duplica_filas(sesion, tmp_path) -> None:
    """El unique (oferta_id, perfil_id) hace que re-puntar actualice."""
    perfil = _perfil(["Windows"], ["Linux"])
    _guardar_perfil(sesion, perfil, tmp_path)
    _guardar_oferta(sesion, ["Windows", "Linux"])

    for _ in range(3):
        evaluar_perfil(sesion, servicio.obtener(sesion, perfil.id))

    assert sesion.query(Match).count() == 1


def test_cambiar_el_cv_re_punta_sin_volver_a_scrapear(sesion, tmp_path) -> None:
    """El motivo de que el matching sea una pasada aparte.

    La oferta no se toca: sólo cambian las skills del CV, y el veredicto pasa
    de descartada a alta. Si el matching viviera adentro de la ingesta, esto
    exigiría volver a traer la oferta del portal.
    """
    _guardar_perfil(sesion, _perfil(["Windows"], ["Linux"]), tmp_path)
    _guardar_oferta(sesion, ["Windows", "Linux"])

    evaluar_perfil(sesion, servicio.obtener(sesion, "soporte-it"))
    antes = sesion.query(Match).one()
    assert antes.nivel == "alta"

    # Ahora el CV pide algo que la oferta no tiene.
    _guardar_perfil(
        sesion,
        _perfil(["Windows", "Kubernetes"], ["Linux"]),
        tmp_path,
    )
    evaluar_perfil(sesion, servicio.obtener(sesion, "soporte-it"))
    despues = sesion.query(Match).one()
    assert despues.nivel == "descartada"
    assert despues.detalle["obligatorias_faltantes"] == ["Kubernetes"]


def test_sin_activos_falla_con_un_mensaje_util(sesion) -> None:
    with pytest.raises(NoHayPerfilesActivos):
        evaluar(sesion)


def test_evaluar_recorre_todos_los_activos(sesion, tmp_path) -> None:
    for pid in ("soporte-it", "data-engineer"):
        _guardar_perfil(sesion, _perfil(["Windows"], id_perfil=pid), tmp_path)
        servicio.activar(sesion, pid)
    _guardar_oferta(sesion, ["Windows"])

    resumen = evaluar(sesion)

    assert sorted(resumen.perfiles) == ["data-engineer", "soporte-it"]
    assert sesion.query(Match).count() == 2


def test_una_skill_fuera_del_catalogo_avisa_pero_no_rompe(sesion, tmp_path) -> None:
    """No es un error: es una skill que nunca va a matchear, y hay que decirlo."""
    perfil = _perfil(["Windows", "soldadura TIG"], ["Linux"])
    _guardar_perfil(sesion, perfil, tmp_path)
    _guardar_oferta(sesion, ["Windows", "Linux"])

    resumen = evaluar_perfil(sesion, servicio.obtener(sesion, perfil.id))

    assert len(resumen.warnings) == 1
    assert "soldadura TIG" in resumen.warnings[0]
    # Igual puntuó, y sin que la desconocida le quite obligatoriedad de encima.
    fila = sesion.query(Match).one()
    assert fila.nivel == "alta"
    assert fila.obligatorios_total == 1


def test_el_resumen_se_lee_bien() -> None:
    resumen = ResumenMatching(perfiles=["soporte-it"], ofertas=4)
    resumen.contar("alta")
    resumen.contar("alta")
    resumen.contar("descartada")

    linea = resumen.linea()
    assert "soporte-it" in linea
    assert "4 ofertas puntuadas" in linea
    assert "2 alta" in linea
    assert "1 descartada" in linea


def test_el_resumen_sin_activos_lo_dice() -> None:
    assert "Sin perfiles activos" in ResumenMatching().linea()


# ------------------------------------------------------- conjuntos para la web


def test_conjuntos_perfil_canonicaliza_y_deduplica() -> None:
    """Lo que usa el dashboard para explicar el puntaje sale de acá.

    Si la vista calculara los conjuntos por su cuenta se olvidaría de deduplicar
    los alias, y la lista explicaría un número que el matching nunca usó: la
    oferta mostraría "1 de 2 obligatorias" cuando el motor la vio con la
    obligatoria cubierta.
    """
    perfil = _perfil(
        ["Help Desk", "Mesa de Ayuda"], ["ServiceNow", "Linux", "soldadura TIG"]
    )

    obligatorias, deseables, desconocidas = conjuntos_perfil(perfil)

    assert obligatorias == {"Mesa de Ayuda"}
    assert deseables == {"ServiceNow", "Linux"}
    assert desconocidas == ["soldadura TIG"]


def test_la_columna_puntaje_se_escribe(sesion) -> None:
    """El orden de la lista sale de `Match.puntaje`, no del JSON.

    Ordenar por `detalle->>'puntaje'` obligaría a castear el JSON en cada
    página de resultados. La columna existe para eso, así que el test la mira.
    """
    perfil = _perfil(["Soporte Técnico"], ["ServiceNow"])
    oferta = _oferta(["Soporte Técnico", "ServiceNow"])
    oferta.external_id = "PUNTAJE-COLUMNA-1"
    sesion.add_all([oferta, Perfil(id=perfil.id, nombre=perfil.nombre, activo=True)])
    sesion.flush()
    sesion.commit()

    evaluar_perfil(sesion, perfil)
    sesion.commit()

    fila = sesion.query(Match).one()
    # 75 de una obligatoria cubierta + 25 de una deseable cubierta.
    assert fila.puntaje == 100
    assert fila.detalle["puntaje"] == fila.puntaje
