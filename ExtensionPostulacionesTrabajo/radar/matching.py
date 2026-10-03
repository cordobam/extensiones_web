"""El matching: puntúa cada oferta contra un perfil y dice por qué.

Es una pasada aparte de la ingesta, a propósito. Editás tu CV y volvés a
puntuar sin volver a scrapear, que con cinco keywords son nueve minutos contra
el portal. Y al revés: si las reglas de puntaje cambian, no hace falta volver a
traer las ofertas, se re-puntúa lo que ya está en la tabla.

La comparación es entre nombres canónicos de skill de las dos partes. El lado de
la oferta ya llega canónico desde la ingesta (`Catalogo.detectar`); el lado del
CV se canonicaliza acá con `Catalogo.resolver`, que traduce "sklearn" a
"scikit-learn" y "Help Desk" a "Mesa de Ayuda".

Sin IA: son reglas, y las reglas se pueden probar con casos esperados a mano.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from radar import catalogo as modulo_catalogo
from radar.catalogo import Catalogo
from radar.config import get_settings
from radar.models import Match, Oferta
from radar.perfiles import servicio
from radar.perfiles.validacion import PerfilCompleto, SkillCv

# Orden de peor a mejor. `descartada` va aparte y no se ordena con las otras:
# es un corte, no un puntaje bajo.
NIVELES = ("descartada", "baja", "media", "alta")

# Techo de puntaje para una oferta descartada. Existe para que ordenar por
# puntaje nunca contradiga el corte: una descartada no puede aparecer arriba de
# una que sí califica, aunque tenga más deseables.
PUNTAJE_MAX_DESCARTADA = 49


class NoHayPerfilesActivos(RuntimeError):
    """No hay ningún perfil activo contra el cual puntuar."""


@dataclass
class ResumenMatching:
    """Qué pasó en una pasada de matching."""

    perfiles: list[str] = field(default_factory=list)
    ofertas: int = 0
    por_nivel: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def contar(self, nivel: str) -> None:
        self.por_nivel[nivel] = self.por_nivel.get(nivel, 0) + 1

    def linea(self) -> str:
        if not self.perfiles:
            return "Sin perfiles activos. Activá alguno en /perfiles."
        partes = [f"{len(self.perfiles)} perfil(es): {', '.join(self.perfiles)}"]
        partes.append(f"{self.ofertas} ofertas puntuadas")
        for nivel in reversed(NIVELES):
            if self.por_nivel.get(nivel):
                partes.append(f"{self.por_nivel[nivel]} {nivel}")
        return ", ".join(partes)


@dataclass
class Resultado:
    """El veredicto de una oferta contra un perfil."""

    nivel: str
    puntaje: int
    obligatorios_ok: int
    obligatorios_total: int
    deseables_ok: int
    deseables_total: int
    detalle: dict


# --------------------------------------------------------------------- helpers


def canonicalizar(skill: SkillCv, cat: Catalogo) -> str | None:
    """El nombre canónico de una skill del CV, o None si el catálogo no la conoce.

    Primero prueba el nombre, después los alias. Hace falta porque la persona
    escribe la skill como conoce, y el catálogo tiene su forma oficial: si el CV
    dice "Help Desk" y el catálogo dice "Mesa de Ayuda", son la misma skill.

    Devolver None no es un error: significa que esa skill nunca va a matchear
    contra ninguna oferta. Por eso el resumen la avisa una vez por pasada, en
    vez de romper la corrida.
    """
    canonico = cat.resolver(skill.nombre)
    if canonico is not None:
        return canonico
    for alias in skill.alias:
        canonico = cat.resolver(alias)
        if canonico is not None:
            return canonico
    return None


def _conjunto_perfil(
    skills: list[SkillCv], cat: Catalogo
) -> tuple[set[str], list[str]]:
    """Skills canónicas del perfil, y las que el catálogo no conoce.

    Deduplica: dos skills del CV que resuelven a la misma canónica ("Help Desk"
    y "Mesa de Ayuda") son una sola skill para el matching. Si no se
    deduplicara, el denominador del ratio se inflaría y una oferta nunca
    llegaría a "alta".
    """
    canonicas: set[str] = set()
    desconocidas: list[str] = []
    for skill in skills:
        canonico = canonicalizar(skill, cat)
        if canonico is None:
            desconocidas.append(skill.nombre)
        else:
            canonicas.add(canonico)
    return canonicas, desconocidas


def conjuntos_perfil(
    perfil: PerfilCompleto, cat: Catalogo | None = None
) -> tuple[set[str], set[str], list[str]]:
    """(obligatorias, deseables, desconocidas) del perfil, ya canónicas.

    Es lo mismo que calcula `evaluar_oferta`, expuesto para que la capa web
    pueda armar el "por qué esta oferta puntúa 78" sin tener que volver a
    canonicalizar. Si la vista calculara los conjuntos por su cuenta, muy
    probablemente se olvidara de deduplicar los alias y contara distinto que
    el matching: la lista explicaría un número que el motor no usó.
    """
    cat = cat if cat is not None else modulo_catalogo.catalogo()
    obligatorias, desconocidas_ob = _conjunto_perfil(perfil.skills_obligatorias, cat)
    deseables, desconocidas_de = _conjunto_perfil(perfil.skills_deseables, cat)
    return obligatorias, deseables, desconocidas_ob + desconocidas_de


def _excluir(perfil: PerfilCompleto, oferta: Oferta) -> list[str]:
    """Palabras de `busqueda.excluir` que aparecen en el aviso.

    Es distinto de `modalidades` y `ubicaciones`, que son preferencias blandas
    y hoy no puntúan: acá la persona escribió "esto no lo quiero ver", así que
    se descarta.
    """
    if not perfil.busqueda.excluir:
        return []
    texto = f"{oferta.titulo} {oferta.descripcion or ''}".casefold()
    return [p for p in perfil.busqueda.excluir if p.strip().casefold() in texto]


def _puntuar(
    obligatorias: set[str],
    deseables: set[str],
    skills_oferta: set[str],
) -> tuple[int, int, int, list[str], list[str]]:
    """(puntaje, obligatorias ok, deseables ok, obligatorias que faltan, deseables ok)."""
    settings = get_settings()

    oblig_ok = obligatorias & skills_oferta
    faltantes = sorted(obligatorias - skills_oferta)

    deseables_ok = deseables & skills_oferta
    ratio_deseables = len(deseables_ok) / len(deseables) if deseables else 1.0

    # Sin obligatorias no hay penalización posible, así que todo el puntaje sale
    # de las deseables. Con cero deseables el ratio es 1: no hay nada que
    # extrañar.
    if obligatorias:
        ratio_oblig = len(oblig_ok) / len(obligatorias)
    else:
        ratio_oblig = 1.0

    puntaje = round(
        settings.match_peso_obligatorias * ratio_oblig
        + settings.match_peso_deseables * ratio_deseables
    )

    return puntaje, len(oblig_ok), len(deseables_ok), faltantes, sorted(deseables_ok)


def _nivel(
    obligatorios_ok: int,
    obligatorios_total: int,
    deseables_ok: int,
    deseables_total: int,
) -> str:
    """El corte, en orden. Primero el requisito duro, después el ratio.

    `descartada` no es "puntaje bajo": es "le falta un requisito que declaraste
    obligatorio". Por eso el puntaje se limita, para que ordenar nunca contradiga
    el corte.
    """
    if obligatorios_ok < obligatorios_total:
        return "descartada"

    ratio_deseables = deseables_ok / deseables_total if deseables_total else 1.0
    if ratio_deseables >= get_settings().match_umbral_alta:
        return "alta"
    if ratio_deseables > 0:
        return "media"
    return "baja"


# ------------------------------------------------------------------ evaluación


def evaluar_oferta(
    perfil: PerfilCompleto, oferta: Oferta, cat: Catalogo | None = None
) -> Resultado:
    """Puntúa una oferta contra un perfil, sin tocar la base.

    Es la función pura del módulo: recibe lo que necesita y devuelve el
    veredicto. Todo lo que hace efectos (leer y escribir `matches`) vive en
    `evaluar_perfil`, así que las reglas se pueden probar sin base de datos.
    """
    cat = cat if cat is not None else modulo_catalogo.catalogo()

    obligatorias, deseables, desconocidas = conjuntos_perfil(perfil, cat)
    skills_oferta = {s.skill for s in oferta.skills}

    puntaje, obl_ok, des_ok, faltantes, deseables_ok = _puntuar(
        obligatorias, deseables, skills_oferta
    )

    excluidas = _excluir(perfil, oferta)
    nivel = "descartada" if excluidas else _nivel(
        obl_ok, len(obligatorias), des_ok, len(deseables)
    )
    if nivel == "descartada":
        puntaje = min(puntaje, PUNTAJE_MAX_DESCARTADA)

    detalle: dict = {
        "puntaje": puntaje,
        "obligatorias": sorted(obligatorias),
        "obligatorias_faltantes": faltantes,
        "deseables_ok": deseables_ok,
        "excluidas": excluidas,
        "modalidad": oferta.modalidad,
        "ubicacion": oferta.ubicacion,
    }
    # `modalidades` y `ubicaciones` no filtran ni puntúan: sólo 62 de 245
    # ofertas reales traen modalidad, así que un filtro duro taparía tres
    # cuartas partes del mercado en silencio. Queda anotado para cuando haya
    # cobertura, y el `soporte-it` de ejemplo ya declara remoto, híbrido y
    # presencial, que es como no filtrar.
    if oferta.modalidad and perfil.busqueda.modalidades:
        detalle["modalidad_no_pide"] = (
            oferta.modalidad not in perfil.busqueda.modalidades
        )

    if desconocidas:
        detalle["skills_desconocidas"] = sorted(desconocidas)

    return Resultado(
        nivel=nivel,
        puntaje=puntaje,
        obligatorios_ok=obl_ok,
        obligatorios_total=len(obligatorias),
        deseables_ok=des_ok,
        deseables_total=len(deseables),
        detalle=detalle,
    )


def _guardar(
    sesion: Session, oferta: Oferta, perfil_id: str, resultado: Resultado
) -> None:
    """Escribe el veredicto, o lo pisa si ya estaba.

    El unique (oferta_id, perfil_id) hace que re-puntuar el mismo perfil sea una
    actualización y no una fila duplicada. Cambiar el CV no requiere borrar nada:
    se corre `match` otra vez.
    """
    fila = (
        sesion.query(Match)
        .filter(Match.oferta_id == oferta.id, Match.perfil_id == perfil_id)
        .one_or_none()
    )
    if fila is None:
        fila = Match(oferta_id=oferta.id, perfil_id=perfil_id)
        sesion.add(fila)

    fila.nivel = resultado.nivel
    fila.puntaje = resultado.puntaje
    fila.obligatorios_ok = resultado.obligatorios_ok
    fila.obligatorios_total = resultado.obligatorios_total
    fila.deseables_ok = resultado.deseables_ok
    fila.deseables_total = resultado.deseables_total
    fila.detalle = resultado.detalle


def evaluar_perfil(
    sesion: Session,
    perfil: PerfilCompleto,
    cat: Catalogo | None = None,
    resumen: ResumenMatching | None = None,
) -> ResumenMatching:
    """Puntúa TODAS las ofertas de la base contra un perfil.

    Todas, y no sólo las nuevas: es lo que hace que editar el CV sirva de algo
    sin volver a scrapear, y lo que deja que las ofertas que ya estaban en la
    base se pongan al día con reglas nuevas.
    """
    cat = cat if cat is not None else modulo_catalogo.catalogo()
    res = resumen if resumen is not None else ResumenMatching()

    _, _, desconocidas = conjuntos_perfil(perfil, cat)
    if desconocidas:
        res.warnings.append(
            f"{perfil.id}: {len(desconocidas)} skill(s) del CV fuera del catálogo, "
            "no van a matchear nunca: " + ", ".join(sorted(desconocidas))
        )

    ofertas = sesion.query(Oferta).all()
    for oferta in ofertas:
        resultado = evaluar_oferta(perfil, oferta, cat)
        _guardar(sesion, oferta, perfil.id, resultado)
        res.ofertas += 1
        res.contar(resultado.nivel)

    if perfil.id not in res.perfiles:
        res.perfiles.append(perfil.id)
    sesion.commit()
    return res


def evaluar(
    sesion: Session, perfil_id: str | None = None
) -> ResumenMatching:
    """Puntúa contra un perfil o contra todos los activos.

    Sin `perfil_id` recorre los activos, igual que la ingesta: la decisión de la
    Etapa 0 fue que pueden convivir varios perfiles, y el matching tiene que ver
    a todos.
    """
    if perfil_id:
        perfiles = [servicio.obtener(sesion, perfil_id)]
    else:
        perfiles = servicio.activos(sesion)

    if not perfiles:
        raise NoHayPerfilesActivos

    resumen = ResumenMatching()
    for perfil in perfiles:
        evaluar_perfil(sesion, perfil, resumen=resumen)
    return resumen
