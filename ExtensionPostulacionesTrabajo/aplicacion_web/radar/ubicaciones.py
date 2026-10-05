"""La provincia de una oferta, en Python y en SQL, con la misma cuenta.

El problema que resuelve: el portal escribe la ubicación como texto libre
`"Ciudad, Provincia"` ("Belgrano, Capital Federal", "Pilar, Buenos Aires"), y
para Buenos Aires lo dice de tres maneras. Sobre 248 ofertas reales, las tres
formas suman 164: el 66%. Con el valor crudo, un dropdown que dijera "Buenos
Aires" mostraría 16 ofertas y escondería 148, y el usuario concluiría que el
filtro no funciona.

Por eso hay dos funciones y no una. `provincia_de` calcula el valor canónico
para el lado de Python, y `expr_provincia` arma la misma cuenta como expresión
SQL para el `WHERE` y el `GROUP BY` del filtro. Si sólo existiera la versión
SQL, el mapa de alias quedaría escrito dos veces (una acá y una en la plantilla
de la migración) y con el tiempo dejarían de coincidir; si sólo existiera la de
Python, el filtro tendría que traer todas las ofertas a memoria para agruparlas
a mano.

La equivalencia entre las dos no es automática: está cubierta por
`test_ubicaciones.py::test_python_y_sql_coinciden`, que compara las dos sobre
las ubicaciones que aparecen en los datos reales.

Decisión de diseño: la provincia NO se guarda como columna. Es una derivación de
`ubicacion`, que se conserva crudo y se muestra en la tarjeta de la oferta. Con
248 ofertas el `GROUP BY` agrupado no se nota, y mientras sea código cambiar el
mapa de alias cuando aparezca una variante nueva es editar un diccionario, no
escribir una migración. El día que el volumen lo justifique, esta expresión se
baja a una columna y el resto del código no cambia."""

from __future__ import annotations

from sqlalchemy import case, func
from sqlalchemy.sql.elements import ColumnElement

from radar.catalogo import normalizar

#: Provincia canónica por forma normalizada (`catalogo.normalizar`: minúsculas,
#: sin acentos, sin puntuación).
#:
#: Las tres claves existen literalmente en los datos: "Capital Federal" (84
#: ofertas), "Buenos Aires-GBA" (64) y "Buenos Aires" (16). Sin esta tabla, el
#: dropdown tendría tres opciones que el usuario percibe como la misma cosa.
#:
#: Sólo se agregan formas que *quiera* juntarse. "Buenos Aires" (la provincia)
#: y "Buenos Aires-GBA" (el área metropolitana) se pegan porque para buscar
#: laburo no se distingue entre el interior y el Gran Buenos Aires; en cambio
#: "Córdoba" y "Córdoba Capital" se dejarían separados, que son otra cosa.
ALIAS_PROVINCIA: dict[str, str] = {
    "capital federal": "Buenos Aires",
    "buenos aires gba": "Buenos Aires",
    "buenos aires": "Buenos Aires",
}

#: Todo lo que hay hasta la primera coma, incluida. Es el equivalente en regex
#: de un `split(",", 1)` de Python, y se usa en `regexp_replace`.
#:
#: Se prefiere a `split_part(ubicacion, ',', 2)` a propósito: `split_part`
#: devuelve el segundo segmento y **descarta el resto**, mientras que el split de
#: Python se queda con todo lo que sigue a la primera coma. Hoy ninguna
#: ubicación real tiene más de una coma, así que ambos dan lo mismo, pero si
#: mañana aparece "Villa María, Oberá, Misiones" el filtro compararía contra
#: "Oberá" y el dropdown mostraría "Oberá, Misiones". Eso no se ve hasta que
#: alguien hace clic y no le coincide nada.
#
#: Sin `$` al final a propósito: en regex de Postgres el `$` ancla al fin de la
#: cadena, y `^[^,]*,$` sólo matchearía cuando la coma es el último carácter.
_HASTA_PRIMERA_COMA = r"^[^,]*,"


def provincia_de(ubicacion: str | None) -> str | None:
    """La provincia canónica de una ubicación del portal.

    "Belgrano, Capital Federal" y "Pilar, Buenos Aires" dan las dos "Buenos
    Aires". Lo que no se reconoce se devuelve como vino, con espacios recortados,
    para que una provincia nueva aparezca sola en el filtro en vez de quedar
    escondida por un mapa que no la conoce.

    `None` cuando no hay nada que mostrar: ubicación vacía, o una que termina en
    coma ("Retiro,"), donde la provincia está pero no se sabe cuál es.
    """
    if not ubicacion:
        return None

    # `partition` y no `split(",", 1)`: el segundo devuelve una lista de un
    # elemento cuando no hay coma, y ese caso hay que distinguir.
    _, coma, resto = ubicacion.partition(",")

    if not coma:
        # Sin coma no hay ciudad/provincia: la ubicación es un solo valor
        # ("Argentina", "Remoto (Argentina)"). Se usa entero.
        return ubicacion.strip() or None

    provincia = resto.strip()
    if not provincia:
        return None
    return ALIAS_PROVINCIA.get(normalizar(provincia), provincia)


def expr_provincia(ubicacion: ColumnElement[str | None]) -> ColumnElement[str | None]:
    """La misma cuenta que `provincia_de`, como expresión SQL.

    Recibe la columna en vez de importar `Oferta`, para que este módulo no
    dependa de los modelos y se pueda probar con cualquier expresión.

    El mapa de alias se arma desde `ALIAS_PROVINCIA`, así que agregar una forma
    nueva es agregar una línea al diccionario y nada más.

    Lo que se compara no es la provincia cruda sino una forma "de clave"
    (`_clave_sql`) que se parece a la de `catalogo.normalizar` en todo lo que
    aparece en los datos. La diferencia que importa: `normalizar` convierte la
    puntuación en espacio, así que "Buenos Aires-GBA" es "buenos aires gba",
    mientras que `lower()` a secas lo deja "buenos aires-gba" y el alias nunca
    matchea. Por eso el guion se traduce antes de comparar.
    """
    # `nullif` para que una ubicación terminada en coma sea NULL y no la cadena
    # vacía: `provincia_de` devuelve `None` en ese caso, y si el `GROUP BY`
    # agrupa una vacía y el filtro no, la cuenta del dropdown no cierra con el
    # total.
    #
    # `btrim` recorta espacios, que es lo que trae el portal: sobre las 248
    # ofertas reales no hay ni una con espacios raros. No recorta tabuladores
    # como sí hace el `strip()` de Python, y esa diferencia es asumida a
    # propósito.
    cruda = func.nullif(
        func.btrim(func.regexp_replace(ubicacion, _HASTA_PRIMERA_COMA, "")),
        "",
    )
    return case(
        *[(clave, valor) for clave, valor in ALIAS_PROVINCIA.items()],
        # `value` es lo que distingue el CASE simple del CASE buscado. Sin él,
        # SQLAlchemy arma un CASE buscado y el primer WHEN matchea siempre: las
        # 248 ofertas habrían salido todas etiquetadas "Buenos Aires".
        value=_clave_sql(cruda),
        else_=cruda,
    )


#: `translate` de Postgres: una cadena de entrada y otra de reemplazo, posición
#: por posición. Se usa para el guion, que es la única puntuación que aparece
#: en las provincias reales ("Buenos Aires-GBA"). El punto no se toca a
#: propósito: `catalogo.normalizar` lo conserva.
_GUIONES = "-"


def _clave_sql(provincia: ColumnElement[str | None]) -> ColumnElement[str | None]:
    """La provincia en la forma en que `catalogo.normalizar` deja una clave.

    Minúsculas, guion convertido en espacio y espacios colapsados. Es lo que hace
    `normalizar` para los caracteres que aparecen en una provincia.

    Lo que **no** hace y `normalizar` sí: quitar acentos. No hace falta porque
    para eso haría falta la extensión `unaccent`, y ninguna clave de
    `ALIAS_PROVINCIA` tiene acentos: "Córdoba" y "Entre Ríos" no son alias, caen
    en el `ELSE` y devuelven su valor crudo, que Python y SQL dan igual. Si
    algún día se agrega una clave acentuada al mapa, esta función tiene que
    usar `unaccent` (o `translate` con los acentos).
    """
    return func.regexp_replace(
        func.lower(func.translate(provincia, _GUIONES, " ")),
        r"\s+",
        " ",
        "g",
    )