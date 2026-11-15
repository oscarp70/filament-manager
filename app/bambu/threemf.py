"""Filamentverbruik uitlezen uit een gesneden Bambu/Orca .3mf (Metadata/slice_info.config)."""

from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field


@dataclass
class FilamentUse:
    index: int  # 1-based filament-id uit de slicer (komt overeen met ams_mapping[index-1])
    material: str
    color: str  # #RRGGBB
    used_g: float
    used_m: float = 0.0
    tray_info_idx: str = ""


@dataclass
class PlateInfo:
    index: int
    weight_g: float
    prediction_s: int
    filaments: list[FilamentUse] = field(default_factory=list)


class ThreeMFError(ValueError):
    pass


def _f(v: str | None) -> float:
    try:
        return float(v or 0)
    except ValueError:
        return 0.0


def _norm_color(c: str | None) -> str:
    c = (c or "").strip().lstrip("#")
    return f"#{c[:6].upper()}" if len(c) >= 6 else ""


def parse_slice_info(xml_bytes: bytes) -> list[PlateInfo]:
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise ThreeMFError(f"slice_info.config is geen geldige XML: {exc}") from exc
    plates: list[PlateInfo] = []
    for plate in root.iter("plate"):
        meta = {m.get("key"): m.get("value") for m in plate.findall("metadata")}
        info = PlateInfo(
            index=int(_f(meta.get("index")) or len(plates) + 1),
            weight_g=_f(meta.get("weight")),
            prediction_s=int(_f(meta.get("prediction"))),
        )
        for fil in plate.findall("filament"):
            info.filaments.append(
                FilamentUse(
                    index=int(_f(fil.get("id"))),
                    material=(fil.get("type") or "").upper(),
                    color=_norm_color(fil.get("color")),
                    used_g=_f(fil.get("used_g")),
                    used_m=_f(fil.get("used_m")),
                    tray_info_idx=fil.get("tray_info_idx") or "",
                )
            )
        plates.append(info)
    return plates


def plate_from_path(path: str | None) -> int | None:
    """'Metadata/plate_3.gcode' -> 3"""
    if not path:
        return None
    m = re.search(r"plate_(\d+)\.gcode", path)
    return int(m.group(1)) if m else None


def read_3mf(data: bytes, plate: int | None = None) -> PlateInfo:
    """Lees het verbruik voor één plaat. Zonder platenummer: de enige (of eerste) plaat met filament."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ThreeMFError("Bestand is geen geldig 3MF/ZIP-archief") from exc
    try:
        xml = zf.read("Metadata/slice_info.config")
    except KeyError as exc:
        raise ThreeMFError("Geen Metadata/slice_info.config: is het bestand wel gesneden (.gcode.3mf)?") from exc
    plates = parse_slice_info(xml)
    if not plates:
        raise ThreeMFError("Geen platen gevonden in slice_info.config")
    if plate is not None:
        for p in plates:
            if p.index == plate:
                return p
    with_fil = [p for p in plates if p.filaments and sum(f.used_g for f in p.filaments) > 0]
    if plate is None and len(with_fil) >= 1:
        return with_fil[0]
    if plate is not None:
        raise ThreeMFError(f"Plaat {plate} niet gevonden in het 3MF-bestand")
    return plates[0]
