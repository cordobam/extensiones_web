"""Consultas para la lista de ofertas del dashboard.

Vive aparte de `api.py` a propósito: `api.py` ya tiene el CRUD de perfiles y
mezclarlo con la consulta de ofertas la vuelve un archivo donde no se encuentra
nada. Acá vive todo lo que responde a "cuál oferta muestro y por qué puntúa
así".

La lista se arma sobre `matches`, no sobre `ofertas`: una oferta sin match no
tiene puntaje, y sin puntaje no tiene lugar en una pantalla ordenada por
relevancia. Las que no han sido puntuadas todavía son un estado vacío de la
pantalla, no un filtro.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlencode

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session, selectinload

from radar.matching import conjuntos_perfil
from radar.models import Match, Oferta, OfertaSkill, Postulacion
from radar.perfiles.validacion import PerfilCompleto
from radar.ubicaciones import expr_provincia

NIVELES = ("alta", "media", "baja", "descartada")

#: Cuántas ofertas entran por página. Suficiente para recorrer con el scroll
#: y poco para que la consulta no traiga la base entera.
POR_PAGINA = 25


@dataclass(frozen=True)
class Filtros:
    """Lo que llega de la query string, ya normalizado.

    Todos los campos son opcionales y vienen "limpios": un nivel que no existe
    o una página negativa se descartan acá, no en la plantilla, para que la URL
    que alguien puede escribir a mano no rompa la consulta.
    """

    niveles: tuple[str, ...] = ()
    texto: str = ""
    skill: str = ""
    provincia: str = ""
    incluir_descartadas: bool = False
    pagina: int = 1

    @property
    def pagina_efectiva(self) -> int:
        return max(1, self.pagina)

    def url(self, pagina: int, perfil_id: str) -> str:
        """El mismo filtro, en otra página.

        Vive acá y no en la plantilla para que los links de paginación lleven
        los filtros puestos: si la URL se armara a mano se perdería el nivel
        elegido, y el usuario volvería a la primera página sin entender por qué
        cambió todo.
        """
        params: list[tuple[str, str]] = [("perfil", perfil_id)]
        for nivel in self.niveles:
            params.append(("nivel", nivel))
        if self.texto:
            params.append(("texto", self.texto))
        if self.skill:
            params.append(("skill", self.skill))
        if self.provincia:
            params.append(("provincia", self.provincia))
        if self.incluir_descartadas:
            params.append(("descartadas", "1"))
        params.append(("pagina", str(max(1, pagina))))
        return "/ofertas?" + urlencode(params)


@dataclass
class FilaOferta:
    """Una oferta de la lista, con su veredicto ya resuelto.

    `coincidencias` y `deseables_ok_nombres` se calculan acá y no en la
    plantilla: la plantilla no debería saber nada de canonicalización.
    """

    oferta: Oferta
    match: Match
    coincidencias: list[str] = field(default_factory=list)
    deseables_ok_nombres: list[str] = field(default_factory=list)
    #: La postulación viva de esta oferta para el perfil, o None si todavía no
    #: se marcó. Va acá y no como JOIN para que la fila sepa si tiene que
    #: mostrar el botón "Postular" o el estado en el que está.
    postulacion: Postulacion | None = None


@dataclass
class ListaOfertas:
    filas: list[FilaOferta] = field(default_factory=list)
    total: int = 0
    total_por_nivel: dict[str, int] = field(default_factory=dict)
    total_descartadas: int = 0
    skills_disponibles: list[str] = field(default_factory=list)
    provincias_disponibles: list[tuple[str, int]] = field(default_factory=list)
    pagina: int = 1
    por_pagina: int = POR_PAGINA

    @property
    def paginas(self) -> int:
        if self.total == 0:
            return 1
        return -(-self.total // self.por_pagina)

    @property
    def rango(self) -> tuple[int, int]:
        """(desde, hasta) de 1 a `total`, para el "mostrando X de Y"."""
        if self.total == 0:
            return 0, 0
        desde = (self.pagina - 1) * self.por_pagina + 1
        return desde, min(desde + self.por_pagina - 1, self.total)


def _base(sesion: Session, perfil_id: str, filtros: Filtros) -> Select:
    consulta = (
        select(Match)
        .join(Match.oferta)
        .where(Match.perfil_id == perfil_id)
    )

    niveles = [n for n in filtros.niveles if n in NIVELES]
    # Sin `descartada` en el filtro, se ocultan. El 75% de las ofertas reales
    # de un perfil bien configurado queda descartada, así que mostrarlas por
    # defecto tapa exactamente las que sirven.
    if not filtros.incluir_descartadas and "descartada" not in niveles:
        consulta = consulta.where(Match.nivel != "descartada")
    if niveles:
        consulta = consulta.where(Match.nivel.in_(niveles))

    texto = filtros.texto.strip()
    if texto:
        # `ilike` y no `like`: nadie escribe el título con mayúsculas exactas
        # para encontrarlo, y los avisos vienen con capitalización heterogénea.
        patron = f"%{texto}%"
        consulta = consulta.where(
            or_(
                Oferta.titulo.ilike(patron),
                Oferta.empresa.ilike(patron),
            )
        )

    if filtros.skill.strip():
        # Por join con `ofertas_skills`, y no por la columna de skills del
        # match: acá interesa si la oferta tiene la skill, no si el perfil la
        # pidió. Y el nombre se compara canónico a canónico porque el skill
        # elegido viene del datalist, que arma la consulta anterior.
        sub = (
            select(OfertaSkill.oferta_id)
            .where(OfertaSkill.skill == filtros.skill.strip())
            .scalar_subquery()
        )
        consulta = consulta.where(Oferta.id.in_(sub))

    if filtros.provincia.strip():
        # Se compara contra `expr_provincia` y no contra `ubicacion` para que el
        # filtro y el `GROUP BY` del dropdown apliquen exactamente el mismo mapa
        # de alias: si el `WHERE` usara el texto crudo y el `GROUP BY` el
        # canónico, elegir "Buenos Aires" mostraría 164 ofertas pero filtraría
        # las 16 que dicen "Buenos Aires" a secas.
        consulta = consulta.where(
            expr_provincia(Oferta.ubicacion) == filtros.provincia.strip()
        )

    return consulta


def listar(
    sesion: Session,
    perfil: PerfilCompleto,
    filtros: Filtros | None = None,
    *,
    cat=None,
) -> ListaOfertas:
    """Las ofertas del perfil, filtradas y ordenadas por puntaje."""
    filtros = filtros if filtros is not None else Filtros()
    pagina = filtros.pagina_efectiva
    cat = cat if cat is not None else _catalogo()

    obligatorias, deseables, _ = conjuntos_perfil(perfil, cat)

    consulta = _base(sesion, perfil.id, filtros)
    consulta = consulta.options(selectinload(Match.oferta).selectinload(Oferta.skills))

    total = sesion.scalar(
        select(func.count()).select_from(consulta.order_by(None).subquery())
    ) or 0

    # `puntaje` es una columna y no `detalle->>'puntaje'`: ordenar por el JSON
    # obligaría a castear en cada página y no usaría índice. El desempate por
    # fecha y luego por id hace la paginación estable: sin eso dos ofertas con
    # el mismo puntaje pueden repetirse o perderse al cambiar de página.
    filas = sesion.scalars(
        consulta.order_by(
            Match.puntaje.desc(), Oferta.fecha_publicacion.desc(), Oferta.id
        )
        .limit(POR_PAGINA)
        .offset((pagina - 1) * POR_PAGINA)
    ).all()

    resultado = ListaOfertas(
        filas=[_armar(f, obligatorias, deseables) for f in filas],
        total=total,
        pagina=pagina,
    )
    _contar_niveles(sesion, perfil.id, resultado)
    resultado.skills_disponibles = _skills_disponibles(sesion, perfil.id)
    resultado.provincias_disponibles = provincias_de(sesion, perfil.id)
    _colgar_postulaciones(sesion, perfil.id, resultado.filas)
    return resultado


def _colgar_postulaciones(
    sesion: Session, perfil_id: str, filas: list[FilaOferta]
) -> None:
    """Le pone a cada fila su postulación, si ya la tiene.

    Va en una consulta aparte y no como JOIN a propósito. Un JOIN por
    `postulaciones` sobre la consulta de la página descartaría de la lista toda
    oferta sin postulación --que son casi todas-- y además dejaría el filtro de
    provincial skills funcionando sobre un producto cartesiano que ya no tiene
    sentido. Acá se consulta sólo lo de la página actual, que son 25 filas.
    """
    if not filas:
        return

    por_oferta = {
        p.oferta_id: p
        for p in sesion.scalars(
            select(Postulacion).where(
                Postulacion.perfil_id == perfil_id,
                Postulacion.oferta_id.in_([f.oferta.id for f in filas]),
            )
        )
    }
    for fila in filas:
        fila.postulacion = por_oferta.get(fila.oferta.id)


def _armar(
    fila: Match, obligatorias: set[str], deseables: set[str]
) -> FilaOferta:
    """Cruza las skills de la oferta con las del perfil, ya canónicas."""
    propias = {s.skill for s in fila.oferta.skills}
    return FilaOferta(
        oferta=fila.oferta,
        match=fila,
        # Se usan los conjuntos del perfil y no `detalle['obligatorias']` para
        # que "coincide" sea la intersección de verdad, y no lo que el motor
        # recuerda de la corrida anterior.
        coincidencias=sorted(propias & obligatorias),
        deseables_ok_nombres=sorted(propias & deseables),
    )


def _contar_niveles(sesion: Session, perfil_id: str, resultado: ListaOfertas) -> None:
    """Los contadores de la cabecera, sobre todas las ofertas del perfil.

    No respetan el filtro de nivel: son el resumen de cómo viene el perfil, y
    un filtro que se autodefine no sirve para decidir si hay que limpiar la
    base.
    """
    filas = sesion.execute(
        select(Match.nivel, func.count())
        .where(Match.perfil_id == perfil_id)
        .group_by(Match.nivel)
    ).all()
    resultado.total_por_nivel = {n: c for n, c in filas}
    resultado.total_descartadas = resultado.total_por_nivel.get("descartada", 0)


def _skills_disponibles(sesion: Session, perfil_id: str) -> list[str]:
    """Skills que aparecen en las ofertas de este perfil, con más ofertas primero.

    El filtro de skill se arma con esto y no con las 219 del catálogo: filtrar
    por una skill que ningún aviso menciona devuelve una lista vacía sin
    explicación de por qué.
    """
    filas = sesion.execute(
        select(OfertaSkill.skill, func.count().label("n"))
        .join(Oferta, Oferta.id == OfertaSkill.oferta_id)
        .join(Match, Match.oferta_id == Oferta.id)
        .where(Match.perfil_id == perfil_id)
        .group_by(OfertaSkill.skill)
        .order_by(func.count().desc(), OfertaSkill.skill)
    ).all()
    return [nombre for nombre, _ in filas]


def provincias_de(sesion: Session, perfil_id: str) -> list[tuple[str, int]]:
    """Provincias de las ofertas de este perfil, con más ofertas primero.

    Va después de `_skills_disponibles` por el mismo motivo que ésa: el filtro se
    arma con lo que de verdad aparece en las ofertas del perfil, no con una lista
    de las 24 provincias, porque elegir una provincia que ningún aviso menciona
    devuelve una pantalla vacía sin explicación.

    Tampoco respeta los otros filtros, igual que los contadores de nivel: si el
    dropdown se autodefiniera, cambiar de provincia vaciaría el resto de opciones
    y no se podría volver atrás.

    El agrupado va sobre `expr_provincia`, la misma expresión del `WHERE` del
    filtro. Si el filtro matching con `provincia=X` y el contador de `X` no
    coinciden, es que las dos expresiones se separaron.
    """
    provincia = expr_provincia(Oferta.ubicacion).label("provincia")
    filas = sesion.execute(
        select(provincia, func.count().label("n"))
        .join(Match, Match.oferta_id == Oferta.id)
        .where(Match.perfil_id == perfil_id)
        .group_by(provincia)
        .order_by(func.count().desc(), provincia)
    ).all()
    return [(nombre, n) for nombre, n in filas if nombre]


def _catalogo():
    from radar import catalogo as modulo_catalogo

    return modulo_catalogo.catalogo()


def esta_puntuada(sesion: Session, perfil_id: str) -> bool:
    """Si el perfil tiene alguna oferta puntuada.

    La pantalla necesita distinguir "no hay ofertas" de "no corriste el
    matching": son dos finales distintos y la instrucción al usuario es
    distinta en cada caso.
    """
    return (
        sesion.scalar(
            select(func.count())
            .select_from(Match)
            .where(Match.perfil_id == perfil_id)
        )
        or 0
    ) > 0


def contar_ofertas(sesion: Session) -> int:
    return sesion.scalar(select(func.count()).select_from(Oferta)) or 0