"""Los perfiles y el catálogo tienen que estar sincronizados.

Este archivo ata las dos cosas: si agregás una skill al CV de un perfil y no
está en `catalogo/skills.yml`, el matching de esa fase futura no la va a
encontrar nunca. El síntoma sería un puntaje inexplicablemente bajo, así que
más vale que falle acá el test.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from radar.catalogo import catalogo
from radar.perfiles.serializacion import desde_yaml

DIRECTORIO = Path(__file__).resolve().parent.parent / "perfiles"


def perfiles_ejemplo() -> list[tuple[str, dict]]:
    encontrados = []
    for ruta in sorted(DIRECTORIO.glob("*.yml.example")):
        datos = yaml.safe_load(ruta.read_text(encoding="utf-8"))
        encontrados.append((ruta.name, datos))
    return encontrados


def test_hay_perfiles_de_ejemplo() -> None:
    assert len(perfiles_ejemplo()) >= 4


@pytest.mark.parametrize("nombre,datos", perfiles_ejemplo(), ids=lambda v: str(v)[:20])
def test_perfil_ejemplo_es_valido(nombre: str, datos: dict) -> None:
    perfil = desde_yaml(DIRECTORIO.joinpath(nombre).read_text(encoding="utf-8"))
    assert perfil.id
    assert perfil.busqueda.keywords


@pytest.mark.parametrize("nombre,datos", perfiles_ejemplo(), ids=lambda v: str(v)[:20])
def test_toda_skill_del_perfil_existe_en_el_catalogo(nombre: str, datos: dict) -> None:
    cat = catalogo()
    habilidades = [s["nombre"] for s in datos["cv"]["skills"]]

    desconocidas = cat.sin_conocer(habilidades)
    assert desconocidas == [], (
        f"{nombre} tiene skills que el catálogo no conoce: {desconocidas}. "
        f"Agregalas a catalogo/skills.yml o usá un nombre canónico."
    )


def test_los_perfiles_ejemplo_no_traen_datos_personales() -> None:
    """Los `.example` se versionan en un repo público.

    No deben llevar nombre, apellido, ciudad ni teléfono reales: si aparecen,
    el test falla y acordamos qué valor de ejemplo usar.
    """
    valores_de_ejemplo = {
        "tu",
        "nombre",
        "tu nombre",
        "tu ciudad",
        "empresa ejemplo",
    }
    for nombre, datos in perfiles_ejemplo():
        contacto = datos["cv"]["contacto"]
        for campo in ("nombre", "apellido", "ciudad"):
            valor = str(contacto.get(campo, "")).strip().casefold()
            assert valor in valores_de_ejemplo, (
                f"{nombre}: contacto.{campo} = {contacto.get(campo)!r} parece un dato "
                f"real. Los .example van versionados en un repo público."
            )

        # El email no debe contener un nombre propio.
        email = str(contacto.get("email", ""))
        assert email.endswith("@ejemplo.com"), f"{nombre}: email {email!r} no es de ejemplo"

        telefono = str(contacto.get("telefono", ""))
        assert "0000-0000" in telefono or not telefono, (
            f"{nombre}: telefono {telefono!r} parece real."
        )


def test_evidencia_vacia_en_los_perfiles_nuevos() -> None:
    """No inventamos experiencia: `evidencia` queda en null hasta que la cargues.

    La evidencia es el grounding de las cartas de la IA. Si viene con texto de
    nuestra invención, el modelo lo va a usar tal cual.
    """
    nuevos = {"soporte-it.yml.example", "atencion-cliente.yml.example"}
    for nombre, datos in perfiles_ejemplo():
        if nombre not in nuevos:
            continue
        for skill in datos["cv"]["skills"]:
            assert skill.get("evidencia") is None, (
                f"{nombre}: la skill {skill['nombre']!r} trae evidencia escrita. "
                f"Dejalo en null y completalo con algo real."
            )
        assert datos["cv"]["experiencia"] == [], (
            f"{nombre}: el ejemplo trae experiencia cargada."
        )