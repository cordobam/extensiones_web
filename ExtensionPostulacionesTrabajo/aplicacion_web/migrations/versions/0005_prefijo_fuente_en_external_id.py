"""prefijo de la fuente en external_id

`ofertas.external_id` es única en toda la tabla, y con una sola fuente eso no
daba problemas: los ids de Computrabajo son 32 caracteres hexadecimales y no se
parecen a los de ningún otro portal. Con un segundo portal, la unicidad deja de
ser una propiedad del dato y pasa a ser una casualidad.

La solución elegida es prefijar el id con la fuente ("computrabajo:ABC123"),
que además hace que la fila diga de dónde es sin mirar la columna `fuente`.
Alternativa: hacer la clave única por (fuente, external_id), que es más
"normal", pero obligaría a cambiar los tres lugares que buscan por external_id
solo --incluida la extensión, que manda el id crudo de la URL del portal.

Los ids sin prefijo que ya estaban en la base se arreglan acá. La condición de
"no pisar una fila que ya tenga el prefijo" es defensa: si alguna vez corrió
una ingesta nueva sobre una base vieja, ya existiría la versión prefijada.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-08 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: Union[str, Sequence[str], None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PREFIJO = "computrabajo:"


def upgrade() -> None:
    """Prefija con `computrabajo:` los ids que no lo tengan."""
    op.execute(
        f"UPDATE ofertas SET external_id = '{PREFIJO}' || external_id "
        f"WHERE external_id NOT LIKE '%:%' "
        f"AND NOT EXISTS ("
        f"  SELECT 1 FROM ofertas otra"
        f"  WHERE otra.external_id = '{PREFIJO}' || ofertas.external_id"
        f")"
    )


def downgrade() -> None:
    """Le saca el prefijo a las ofertas de Computrabajo.

    Sólo a las que lo traen: las de otros portales no existían antes de la
    0005, y quitarles el prefijo las dejaría colgando de un id que no les
    pertenece. Como en el upgrade, no se pisa una fila que ya tenga el id crudo.
    """
    op.execute(
        f"UPDATE ofertas SET external_id = substring(external_id from {len(PREFIJO) + 1}) "
        f"WHERE external_id LIKE '{PREFIJO}%' "
        f"AND NOT EXISTS ("
        f"  SELECT 1 FROM ofertas otra"
        f"  WHERE otra.external_id = substring(ofertas.external_id from {len(PREFIJO) + 1})"
        f")"
    )
