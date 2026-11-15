import json


def _add(client, sample, **kw):
    r = client.post("/api/filaments", json={**sample, **kw})
    assert r.status_code == 201, r.text
    return r.json()


# ---------- RFID ----------

def test_rfid_api(client, sample):
    f = _add(client, sample, rfid="  04:a3:1f:b2  ")
    assert f["rfid"] == "04:a3:1f:b2"
    assert client.get("/api/filaments/by-rfid/04:A3:1F:B2").json()["id"] == f["id"]
    assert client.get("/api/filaments/by-rfid/onbekend").status_code == 404
    # dubbel (hoofdletterongevoelig) -> 409
    r = client.post("/api/filaments", json={**sample, "rfid": "04:A3:1F:B2"})
    assert r.status_code == 409 and "#%d" % f["id"] in r.json()["detail"]
    # leeg -> null; meerdere spoelen zonder RFID mogen
    assert _add(client, sample, rfid="")["rfid"] is None
    assert _add(client, sample)["rfid"] is None
    # PATCH naar bestaande RFID van andere spoel -> 409
    g = _add(client, sample, rfid="X1")
    assert client.patch(f"/api/filaments/{g['id']}", json={"rfid": "04:a3:1f:b2"}).status_code == 409
    assert client.patch(f"/api/filaments/{g['id']}", json={"rfid": None}).json()["rfid"] is None
    # zoeken
    assert [x["id"] for x in client.get("/api/filaments?q=a3:1f").json()] == [f["id"]]


def test_rfid_web_form(client):
    form = {"material": "PLA", "color_name": "Rood", "r": "200", "g": "0", "b": "0",
            "price_per_kg": "20", "weight_new": "1000", "weight_current": "1000", "rfid": "TAG-1"}
    assert client.post("/filaments/new", data=form, follow_redirects=False).status_code == 303
    r = client.post("/filaments/new", data={**form, "color_name": "Blauw"})
    assert r.status_code == 409 and "al gekoppeld aan spoel" in r.text
    fid = client.get("/api/filaments").json()[0]["id"]
    # Eigen RFID behouden bij bewerken mag
    assert client.post(f"/filaments/{fid}/edit", data=form, follow_redirects=False).status_code == 303
    page = client.get("/")
    assert "TAG-1" in page.text
    # Scan = zoekterm; exacte match wordt gemarkeerd
    r = client.get("/?q=tag-1")
    assert "card-highlight" in r.text


# ---------- Afdrukken ----------

def test_print_page(client, sample):
    _add(client, sample, rfid="R-1", weight_current=50)
    _add(client, sample, material="PETG", color_name="Clear", transparent=True)
    r = client.get("/inventory/print")
    assert r.status_code == 200
    t = r.text
    assert "Voorraadoverzicht filament" in t and "window.print()" in t
    assert "<h2>" in t and "PETG" in t and "R-1" in t and "bijna op" in t
    r = client.get("/inventory/print?opts=1&material=PLA&count=1")
    assert "Clear" not in r.text and "Geteld" in r.text and "<h2>" not in r.text and "Filter: PLA" in r.text


# ---------- Export / import ----------

def test_export_import_roundtrip(client, sample):
    a = _add(client, sample, rfid="AAA")
    _add(client, sample, color_name="Wit", r=250, g=250, b=250, transparent=True, price_per_kg="19.95")
    export = client.get("/settings/export.json")
    assert export.status_code == 200 and "attachment" in export.headers["content-disposition"]
    data = export.json()
    assert data["count"] == 2 and data["format_version"] == 1

    # Samenvoegen van eigen export: niets dubbel, alleen bijwerken
    r = client.post("/api/import?mode=merge&dry_run=true", content=export.content)
    assert r.json() == {"mode": "merge", "new": 0, "updated": 2, "deleted": 0, "errors": [], "applied": False}

    # Wijzig een spoel in het bestand en voeg er één toe
    data["filaments"][0]["weight_current"] = 111
    data["filaments"].append({**data["filaments"][1], "id": None, "color_name": "Nieuw", "rfid": "BBB"})
    r = client.post("/api/import?mode=merge", content=json.dumps(data))
    assert r.json()["new"] == 1 and r.json()["updated"] == 2 and r.json()["applied"] is True
    assert client.get(f"/api/filaments/{a['id']}").json()["weight_current"] == 111
    assert len(client.get("/api/filaments").json()) == 3

    # Vervangen
    r = client.post("/api/import?mode=replace", content=export.content)
    assert r.json()["deleted"] == 3 and r.json()["applied"]
    items = client.get("/api/filaments").json()
    assert len(items) == 2 and {i["rfid"] for i in items} == {"AAA", None}
    # created_at blijft behouden
    assert {i["created_at"][:19] for i in items} == {f["created_at"][:19] for f in data["filaments"][:2]}


def test_csv_export_import(client, sample):
    _add(client, sample, rfid="CSV-1", price_per_kg="22.99")
    csv_bytes = client.get("/settings/export.csv").content
    text = csv_bytes.decode("utf-8-sig")
    assert text.splitlines()[0].startswith("id;material;color_name") and "22,99" in text and ";nee;" in text
    r = client.post("/api/import?mode=replace", content=csv_bytes)
    assert r.json()["applied"] and r.json()["new"] == 1
    f = client.get("/api/filaments").json()[0]
    assert f["price_per_kg"] == "22.99" and f["rfid"] == "CSV-1" and f["transparent"] is False


def test_import_errors_change_nothing(client, sample):
    _add(client, sample)
    bad = {"filaments": [
        {**sample, "weight_current": 5000},
        {**sample, "material": "NYLON"},
        {**sample, "rfid": "D"}, {**sample, "rfid": "d"},
    ]}
    r = client.post("/api/import?mode=replace", content=json.dumps(bad))
    assert r.status_code == 422
    errs = r.json()["detail"]["errors"]
    assert len(errs) == 3 and any("RFID" in e for e in errs)
    assert len(client.get("/api/filaments").json()) == 1
    assert client.post("/api/import", content=b"geen json of csv").status_code == 422
    assert client.post("/api/import", content=b"").status_code == 422


def test_settings_web_flow(client, sample):
    _add(client, sample, rfid="W1")
    assert "Export JSON" in client.get("/settings").text
    exp = client.get("/settings/export.json").content
    # Preview zonder bestand
    r = client.post("/settings/import", data={"mode": "merge"})
    assert r.status_code == 422 and "Kies eerst" in r.text
    # Preview (vervangen) toont aantallen en bevestigingsformulier
    r = client.post("/settings/import", data={"mode": "replace"}, files={"file": ("x.json", exp, "application/json")})
    assert r.status_code == 200 and "Vervangen en importeren" in r.text and "data-confirm" in r.text
    # Uitvoeren met payload uit de preview
    import re, html
    payload = html.unescape(re.search(r'<textarea name="payload" hidden>(.*?)</textarea>', r.text, re.S).group(1))
    r = client.post("/settings/import/apply", data={"mode": "replace", "payload": payload}, follow_redirects=False)
    assert r.status_code == 303 and "imported=1" in r.headers["location"]
    assert "Import voltooid" in client.get(r.headers["location"]).text
    assert client.get("/api/filaments").json()[0]["rfid"] == "W1"
    # Foutief bestand
    r = client.post("/settings/import", data={"mode": "merge"}, files={"file": ("x.json", b"{kapot", "application/json")})
    assert r.status_code == 422 and "Ongeldige JSON" in r.text


def test_preview_has_no_success_banner(client, sample):
    _add(client, sample)
    exp = client.get("/settings/export.json").content
    r = client.post("/settings/import", data={"mode": "merge"}, files={"file": ("x.json", exp, "application/json")})
    assert "Import voltooid" not in r.text and "Importeren" in r.text
