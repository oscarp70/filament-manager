"""Voorbeeldspoelen toevoegen.

Gebruik:
    docker compose exec app python -m app.seed          # alleen als de tabel leeg is
    docker compose exec app python -m app.seed --force  # altijd toevoegen
"""

from __future__ import annotations

import sys
from decimal import Decimal

from sqlalchemy import func, select

from .database import SessionLocal
from .models import Filament

SAMPLES = [
    dict(material="PLA", color_name="Galaxy Black", r=27, g=27, b=34, transparent=False,
         price_per_kg=Decimal("22.99"), weight_new=1000, weight_current=820),
    dict(material="PLA", color_name="Signaal Oranje", r=247, g=127, b=0, transparent=False,
         price_per_kg=Decimal("19.95"), weight_new=1000, weight_current=1000),
    dict(material="PLA", color_name="Arctic White", r=244, g=244, b=242, transparent=False,
         price_per_kg=Decimal("19.95"), weight_new=1000, weight_current=95),
    dict(material="PETG", color_name="Clear Blue", r=64, g=156, b=255, transparent=True,
         price_per_kg=Decimal("24.50"), weight_new=1000, weight_current=640),
    dict(material="PETG", color_name="Transparant Naturel", r=230, g=236, b=232, transparent=True,
         price_per_kg=Decimal("23.00"), weight_new=1000, weight_current=410),
    dict(material="PETG", color_name="Forest Green", r=42, g=125, b=75, transparent=False,
         price_per_kg=Decimal("24.50"), weight_new=1000, weight_current=300),
    dict(material="ABS", color_name="Traffic Red", r=204, g=36, b=41, transparent=False,
         price_per_kg=Decimal("21.50"), weight_new=1000, weight_current=730),
    dict(material="ASA", color_name="Antraciet", r=61, g=66, b=73, transparent=False,
         price_per_kg=Decimal("29.90"), weight_new=750, weight_current=90),
    dict(material="ASA", color_name="Lavendel", r=169, g=140, b=222, transparent=False,
         price_per_kg=Decimal("31.00"), weight_new=1000, weight_current=1000),
]


def seed(force: bool = False) -> int:
    with SessionLocal() as db:
        existing = db.scalar(select(func.count()).select_from(Filament)) or 0
        if existing and not force:
            print(f"Database bevat al {existing} spoelen; niets toegevoegd (gebruik --force).")
            return 0
        db.add_all(Filament(**s) for s in SAMPLES)
        db.commit()
        print(f"{len(SAMPLES)} voorbeeldspoelen toegevoegd.")
        return len(SAMPLES)


if __name__ == "__main__":
    seed(force="--force" in sys.argv)
