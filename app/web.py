"""Webinterface (Jinja2 + HTMX)."""

from __future__ import annotations

import os
import re
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlalchemy.orm import Session

from . import crud, transfer
from .config import get_settings
from .database import get_db
from .models import Filament
from .schemas import FilamentCreate, dutch_errors
from .spool import spool_svg

router = APIRouter(include_in_schema=False)
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


# --- Jinja-filters (Nederlandse notatie) ---

def _fmt_number(value: float | Decimal | int, decimals: int = 0) -> str:
    s = f"{float(value):,.{decimals}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def eur(value: Decimal | float) -> str:
    return f"€ {_fmt_number(value, 2)}"


def grams(value: int) -> str:
    return f"{_fmt_number(value)} g"


def kg(value: int) -> str:
    return f"{_fmt_number(value / 1000, 2)} kg"


def pct(value: float) -> str:
    return f"{_fmt_number(value, 0)}%"


_TZ = ZoneInfo(os.getenv("TZ") or "Europe/Amsterdam")


def localdt(value: datetime | None, fmt: str = "%d-%m-%Y %H:%M") -> str:
    if value is None:
        return ""
    if value.tzinfo is None:
        from datetime import timezone

        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(_TZ).strftime(fmt)


templates.env.filters.update(eur=eur, grams=grams, kg=kg, pct=pct, localdt=localdt)
templates.env.globals.update(spool_svg=spool_svg, settings=get_settings(), is_low_stock=crud.is_low_stock)


# --- Helpers ---

def _is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


def _parse_bool(value: str | None) -> bool | None:
    if value in (None, "", "all"):
        return None
    return value.lower() in ("1", "true", "yes", "ja", "on")


HEX_RE = re.compile(r"^#?([0-9a-fA-F]{6})$")


def _form_to_raw(form: Any) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "material": (form.get("material") or "").strip(),
        "color_name": (form.get("color_name") or "").strip(),
        "transparent": form.get("transparent") in ("on", "true", "1"),
        "price_per_kg": (form.get("price_per_kg") or "").strip().replace(",", "."),
        "weight_new": (form.get("weight_new") or "").strip(),
        "weight_current": (form.get("weight_current") or "").strip(),
        "rfid": (form.get("rfid") or "").strip(),
    }
    for ch in ("r", "g", "b"):
        raw[ch] = (form.get(ch) or "").strip()
    # Zonder JavaScript komt alleen de hexwaarde van de color picker mee
    if not all(raw[ch] for ch in ("r", "g", "b")):
        m = HEX_RE.match((form.get("hex") or "").strip())
        if m:
            h = m.group(1)
            raw["r"], raw["g"], raw["b"] = (int(h[i : i + 2], 16) for i in (0, 2, 4))
    return raw


def _form_defaults(f: Filament | None = None) -> dict[str, Any]:
    settings = get_settings()
    if f is None:
        return {
            "material": settings.materials[0],
            "color_name": "",
            "r": 230, "g": 57, "b": 70,
            "transparent": False,
            "price_per_kg": "",
            "weight_new": settings.default_weight_new,
            "weight_current": settings.default_weight_new,
            "rfid": "",
        }
    return {
        "material": f.material,
        "color_name": f.color_name,
        "r": f.r, "g": f.g, "b": f.b,
        "transparent": f.transparent,
        "price_per_kg": f"{f.price_per_kg:.2f}",
        "weight_new": f.weight_new,
        "weight_current": f.weight_current,
        "rfid": f.rfid or "",
    }


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(str(value).replace(",", ".")))
    except (TypeError, ValueError):
        return default


def _hex_of(values: dict[str, Any]) -> str:
    r, g, b = (max(0, min(255, _safe_int(values.get(c)))) for c in ("r", "g", "b"))
    return f"#{r:02x}{g:02x}{b:02x}"


def _get_or_404(db: Session, filament_id: int) -> Filament:
    obj = crud.get_filament(db, filament_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="Filament niet gevonden")
    return obj


def _stats_ctx(db: Session) -> dict[str, Any]:
    return {"stats": crud.compute_stats(crud.list_filaments(db))}


# --- Routes ---

def _apply_filters(
    db: Session, material: str | None, transparent: str | None, sort: str, q: str | None
) -> tuple[list[Filament], dict[str, str]]:
    settings = get_settings()
    material = material.upper() if material and material.upper() in settings.materials else None
    if sort not in crud.SORT_OPTIONS:
        sort = "date"
    transparent_filter = _parse_bool(transparent)
    q = (q or "").strip()
    items = crud.list_filaments(db, material=material, transparent=transparent_filter, sort=sort, q=q or None)
    filters = {
        "material": material or "",
        "transparent": "" if transparent_filter is None else ("true" if transparent_filter else "false"),
        "sort": sort,
        "q": q,
    }
    return items, filters


@router.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    material: str | None = None,
    transparent: str | None = None,
    sort: str = "date",
    q: str | None = None,
    saved: int | None = None,
    db: Session = Depends(get_db),
) -> Response:
    items, filters = _apply_filters(db, material, transparent, sort, q)
    # Gescande RFID die exact één spoel oplevert: die spoel markeren
    if filters["q"] and saved is None:
        exact = crud.find_by_rfid(db, filters["q"])
        if exact is not None and exact in items:
            saved = exact.id
    ctx = {
        "request": request,
        "items": items,
        "filters": filters,
        "sort_options": crud.SORT_OPTIONS,
        "saved": saved,
        **_stats_ctx(db),
    }
    if _is_htmx(request) and request.headers.get("HX-Target") == "inventory":
        return templates.TemplateResponse(request, "partials/inventory.html", ctx)
    return templates.TemplateResponse(request, "dashboard.html", ctx)


@router.get("/preview", response_class=HTMLResponse)
def preview(request: Request) -> HTMLResponse:
    q = request.query_params
    values = {c: q.get(c) for c in ("r", "g", "b")}
    if not all(values.values()):
        m = HEX_RE.match(q.get("hex", ""))
        if m:
            h = m.group(1)
            values = {c: int(h[i : i + 2], 16) for c, i in zip("rgb", (0, 2, 4))}
    r, g, b = (max(0, min(255, _safe_int(values.get(c)))) for c in "rgb")
    weight_new = _safe_int(q.get("weight_new"), 1000) or 1000
    weight_current = max(0, min(weight_new, _safe_int(q.get("weight_current"), weight_new)))
    percent = weight_current / weight_new * 100
    svg = spool_svg(r, g, b, q.get("transparent") in ("on", "true", "1"), percent, uid="preview",
                    title="Voorbeeld", css_class="spool spool-lg")
    return HTMLResponse(f'{svg}<div class="preview-pct">{pct(percent)} resterend</div>')


@router.get("/filaments/new", response_class=HTMLResponse)
def new_form(request: Request) -> HTMLResponse:
    values = _form_defaults()
    return templates.TemplateResponse(
        request, "form.html", {"values": values, "hex": _hex_of(values), "errors": {}, "filament": None}
    )


@router.post("/filaments/new", response_class=HTMLResponse)
async def create(request: Request, db: Session = Depends(get_db)) -> Response:
    raw = _form_to_raw(await request.form())
    try:
        data = FilamentCreate(**raw)
    except ValidationError as exc:
        return templates.TemplateResponse(
            request, "form.html",
            {"values": raw, "hex": _hex_of(raw), "errors": dutch_errors(exc), "filament": None},
            status_code=422,
        )
    other = crud.find_by_rfid(db, data.rfid)
    if other is not None:
        return templates.TemplateResponse(
            request, "form.html",
            {"values": raw, "hex": _hex_of(raw), "errors": {"rfid": crud.rfid_conflict_message(other)},
             "filament": None},
            status_code=409,
        )
    obj = crud.create_filament(db, data)
    return RedirectResponse(f"/?saved={obj.id}", status_code=303)


@router.get("/filaments/{filament_id}/edit", response_class=HTMLResponse)
def edit_form(filament_id: int, request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    obj = _get_or_404(db, filament_id)
    values = _form_defaults(obj)
    return templates.TemplateResponse(
        request, "form.html", {"values": values, "hex": obj.hex, "errors": {}, "filament": obj}
    )


@router.post("/filaments/{filament_id}/edit", response_class=HTMLResponse)
async def update(filament_id: int, request: Request, db: Session = Depends(get_db)) -> Response:
    obj = _get_or_404(db, filament_id)
    raw = _form_to_raw(await request.form())
    try:
        data = FilamentCreate(**raw)
    except ValidationError as exc:
        return templates.TemplateResponse(
            request, "form.html",
            {"values": raw, "hex": _hex_of(raw), "errors": dutch_errors(exc), "filament": obj},
            status_code=422,
        )
    other = crud.find_by_rfid(db, data.rfid, exclude_id=obj.id)
    if other is not None:
        return templates.TemplateResponse(
            request, "form.html",
            {"values": raw, "hex": _hex_of(raw), "errors": {"rfid": crud.rfid_conflict_message(other)},
             "filament": obj},
            status_code=409,
        )
    crud.replace_filament(db, obj, data)
    return RedirectResponse(f"/?saved={obj.id}", status_code=303)


@router.post("/filaments/{filament_id}/weight", response_class=HTMLResponse)
async def quick_weight(filament_id: int, request: Request, db: Session = Depends(get_db)) -> Response:
    obj = _get_or_404(db, filament_id)
    form = await request.form()
    raw_value = (form.get("weight_current") or "").strip().replace(",", ".")
    error = None
    try:
        value = int(float(raw_value))
        if value < 0:
            error = "Gewicht kan niet negatief zijn"
        elif value > obj.weight_new:
            error = f"Maximaal {grams(obj.weight_new)} (gewicht nieuw)"
    except ValueError:
        error = "Vul een geldig gewicht in"

    if error is None:
        obj.weight_current = value
        db.commit()
        db.refresh(obj)

    if not _is_htmx(request):
        return RedirectResponse(f"/?saved={obj.id}", status_code=303)
    ctx = {"f": obj, "weight_error": error, "highlight": error is None, "oob_stats": True, **_stats_ctx(db)}
    return templates.TemplateResponse(request, "partials/card_update.html", ctx)


@router.delete("/filaments/{filament_id}", response_class=HTMLResponse)
def delete_htmx(filament_id: int, request: Request, db: Session = Depends(get_db)) -> Response:
    crud.delete_filament(db, _get_or_404(db, filament_id))
    return templates.TemplateResponse(request, "partials/stats.html", {"oob": True, **_stats_ctx(db)})


@router.post("/filaments/{filament_id}/delete")
def delete_form(filament_id: int, db: Session = Depends(get_db)) -> Response:
    crud.delete_filament(db, _get_or_404(db, filament_id))
    return RedirectResponse("/", status_code=303)


# --- Afdrukbaar overzicht ---

@router.get("/inventory/print", response_class=HTMLResponse)
def inventory_print(
    request: Request,
    material: str | None = None,
    transparent: str | None = None,
    sort: str = "color",
    q: str | None = None,
    group: str | None = None,
    count: str | None = None,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    from datetime import timezone
    from urllib.parse import urlencode

    items, filters = _apply_filters(db, material, transparent, sort, q)
    # Standaard gegroepeerd; bij een submit van het optieformulier telt de checkbox
    submitted = request.query_params.get("opts") == "1"
    do_group = (group == "1") if submitted else True
    count_col = count == "1"

    settings = get_settings()
    if do_group:
        order = {m: i for i, m in enumerate(settings.materials)}
        groups_map: dict[str, list[Filament]] = {}
        for f in items:
            groups_map.setdefault(f.material, []).append(f)
        groups = sorted(groups_map.items(), key=lambda kv: (order.get(kv[0], 999), kv[0]))
    else:
        groups = [("", items)]

    desc = []
    if filters["material"]:
        desc.append(filters["material"])
    if filters["transparent"] == "true":
        desc.append("transparant")
    elif filters["transparent"] == "false":
        desc.append("niet transparant")
    if filters["q"]:
        desc.append(f'zoekterm "{filters["q"]}"')
    back = {k: v for k, v in filters.items() if v and not (k == "sort" and v == "date")}

    return templates.TemplateResponse(
        request,
        "print.html",
        {
            "items": items,
            "groups": groups,
            "group": do_group,
            "count_col": count_col,
            "filters": filters,
            "filter_desc": ", ".join(desc),
            "back_query": urlencode(back),
            "totals": crud.compute_stats(items),
            "now": datetime.now(timezone.utc),
        },
    )


# --- Instellingen: export en import ---

def _settings_ctx(db: Session, **extra: Any) -> dict[str, Any]:
    from sqlalchemy import text as sql_text

    try:
        revision = db.execute(sql_text("SELECT version_num FROM alembic_version")).scalar()
    except Exception:  # tabel bestaat niet (bv. in tests)
        db.rollback()
        revision = None
    url = make_url_safe(get_settings().database_url)
    return {"count": len(crud.list_filaments(db)), "revision": revision, "db_url": url, **extra}


def make_url_safe(url: str) -> str:
    from sqlalchemy.engine import make_url

    try:
        return make_url(url).render_as_string(hide_password=True)
    except Exception:
        return "?"


def _export_filename(ext: str) -> str:
    return f"filament-export-{datetime.now(_TZ).strftime('%Y%m%d-%H%M')}.{ext}"


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, imported: int | None = None, mode: str | None = None,
                  db: Session = Depends(get_db)) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "settings.html", _settings_ctx(db, imported=imported, imported_mode=mode)
    )


@router.get("/settings/export.json")
def export_json_download(db: Session = Depends(get_db)) -> Response:
    return Response(
        transfer.export_json(db),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{_export_filename("json")}"'},
    )


@router.get("/settings/export.csv")
def export_csv_download(db: Session = Depends(get_db)) -> Response:
    return Response(
        transfer.export_csv(db).encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{_export_filename("csv")}"'},
    )


@router.post("/settings/import", response_class=HTMLResponse)
async def import_preview(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    form = await request.form()
    mode = form.get("mode") or "merge"
    upload = form.get("file")
    error = None
    plan = None
    filename = getattr(upload, "filename", "") or ""
    if upload is None or not filename:
        error = "Kies eerst een exportbestand (.json of .csv)"
    else:
        content = await upload.read()
        try:
            plan = transfer.plan_import(db, content, mode)
        except transfer.ImportFormatError as exc:
            error = str(exc)
    return templates.TemplateResponse(
        request, "settings.html",
        _settings_ctx(db, plan=plan, import_error=error, mode=mode, filename=filename,
                      payload=transfer.plan_to_json(plan) if plan and plan.ok else None),
        status_code=200 if plan and plan.ok else 422,
    )


@router.post("/settings/import/apply", response_class=HTMLResponse)
async def import_apply(request: Request, db: Session = Depends(get_db)) -> Response:
    from sqlalchemy.exc import IntegrityError

    form = await request.form()
    mode = form.get("mode") or "merge"
    payload = (form.get("payload") or "").encode("utf-8")
    error = None
    plan = None
    try:
        # Opnieuw plannen tegen de huidige database (die kan intussen gewijzigd zijn)
        plan = transfer.plan_import(db, payload, mode)
        transfer.apply_import(db, plan)
    except transfer.ImportFormatError as exc:
        error = str(exc)
    except IntegrityError:
        db.rollback()
        error = "De import botst met bestaande gegevens (dubbele RFID). Er is niets gewijzigd."
    if error:
        return templates.TemplateResponse(
            request, "settings.html", _settings_ctx(db, plan=plan, import_error=error, mode=mode),
            status_code=422,
        )
    return RedirectResponse(f"/settings?imported={len(plan.rows)}&mode={mode}", status_code=303)
