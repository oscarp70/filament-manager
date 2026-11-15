# Filament Manager

Webapplicatie voor het beheren van 3D-printfilament. Per spoel leg je het materiaal, de kleur, de prijs en het gewicht vast. Het dashboard toont je voorraad als spoelen in de echte kleur: hoe voller de spoel, hoe dikker de wikkeling.

![Dashboard](docs/dashboard-licht.png)

## Functies

- **Dashboard** met een kaart per spoel en een SVG-spoel in de opgeslagen RGB-kleur. De wikkeldikte schaalt mee met het percentage resterend, en transparant filament krijgt een doorschijnend, gestreept effect met glans.
- **Totalen**: aantal spoelen, totaal gewicht, voorraadwaarde, aantal bijna lege spoelen en een uitsplitsing per materiaal.
- **Filteren** op materiaal en transparantie, en **sorteren** op kleur (tint), resterend gewicht, datum of naam.
- **"Bijna op"-markering** onder een instelbare drempel (standaard 15%).
- **Toevoegen en bewerken** met een color picker, hex-veld en R/G/B-velden die onderling synchroon blijven, een palet met veelgebruikte kleuren en een **live voorbeeld** van de spoel.
- **RFID** per spoel (optioneel, uniek). Het zoekveld op het dashboard werkt ook met een USB-RFID-lezer die als toetsenbord werkt: scan de tag en de juiste spoel wordt gemarkeerd. Opzoeken kan ook via de API: `/api/filaments/by-rfid/{rfid}`.
- **Snel gewicht bijwerken** vanaf de kaart, bijvoorbeeld na een print, zonder het volledige formulier.
- **Voorraadoverzicht afdrukken of opslaan als PDF** via de knop *Afdrukken / PDF*: een A4-overzicht met kleurstalen, RFID, gewichten en waarde. Het volgt de actieve filters, kan per materiaal groeperen en heeft optioneel een kolom om af te vinken bij een telling.
- **Instellingenscherm** (⚙︎) met **export** (JSON als volledige back-up, CSV voor Excel) en **import** (*samenvoegen* of *vervangen*). Je ziet eerst een controle-overzicht, en de import gebeurt in één keer: alles of niets.
- **Verwijderen** met een bevestigingsdialoog.
- **REST API** onder `/api` met interactieve documentatie op `/docs`.
- Responsive voor telefoon, met een **licht en donker thema** (volgt het systeem en is te wisselen met de knop rechtsboven).

Voorbeeld van een afgedrukt overzicht: [docs/voorbeeld-voorraadoverzicht.pdf](docs/voorbeeld-voorraadoverzicht.pdf)

| Formulier (donker thema) | Mobiel |
|---|---|
| ![Formulier](docs/formulier.png) | ![Mobiel](docs/mobiel.png) |

## Techniek

| Onderdeel | Keuze |
|---|---|
| Backend | Python 3.12, FastAPI, SQLAlchemy 2, Alembic |
| Database | PostgreSQL 16 (eigen container, named volume `filament_db`) |
| Frontend | Server-rendered Jinja2 met HTMX. Geen build-stap, htmx wordt lokaal meegeleverd en er is geen CDN nodig |
| Draaien | Docker Compose: services `app` en `db` |

Opgeslagen per spoel: `material`, `color_name`, `r`, `g`, `b` (0–255), `transparent`, `price_per_kg` (2 decimalen), `weight_new` en `weight_current` (gram), `rfid` (optioneel, uniek), `created_at` en `updated_at`.
Berekend en niet opgeslagen: `hex`, `percent_remaining` (= gewicht nu / gewicht nieuw) en `remaining_value` (= gewicht nu / 1000 × prijs per kilo).

## Installatie

Je hebt Docker met de Compose-plugin nodig.

```bash
git clone https://github.com/oscarp70/filament-manager.git
cd filament-manager
cp .env.example .env
# Zet in .env in elk geval een eigen POSTGRES_PASSWORD
docker compose up -d
```

Open daarna **http://localhost:8080**, of de poort die je in `APP_PORT` hebt gezet.

Bij elke start wacht de app tot de database klaar is en voert dan automatisch de databasemigraties uit (`alembic upgrade head`).

### Voorbeelddata

Zet `SEED_DEMO=true` in `.env` voor de eerste start, of voeg de voorbeeldspoelen later toe:

```bash
docker compose exec app python -m app.seed          # alleen als de database leeg is
docker compose exec app python -m app.seed --force  # altijd toevoegen
```

### Bijwerken

```bash
# 1. Eerst een back-up (zie hieronder, of via Instellingen → Export JSON)
# 2. Nieuwe versie ophalen en opnieuw bouwen
git pull
docker compose up -d --build
```

Bij het starten voert de app automatisch de nieuwe databasemigraties uit. Bestaande spoelen blijven behouden. Zo voegt migratie `0002_rfid` alleen een lege, optionele kolom `rfid` toe. Welke schemaversie actief is, zie je onder **Instellingen → Configuratie**.

## Configuratie (`.env`)

| Variabele | Standaard | Betekenis |
|---|---|---|
| `POSTGRES_USER` | `filament` | Databasegebruiker |
| `POSTGRES_PASSWORD` | *(verplicht)* | Databasewachtwoord |
| `POSTGRES_DB` | `filament` | Databasenaam |
| `APP_PORT` | `8080` | Poort op de host |
| `APP_TITLE` | `Filament Manager` | Titel in de interface |
| `MATERIALS` | `PLA,PETG,ABS,ASA` | Beschikbare materialen, kommagescheiden. Voeg er bv. `TPU,PA,PC` aan toe |
| `LOW_STOCK_THRESHOLD` | `15` | Drempel in % voor de "bijna op"-markering |
| `DEFAULT_WEIGHT_NEW` | `1000` | Standaardgewicht van een nieuwe spoel in het formulier |
| `SEED_DEMO` | `false` | Voorbeeldspoelen toevoegen bij de start (alleen als de database leeg is) |
| `TZ` | `Europe/Amsterdam` | Tijdzone voor de weergave van datums |

Na een wijziging: `docker compose up -d`.

> Materiaal **verwijderen** uit `MATERIALS` terwijl er nog spoelen van dat materiaal zijn? Die spoelen blijven gewoon staan en tellen mee in de totalen. Je kunt er alleen niet meer op filteren, en bij het bewerken moet je een ander materiaal kiezen.

## Export en import (instellingenscherm)

![Instellingen](docs/instellingen.png)

Ga naar **⚙︎ Instellingen**:

- **Export JSON** is een volledige back-up van alle spoelen, inclusief aanmaakdatum. Dit is het aanbevolen formaat om terug te zetten.
- **Export CSV** gebruikt puntkomma en decimale komma en opent direct in Excel. Ook dit formaat kun je weer importeren.
- **Import** accepteert `.json` en `.csv`:
  - *Samenvoegen*: bestaande spoelen worden bijgewerkt. Een spoel wordt herkend aan de RFID, of aan het id wanneer kleurnaam en materiaal overeenkomen. De rest wordt toegevoegd en er wordt niets verwijderd. Importeer je je eigen export opnieuw, dan ontstaan er dus geen dubbele spoelen.
  - *Vervangen*: alle huidige spoelen worden verwijderd en vervangen door de inhoud van het bestand.
  - Je krijgt altijd eerst een controle met het aantal nieuwe, bij te werken en te verwijderen spoelen en eventuele fouten. Bevat het bestand een fout, dan wordt er niets gewijzigd.

Via de API: `GET /api/export` en `POST /api/import?mode=merge|replace&dry_run=true` (body is de JSON- of CSV-inhoud).

## Back-up en herstel (database)

### Back-up maken

```bash
mkdir -p backups
docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists' \
  > backups/filament-$(date +%F).sql
```

Automatisch elke nacht om 03:00 via cron. Pas het pad aan naar je eigen map:

```cron
0 3 * * * cd /opt/filament-manager && docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists' > backups/filament-$(date +\%F).sql
```

### Back-up terugzetten

```bash
docker compose stop app
docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < backups/filament-2026-11-15.sql
docker compose start app
```

Door `--clean --if-exists` overschrijft de dump de bestaande tabellen.

### Alles wissen (let op!)

```bash
docker compose down -v   # verwijdert ook het volume met alle data
```

## REST API

Je vindt de volledige, interactieve documentatie op **`/docs`** (Swagger) en **`/redoc`**.

| Methode | Pad | Omschrijving |
|---|---|---|
| `GET` | `/api/filaments` | Lijst. Query: `material`, `transparent`, `low_stock`, `q` (zoeken in kleurnaam/RFID), `sort` (`date`, `date_asc`, `color`, `weight`, `weight_asc`, `name`) |
| `GET` | `/api/filaments/{id}` | Eén spoel |
| `GET` | `/api/filaments/by-rfid/{rfid}` | Spoel opzoeken op RFID (hoofdletterongevoelig) |
| `POST` | `/api/filaments` | Nieuwe spoel |
| `PUT` | `/api/filaments/{id}` | Spoel volledig vervangen |
| `PATCH` | `/api/filaments/{id}` | Spoel gedeeltelijk bijwerken |
| `PATCH` | `/api/filaments/{id}/weight` | Alleen `weight_current` bijwerken |
| `DELETE` | `/api/filaments/{id}` | Spoel verwijderen |
| `GET` | `/api/stats` | Totalen en uitsplitsing per materiaal |
| `GET` | `/api/materials` | Beschikbare materialen |
| `GET` | `/api/export` | Volledige export (JSON) |
| `POST` | `/api/import` | Import. Query: `mode=merge\|replace`, `dry_run=true\|false` |
| `GET` | `/health` | Healthcheck (controleert ook de database) |

Voorbeeld:

```bash
curl -X POST http://localhost:8080/api/filaments \
  -H 'Content-Type: application/json' \
  -d '{"material":"PETG","color_name":"Clear Blue","r":64,"g":156,"b":255,
       "transparent":true,"price_per_kg":24.50,"weight_new":1000,"weight_current":1000}'

curl -X PATCH http://localhost:8080/api/filaments/1/weight \
  -H 'Content-Type: application/json' -d '{"weight_current": 640}'
```

### Home Assistant

Met een REST-sensor toon je bijvoorbeeld de voorraadwaarde en het aantal bijna lege spoelen:

```yaml
rest:
  - resource: http://filament-host:8080/api/stats
    scan_interval: 300
    sensor:
      - name: Filament voorraadwaarde
        value_template: "{{ value_json.value | float }}"
        unit_of_measurement: "€"
      - name: Filament voorraad
        value_template: "{{ (value_json.weight_g / 1000) | round(2) }}"
        unit_of_measurement: "kg"
      - name: Filament bijna op
        value_template: "{{ value_json.low_stock_count }}"
```

## Ontwikkelen

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt pytest httpx

# Tests: standaard tegen een tijdelijke SQLite-database
pytest
# of tegen PostgreSQL
TEST_DATABASE_URL=postgresql+psycopg://user:pass@localhost/filament_test pytest

# Lokaal draaien tegen een database naar keuze
export DATABASE_URL=postgresql+psycopg://user:pass@localhost/filament
alembic upgrade head
uvicorn app.main:app --reload --port 8080
```

Een nieuwe databasemigratie maak je na een wijziging in `app/models.py` met:

```bash
alembic revision --autogenerate -m "omschrijving"
```

### Projectstructuur

```
app/
  main.py          FastAPI-app, routers, /health
  api.py           REST API (/api)
  web.py           Webinterface (HTMX-routes, formulieren)
  crud.py          Databaselogica, sortering en statistieken
  models.py        SQLAlchemy-model Filament
  schemas.py       Pydantic-schema's, validatie, Nederlandse foutmeldingen
  spool.py         SVG-generator voor de spoel
  transfer.py      Export en import (JSON/CSV)
  seed.py          Voorbeelddata
  templates/       Jinja2-templates
  static/          CSS, JavaScript, htmx
alembic/           Databasemigraties
scripts/entrypoint.sh
tests/
Dockerfile
docker-compose.yml
.env.example
```
