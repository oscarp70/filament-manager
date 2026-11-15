"""REST API onder /api — bruikbaar voor integraties (bv. Home Assistant)."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from sqlalchemy.orm import Session

from . import crud
from .config import get_settings
from .database import get_db
from .models import Filament
from .schemas import FilamentCreate, FilamentRead, FilamentUpdate, Stats, WeightUpdate

router = APIRouter(prefix="/api", tags=["api"])

SortParam = Literal["date", "date_asc", "color", "weight", "weight_asc", "name"]


def _read(f: Filament) -> FilamentRead:
    out = FilamentRead.model_validate(f)
    out.low_stock = crud.is_low_stock(f)
    return out


def _get_or_404(db: Session, filament_id: int) -> Filament:
    obj = crud.get_filament(db, filament_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="Filament niet gevonden")
    return obj


@router.get("/materials", response_model=list[str])
def list_materials() -> list[str]:
    return get_settings().materials


@router.get("/filaments", response_model=list[FilamentRead])
def list_filaments(
    material: str | None = None,
    transparent: bool | None = None,
    sort: SortParam = "date",
    low_stock: bool | None = Query(None, description="Alleen spoelen onder (true) of boven (false) de drempel"),
    db: Session = Depends(get_db),
) -> list[FilamentRead]:
    items = crud.list_filaments(db, material=material, transparent=transparent, sort=sort)
    if low_stock is not None:
        items = [f for f in items if crud.is_low_stock(f) == low_stock]
    return [_read(f) for f in items]


@router.get("/filaments/{filament_id}", response_model=FilamentRead)
def get_filament(filament_id: int, db: Session = Depends(get_db)) -> FilamentRead:
    return _read(_get_or_404(db, filament_id))


@router.post("/filaments", response_model=FilamentRead, status_code=status.HTTP_201_CREATED)
def create_filament(data: FilamentCreate, db: Session = Depends(get_db)) -> FilamentRead:
    return _read(crud.create_filament(db, data))


@router.put("/filaments/{filament_id}", response_model=FilamentRead)
def replace_filament(filament_id: int, data: FilamentCreate, db: Session = Depends(get_db)) -> FilamentRead:
    obj = _get_or_404(db, filament_id)
    return _read(crud.replace_filament(db, obj, data))


@router.patch("/filaments/{filament_id}", response_model=FilamentRead)
def update_filament(filament_id: int, patch: FilamentUpdate, db: Session = Depends(get_db)) -> FilamentRead:
    obj = _get_or_404(db, filament_id)
    try:
        data = crud.merged_update(obj, patch)
    except ValidationError as exc:
        raise RequestValidationError(exc.errors(include_url=False)) from exc
    return _read(crud.replace_filament(db, obj, data))


@router.patch("/filaments/{filament_id}/weight", response_model=FilamentRead)
def update_weight(filament_id: int, body: WeightUpdate, db: Session = Depends(get_db)) -> FilamentRead:
    obj = _get_or_404(db, filament_id)
    try:
        data = crud.merged_update(obj, FilamentUpdate(weight_current=body.weight_current))
    except ValidationError as exc:
        raise RequestValidationError(exc.errors(include_url=False)) from exc
    return _read(crud.replace_filament(db, obj, data))


@router.delete("/filaments/{filament_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_filament(filament_id: int, db: Session = Depends(get_db)) -> Response:
    crud.delete_filament(db, _get_or_404(db, filament_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/stats", response_model=Stats)
def stats(db: Session = Depends(get_db)) -> Stats:
    return crud.compute_stats(crud.list_filaments(db))
