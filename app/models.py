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
