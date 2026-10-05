"""presentacion en perfiles

Agrega el texto de presentación que la extensión escribe en el formulario.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-05 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: Union[str, Sequence[str], None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Agrega la columna, nullable y vacía.

    No hay backfill que hacer y no lleva `server_default`, por dos razones.

    La primera es que no hay nada que copiar: el texto de presentación es
    algo que escribís vos, no algo que se pueda derivar de los datos que ya
    están. Inventar un valor por perfil sería peor que dejarlo vacío, porque
    la extensión lo escribiría en un formulario real.

    La segunda es la misma de la 0003: las fechas y los textos los pone el ORM,
    y un default de servidor dejaría dos caminos para llenar la columna según
    quién escriba la fila.
    """
    op.add_column(
        "perfiles",
        sa.Column("presentacion", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    """Saca la columna.

    El texto no se guarda en ningún otro lado, así que el downgrade lo pierde.
    Es aceptable en un downgrade: si volvés atrás y después adelante otra vez,
    queda en NULL y lo volvés a escribir.
    """
    op.drop_column("perfiles", "presentacion")