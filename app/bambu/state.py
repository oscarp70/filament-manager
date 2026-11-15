"""Pure logica: printerstatus bijhouden, AMS-sloten, RFID-matching en sloten aan 3MF-filamenten koppelen."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .threemf import FilamentUse

EXTERNAL_SLOT = 254
ACTIVE_STATES = {"PREPARE", "RUNNING", "PAUSE", "SLICING"}
END_STATES = {"FINISH", "FAILED"}


def slot_label(slot: int | None) -> str:
    if slot is None:
        return "onbekend"
    if slot == EXTERNAL_SLOT:
        return "Externe spoel"
    return f"AMS {slot // 4 + 1} · slot {slot % 4 + 1}"


@dataclass
class Tray:
    slot: int
    present: bool
    material: str = ""
    color: str = ""  # #RRGGBB
    tag_uid: str = ""
    tray_uuid: str = ""
    remain: int = -1  # % volgens AMS (alleen Bambu-spoelen met RFID), -1 = onbekend
    weight: int = 0  # gram volgens tag
    name: str = ""

    @property
    def label(self) -> str:
        return slot_label(self.slot)

    @property
    def has_tag(self) -> bool:
        return bool(normalize_tag(self.tag_uid).strip("0") or normalize_tag(self.tray_uuid).strip("0"))


def _to_int(v: Any, default: int = -1) -> int:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return default


def _color(v: Any) -> str:
    s = str(v or "").strip().lstrip("#")
    return f"#{s[:6].upper()}" if len(s) >= 6 else ""


def _tray_from(d: dict[str, Any], slot: int) -> Tray:
    present = bool(d.get("tray_type"))
    return Tray(
        slot=slot,
        present=present,
        material=str(d.get("tray_type") or "").upper(),
        color=_color(d.get("tray_color")),
        tag_uid=str(d.get("tag_uid") or ""),
        tray_uuid=str(d.get("tray_uuid") or ""),
        remain=_to_int(d.get("remain"), -1),
        weight=_to_int(d.get("tray_weight"), 0),
        name=str(d.get("tray_sub_brands") or d.get("tray_id_name") or ""),
    )


class PrinterState:
    """Houdt de laatst bekende status bij. De P1-serie stuurt alleen wijzigingen, dus we voegen samen."""

    def __init__(self) -> None:
        self.data: dict[str, Any] = {}

    def merge(self, report: dict[str, Any]) -> None:
        p = report.get("print")
        if not isinstance(p, dict):
            return
        for key, value in p.items():
            if key == "ams" and isinstance(value, dict):
                cur = self.data.setdefault("ams", {})
                if isinstance(value.get("ams"), list):
                    # Per AMS-unit en per tray samenvoegen
                    units = {str(u.get("id")): u for u in cur.get("ams", []) if isinstance(u, dict)}
                    for unit in value["ams"]:
                        if not isinstance(unit, dict):
                            continue
                        uid = str(unit.get("id"))
                        old = units.get(uid, {})
                        trays = {str(t.get("id")): t for t in old.get("tray", []) if isinstance(t, dict)}
                        for t in unit.get("tray", []) or []:
                            if isinstance(t, dict):
                                tid = str(t.get("id"))
                                if len(t) <= 1:  # lege tray: alleen {"id": ..}
                                    trays[tid] = dict(t)
                                else:
                                    trays[tid] = {**trays.get(tid, {}), **t}
                        merged = {**old, **{k: v for k, v in unit.items() if k != "tray"}}
                        merged["tray"] = list(trays.values())
                        units[uid] = merged
                    cur["ams"] = list(units.values())
                for k, v in value.items():
                    if k != "ams":
                        cur[k] = v
            elif key == "vt_tray" and isinstance(value, dict):
                self.data["vt_tray"] = {**self.data.get("vt_tray", {}), **value}
            else:
                self.data[key] = value

    # --- Eigenschappen ---
    @property
    def gcode_state(self) -> str:
        return str(self.data.get("gcode_state") or "").upper()

    @property
    def progress(self) -> int:
        return max(0, min(100, _to_int(self.data.get("mc_percent"), 0)))

    @property
    def remaining_min(self) -> int:
        return _to_int(self.data.get("mc_remaining_time"), 0)

    @property
    def subtask_name(self) -> str:
        return str(self.data.get("subtask_name") or "")

    @property
    def gcode_file(self) -> str:
        return str(self.data.get("gcode_file") or "")

    @property
    def layer(self) -> tuple[int, int]:
        return _to_int(self.data.get("layer_num"), 0), _to_int(self.data.get("total_layer_num"), 0)

    @property
    def job_key(self) -> str:
        task = str(self.data.get("task_id") or "").strip()
        if task and task != "0":
            return f"task:{task}"
        start = str(self.data.get("gcode_start_time") or "").strip()
        return f"name:{self.subtask_name}|{start}"

    @property
    def has_identity(self) -> bool:
        """Weten we al welke print het is? (na een herstart komen eerst soms alleen deltaberichten)"""
        task = str(self.data.get("task_id") or "").strip()
        return bool((task and task != "0") or self.subtask_name)

    @property
    def tray_now(self) -> int | None:
        ams = self.data.get("ams") or {}
        v = _to_int(ams.get("tray_now"), 255)
        if v == 255:
            return None
        return v

    @property
    def mapping(self) -> list[int] | None:
        m = self.data.get("mapping")
        return [int(x) for x in m] if isinstance(m, list) and m else None

    def trays(self) -> list[Tray]:
        out: list[Tray] = []
        ams = self.data.get("ams") or {}
        for unit in sorted(ams.get("ams", []) or [], key=lambda u: _to_int(u.get("id"), 0)):
            aid = _to_int(unit.get("id"), 0)
            for t in sorted(unit.get("tray", []) or [], key=lambda t: _to_int(t.get("id"), 0)):
                out.append(_tray_from(t, aid * 4 + _to_int(t.get("id"), 0)))
        vt = self.data.get("vt_tray")
        if isinstance(vt, dict):
            out.append(_tray_from(vt, EXTERNAL_SLOT))
        return out

    def tray_map(self) -> dict[int, Tray]:
        return {t.slot: t for t in self.trays()}


# --- RFID ---

def normalize_tag(value: str | None) -> str:
    return re.sub(r"[^0-9A-F]", "", (value or "").upper())


def rfid_matches(rfid: str | None, tray: Tray) -> bool:
    """Vergelijk het RFID-veld van een spoel met de tag die de AMS leest.

    Werkt met de UID zoals een telefoon hem toont (bv. 'D5:3E:55:05') en met de volledige
    tag_uid (16 hex) of tray_uuid (32 hex) uit de printer.
    """
    r = normalize_tag(rfid)
    if len(r) < 8:
        return False
    tag = normalize_tag(tray.tag_uid)
    uuid = normalize_tag(tray.tray_uuid)
    if tag.strip("0") and (r == tag or tag.startswith(r)):
        return True
    return bool(uuid.strip("0")) and r == uuid


# --- Sloten koppelen ---

def decode_mapping_value(v: int) -> int | None:
    if v is None or v < 0:
        return None
    if v >= 256:
        ams_id, tray_id = v >> 8, v & 0xFF
        if ams_id >= 254:
            return EXTERNAL_SLOT
        return ams_id * 4 + tray_id
    if v in (254, 255):
        return EXTERNAL_SLOT if v == 254 else None
    return v


def _rgb(c: str) -> tuple[int, int, int] | None:
    c = c.lstrip("#")
    if len(c) < 6:
        return None
    try:
        return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    except ValueError:
        return None


def color_distance(a: str, b: str) -> float:
    ra, rb = _rgb(a), _rgb(b)
    if ra is None or rb is None:
        return 1e6
    return sum((x - y) ** 2 for x, y in zip(ra, rb)) ** 0.5


def resolve_slots(
    filaments: list[FilamentUse],
    ams_mapping: list[int] | None,
    used_slots: list[int],
    trays: dict[int, Tray],
) -> dict[int, int | None]:
    """Bepaal per 3MF-filament (index) uit welk slot het kwam."""
    used = [f for f in filaments if f.used_g > 0]
    result: dict[int, int | None] = {f.index: None for f in used}

    # 1. Expliciete AMS-mapping uit het printcommando of de status
    if ams_mapping:
        for f in used:
            i = f.index - 1
            if 0 <= i < len(ams_mapping):
                result[f.index] = decode_mapping_value(ams_mapping[i])
        if all(v is not None for v in result.values()):
            return result

    # 2. Eén filament: het slot dat tijdens de print actief was
    if len(used) == 1 and result[used[0].index] is None:
        if used_slots:
            result[used[0].index] = used_slots[0]
        elif EXTERNAL_SLOT in trays and trays[EXTERNAL_SLOT].present and not any(
            t.present for s, t in trays.items() if s != EXTERNAL_SLOT
        ):
            result[used[0].index] = EXTERNAL_SLOT
        return result

    # 3. Meerdere filamenten: op materiaal en kleur matchen, bij voorkeur binnen de gebruikte sloten
    pool = [s for s in used_slots if s in trays] or [s for s, t in trays.items() if t.present]
    taken = {v for v in result.values() if v is not None}
    for f in sorted(used, key=lambda x: -x.used_g):
        if result[f.index] is not None:
            continue
        best, best_d = None, 1e9
        for s in pool:
            if s in taken:
                continue
            t = trays.get(s)
            if t is None:
                continue
            d = color_distance(f.color, t.color)
            if f.material and t.material and f.material != t.material:
                d += 1000
            if d < best_d:
                best, best_d = s, d
        if best is not None and best_d < 1000:
            result[f.index] = best
            taken.add(best)
    return result
