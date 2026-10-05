"""creado_en en postulaciones

Agrega la fecha en que la postulación entró al embudo.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-04 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: Union[str, Sequence[str], None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Agrega la columna y pone `pendiente` a las que existieran.

    No se usa `server_default` a propósito: las fechas las pone `_utcnow` desde
    el ORM en el resto del modelo, y un default de servidor dejaría dos caminos
    distintos para llenarla según quién escriba la fila.

    De paso, `fecha_postulacion` es cuando la mandaste: una postulación que sigue
    en `pendiente` no la tiene. Las filas que existieran son de antes de que
    existiera el embudo, así que no hay forma de saber si se mandaron, y se
    dejan con la fecha en NULL en vez de inventarles una.
    """
    op.add_column(
        "postulaciones",
        sa.Column("creado_en", sa.DateTime(timezone=True), nullable=True),
    )

    op.execute("UPDATE postulaciones SET creado_en = now() WHERE creado_en IS NULL")

    op.alter_column(
        "postulaciones",
        "creado_en",
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
    )

    op.execute(
        "UPDATE postulaciones SET estado = 'pendiente' WHERE estado = 'descartada'"
    )


def downgrade() -> None:
    """Saca la columna y devuelve el default anterior del estado.

    `descartada` como default era un valor de relleno de una tabla que no la
    escribía nadie, no un estado real del embudo, así que volver a él no deja
    datos coherentes atrás: no hay nada que recuperar.
    """
    op.execute("UPDATE postulaciones SET estado = 'descartada' WHERE estado = 'pendiente'")
    op.drop_column("postulaciones", "creado_en")