import os
import sys
from pathlib import Path

import pytest

# Testdatabase: TEST_DATABASE_URL (bv. PostgreSQL) of anders een tijdelijk SQLite-bestand
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["DATABASE_URL"] = os.getenv("TEST_DATABASE_URL", f"sqlite:///{ROOT / 'test.sqlite'}")
os.environ.setdefault("LOW_STOCK_THRESHOLD", "15")

from fastapi.testclient import TestClient  # noqa: E402

from app.database import Base, engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture()
def client():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with TestClient(app) as c:
        yield c
    Base.metadata.drop_all(engine)


@pytest.fixture()
def sample():
    return {
        "material": "PLA",
        "color_name": "Galaxy Black",
        "r": 27, "g": 27, "b": 34,
        "transparent": False,
        "price_per_kg": "22.99",
        "weight_new": 1000,
        "weight_current": 820,
    }
