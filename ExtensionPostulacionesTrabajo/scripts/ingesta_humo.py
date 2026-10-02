"""Corrida de humo de la ingesta, contra el portal real.

Es lo más parecido al criterio de terminado de la fase 5: corre la ingesta dos
veces seguidas y comprueba que la segunda no agrega nada ni vuelve a bajar
detalles.

Uso:

    python scripts/ingesta_humo.py --perfil soporte-it --dias 2
    python scripts/ingesta_humo.py --sin-detalle --dias 7
    python scripts/ingesta_humo.py --listados      # no baja ninguna página de oferta

Sin `--perfil` recorre todos los perfiles activos.

Esto pega contra Computrabajo de verdad, con la pausa de `request_delay` de la
config entre requests. Con 5 keywords y detalles puede tardar un minuto.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from sqlalchemy import delete, func, select  # noqa: E402

from radar.db import SessionLocal  # noqa: E402
from radar.ingesta import ingestar  # noqa: E402
from radar.models import Match, Oferta, Perfil  # noqa: E402
from radar.perfiles import servicio  # noqa: E402


def _limpiar_ofertas(sesion) -> None:
    """Deja la tabla de ofertas como estaba. Los perfiles se respetan."""
    say = "borro las ofertas de la base (los perfiles quedan)"
    print(f"  {say}")
    sesion.execute(delete(Match))
    sesion.execute(delete(Oferta))
    sesion.commit()


def _estado(sesion) -> str:
    total = sesion.scalar(select(func.count()).select_from(Oferta))
    con_fecha = sesion.scalar(
        select(func.count()).select_from(Oferta).where(Oferta.fecha_publicacion.isnot(None))
    )
    con_desc = sesion.scalar(
        select(func.count()).select_from(Oferta).where(Oferta.descripcion.isnot(None))
    )
    return f"{total} ofertas ({con_fecha} con fecha, {con_desc} con descripción)"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--perfil", help="id del perfil. Sin esto, todos los activos.")
    parser.add_argument("--dias", type=int, help="ventana temporal en días.")
    parser.add_argument(
        "--sin-detalle",
        action="store_true",
        help="no baja las páginas de detalle: rápido, pero sin descripción ni skills.",
    )
    parser.add_argument(
        "--listados",
        action="store_true",
        help="no baja detalles y limpia las ofertas antes. Para probar el listado solo.",
    )
    parser.add_argument("--limpiar", action="store_true", help="borra las ofertas y sale.")
    args = parser.parse_args()

    if args.limpiar:
        with SessionLocal() as sesion:
            _limpiar_ofertas(sesion)
        return 0

    con_detalle = not (args.sin_detalle or args.listados)
    # Un `ahora` fijo para las dos corridas: las fechas del portal son relativas
    # ("Hace 19 horas"), así que sin esto cada parseo da un instante distinto y
    # la comparación de "cambió algo" marca todo como modificado.
    referencia = datetime.now(timezone.utc)

    with SessionLocal() as sesion:
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

        print("Perfiles:", ", ".join(f"{p.id} ({len(p.busqueda.keywords)} keywords)" for p in perfiles))
        print("Antes: ", _estado(sesion))
        print("Ventana:", args.dias or "la de la config", "días")
        if not con_detalle:
            print("Detalle: no (sin descripción ni skills)")
        print()

        for corrida in (1, 2):
            print(f"--- corrida {corrida} ---")
            inicio = time.monotonic()
            resumen = ingestar(
                sesion,
                perfil_id=args.perfil,
                dias=args.dias,
                con_detalle=con_detalle,
                ahora=referencia,
            )
            took = time.monotonic() - inicio
            print(f"  {resumen.linea()}")
            print(f"  {resumen.ofertas_vistas} vistas en {took:.1f}s")
            if resumen.fallidas:
                print(f"  fallidas ({len(resumen.fallidas)}):")
                for donde, error in resumen.fallidas[:5]:
                    print(f"    {donde}: {error[:90]}")
            print()

        print("Después:", _estado(sesion))

        if args.listados:
            _limpiar_ofertas(sesion)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
