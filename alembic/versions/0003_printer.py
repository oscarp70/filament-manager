"""Printerkoppeling: instellingen, AMS-toewijzing, printjobs en verbruik

Voegt alleen nieuwe tabellen toe; bestaande spoelen blijven ongewijzigd.

Revision ID: 0003_printer
Revises: 0002_rfid
Create Date: 2026-11-15
"""
from alembic import op
import sqlalchemy as sa

revision = "0003_printer"
down_revision = "0002_rfid"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(length=64), primary_key=True),
        sa.Column("value", sa.Text(), nullable=True),
    )
    op.create_table(
        "ams_assignments",
        sa.Column("slot", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("filament_id", sa.Integer(), sa.ForeignKey("filaments.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="manual"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "print_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_key", sa.String(length=200), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("plate", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="running"),
        sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("usage_source", sa.String(length=16), nullable=False, server_default="none"),
        sa.Column("total_g", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("data", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_print_jobs_job_key", "print_jobs", ["job_key"])
    op.create_table(
        "print_usages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("print_jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("filament_id", sa.Integer(), sa.ForeignKey("filaments.id", ondelete="SET NULL"), nullable=True),
        sa.Column("slot", sa.Integer(), nullable=True),
        sa.Column("filament_index", sa.Integer(), nullable=True),
        sa.Column("material", sa.String(length=32), nullable=True),
        sa.Column("color", sa.String(length=16), nullable=True),
        sa.Column("grams", sa.Numeric(10, 2), nullable=False),
        sa.Column("deducted_g", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("deducted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_print_usages_job_id", "print_usages", ["job_id"])
    op.create_index("ix_print_usages_filament_id", "print_usages", ["filament_id"])


def downgrade() -> None:
    op.drop_index("ix_print_usages_filament_id", table_name="print_usages")
    op.drop_index("ix_print_usages_job_id", table_name="print_usages")
    op.drop_table("print_usages")
    op.drop_index("ix_print_jobs_job_key", table_name="print_jobs")
    op.drop_table("print_jobs")
    op.drop_table("ams_assignments")
    op.drop_table("app_settings")
