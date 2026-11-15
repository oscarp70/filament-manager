#!/bin/sh
set -e

echo "Wachten op de database..."
python - <<'PY'
import sys, time
from sqlalchemy import create_engine, text
from app.config import get_settings

engine = create_engine(get_settings().database_url, pool_pre_ping=True)
for attempt in range(60):
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        print("Database bereikbaar.")
        break
    except Exception as exc:  # noqa: BLE001
        print(f"  nog niet bereikbaar ({attempt + 1}/60): {exc.__class__.__name__}")
        time.sleep(2)
else:
    sys.exit("Database niet bereikbaar, stoppen.")
PY

echo "Migraties uitvoeren..."
alembic upgrade head

if [ "${SEED_DEMO:-false}" = "true" ]; then
  python -m app.seed
fi

exec "$@"
