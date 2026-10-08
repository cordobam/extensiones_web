"""La línea de comandos: `python -m radar ingest` y `python -m radar match`.

Existe para iterar rápido y ver los números en la terminal, que es lo que hace
falta mientras se ajustan las reglas de puntaje. La app web sirve para cargar
perfiles; esto sirve para correr el pipeline.

Es argparse de la biblioteca estándar, sin dependencias nuevas, igual que
`scripts/init_db.py`. El smoke `scripts/ingesta_humo.py` queda aparte: ese corre
la ingesta DOS veces y limpia, que es la prueba de que la segunda no agrega
nada. Eso no es un comando, es una prueba.

Códigos de salida: 0 todo bien, 1 algo falló (no hay perfiles, perfil
inexistente, error de red), 2 el comando está mal escrito (lo de argparse).
"""

from __future__ import annotations

import argparse
import sys
import traceback

from sqlalchemy import delete

from radar import fuentes as registro
from radar import matching
from radar.db import SessionLocal
from radar.ingesta import ingestar
from radar.matching import NoHayPerfilesActivos
from radar.models import Match, Oferta
from radar.perfiles import servicio

DESCRIPCION = "Radar de ofertas: traer del portal y puntuar contra tu perfil."


def _preparar_consola() -> None:
    """Que la salida no reviente por un carácter que la consola no tiene.

    La consola de Windows viene en cp1252, y el `✗` de los mensajes de error es
    U+2717: un guion oblicuo ballot, que en cp1252 no existe. `print` de un
    carácter que no se puede escribir lanza `UnicodeEncodeError` **en el `print`**
    y no en la línea que lo pidió, así que el `except Exception` de abajo lo
    capturaba y lo re-emitía como `✗ UnicodeEncodeError`, y el segundo `print`
    volvía a fallar. El mensaje real del error se perdía en el intento de
    mostrarlo.

    Con `errors="replace"` el carácter problemático sale como `?` y el resto del
    mensaje se lee igual. No se fuerza UTF-8 a propósito: si la consola está en
    cp1252 y se le escriben bytes UTF-8, lo que se ve son dos caracteres
    raros en vez de uno. Para ver el `✗` bien, `PYTHONIOENCODING=utf-8`, que es
    lo que hace falta igual en Windows.

    El `except` es porque bajo pytest `sys.stdout` no siempre es el `TextIOWrapper`
    original: `capsys` lo reemplaza por otra cosa, y si no tuviera
    `reconfigure` no habría que romper los tests por un detalle de la consola.
    """
    for flujo in (sys.stdout, sys.stderr):
        try:
            flujo.reconfigure(errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def _fallo(error: Exception) -> None:
    """El error y su traceback.

    El traceback va aparte porque esta CLI existe justo para ver qué se está
    rompiendo (ver el docstring del módulo): con sólo `str(error)`, un error de
    red y un `KeyError` interno se ven iguales, y `str()` de una excepción sin
    mensaje --`KeyError('puntaje')`-- es media línea de contexto que se pierde.

    El mensaje sigue a stdout porque es parte del reporte del comando y hay tests
    que lo leen ahí; el traceback va a stderr, que es donde un programa escribe
    sus diagnósticos y donde se puede redirigir para no verlo.
    """
    print(f"✗ {error}")
    # El mensaje va a stdout y el traceback a stderr, que no está bufferizado
    # igual: sin este flush, el traceback se imprime antes y el resumen del error
    # queda al final, que se lee al revés.
    sys.stdout.flush()
    traceback.print_exception(type(error), error, error.__traceback__, file=sys.stderr)


def _limpiar(sesion) -> None:
    """Borra ofertas y matches, pero no los perfiles.

    El orden importa por la clave foránea: los matches cuelgan de las ofertas.
    """
    sesion.execute(delete(Match))
    sesion.execute(delete(Oferta))
    sesion.commit()
    print("Ofertas y matches borrados. Los perfiles quedan.")


def _cmd_ingest(args: argparse.Namespace) -> int:
    if args.limpiar:
        with SessionLocal() as sesion:
            _limpiar(sesion)
        return 0

    with SessionLocal() as sesion:
        if args.listados:
            _limpiar(sesion)

        try:
            perfiles = (
                [servicio.obtener(sesion, args.perfil)]
                if args.perfil
                else servicio.activos(sesion)
            )
        except Exception as error:  # noqa: BLE001
            _fallo(error)
            return 1

        if not perfiles:
            print("No hay perfiles activos. Activá alguno en /perfiles.")
            return 1

        print(
            "Perfiles:",
            ", ".join(f"{p.id} ({len(p.busqueda.keywords)} keywords)" for p in perfiles),
        )

        # `--fuente` no toca la config: es para una corrida puntual (probar un
        # portal, evitar uno que está caído) sin editar el .env. Las crea esta
        # función, así que las cierra esta función.
        fuentes = [registro.crear(fuente_id) for fuente_id in (args.fuente or [])] or None
        try:
            resumen = ingestar(
                sesion,
                perfil_id=args.perfil,
                dias=args.dias,
                # `--listados` también implica no bajar detalles: es el modo para
                # probar el pipeline completo en segundos. Si bajara los
                # detalles, serían cinco keywords x 20 ofertas x 2 s de pausa.
                con_detalle=not (args.sin_detalle or args.listados),
                fuentes=fuentes,
            )
        except Exception as error:  # noqa: BLE001
            _fallo(error)
            return 1
        finally:
            for fuente in fuentes or []:
                fuente.close()

        print(f"  fuentes: {', '.join(resumen.fuentes)}")
        print(f"  {resumen.linea()}")
        if resumen.fallidas:
            print(f"  fallidas ({len(resumen.fallidas)}):")
            for donde, error in resumen.fallidas[:10]:
                print(f"    {donde}: {error[:90]}")

        if args.listados:
            _limpiar(sesion)

    return 0


def _cmd_match(args: argparse.Namespace) -> int:
    with SessionLocal() as sesion:
        try:
            resumen = matching.evaluar(sesion, args.perfil)
        except NoHayPerfilesActivos:
            print("No hay perfiles activos. Activá alguno en /perfiles.")
            return 1
        except Exception as error:  # noqa: BLE001
            _fallo(error)
            return 1

        print(f"  {resumen.linea()}")
        for aviso in resumen.warnings:
            print(f"  aviso: {aviso}")

        if args.top:
            print()
            print(f"  Las {args.top} mejores:")
            # Ordena por la columna `puntaje`, que es lo mismo que usa el
            # dashboard. Antes salía del JSON con un cast a entero, porque
            # `->>` ordena "100" antes que "50" al compararlo como texto.
            filas = (
                sesion.query(Match.puntaje, Match.nivel, Oferta.titulo)
                .join(Oferta, Match.oferta_id == Oferta.id)
                .filter(Match.perfil_id.in_(resumen.perfiles))
                .filter(Match.nivel != "descartada")
                # El id desempata, para que dos corridas con puntajes iguales
                # devuelvan las mismas ofertas en el mismo orden.
                .order_by(Match.puntaje.desc(), Oferta.fecha_publicacion.desc(), Oferta.id)
                .limit(args.top)
                .all()
            )
            for valor, nivel, titulo in filas:
                print(f"    {valor:3} {nivel:6} {titulo[:52]}")

    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="radar", description=DESCRIPCION)
    sub = parser.add_subparsers(dest="comando", required=True)

    ingest = sub.add_parser("ingest", help="trae ofertas del portal.")
    ingest.add_argument("--perfil", help="id del perfil. Sin esto, todos los activos.")
    ingest.add_argument(
        "--fuente",
        action="append",
        choices=registro.disponibles(),
        default=None,
        metavar="ID",
        help=(
            "sólo esta fuente (se puede repetir). Sin esto, las de "
            "FUENTES_ACTIVAS: " + ", ".join(registro.disponibles()) + "."
        ),
    )
    ingest.add_argument(
        "--dias", type=int, help="ventana temporal en días. Sin esto, la de la config."
    )
    ingest.add_argument(
        "--sin-detalle",
        action="store_true",
        help="no baja las páginas de detalle: rápido, pero sin descripción ni skills.",
    )
    ingest.add_argument(
        "--listados",
        action="store_true",
        help="limpia antes y después, y no baja detalles. Para probar el pipeline.",
    )
    ingest.add_argument("--limpiar", action="store_true", help="borra ofertas y matches y sale.")
    ingest.set_defaults(func=_cmd_ingest)

    match = sub.add_parser("match", help="puntúa las ofertas contra el perfil.")
    match.add_argument("--perfil", help="id del perfil. Sin esto, todos los activos.")
    match.add_argument(
        "--top",
        type=int,
        default=0,
        metavar="N",
        help="muestra las N mejores ofertas ya puntuadas.",
    )
    match.set_defaults(func=_cmd_match)

    return parser


def main(argv: list[str] | None = None) -> int:
    _preparar_consola()
    args = _parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
