"""Tabel filaments

Revision ID: 0001_initial
Revises:
Create Date: 2026-11-15
"""
from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "filaments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("material", sa.String(length=20), nullable=False),
        sa.Column("color_name", sa.String(length=100), nullable=False),
        sa.Column("r", sa.SmallInteger(), nullable=False),
        sa.Column("g", sa.SmallInteger(), nullable=False),
        sa.Column("b", sa.SmallInteger(), nullable=False),
        sa.Column("transparent", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("price_per_kg", sa.Numeric(8, 2), nullable=False),
        sa.Column("weight_new", sa.Integer(), nullable=False, server_default="1000"),
        sa.Column("weight_current", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("r BETWEEN 0 AND 255", name="ck_filaments_r"),
        sa.CheckConstraint("g BETWEEN 0 AND 255", name="ck_filaments_g"),
        sa.CheckConstraint("b BETWEEN 0 AND 255", name="ck_filaments_b"),
        sa.CheckConstraint("price_per_kg >= 0", name="ck_filaments_price"),
        sa.CheckConstraint("weight_new > 0", name="ck_filaments_weight_new"),
        sa.CheckConstraint(
            "weight_current >= 0 AND weight_current <= weight_new", name="ck_filaments_weight_current"
        ),
    )
    op.create_index("ix_filaments_material", "filaments", ["material"])


def downgrade() -> None:
    op.drop_index("ix_filaments_material", table_name="filaments")
    op.drop_table("filaments")
