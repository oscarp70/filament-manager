from __future__ import annotations

import colorsys
from collections.abc import Sequence
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .models import Filament
from .schemas import FilamentCreate, FilamentUpdate, MaterialStats, Stats

SORT_OPTIONS = {
    "date": "Nieuwste eerst",
    "date_asc": "Oudste eerst",
    "color": "Kleur",
    "weight": "Meeste resterend",
    "weight_asc": "Minste resterend",
    "name": "Kleurnaam",
}


def _hue_key(f: Filament) -> tuple[int, float, float]:
    h, l, s = colorsys.rgb_to_hls(f.r / 255, f.g / 255, f.b / 255)
    # Grijstinten (lage verzadiging) apart achteraan, gesorteerd op lichtheid
    if s < 0.12:
        return (1, -l, 0.0)
    # Roodtinten net onder 360° bij de andere roden houden
    if h > 0.95:
        h -= 1
    return (0, h, -l)


def is_low_stock(f: Filament) -> bool:
    return f.percent_remaining < get_settings().low_stock_threshold


def list_filaments(
    db: Session,
    material: str | None = None,
    transparent: bool | None = None,
    sort: str = "date",
) -> list[Filament]:
    stmt = select(Filament)
    if material:
        stmt = stmt.where(Filament.material == material.upper())
    if transparent is not None:
        stmt = stmt.where(Filament.transparent == transparent)

    if sort == "date_asc":
        stmt = stmt.order_by(Filament.created_at.asc(), Filament.id.asc())
    elif sort == "weight":
        stmt = stmt.order_by(Filament.weight_current.desc(), Filament.id.asc())
    elif sort == "weight_asc":
        stmt = stmt.order_by(Filament.weight_current.asc(), Filament.id.asc())
    elif sort == "name":
        stmt = stmt.order_by(Filament.color_name.asc(), Filament.id.asc())
    else:
        stmt = stmt.order_by(Filament.created_at.desc(), Filament.id.desc())

    items = list(db.scalars(stmt))
    if sort == "color":
        items.sort(key=_hue_key)
    return items


def get_filament(db: Session, filament_id: int) -> Filament | None:
    return db.get(Filament, filament_id)


def create_filament(db: Session, data: FilamentCreate) -> Filament:
    obj = Filament(**data.model_dump())
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


def replace_filament(db: Session, obj: Filament, data: FilamentCreate) -> Filament:
    for key, value in data.model_dump().items():
        setattr(obj, key, value)
    db.commit()
    db.refresh(obj)
    return obj


def merged_update(obj: Filament, patch: FilamentUpdate) -> FilamentCreate:
    """Voeg een PATCH samen met het bestaande record en valideer het geheel (kan ValidationError geven)."""
    current = {k: getattr(obj, k) for k in FilamentCreate.model_fields}
    current.update(patch.model_dump(exclude_unset=True))
    return FilamentCreate(**current)


def delete_filament(db: Session, obj: Filament) -> None:
    db.delete(obj)
    db.commit()


def compute_stats(items: Sequence[Filament]) -> Stats:
    settings = get_settings()
    per: dict[str, MaterialStats] = {
        m: MaterialStats(material=m, count=0, weight_g=0, value=Decimal("0.00")) for m in settings.materials
    }
    total_weight = 0
    total_value = Decimal("0.00")
    low = 0
    for f in items:
        ms = per.setdefault(f.material, MaterialStats(material=f.material, count=0, weight_g=0, value=Decimal("0.00")))
        ms.count += 1
        ms.weight_g += f.weight_current
        ms.value += f.remaining_value
        total_weight += f.weight_current
        total_value += f.remaining_value
        if is_low_stock(f):
            low += 1
    return Stats(
        count=len(items),
        weight_g=total_weight,
        value=total_value,
        low_stock_count=low,
        low_stock_threshold=settings.low_stock_threshold,
        per_material=[m for m in per.values() if m.count or m.material in settings.materials],
    )
