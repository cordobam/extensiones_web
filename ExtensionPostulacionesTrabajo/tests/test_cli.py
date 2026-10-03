"""Tests de la línea de comandos.

La CLI se prueba con `main(argv)` y leyendo lo que imprime, no ejecutando el
proceso: es lo mismo que ve la persona y evita depender del intérprete.

`ingest` sí toca la red, así que los tests que lo corren usan
`monkeypatch` sobre `radar.cli.ingestar` para no pegarle al portal. El
`ingest` de verdad se prueba en `scripts/ingesta_humo.py`.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import delete

from radar import cli
from radar.db import SessionLocal
from radar.ingesta import Resumen
from radar.models import Match, Oferta, OfertaSkill, Perfil
from radar.perfiles import servicio
from radar.perfiles.validacion import (
    Busqueda,
    Contacto,
    Cv,
    PerfilCompleto,
    SkillCv,
)


@pytest.fixture(autouse=True)
def _base_limpia() -> Iterator:
    s = SessionLocal()
    for modelo in (Match, Oferta, Perfil):
        s.execute(delete(modelo))
    s.commit()
    s.close()
    yield
    s = SessionLocal()
    for modelo in (Match, Oferta, Perfil):
        s.execute(delete(modelo))
    s.commit()
    s.close()


@pytest.fixture
def _perfil(tmp_path) -> PerfilCompleto:
    """Un perfil activo en la base, escrito en un directorio temporal."""
    perfil = PerfilCompleto(
        id="soporte-it",
        nombre="Soporte IT",
        busqueda=Busqueda(keywords=["mesa de ayuda"]),
        cv=Cv(
            contacto=Contacto(nombre="Ada", apellido="Lovelace", email="ada@example.com"),
            skills=[
                SkillCv(nombre="Mesa de Ayuda", obligatorio=True),
                SkillCv(nombre="Windows", obligatorio=True),
                SkillCv(nombre="Linux"),
            ],
        ),
    )
    with SessionLocal() as s:
        servicio.guardar(s, perfil, directorio=tmp_path)
        servicio.activar(s, perfil.id)
    return perfil


def _oferta(skills: list[str], titulo: str = "Mesa de Ayuda") -> None:
    with SessionLocal() as s:
        s.add(
            Oferta(
                external_id="c" * 32 + str(len(s.query(Oferta).all())),
                fuente="computrabajo",
                titulo=titulo,
                skills=[OfertaSkill(skill=x) for x in skills],
            )
        )
        s.commit()


def _sin_red(monkeypatch, resumen: Resumen | None = None) -> list[dict]:
    """Reemplaza la ingesta real por una que no hace nada.

    Devuelve la lista donde se van anotando los argumentos que recibió, para que
    el test pueda assert sobre ellos sin tocar el módulo `cli`.
    """
    calls: list[dict] = []

    def falso(sesion, perfil_id=None, dias=None, con_detalle=True):
        calls.append({"perfil": perfil_id, "dias": dias, "con_detalle": con_detalle})
        return resumen or Resumen(
            perfiles=[perfil_id] if perfil_id else ["soporte-it"],
            ofertas_vistas=20,
            nuevas=12,
            fuera_de_ventana=8,
        )

    monkeypatch.setattr(cli, "ingestar", falso)
    return calls


# ------------------------------------------------------------------- dispatch


def test_sin_comando_falla_con_el_uso(capsys) -> None:
    with pytest.raises(SystemExit) as excepcion:
        cli.main([])
    assert excepcion.value.code == 2
    assert "ingest" in capsys.readouterr().err


def test_comando_desconocido_falla(capsys) -> None:
    with pytest.raises(SystemExit) as excepcion:
        cli.main(["postular"])
    assert excepcion.value.code == 2


# --------------------------------------------------------------------- ingest


def test_ingest_sin_activos_lo_dice_y_no_anda_a_la_red(capsys, monkeypatch) -> None:
    def falso(*args, **kwargs):
        raise AssertionError("no debería haber corrido la ingesta")

    monkeypatch.setattr(cli, "ingestar", falso)

    codigo = cli.main(["ingest"])

    assert codigo == 1
    assert "No hay perfiles activos" in capsys.readouterr().out


def test_ingest_reporta_el_resumen(capsys, monkeypatch, _perfil) -> None:
    _sin_red(monkeypatch)

    codigo = cli.main(["ingest", "--dias", "3"])

    assert codigo == 0
    salida = capsys.readouterr().out
    assert "20 vistas" in salida
    assert "12 nuevas" in salida
    assert "8 fuera de la ventana" in salida


def test_ingest_pasa_los_argumentos(capsys, monkeypatch, _perfil) -> None:
    llamadas = _sin_red(monkeypatch)

    cli.main(["ingest", "--perfil", "soporte-it", "--dias", "5", "--sin-detalle"])

    assert llamadas == [{"perfil": "soporte-it", "dias": 5, "con_detalle": False}]


def test_ingest_por_defecto_pide_detalle(capsys, monkeypatch, _perfil) -> None:
    llamadas = _sin_red(monkeypatch)

    cli.main(["ingest"])

    assert llamadas[0]["con_detalle"] is True
    assert llamadas[0]["dias"] is None


def test_ingest_con_perfil_inexistente_falla(capsys) -> None:
    codigo = cli.main(["ingest", "--perfil", "no-existe"])

    assert codigo == 1
    assert "✗" in capsys.readouterr().out


def test_ingest_lista_las_ofertas_fallidas(capsys, monkeypatch, _perfil) -> None:
    _sin_red(
        monkeypatch,
        Resumen(
            perfiles=["soporte-it"],
            ofertas_vistas=20,
            fallidas=[("abcd1234", "ReadTimeout"), ("ef567890", "ConnectError")],
        ),
    )

    cli.main(["ingest"])

    salida = capsys.readouterr().out
    assert "fallidas (2)" in salida
    assert "abcd1234: ReadTimeout" in salida


def test_ingest_limpiar_no_anda_a_la_red(capsys, monkeypatch) -> None:
    def falso(*args, **kwargs):
        raise AssertionError("no debería haber corrido la ingesta")

    monkeypatch.setattr(cli, "ingestar", falso)
    _oferta(["Mesa de Ayuda"])

    codigo = cli.main(["ingest", "--limpiar"])

    assert codigo == 0
    assert "borrados" in capsys.readouterr().out
    with SessionLocal() as s:
        assert s.query(Oferta).count() == 0


def test_limpiar_borra_matches_tambien(capsys, _perfil) -> None:
    _oferta(["Mesa de Ayuda", "Windows", "Linux"])
    assert cli.main(["match"]) == 0
    with SessionLocal() as s:
        assert s.query(Match).count() == 1

    cli.main(["ingest", "--limpiar"])

    with SessionLocal() as s:
        assert s.query(Match).count() == 0


def test_limpiar_no_toca_los_perfiles(capsys, _perfil) -> None:
    """El comando borra ofertas y matches. Los perfiles son de la persona.

    Este test antes no probaba nada: el fixture autouse ya había borrado todos
    los perfiles, así que `count() == 0` daba verde siempre, incluso si el
    comando los borraba. Ahora primero se crea uno y se comprueba que sigue
    ahí.
    """
    _oferta(["Mesa de Ayuda"])

    cli.main(["ingest", "--limpiar"])

    with SessionLocal() as s:
        assert s.query(Oferta).count() == 0
        assert s.query(Perfil).count() == 1
        assert s.query(Perfil).one().id == "soporte-it"


def test_ingest_listados_limpia_antes_y_despues(capsys, monkeypatch, _perfil) -> None:
    llamadas = _sin_red(monkeypatch)
    _oferta(["Mesa de Ayuda"])

    cli.main(["ingest", "--listados"])

    salida = capsys.readouterr().out
    assert salida.count("borrados") == 2
    assert llamadas[0]["con_detalle"] is False
    with SessionLocal() as s:
        assert s.query(Oferta).count() == 0


# ---------------------------------------------------------------------- match


def test_match_sin_activos_lo_dice(capsys) -> None:
    codigo = cli.main(["match"])

    assert codigo == 1
    assert "No hay perfiles activos" in capsys.readouterr().out


def test_match_reporta_la_distribucion_de_niveles(capsys, _perfil) -> None:
    _oferta(["Mesa de Ayuda", "Windows", "Linux"])  # alta
    _oferta(["Mesa de Ayuda", "Windows", "Linux"])  # alta
    _oferta(["Mesa de Ayuda"])  # descartada

    codigo = cli.main(["match"])

    assert codigo == 0
    salida = capsys.readouterr().out
    assert "3 ofertas puntuadas" in salida
    assert "2 alta" in salida
    assert "1 descartada" in salida


def test_match_con_top_muestra_las_mejores(capsys, _perfil) -> None:
    _oferta(["Mesa de Ayuda", "Windows", "Linux"], titulo="La mejor de todas")
    _oferta(["Mesa de Ayuda", "Windows"], titulo="Sin deseables pero apta")
    _oferta(["Mesa de Ayuda"], titulo="Le falta Windows enterita")

    cli.main(["match", "--top", "5"])

    salida = capsys.readouterr().out
    assert "Las 5 mejores" in salida
    assert "La mejor de todas" in salida
    assert "Sin deseables pero apta" in salida
    # La que no tiene Windows quedó descartada y no se lista.
    assert "Le falta Windows" not in salida


def test_match_top_saca_las_descartadas_del_listado(capsys, _perfil) -> None:
    """El corte manda: una descartada no se lista aunque tenga deseables."""
    _oferta(["Mesa de Ayuda", "Linux"], titulo="Descartada con muchas deseables")
    _oferta(["Mesa de Ayuda", "Windows", "Linux"], titulo="Apta y sin extras")

    cli.main(["match", "--top", "5"])

    salida = capsys.readouterr().out
    assert "Apta y sin extras" in salida
    assert "Descartada con muchas" not in salida


def test_match_top_ordena_de_verdad_por_puntaje(capsys, _perfil) -> None:
    """100 antes que 60, no al revés: por eso el cast a entero y no texto."""
    _oferta(["Mesa de Ayuda", "Windows", "Linux"], titulo="Puntaje 100")
    _oferta(["Mesa de Ayuda", "Windows"], titulo="Puntaje 75")

    cli.main(["match", "--top", "2"])

    salida = capsys.readouterr().out
    assert salida.index("Puntaje 100") < salida.index("Puntaje 75")


def test_match_avisa_de_las_skills_fuera_del_catalogo(capsys, tmp_path) -> None:
    perfil = PerfilCompleto(
        id="raro",
        nombre="Raro",
        busqueda=Busqueda(keywords=["mesa de ayuda"]),
        cv=Cv(
            contacto=Contacto(nombre="Ada", apellido="Lovelace", email="ada@example.com"),
            skills=[SkillCv(nombre="soldadura TIG", obligatorio=True)],
        ),
    )
    with SessionLocal() as s:
        servicio.guardar(s, perfil, directorio=tmp_path)
        servicio.activar(s, perfil.id)
    _oferta(["Mesa de Ayuda"])

    cli.main(["match"])

    assert "soldadura TIG" in capsys.readouterr().out


def test_match_con_perfil_inexistente_falla(capsys) -> None:
    codigo = cli.main(["match", "--perfil", "no-existe"])

    assert codigo == 1
    assert "✗" in capsys.readouterr().out
