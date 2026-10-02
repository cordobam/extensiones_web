"""Catálogo canónico de skills: carga, resolución de alias y detección en texto.

El problema que resuelve: las ofertas escriben las skills de mil maneras
("Apache Spark", "spark", "pyspark") y tu CV las escribe de otra. Sin un nombre
canónico, comparar CV contra oferta es comparar cadenas y no matchea nada.
Acá cada skill tiene un nombre único, y el texto libre se traduce a ese nombre.

Decisión de diseño (Fase 4, opción A): el catálogo vive en un YAML versionado en
`catalogo/skills.yml` y no en la base. Es un dato de referencia, cambia poco, y
versionarlo con git es justo lo que se quiere: se ve quién agregó qué skill y
cuándo. La alternativa (tabla `skills` + `skill_aliases`) sólo va a hacer falta
el día que quieras estadísticas del tipo "estas 3 skills son las que más filtran
ofertas", y para entonces es una migración de una vez.
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from radar.config import RAIZ

RUTA_CATALOGO = RAIZ / "catalogo" / "skills.yml"

# Caracteres que cuentan como parte de una palabra, para armar los límites de
# las búsquedas. Incluye los signos porque skills como "C++", "C#" y ".NET" son
# reales, y sus signos son parte del nombre.
_CARACTERES_PALABRA = r"0-9a-záéíóúüñ+#"

# El punto va en la frontera de atrás pero no en la de adelante, y esa asimetría
# es a propósito:
#   - atrás: "js" no debe matchear dentro de "node.js", ni "net" dentro de
#     "asp.net".
#   - adelante: el punto de fin de oración no es parte de la palabra, así que
#     "Kafka." tiene que seguir detectando Kafka.
_CARACTERES_ANTES = _CARACTERES_PALABRA + r"\."

# Longitud mínima para que una forma sea indexada. Una forma de un solo carácter
# matchearía medio idioma: "R" aparecería en cada "R$" del texto.
_LONGITUD_MINIMA = 2


def normalizar(texto: str) -> str:
    """Pasa texto libre a la forma en que se indexan las skills.

    Minúsculas, sin acentos, sin puntuación, espacios colapsados. La idea es que
    "Power BI", "power-bi", "POWER_BI" y "power bi" terminen en la misma clave.
    """
    sin_acentos = "".join(
        c
        for c in unicodedata.normalize("NFD", texto.lower())
        if not unicodedata.combining(c)
    )
    # El guion bajo se Come: en las ofertas aparece como separador.
    sin_guiones = sin_acentos.replace("_", " ")
    # Cualquier cosa que no sea letra, dígito, espacio o signo pasa a ser
    # separador.
    limpio = re.sub(r"[^\w\s+#.]", " ", sin_guiones)
    return " ".join(limpio.split())


def _variantes(texto: str) -> set[str]:
    """Las formas en que una skill puede aparecer escrita en una oferta.

    Además de la forma normalizada, agrega la variante con guiones, porque
    "Node.js" y "node-js" son la misma skill y `normalizar` se come ese
    separador. Sin esto, "node-js" quedaría fuera.
    """
    base = normalizar(texto)
    if not base:
        return set()
    return {forma for forma in {base, base.replace(" ", "-")} if len(forma) >= _LONGITUD_MINIMA}


class Skill(BaseModel):
    """Una skill del catálogo, con las formas alternativas en que se la nombra."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    nombre: str = Field(min_length=1)
    categoria: str = Field(min_length=1)
    alias: list[str] = Field(default_factory=list)

    @field_validator("alias")
    @classmethod
    def _alias_sin_repetidos(cls, valor: list[str]) -> list[str]:
        vistos: set[str] = set()
        for alias in valor:
            clave = normalizar(alias)
            if not clave:
                continue
            if clave in vistos:
                raise ValueError(f"Alias repetido dentro de la skill: {alias!r}")
            vistos.add(clave)
        return valor

    @property
    def clave(self) -> str:
        """Nombre canónico normalizado. Es la clave de comparación."""
        return normalizar(self.nombre)


class ArchivoCatalogo(BaseModel):
    """El `catalogo/skills.yml` tal cual, antes de indexarlo."""

    model_config = ConfigDict(extra="forbid")

    version: int = 1
    categorias: list[str] = Field(default_factory=list)
    skills: list[Skill]


class ErrorCatalogo(ValueError):
    """El YAML está mal y hay que arreglarlo antes de seguir."""


class Catalogo:
    """Índice de skills listo para consultar.

    Se construye una vez al importar (`catalogo()`) y después sólo se lee. Todas
    las consultas son a diccionario o expresiones regulares compiladas de
    antemano, así que no tocan la base ni la red. Es seguro compartirlo.
    """

    def __init__(self, skills: list[Skill]) -> None:
        self.skills = skills
        self._por_clave: dict[str, Skill] = {}
        self._indice: dict[str, str] = {}
        self._patrones: dict[str, re.Pattern[str]] = {}
        self._construir()

    def _construir(self) -> None:
        # Un mismo nombre canónico no puede aparecer dos veces.
        for skill in self.skills:
            if skill.clave in self._por_clave:
                otra = self._por_clave[skill.clave].nombre
                raise ErrorCatalogo(
                    f"Skill repetida: {skill.nombre!r} ya estaba como {otra!r}"
                )
            self._por_clave[skill.clave] = skill

        for skill in self.skills:
            for forma in _variantes(skill.nombre):
                self._registrar(forma, skill)
            for alias in skill.alias:
                for forma in _variantes(alias):
                    self._registrar(forma, skill)

    def _registrar(self, forma: str, skill: Skill) -> None:
        # La regla de oro: una forma no puede pertenecer a dos skills. Si "spark"
        # fuera alias de Apache Spark y de PySpark, el catálogo no carga. Es
        # preferible fallar al arrancar y no devolver puntajes raros más adelante.
        previa = self._indice.get(forma)
        if previa is not None and previa != skill.nombre:
            raise ErrorCatalogo(
                f"La forma {forma!r} corresponde a dos skills: "
                f"{previa!r} y {skill.nombre!r}. Revisá los alias."
            )
        self._indice[forma] = skill.nombre
        self._patrones[forma] = re.compile(
            rf"(?<![{_CARACTERES_ANTES}]){re.escape(forma)}(?![{_CARACTERES_PALABRA}])"
        )

    # ------------------------------------------------------------------ lecturas

    def buscar_nombre(self, nombre: str) -> Skill | None:
        """Devuelve la skill cuyo nombre canónico coincide exactamente.

        Acepta "Apache Spark", "apache spark" y "APACHE  SPARK". No acepta
        alias: para eso está `resolver`.
        """
        clave = normalizar(nombre)
        if not clave:
            return None
        return self._por_clave.get(clave)

    def resolver(self, texto: str) -> str | None:
        """Traduce texto libre al nombre canónico de la skill que representa.

        Devuelve None si el texto no corresponde a ninguna skill conocida. Si el
        texto matchea varias, gana la forma más larga: en "Apache Spark"
        importa más "apache spark" que "spark".

        Es un lookup tolerante, pensado para una skill ya identificada
        (el nombre de una skill del CV, o el valor de un `<select>`). Para
        revisar un texto largo usá `detectar`.
        """
        limpio = normalizar(texto)
        if not limpio:
            return None

        # Coincidencia exacta: el caso más común y el más barato. Contempla los
        # nombres de una sola letra, que no están en el índice de búsqueda.
        if limpio in self._por_clave:
            return self._por_clave[limpio].nombre
        if limpio in self._indice:
            return self._indice[limpio]

        mejor: str | None = None
        mejor_largo = 0
        for forma, patron in self._patrones.items():
            if len(forma) > mejor_largo and patron.search(limpio):
                mejor_largo = len(forma)
                mejor = self._indice[forma]
        return mejor

    def detectar(self, texto: str) -> list[str]:
        """Devuelve las skills canónicas que aparecen en un texto, ordenadas.

        Pensado para el título y la descripción de una oferta. Cada skill
        aparece una sola vez, sin importar cuántas veces se la mencione, y el
        orden es el de aparición en el texto.

        Los límites de palabra evitan el error clásico: "spark" no matchea dentro
        de "pyspark", y "js" no matchea dentro de "node.js".
        """
        limpio = normalizar(texto)
        if not limpio:
            return []

        primeras: dict[str, int] = {}
        for forma, patron in self._patrones.items():
            if forma not in limpio:
                continue
            match = patron.search(limpio)
            if match is None:
                continue
            nombre = self._indice[forma]
            # Si la skill aparece varias veces, queda la más temprana.
            if nombre not in primeras or match.start() < primeras[nombre]:
                primeras[nombre] = match.start()

        return sorted(primeras, key=lambda nombre: primeras[nombre])

    def por_categoria(self) -> dict[str, list[str]]:
        """Nombres canónicos agrupados por categoría, en orden alfabético."""
        grupos: dict[str, list[str]] = defaultdict(list)
        for skill in self.skills:
            grupos[skill.categoria].append(skill.nombre)
        return {categoria: sorted(nombres) for categoria, nombres in sorted(grupos.items())}

    def sin_conocer(self, nombres: list[str]) -> list[str]:
        """De una lista de skills escritas a mano, las que no están en catálogo.

        Un alias también cuenta como conocida: si escribís "Airflow" en vez de
        "Apache Airflow", la skill no es desconocida, está mal nombrada. Para ese
        caso está `normalizar_lista`.

        Pensado para la UI: en vez de fallar, avisás "estas 3 skills del CV no las
        conozco, puede que el matching no las encuentre".
        """
        desconocidas = {nombre for nombre in nombres if normalizar(nombre) and self.resolver(nombre) is None}
        return sorted(desconocidas)

    def normalizar_lista(self, nombres: list[str]) -> dict[str, str]:
        """Mapéa cómo escribió la persona cada skill al nombre canónico.

        Sólo devuelve las que cambian: {"sklearn": "scikit-learn"}. La UI lo
        usa para sugerir "esa skill existe, guardala como scikit-learn".
        """
        cambios: dict[str, str] = {}
        for nombre in nombres:
            canonico = self.resolver(nombre)
            if canonico is not None and canonico != nombre:
                cambios[nombre] = canonico
        return cambios

    def __len__(self) -> int:
        return len(self.skills)

    def __repr__(self) -> str:
        return f"<Catalogo {len(self.skills)} skills>"


def _cargar_archivo(ruta: Path) -> Catalogo:
    if not ruta.exists():
        raise FileNotFoundError(f"No encontré el catálogo de skills: {ruta}")

    datos = yaml.safe_load(ruta.read_text(encoding="utf-8")) or {}
    archivo = ArchivoCatalogo.model_validate(datos)

    if not archivo.skills:
        raise ErrorCatalogo(f"El catálogo {ruta} no tiene ninguna skill")

    if archivo.categorias:
        declaradas = {c.casefold() for c in archivo.categorias}
        usadas = {s.categoria.casefold() for s in archivo.skills}
        desconocidas = usadas - declaradas
        if desconocidas:
            raise ErrorCatalogo(
                "Categorías sin declarar en el archivo: "
                f"{', '.join(sorted(desconocidas))}"
            )

    return Catalogo(archivo.skills)


@lru_cache
def catalogo(ruta: Path | None = None) -> Catalogo:
    """Catálogo cargado. Cacheado: llamarlo las veces que haga falta es gratis."""
    return _cargar_archivo(ruta or RUTA_CATALOGO)


def recargar() -> Catalogo:
    """Vuelve a leer el YAML, para tests o para ver cambios sin reiniciar."""
    catalogo.cache_clear()
    return catalogo()