"""puntaje de match

Saca el puntaje del JSON `detalle` y lo deja en una columna propia.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-03 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: Union[str, Sequence[str], None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Agrega la columna y la llena desde el JSON.

    El backfill va antes del NOT NULL a propósito. Hay 252 ofertas ya puntuadas
    en la base de desarrollo, y si la columna naciera NOT NULL con default 0 la
    migración no podría aplicarse sobre ellas. Y peor que fallar: si se
    aceptara el default, la lista del dashboard arrancaría con las 252 ofertas
    en puntaje 0, que es exactamente el orden que no se debe ver.
    """
    op.add_column("matches", sa.Column("puntaje", sa.Integer(), nullable=True))

    # `detalle` es `json`, no `jsonb`, así que el operador `?` no existe acá.
    # `->>` sí funciona en los dos, y devuelve NULL si la clave no está: el
    # `~` sobre NULL da NULL, el WHERE no pasa, y la fila queda con puntaje
    # NULL. Esa fila hace fallar el NOT NULL de abajo, que es lo que quiero:
    # prefiero que la migración me grite a dejar 252 ofertas en puntaje 0.
    op.execute(
        "UPDATE matches SET puntaje = (detalle->>'puntaje')::int "
        "WHERE (detalle->>'puntaje') ~ '^[0-9]+$'"
    )

    op.alter_column(
        "matches", "puntaje", existing_type=sa.Integer(), nullable=False
    )
    op.create_index(op.f("ix_matches_puntaje"), "matches", ["puntaje"], unique=False)


def downgrade() -> None:
    """Saca la columna y el índice. El puntaje sigue dentro de `detalle`."""
    op.drop_index(op.f("ix_matches_puntaje"), table_name="matches")
    op.drop_column("matches", "puntaje")