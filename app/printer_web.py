"""Webinterface en API voor de printerkoppeling."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from .bambu import usage as U
from .bambu.ftp import test_connection as test_ftp
from .bambu.service import STATUS_LABELS, service, test_mqtt
from .bambu.state import EXTERNAL_SLOT, slot_label
from .database import get_db
from .models import Filament, PrintJob, PrintUsage
from .web import localdt, templates

router = APIRouter()
templates.env.globals.update(slot_label=slot_label, JOB_STATUS=STATUS_LABELS, job_data=U.job_data)

SOURCE_LABELS = {
    "3mf": "uit printbestand",
    "ams_remain": "geschat via AMS-percentage",
    "manual": "handmatig",
    "none": "onbekend",
}
templates.env.globals["USAGE_SOURCE"] = SOURCE_LABELS


def _is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


def _spools(db: Session) -> list[Filament]:
    return list(db.scalars(select(Filament).order_by(Filament.material, func.lower(Filament.color_name))))


def _trays_ctx(db: Session) -> dict[str, Any]:
    trays = service.state.trays()
    assigned = U.assignments(db)
    spools = _spools(db)
    by_id = {f.id: f for f in spools}
    rows = []
    known_slots = {t.slot for t in trays}
    for t in trays:
        a = assigned.get(t.slot)
        f = by_id.get(a.filament_id) if a and a.filament_id else None
        warn = None
        if f is not None and t.present and t.material and f.material != t.material:
            warn = f"AMS meldt {t.material}, spoel is {f.material}"
        rows.append({"tray": t, "assignment": a, "filament": f, "warn": warn})
    # Toewijzingen voor sloten die (nog) niet door de printer zijn gemeld
    offline = [
        {"slot": s, "assignment": a, "filament": by_id.get(a.filament_id)}
        for s, a in sorted(assigned.items()) if s not in known_slots and a.filament_id
    ]
    status = service.status()
    status["last_message_local"] = localdt(service.last_message_at, "%H:%M:%S") if service.last_message_at else ""
    return {"trays": rows, "offline_assignments": offline, "spools": spools, "tray_now": service.state.tray_now,
            "status": status}


def _jobs(db: Session, limit: int = 50) -> list[PrintJob]:
    stmt = (
        select(PrintJob)
        .options(selectinload(PrintJob.usages).selectinload(PrintUsage.filament))
        .order_by(PrintJob.id.desc())
        .limit(limit)
    )
    return list(db.scalars(stmt))


def _job_ctx(db: Session, job: PrintJob, **extra: Any) -> dict[str, Any]:
    return {"job": job, "spools": _spools(db), "data": U.job_data(job), **extra}


# ------------------------------------------------------------------ pagina's

@router.get("/printer", response_class=HTMLResponse)
def printer_page(request: Request, msg: str | None = None, db: Session = Depends(get_db)) -> HTMLResponse:
    if service.state.trays():
        with service.lock:  # niet tegelijk met de MQTT-thread
            service.refresh_assignments()
    ctx = {
        **_trays_ctx(db),
        "jobs": _jobs(db),
        "config": U.get_settings(db),
        "msg": msg,
        "external_slot": EXTERNAL_SLOT,
    }
    return templates.TemplateResponse(request, "printer.html", ctx)


@router.get("/printer/live", response_class=HTMLResponse)
def printer_live(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    """Wordt elke paar seconden opgehaald door HTMX: status en AMS-sloten."""
    return templates.TemplateResponse(request, "partials/printer_live.html", _trays_ctx(db))


@router.post("/printer/slots/{slot}", response_class=HTMLResponse)
async def assign_slot(slot: int, request: Request, db: Session = Depends(get_db)) -> Response:
    form = await request.form()
    raw = (form.get("filament_id") or "").strip()
    fid = int(raw) if raw.isdigit() else None
    if fid is not None and db.get(Filament, fid) is None:
        raise HTTPException(404, "Spoel niet gevonden")
    U.assign_slot(db, slot, fid, "manual")
    if _is_htmx(request):
        return templates.TemplateResponse(request, "partials/printer_live.html", _trays_ctx(db))
    return RedirectResponse("/printer", status_code=303)


# ------------------------------------------------------------------ instellingen

@router.post("/printer/settings", response_class=HTMLResponse)
async def save_printer_settings(request: Request, db: Session = Depends(get_db)) -> Response:
    form = await request.form()
    current = U.get_settings(db)
    code = (form.get("bambu_access_code") or "").strip()
    values = {
        "bambu_enabled": "true" if form.get("bambu_enabled") else "false",
        "bambu_host": (form.get("bambu_host") or "").strip(),
        "bambu_serial": (form.get("bambu_serial") or "").strip().upper(),
        "bambu_access_code": code or current.get("bambu_access_code", ""),
        "bambu_auto_deduct": "true" if form.get("bambu_auto_deduct") else "false",
    }
    U.save_settings(db, values)
    service.reload()
    return RedirectResponse("/printer?msg=saved#instellingen", status_code=303)


@router.post("/printer/test", response_class=HTMLResponse)
async def test_printer(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    form = await request.form()
    saved = U.get_settings(db)
    host = (form.get("bambu_host") or saved["bambu_host"]).strip()
    serial = (form.get("bambu_serial") or saved["bambu_serial"]).strip().upper()
    code = (form.get("bambu_access_code") or saved["bambu_access_code"]).strip()
    results: list[tuple[str, bool, str]] = []
    if not (host and serial and code):
        results.append(("Gegevens", False, "Vul IP-adres, serienummer en toegangscode in"))
    else:
        ok, msg = test_mqtt(host, serial, code)
        results.append(("MQTT (status)", ok, msg))
        ok, msg = test_ftp(host, code)
        results.append(("FTPS (printbestanden)", ok, msg))
    return templates.TemplateResponse(request, "partials/printer_test.html", {"results": results})


# ------------------------------------------------------------------ verbruik beheren

def _usage_or_404(db: Session, usage_id: int) -> PrintUsage:
    u = db.get(PrintUsage, usage_id)
    if u is None:
        raise HTTPException(404, "Verbruiksregel niet gevonden")
    return u


def _job_response(request: Request, db: Session, job_id: int, error: str | None = None,
                  info: str | None = None) -> Response:
    if not _is_htmx(request):
        return RedirectResponse(f"/printer#job-{job_id}", status_code=303)
    db.expire_all()
    job = db.get(PrintJob, job_id)
    if job is None:
        return HTMLResponse("")
    return templates.TemplateResponse(request, "partials/print_job.html", _job_ctx(db, job, error=error, info=info))


@router.post("/printer/usages/{usage_id}/assign", response_class=HTMLResponse)
async def usage_assign(usage_id: int, request: Request, db: Session = Depends(get_db)) -> Response:
    u = _usage_or_404(db, usage_id)
    form = await request.form()
    raw = (form.get("filament_id") or "").strip()
    fid = int(raw) if raw.isdigit() else None
    U.reassign(db, u, fid, apply=True)
    return _job_response(request, db, u.job_id)


@router.post("/printer/usages/{usage_id}/undo", response_class=HTMLResponse)
def usage_undo(usage_id: int, request: Request, db: Session = Depends(get_db)) -> Response:
    u = _usage_or_404(db, usage_id)
    U.undo(db, u)
    return _job_response(request, db, u.job_id)


@router.post("/printer/usages/{usage_id}/deduct", response_class=HTMLResponse)
def usage_deduct(usage_id: int, request: Request, db: Session = Depends(get_db)) -> Response:
    u = _usage_or_404(db, usage_id)
    U.deduct(db, u)
    return _job_response(request, db, u.job_id)


@router.post("/printer/usages/{usage_id}/grams", response_class=HTMLResponse)
async def usage_grams(usage_id: int, request: Request, db: Session = Depends(get_db)) -> Response:
    u = _usage_or_404(db, usage_id)
    form = await request.form()
    try:
        grams = Decimal((form.get("grams") or "").replace(",", "."))
        if grams < 0 or grams > 100000:
            raise InvalidOperation
    except InvalidOperation:
        return _job_response(request, db, u.job_id, error="Vul een geldig aantal gram in")
    U.set_grams(db, u, grams)
    return _job_response(request, db, u.job_id)


@router.post("/printer/usages/{usage_id}/delete", response_class=HTMLResponse)
def usage_delete(usage_id: int, request: Request, db: Session = Depends(get_db)) -> Response:
    u = _usage_or_404(db, usage_id)
    job_id = u.job_id
    U.undo(db, u, commit=False)
    job = u.job
    db.delete(u)
    db.flush()
    job.total_g = sum((x.grams for x in job.usages if x.id != usage_id), Decimal(0))
    db.commit()
    return _job_response(request, db, job_id)


@router.post("/printer/jobs/{job_id}/usages", response_class=HTMLResponse)
async def job_add_usage(job_id: int, request: Request, db: Session = Depends(get_db)) -> Response:
    job = db.get(PrintJob, job_id)
    if job is None:
        raise HTTPException(404)
    form = await request.form()
    raw = (form.get("filament_id") or "").strip()
    fid = int(raw) if raw.isdigit() else None
    try:
        grams = Decimal((form.get("grams") or "").replace(",", "."))
        if grams <= 0 or grams > 100000:
            raise InvalidOperation
    except InvalidOperation:
        return _job_response(request, db, job_id, error="Vul een geldig aantal gram in")
    f = db.get(Filament, fid) if fid else None
    u = PrintUsage(filament_id=fid, grams=U.q2(grams), material=f.material if f else None,
                   color=f.hex.upper() if f else None)
    job.usages.append(u)
    if job.usage_source == "none":
        job.usage_source = "manual"
    job.note = None
    db.flush()
    job.total_g = sum((x.grams for x in job.usages), Decimal(0))
    if fid:
        U.deduct(db, u, commit=False)
    db.commit()
    return _job_response(request, db, job_id)


@router.post("/printer/jobs/{job_id}/undo-all", response_class=HTMLResponse)
def job_undo_all(job_id: int, request: Request, db: Session = Depends(get_db)) -> Response:
    job = db.get(PrintJob, job_id)
    if job is None:
        raise HTTPException(404)
    for u in job.usages:
        U.undo(db, u, commit=False)
    db.commit()
    return _job_response(request, db, job_id)


@router.post("/printer/jobs/{job_id}/deduct-all", response_class=HTMLResponse)
def job_deduct_all(job_id: int, request: Request, db: Session = Depends(get_db)) -> Response:
    job = db.get(PrintJob, job_id)
    if job is None:
        raise HTTPException(404)
    for u in job.usages:
        U.deduct(db, u, commit=False)
    db.commit()
    return _job_response(request, db, job_id)


@router.post("/printer/jobs/{job_id}/recompute", response_class=HTMLResponse)
def job_recompute(job_id: int, request: Request, db: Session = Depends(get_db)) -> Response:
    ok, msg = service.recompute(job_id)
    return _job_response(request, db, job_id, error=None if ok else msg, info=msg if ok else None)


@router.post("/printer/jobs/{job_id}/delete", response_class=HTMLResponse)
def job_delete(job_id: int, request: Request, db: Session = Depends(get_db)) -> Response:
    """Verwijdert de print uit de geschiedenis. Afgeschreven gewicht blijft afgeschreven."""
    job = db.get(PrintJob, job_id)
    if job is None:
        raise HTTPException(404)
    if job.status == "running":
        return _job_response(request, db, job_id, error="Een lopende print kan niet worden verwijderd")
    db.delete(job)
    db.commit()
    if _is_htmx(request):
        return HTMLResponse("")
    return RedirectResponse("/printer", status_code=303)


# ------------------------------------------------------------------ API

@router.get("/api/printer/status", tags=["printer"])
def api_status(db: Session = Depends(get_db)) -> dict[str, Any]:
    assigned = U.assignments(db)
    return {
        **service.status(),
        "trays": [
            {
                "slot": t.slot, "label": t.label, "present": t.present, "material": t.material, "color": t.color,
                "remain": t.remain, "tag_uid": t.tag_uid,
                "filament_id": assigned[t.slot].filament_id if t.slot in assigned else None,
            }
            for t in service.state.trays()
        ],
    }


@router.get("/api/printer/jobs", tags=["printer"])
def api_jobs(limit: int = 50, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return [
        {
            "id": j.id, "name": j.name, "status": j.status, "progress": j.progress, "plate": j.plate,
            "usage_source": j.usage_source, "total_g": float(j.total_g or 0),
            "started_at": j.started_at, "ended_at": j.ended_at,
            "usages": [
                {"id": u.id, "filament_id": u.filament_id, "slot": u.slot, "material": u.material,
                 "color": u.color, "grams": float(u.grams), "deducted_g": float(u.deducted_g or 0),
                 "deducted": u.deducted_at is not None}
                for u in j.usages
            ],
        }
        for j in _jobs(db, limit=max(1, min(limit, 500)))
    ]
