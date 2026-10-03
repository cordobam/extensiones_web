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

from sqlalchemy import delete

from radar import matching
from radar.db import SessionLocal
from radar.ingesta import ingestar
from radar.matching import NoHayPerfilesActivos
from radar.models import Match, Oferta
from radar.perfiles import servicio

DESCRIPCION = "Radar de ofertas: traer del portal y puntuar contra tu perfil."


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
            print(f"✗ {error}")
            return 1

        if not perfiles:
            print("No hay perfiles activos. Activá alguno en /perfiles.")
            return 1

        print(
            "Perfiles:",
            ", ".join(f"{p.id} ({len(p.busqueda.keywords)} keywords)" for p in perfiles),
        )

        try:
            resumen = ingestar(
                sesion,
                perfil_id=args.perfil,
                dias=args.dias,
                # `--listados` también implica no bajar detalles: es el modo para
                # probar el pipeline completo en segundos. Si bajara los
                # detalles, serían cinco keywords x 20 ofertas x 2 s de pausa.
                con_detalle=not (args.sin_detalle or args.listados),
            )
        except Exception as error:  # noqa: BLE001
            print(f"✗ {error}")
            return 1

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
            print(f"✗ {error}")
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
    args = _parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
