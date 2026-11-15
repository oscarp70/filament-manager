from __future__ import annotations

from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import Boolean, CheckConstraint, DateTime, Integer, Numeric, SmallInteger, String, func
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class Filament(Base):
    __tablename__ = "filaments"
    __table_args__ = (
        CheckConstraint("r BETWEEN 0 AND 255", name="ck_filaments_r"),
        CheckConstraint("g BETWEEN 0 AND 255", name="ck_filaments_g"),
        CheckConstraint("b BETWEEN 0 AND 255", name="ck_filaments_b"),
        CheckConstraint("price_per_kg >= 0", name="ck_filaments_price"),
        CheckConstraint("weight_new > 0", name="ck_filaments_weight_new"),
        CheckConstraint(
            "weight_current >= 0 AND weight_current <= weight_new", name="ck_filaments_weight_current"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    material: Mapped[str] = mapped_column(String(20), index=True)
    color_name: Mapped[str] = mapped_column(String(100))
    r: Mapped[int] = mapped_column(SmallInteger)
    g: Mapped[int] = mapped_column(SmallInteger)
    b: Mapped[int] = mapped_column(SmallInteger)
    transparent: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    price_per_kg: Mapped[Decimal] = mapped_column(Numeric(8, 2))
    weight_new: Mapped[int] = mapped_column(Integer, default=1000, server_default="1000")
    weight_current: Mapped[int] = mapped_column(Integer)
    rfid: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # --- Afgeleide waarden (niet opgeslagen) ---
    @property
    def hex(self) -> str:
        return f"#{self.r:02x}{self.g:02x}{self.b:02x}"

    @property
    def percent_remaining(self) -> float:
        if not self.weight_new:
            return 0.0
        return round(self.weight_current / self.weight_new * 100, 1)

    @property
    def remaining_value(self) -> Decimal:
        value = Decimal(self.weight_current) / Decimal(1000) * Decimal(self.price_per_kg)
        return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------------------
# Printerkoppeling (Bambu Lab)
# ---------------------------------------------------------------------------

from sqlalchemy import ForeignKey, Text  # noqa: E402
from sqlalchemy.orm import relationship  # noqa: E402


class AppSetting(Base):
    """Eenvoudige key/value-opslag voor instellingen die via de webinterface worden beheerd."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)


class AmsAssignment(Base):
    """Welke spoel zit in welk AMS-slot. slot = ams_id * 4 + tray_id (0..15), 254 = externe spoel."""

    __tablename__ = "ams_assignments"

    slot: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    filament_id: Mapped[int | None] = mapped_column(
        ForeignKey("filaments.id", ondelete="SET NULL"), nullable=True
    )
    source: Mapped[str] = mapped_column(String(16), default="manual", server_default="manual")  # manual / rfid
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    filament: Mapped["Filament | None"] = relationship()


class PrintJob(Base):
    __tablename__ = "print_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_key: Mapped[str] = mapped_column(String(200), index=True)
    name: Mapped[str] = mapped_column(String(255), default="", server_default="")
    plate: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # running / finished / failed / cancelled
    status: Mapped[str] = mapped_column(String(16), default="running", server_default="running")
    progress: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # Bron van het verbruik: 3mf / ams_remain / manual / none
    usage_source: Mapped[str] = mapped_column(String(16), default="none", server_default="none")
    total_g: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0, server_default="0")
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    data: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON: snapshot, mapping, 3mf-gegevens
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    usages: Mapped[list["PrintUsage"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="PrintUsage.id"
    )


class PrintUsage(Base):
    __tablename__ = "print_usages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("print_jobs.id", ondelete="CASCADE"), index=True)
    filament_id: Mapped[int | None] = mapped_column(
        ForeignKey("filaments.id", ondelete="SET NULL"), nullable=True, index=True
    )
    slot: Mapped[int | None] = mapped_column(Integer, nullable=True)
    filament_index: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 1-based index in de 3MF
    material: Mapped[str | None] = mapped_column(String(32), nullable=True)
    color: Mapped[str | None] = mapped_column(String(16), nullable=True)
    grams: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    deducted_g: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0, server_default="0")
    deducted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    job: Mapped[PrintJob] = relationship(back_populates="usages")
    filament: Mapped["Filament | None"] = relationship()

    @property
    def is_deducted(self) -> bool:
        return self.deducted_at is not None
