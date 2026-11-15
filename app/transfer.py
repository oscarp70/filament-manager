"""Export en import van de filamentvoorraad (JSON en CSV)."""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import ValidationError
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .models import Filament
from .schemas import FilamentCreate, dutch_errors

FORMAT_VERSION = 1
DATA_FIELDS = [
    "material", "color_name", "r", "g", "b", "transparent",
    "price_per_kg", "weight_new", "weight_current", "rfid",
]
CSV_FIELDS = ["id", *DATA_FIELDS[:5], "hex", *DATA_FIELDS[5:], "created_at", "updated_at"]
MAX_IMPORT_BYTES = 5 * 1024 * 1024


# ---------------- Export ----------------

def _row(f: Filament) -> dict[str, Any]:
    return {
        "id": f.id,
        "material": f.material,
        "color_name": f.color_name,
        "r": f.r, "g": f.g, "b": f.b,
        "hex": f.hex,
        "transparent": f.transparent,
        "price_per_kg": f"{Decimal(f.price_per_kg):.2f}",
        "weight_new": f.weight_new,
        "weight_current": f.weight_current,
        "rfid": f.rfid,
        "created_at": f.created_at.isoformat() if f.created_at else None,
        "updated_at": f.updated_at.isoformat() if f.updated_at else None,
    }


def _all(db: Session) -> list[Filament]:
    return list(db.scalars(select(Filament).order_by(Filament.id)))


def export_json(db: Session) -> str:
    rows = [_row(f) for f in _all(db)]
    payload = {
        "app": "filament-manager",
        "format_version": FORMAT_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "count": len(rows),
        "filaments": rows,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def export_csv(db: Session) -> str:
    """CSV voor Excel (NL): puntkomma als scheidingsteken, decimale komma, UTF-8 met BOM."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=CSV_FIELDS, delimiter=";", lineterminator="\r\n")
    writer.writeheader()
    for f in _all(db):
        row = _row(f)
        row["price_per_kg"] = row["price_per_kg"].replace(".", ",")
        row["transparent"] = "ja" if f.transparent else "nee"
        row["rfid"] = row["rfid"] or ""
        writer.writerow(row)
    return "﻿" + buf.getvalue()


# ---------------- Import ----------------

class ImportFormatError(ValueError):
    pass


def _parse_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    return str(v or "").strip().lower() in ("1", "true", "ja", "yes", "y", "j", "waar", "x", "on")


def _parse_price(v: Any) -> Any:
    if v is None or isinstance(v, (int, float, Decimal)):
        return v
    s = str(v).strip().replace("€", "").replace(" ", "")
    if "," in s and "." in s:  # 1.234,56
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", ".")
    return s


def _parse_dt(v: Any) -> datetime | None:
    if not v:
        return None
    try:
        dt = datetime.fromisoformat(str(v).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _int_or_none(v: Any) -> int | None:
    try:
        return int(str(v).strip()) if str(v or "").strip() else None
    except ValueError:
        return None


def _rows_from_bytes(content: bytes) -> list[dict[str, Any]]:
    if len(content) > MAX_IMPORT_BYTES:
        raise ImportFormatError("Bestand is te groot (maximaal 5 MB)")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("latin-1")
    stripped = text.lstrip()
    if not stripped:
        raise ImportFormatError("Het bestand is leeg")

    if stripped[0] in "[{":
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise ImportFormatError(f"Ongeldige JSON: {exc.msg} (regel {exc.lineno})") from exc
        rows = data.get("filaments") if isinstance(data, dict) else data
        if not isinstance(rows, list):
            raise ImportFormatError("JSON bevat geen lijst 'filaments'")
        if not all(isinstance(r, dict) for r in rows):
            raise ImportFormatError("Elke spoel in de JSON moet een object zijn")
        return rows

    # CSV: scheidingsteken herkennen (; of ,)
    first = stripped.splitlines()[0]
    delimiter = ";" if first.count(";") >= first.count(",") else ","
    reader = csv.DictReader(io.StringIO(stripped), delimiter=delimiter)
    if not reader.fieldnames or "color_name" not in [h.strip() for h in reader.fieldnames]:
        raise ImportFormatError("CSV mist de kolomkoppen (verwacht o.a. 'material' en 'color_name')")
    return [{(k or "").strip(): v for k, v in r.items()} for r in reader]


def _hex_to_rgb(raw: dict[str, Any]) -> None:
    if all(str(raw.get(c, "")).strip() != "" for c in "rgb"):
        return
    h = str(raw.get("hex") or "").strip().lstrip("#")
    if len(h) == 6:
        try:
            raw["r"], raw["g"], raw["b"] = (int(h[i:i + 2], 16) for i in (0, 2, 4))
        except ValueError:
            pass


@dataclass
class ImportRow:
    line: int
    data: FilamentCreate
    source_id: int | None
    created_at: datetime | None
    updated_at: datetime | None
    action: str = "new"  # new / update
    target_id: int | None = None


@dataclass
class ImportPlan:
    mode: str
    rows: list[ImportRow] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    to_delete: int = 0

    @property
    def new_count(self) -> int:
        return sum(1 for r in self.rows if r.action == "new")

    @property
    def update_count(self) -> int:
        return sum(1 for r in self.rows if r.action == "update")

    @property
    def ok(self) -> bool:
        return not self.errors and bool(self.rows)


def plan_import(db: Session, content: bytes, mode: str) -> ImportPlan:
    """Lees en valideer een importbestand zonder iets te wijzigen."""
    if mode not in ("merge", "replace"):
        raise ImportFormatError("Onbekende importmodus")
    plan = ImportPlan(mode=mode)
    raw_rows = _rows_from_bytes(content)
    if not raw_rows:
        plan.errors.append("Het bestand bevat geen spoelen")
        return plan

    seen_rfid: dict[str, int] = {}
    for idx, src in enumerate(raw_rows, start=1):
        raw = {k: src.get(k) for k in (*DATA_FIELDS, "hex")}
        _hex_to_rgb(raw)
        raw["transparent"] = _parse_bool(raw.get("transparent"))
        raw["price_per_kg"] = _parse_price(raw.get("price_per_kg"))
        label = f"Spoel {idx}" + (f" ({src.get('color_name')})" if src.get("color_name") else "")
        try:
            data = FilamentCreate(**{k: v for k, v in raw.items() if k != "hex"})
        except ValidationError as exc:
            msgs = "; ".join(f"{k if k != '__all__' else 'algemeen'}: {m}" for k, m in dutch_errors(exc).items())
            plan.errors.append(f"{label}: {msgs}")
            continue
        if data.rfid:
            key = data.rfid.upper()
            if key in seen_rfid:
                plan.errors.append(f"{label}: RFID {data.rfid} komt ook voor bij spoel {seen_rfid[key]}")
                continue
            seen_rfid[key] = idx
        plan.rows.append(ImportRow(
            line=idx, data=data, source_id=_int_or_none(src.get("id")),
            created_at=_parse_dt(src.get("created_at")), updated_at=_parse_dt(src.get("updated_at")),
        ))

    existing = _all(db)
    if mode == "replace":
        plan.to_delete = len(existing)
        return plan

    # Samenvoegen: eerst op RFID, dan op id als kleurnaam en materiaal ook kloppen
    by_rfid = {f.rfid.upper(): f for f in existing if f.rfid}
    by_id = {f.id: f for f in existing}
    claimed: set[int] = set()
    for row in plan.rows:
        target = by_rfid.get(row.data.rfid.upper()) if row.data.rfid else None
        if target is None and row.source_id in by_id:
            cand = by_id[row.source_id]
            if cand.material == row.data.material and cand.color_name.lower() == row.data.color_name.lower():
                target = cand
        if target is not None and target.id not in claimed:
            claimed.add(target.id)
            row.action, row.target_id = "update", target.id
        elif row.data.rfid:
            # Nieuwe spoel met een RFID die al bij een andere bestaande spoel hoort
            other = by_rfid.get(row.data.rfid.upper())
            if other is not None:
                plan.errors.append(
                    f"Spoel {row.line} ({row.data.color_name}): RFID {row.data.rfid} is al gekoppeld aan spoel #{other.id}"
                )
    return plan


def apply_import(db: Session, plan: ImportPlan) -> None:
    """Voer een gevalideerd plan uit in één transactie (alles of niets)."""
    if not plan.ok:
        raise ImportFormatError("Import bevat fouten en is niet uitgevoerd")
    try:
        if plan.mode == "replace":
            db.execute(delete(Filament))
            db.flush()
        for row in plan.rows:
            values = row.data.model_dump()
            if row.action == "update" and row.target_id is not None:
                obj = db.get(Filament, row.target_id)
                for k, v in values.items():
                    setattr(obj, k, v)
            else:
                obj = Filament(**values)
                if row.created_at:
                    obj.created_at = row.created_at
                if row.updated_at:
                    obj.updated_at = row.updated_at
                db.add(obj)
        db.commit()
    except Exception:
        db.rollback()
        raise


def plan_to_json(plan: ImportPlan) -> str:
    """Compacte weergave van een gevalideerd plan, om tussen preview en uitvoeren mee te sturen."""
    return json.dumps({
        "filaments": [
            {**r.data.model_dump(mode="json"), "id": r.source_id,
             "created_at": r.created_at.isoformat() if r.created_at else None,
             "updated_at": r.updated_at.isoformat() if r.updated_at else None}
            for r in plan.rows
        ]
    }, ensure_ascii=False)
