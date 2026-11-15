from __future__ import annotations

from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.orm import Session

from . import api, web
from .config import get_settings
from .database import get_db

app = FastAPI(
    title=get_settings().app_title,
    description="Beheer je 3D-printfilament: voorraad, kleuren en waarde.",
    version="1.0.0",
)

app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")
app.include_router(api.router)
app.include_router(web.router)


@app.get("/health", tags=["system"])
def health(db: Session = Depends(get_db)) -> JSONResponse:
    try:
        db.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - alleen bij databaseproblemen
        return JSONResponse({"status": "error", "detail": str(exc)}, status_code=503)
    return JSONResponse({"status": "ok"})
