"""Databasebewerkingen voor printverbruik: afschrijven, terugdraaien, toewijzen, instellingen."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import AmsAssignment, AppSetting, Filament, PrintJob, PrintUsage

TWO = Decimal("0.01")

# --- Instellingen ---

SETTING_KEYS = {
    "bambu_enabled": "false",
    "bambu_host": "",
    "bambu_serial": "",
    "bambu_access_code": "",
    "bambu_auto_deduct": "true",
}


def get_settings(db: Session) -> dict[str, str]:
    # Standaardwaarden mogen uit de environment komen (BAMBU_HOST, BAMBU_SERIAL, ...);
    # wat via de webinterface is opgeslagen gaat voor.
    values = {k: (os.getenv(k.upper()) or v) for k, v in SETTING_KEYS.items()}
    for row in db.scalars(select(AppSetting).where(AppSetting.key.in_(SETTING_KEYS.keys()))):
        if row.value is not None:
            values[row.key] = row.value
    return values


def save_settings(db: Session, values: dict[str, str]) -> None:
    for key, value in values.items():
        if key not in SETTING_KEYS:
            continue
        row = db.get(AppSetting, key)
        if row is None:
            db.add(AppSetting(key=key, value=value))
        else:
            row.value = value
    db.commit()


def is_true(v: str | None) -> bool:
    return str(v or "").lower() in ("1", "true", "on", "ja", "yes")


# --- Slottoewijzing ---

def assignments(db: Session) -> dict[int, AmsAssignment]:
    return {a.slot: a for a in db.scalars(select(AmsAssignment))}


def assign_slot(db: Session, slot: int, filament_id: int | None, source: str = "manual") -> None:
    row = db.get(AmsAssignment, slot)
    if row is None:
        row = AmsAssignment(slot=slot)
        db.add(row)
    row.filament_id = filament_id
    row.source = source
    db.commit()


# --- Verbruik ---

def q2(v: float | Decimal) -> Decimal:
    return Decimal(str(v)).quantize(TWO, rounding=ROUND_HALF_UP)


def deduct(db: Session, usage: PrintUsage, commit: bool = True) -> Decimal:
    """Schrijf het verbruik af van de gekoppelde spoel. Geeft het afgeschreven aantal gram."""
    if usage.deducted_at is not None or usage.filament_id is None:
        return Decimal(0)
    fil = db.get(Filament, usage.filament_id)
    if fil is None:
        return Decimal(0)
    grams = q2(usage.grams)
    whole = int(grams.to_integral_value(rounding=ROUND_HALF_UP))
    take = min(whole, fil.weight_current)
    fil.weight_current -= take
    usage.deducted_g = Decimal(take)
    usage.deducted_at = datetime.now(timezone.utc)
    if commit:
        db.commit()
    return Decimal(take)


def undo(db: Session, usage: PrintUsage, commit: bool = True) -> Decimal:
    """Draai een afschrijving terug (het gewicht komt terug op de spoel)."""
    if usage.deducted_at is None:
        return Decimal(0)
    back = int(usage.deducted_g or 0)
    if usage.filament_id is not None:
        fil = db.get(Filament, usage.filament_id)
        if fil is not None:
            fil.weight_current = min(fil.weight_new, fil.weight_current + back)
    usage.deducted_g = Decimal(0)
    usage.deducted_at = None
    if commit:
        db.commit()
    return Decimal(back)


def reassign(db: Session, usage: PrintUsage, filament_id: int | None, apply: bool = True) -> None:
    """Koppel verbruik aan een andere spoel; een eerdere afschrijving wordt eerst teruggedraaid."""
    was_deducted = usage.deducted_at is not None
    undo(db, usage, commit=False)
    usage.filament_id = filament_id
    db.flush()
    if filament_id is not None and (apply or was_deducted):
        deduct(db, usage, commit=False)
    db.commit()


def set_grams(db: Session, usage: PrintUsage, grams: Decimal) -> None:
    was_deducted = usage.deducted_at is not None
    undo(db, usage, commit=False)
    usage.grams = q2(grams)
    db.flush()
    if was_deducted:
        deduct(db, usage, commit=False)
    job = usage.job
    job.total_g = sum((q2(u.grams) for u in job.usages), Decimal(0))
    db.commit()


def job_data(job: PrintJob) -> dict[str, Any]:
    try:
        return json.loads(job.data or "{}")
    except ValueError:
        return {}


def set_job_data(job: PrintJob, data: dict[str, Any]) -> None:
    job.data = json.dumps(data, ensure_ascii=False, default=str)
