from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .config import get_settings

ColorChannel = Annotated[int, Field(ge=0, le=255)]


class FilamentBase(BaseModel):
    material: str = Field(min_length=1, max_length=20)
    color_name: str = Field(min_length=1, max_length=100)
    r: ColorChannel
    g: ColorChannel
    b: ColorChannel
    transparent: bool = False
    price_per_kg: Decimal = Field(ge=0, max_digits=8, decimal_places=2)
    weight_new: int = Field(gt=0, le=100_000)
    weight_current: int = Field(ge=0, le=100_000)

    @field_validator("color_name")
    @classmethod
    def _strip_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Kleurnaam is verplicht")
        return v

    @field_validator("material")
    @classmethod
    def _check_material(cls, v: str) -> str:
        v = v.strip().upper()
        allowed = get_settings().materials
        if v not in allowed:
            raise ValueError(f"Onbekend materiaal; kies uit {', '.join(allowed)}")
        return v

    @model_validator(mode="after")
    def _check_weights(self) -> "FilamentBase":
        if self.weight_current > self.weight_new:
            raise ValueError("Gewicht nu mag niet groter zijn dan gewicht nieuw")
        return self


class FilamentCreate(FilamentBase):
    pass


class FilamentUpdate(BaseModel):
    """Gedeeltelijke update (PATCH). Na samenvoegen wordt opnieuw volledig gevalideerd."""

    material: str | None = None
    color_name: str | None = None
    r: int | None = None
    g: int | None = None
    b: int | None = None
    transparent: bool | None = None
    price_per_kg: Decimal | None = None
    weight_new: int | None = None
    weight_current: int | None = None


class WeightUpdate(BaseModel):
    weight_current: int = Field(ge=0)


class FilamentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    material: str
    color_name: str
    r: int
    g: int
    b: int
    hex: str
    transparent: bool
    price_per_kg: Decimal
    weight_new: int
    weight_current: int
    percent_remaining: float
    remaining_value: Decimal
    low_stock: bool = False
    created_at: datetime
    updated_at: datetime


class MaterialStats(BaseModel):
    material: str
    count: int
    weight_g: int
    value: Decimal


class Stats(BaseModel):
    count: int
    weight_g: int
    value: Decimal
    low_stock_count: int
    low_stock_threshold: float
    per_material: list[MaterialStats]


# --- Nederlandse foutmeldingen voor het webformulier ---

FIELD_LABELS = {
    "material": "Materiaal",
    "color_name": "Kleurnaam",
    "r": "Rood (R)",
    "g": "Groen (G)",
    "b": "Blauw (B)",
    "transparent": "Transparant",
    "price_per_kg": "Prijs per kilo",
    "weight_new": "Gewicht nieuw",
    "weight_current": "Gewicht nu",
}


def dutch_errors(exc: ValidationError) -> dict[str, str]:
    """Zet pydantic-fouten om naar {veld: Nederlandse melding}. Modelfouten komen onder '__all__'."""
    out: dict[str, str] = {}
    for err in exc.errors():
        loc = err.get("loc") or ()
        fld = str(loc[0]) if loc else "__all__"
        typ = err.get("type", "")
        ctx = err.get("ctx") or {}
        if typ == "missing":
            msg = "Dit veld is verplicht"
        elif typ in ("int_parsing", "int_from_float"):
            msg = "Vul een geheel getal in"
        elif typ in ("decimal_parsing", "float_parsing"):
            msg = "Vul een geldig bedrag in"
        elif typ == "greater_than_equal":
            msg = f"Moet minimaal {ctx.get('ge')} zijn"
        elif typ == "greater_than":
            msg = f"Moet groter zijn dan {ctx.get('gt')}"
        elif typ == "less_than_equal":
            msg = f"Mag maximaal {ctx.get('le')} zijn"
        elif typ == "string_too_short":
            msg = "Dit veld is verplicht"
        elif typ == "string_too_long":
            msg = f"Maximaal {ctx.get('max_length')} tekens"
        elif typ == "decimal_max_places":
            msg = "Maximaal 2 decimalen"
        elif typ in ("decimal_max_digits", "decimal_whole_digits"):
            msg = "Bedrag is te groot"
        elif typ == "value_error":
            msg = str(ctx.get("error") or err.get("msg", "Ongeldige waarde"))
        else:
            msg = err.get("msg", "Ongeldige waarde")
        if fld == "__all__" and "Gewicht nu" in msg:
            fld = "weight_current"
        out.setdefault(fld, msg)
    return out
