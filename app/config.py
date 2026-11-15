"""Applicatieconfiguratie, gelezen uit environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache


def _build_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if url:
        return url
    user = os.getenv("POSTGRES_USER", "filament")
    password = os.getenv("POSTGRES_PASSWORD", "filament")
    host = os.getenv("POSTGRES_HOST", "db")
    port = os.getenv("POSTGRES_PORT", "5432")
    name = os.getenv("POSTGRES_DB", "filament")
    # URL.create codeert speciale tekens (@ : / # % ?) in gebruikersnaam en wachtwoord correct
    from sqlalchemy.engine import URL

    return URL.create(
        "postgresql+psycopg",
        username=user,
        password=password,
        host=host,
        port=int(port),
        database=name,
    ).render_as_string(hide_password=False)


def _parse_materials(raw: str) -> list[str]:
    items = [m.strip().upper() for m in raw.split(",") if m.strip()]
    # Volgorde behouden, dubbelen verwijderen
    return list(dict.fromkeys(items)) or ["PLA"]


@dataclass(frozen=True)
class Settings:
    database_url: str = field(default_factory=_build_database_url)
    materials: list[str] = field(
        default_factory=lambda: _parse_materials(os.getenv("MATERIALS", "PLA,PETG,ABS,ASA"))
    )
    low_stock_threshold: float = field(
        default_factory=lambda: float(os.getenv("LOW_STOCK_THRESHOLD", "15"))
    )
    default_weight_new: int = field(
        default_factory=lambda: int(os.getenv("DEFAULT_WEIGHT_NEW", "1000"))
    )
    app_title: str = field(default_factory=lambda: os.getenv("APP_TITLE", "Filament Manager"))


@lru_cache
def get_settings() -> Settings:
    return Settings()
