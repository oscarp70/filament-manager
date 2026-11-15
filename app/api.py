"""REST API onder /api — bruikbaar voor integraties (bv. Home Assistant)."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import crud, transfer
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
    q: str | None = Query(None, description="Zoek in kleurnaam en RFID"),
    db: Session = Depends(get_db),
) -> list[FilamentRead]:
    items = crud.list_filaments(db, material=material, transparent=transparent, sort=sort, q=q)
    if low_stock is not None:
        items = [f for f in items if crud.is_low_stock(f) == low_stock]
    return [_read(f) for f in items]


def _check_rfid(db: Session, rfid: str | None, exclude_id: int | None = None) -> None:
    other = crud.find_by_rfid(db, rfid, exclude_id=exclude_id)
    if other is not None:
        raise HTTPException(status_code=409, detail=crud.rfid_conflict_message(other))


@router.get("/filaments/by-rfid/{rfid}", response_model=FilamentRead)
def get_by_rfid(rfid: str, db: Session = Depends(get_db)) -> FilamentRead:
    """Zoek een spoel op RFID-tag (hoofdletterongevoelig), bv. vanuit een scanner of Home Assistant."""
    obj = crud.find_by_rfid(db, rfid)
    if obj is None:
        raise HTTPException(status_code=404, detail="Geen spoel met deze RFID")
    return _read(obj)


@router.get("/filaments/{filament_id}", response_model=FilamentRead)
def get_filament(filament_id: int, db: Session = Depends(get_db)) -> FilamentRead:
    return _read(_get_or_404(db, filament_id))


@router.post("/filaments", response_model=FilamentRead, status_code=status.HTTP_201_CREATED)
def create_filament(data: FilamentCreate, db: Session = Depends(get_db)) -> FilamentRead:
    _check_rfid(db, data.rfid)
    return _read(crud.create_filament(db, data))


@router.put("/filaments/{filament_id}", response_model=FilamentRead)
def replace_filament(filament_id: int, data: FilamentCreate, db: Session = Depends(get_db)) -> FilamentRead:
    obj = _get_or_404(db, filament_id)
    _check_rfid(db, data.rfid, exclude_id=obj.id)
    return _read(crud.replace_filament(db, obj, data))


@router.patch("/filaments/{filament_id}", response_model=FilamentRead)
def update_filament(filament_id: int, patch: FilamentUpdate, db: Session = Depends(get_db)) -> FilamentRead:
    obj = _get_or_404(db, filament_id)
    try:
        data = crud.merged_update(obj, patch)
    except ValidationError as exc:
        raise RequestValidationError(exc.errors(include_url=False)) from exc
    _check_rfid(db, data.rfid, exclude_id=obj.id)
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


@router.get("/export", tags=["export/import"])
def export_all(db: Session = Depends(get_db)) -> Response:
    """Volledige export als JSON (zelfde formaat als op het instellingenscherm)."""
    return Response(transfer.export_json(db), media_type="application/json")


@router.post("/import", tags=["export/import"])
async def import_all(
    request: Request,
    mode: Literal["merge", "replace"] = "merge",
    dry_run: bool = False,
    db: Session = Depends(get_db),
) -> dict:
    """Importeer een JSON-export (body) of CSV. `mode=replace` wist eerst alle spoelen. `dry_run=true` controleert alleen."""
    try:
        plan = transfer.plan_import(db, await request.body(), mode)
    except transfer.ImportFormatError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    result = {"mode": mode, "new": plan.new_count, "updated": plan.update_count,
              "deleted": plan.to_delete, "errors": plan.errors, "applied": False}
    if plan.errors:
        raise HTTPException(status_code=422, detail=result)
    if not dry_run:
        try:
            transfer.apply_import(db, plan)
        except IntegrityError as exc:
            raise HTTPException(status_code=409, detail="Dubbele RFID; niets gewijzigd") from exc
        result["applied"] = True
    return result
