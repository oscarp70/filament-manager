"""RFID-veld toevoegen

Bestaande spoelen blijven behouden; het nieuwe veld is optioneel (NULL).

Revision ID: 0002_rfid
Revises: 0001_initial
Create Date: 2026-11-15
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_rfid"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("filaments", sa.Column("rfid", sa.String(length=64), nullable=True))
    op.create_index("ix_filaments_rfid", "filaments", ["rfid"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_filaments_rfid", table_name="filaments")
    op.drop_column("filaments", "rfid")
