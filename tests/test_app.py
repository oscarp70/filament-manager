from decimal import Decimal


# ---------- API ----------

def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_api_crud(client, sample):
    r = client.post("/api/filaments", json=sample)
    assert r.status_code == 201, r.text
    f = r.json()
    assert f["hex"] == "#1b1b22"
    assert f["percent_remaining"] == 82.0
    assert Decimal(f["remaining_value"]) == Decimal("18.85")
    fid = f["id"]

    assert client.get(f"/api/filaments/{fid}").json()["color_name"] == "Galaxy Black"

    r = client.patch(f"/api/filaments/{fid}", json={"color_name": "Space Black", "transparent": True})
    assert r.status_code == 200
    assert r.json()["color_name"] == "Space Black" and r.json()["transparent"] is True

    r = client.patch(f"/api/filaments/{fid}/weight", json={"weight_current": 100})
    assert r.json()["weight_current"] == 100
    assert r.json()["low_stock"] is True

    full = {**sample, "material": "ASA", "weight_current": 500}
    r = client.put(f"/api/filaments/{fid}", json=full)
    assert r.json()["material"] == "ASA"

    assert client.delete(f"/api/filaments/{fid}").status_code == 204
    assert client.get(f"/api/filaments/{fid}").status_code == 404


def test_api_validation(client, sample):
    assert client.post("/api/filaments", json={**sample, "weight_current": 1200}).status_code == 422
    assert client.post("/api/filaments", json={**sample, "r": 300}).status_code == 422
    assert client.post("/api/filaments", json={**sample, "material": "NYLON"}).status_code == 422
    assert client.post("/api/filaments", json={**sample, "price_per_kg": "-1"}).status_code == 422
    assert client.post("/api/filaments", json={**sample, "color_name": "  "}).status_code == 422
    fid = client.post("/api/filaments", json=sample).json()["id"]
    assert client.patch(f"/api/filaments/{fid}", json={"weight_new": 500}).status_code == 422
    assert client.patch(f"/api/filaments/{fid}/weight", json={"weight_current": -5}).status_code == 422


def test_api_filter_sort_stats(client, sample):
    client.post("/api/filaments", json=sample)
    client.post("/api/filaments", json={**sample, "material": "PETG", "color_name": "Clear",
                                         "transparent": True, "r": 64, "g": 156, "b": 255, "weight_current": 100})
    client.post("/api/filaments", json={**sample, "material": "ABS", "color_name": "Red",
                                         "r": 220, "g": 20, "b": 30, "weight_current": 1000})
    assert len(client.get("/api/filaments?material=petg").json()) == 1
    assert len(client.get("/api/filaments?transparent=true").json()) == 1
    assert [f["weight_current"] for f in client.get("/api/filaments?sort=weight").json()] == [1000, 820, 100]
    assert len(client.get("/api/filaments?low_stock=true").json()) == 1
    colors = [f["color_name"] for f in client.get("/api/filaments?sort=color").json()]
    assert colors == ["Red", "Clear", "Galaxy Black"]  # rood, blauw, dan grijstinten

    s = client.get("/api/stats").json()
    assert s["count"] == 3 and s["weight_g"] == 1920 and s["low_stock_count"] == 1
    per = {m["material"]: m for m in s["per_material"]}
    assert per["ASA"]["count"] == 0 and per["PETG"]["weight_g"] == 100


# ---------- Webinterface ----------

def test_dashboard_empty_and_filled(client, sample):
    r = client.get("/")
    assert r.status_code == 200 and "Nog geen filament" in r.text
    client.post("/api/filaments", json={**sample, "weight_current": 50})
    r = client.get("/")
    assert "Galaxy Black" in r.text and "Bijna op" in r.text and "<svg" in r.text


def test_htmx_filter_returns_partial(client, sample):
    client.post("/api/filaments", json=sample)
    r = client.get("/?material=ABS", headers={"HX-Request": "true", "HX-Target": "inventory"})
    assert r.text.lstrip().startswith('<section id="inventory"')
    assert "Geen spoelen die aan dit filter voldoen" in r.text


def test_web_create_edit_delete(client):
    form = {"material": "PETG", "color_name": "Clear Blue", "hex": "#409cff", "r": "64", "g": "156", "b": "255",
            "transparent": "on", "price_per_kg": "24,50", "weight_new": "1000", "weight_current": "1000"}
    r = client.post("/filaments/new", data=form, follow_redirects=False)
    assert r.status_code == 303
    f = client.get("/api/filaments").json()[0]
    assert f["transparent"] is True and f["price_per_kg"] == "24.50" and (f["r"], f["g"], f["b"]) == (64, 156, 255)

    # Alleen hex (zonder JavaScript) wordt ook omgezet naar RGB
    r = client.post(f"/filaments/{f['id']}/edit",
                    data={**form, "r": "", "g": "", "b": "", "hex": "#ff0000"}, follow_redirects=False)
    assert r.status_code == 303
    assert (client.get(f"/api/filaments/{f['id']}").json()["r"]) == 255

    # Validatie met Nederlandse melding
    r = client.post("/filaments/new", data={**form, "weight_current": "1500"})
    assert r.status_code == 422 and "Gewicht nu mag niet groter zijn dan gewicht nieuw" in r.text
    r = client.post("/filaments/new", data={**form, "color_name": "", "price_per_kg": "abc"})
    assert r.status_code == 422 and "Vul een geldig bedrag in" in r.text

    assert client.get(f"/filaments/{f['id']}/edit").status_code == 200
    r = client.post(f"/filaments/{f['id']}/delete", follow_redirects=False)
    assert r.status_code == 303 and client.get("/api/filaments").json() == []


def test_quick_weight_and_htmx_delete(client, sample):
    fid = client.post("/api/filaments", json=sample).json()["id"]
    h = {"HX-Request": "true"}
    r = client.post(f"/filaments/{fid}/weight", data={"weight_current": "400"}, headers=h)
    assert f'id="filament-{fid}"' in r.text and 'id="stats"' in r.text and "hx-swap-oob" in r.text
    assert client.get(f"/api/filaments/{fid}").json()["weight_current"] == 400

    r = client.post(f"/filaments/{fid}/weight", data={"weight_current": "5000"}, headers=h)
    assert "Maximaal" in r.text
    assert client.get(f"/api/filaments/{fid}").json()["weight_current"] == 400

    r = client.delete(f"/filaments/{fid}", headers=h)
    assert r.status_code == 200 and "hx-swap-oob" in r.text
    assert client.get(f"/api/filaments/{fid}").status_code == 404


def test_preview_svg(client):
    r = client.get("/preview?r=10&g=20&b=30&transparent=on&weight_new=1000&weight_current=250")
    assert "rgb(10,20,30)" in r.text and "stripes" in r.text and "25% resterend" in r.text
    r = client.get("/preview?hex=%23ff8800&weight_new=1000&weight_current=1000")
    assert "rgb(255,136,0)" in r.text
