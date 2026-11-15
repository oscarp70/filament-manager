"""Tests voor de Bambu-koppeling met een nagebootste P1S (MQTT-berichten en 3MF-bestanden)."""

import io
import zipfile

import pytest

from app.bambu import usage as U
from app.bambu.service import BambuService
from app.bambu.state import EXTERNAL_SLOT, PrinterState, decode_mapping_value, rfid_matches, Tray
from app.bambu.threemf import ThreeMFError, plate_from_path, read_3mf
from app.database import SessionLocal
from app.models import Filament, PrintJob


# ---------------------------------------------------------------- hulpmiddelen

def make_3mf(plates: dict[int, list[tuple[int, str, str, float]]]) -> bytes:
    """plates = {plaatnr: [(filament_id, type, kleur, used_g), ...]}"""
    xml = ['<?xml version="1.0" encoding="UTF-8"?><config><header><header_item key="X-BBL-Client-Type" value="slicer"/></header>']
    for idx, fils in plates.items():
        total = sum(f[3] for f in fils)
        xml.append(f'<plate><metadata key="index" value="{idx}"/><metadata key="prediction" value="3600"/>'
                   f'<metadata key="weight" value="{total:.2f}"/>')
        for fid, typ, color, g in fils:
            xml.append(f'<filament id="{fid}" tray_info_idx="GFL99" type="{typ}" color="{color}" '
                       f'used_m="{g / 3:.2f}" used_g="{g:.2f}"/>')
        xml.append("</plate>")
    xml.append("</config>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("Metadata/slice_info.config", "".join(xml))
        z.writestr("Metadata/plate_1.gcode", "; gcode")
    return buf.getvalue()


AMS_FULL = {
    "print": {
        "ams": {
            "ams": [{
                "id": "0", "humidity": "4", "temp": "0.0",
                "tray": [
                    {"id": "0", "tray_type": "PLA", "tray_color": "1B1B22FF", "tag_uid": "D53E550500000100",
                     "tray_uuid": "A1B2C3D4E5F60718293A4B5C6D7E8F90", "remain": 80, "tray_weight": "1000",
                     "tray_sub_brands": "PLA Basic"},
                    {"id": "1", "tray_type": "PETG", "tray_color": "409CFFFF", "tag_uid": "0000000000000000",
                     "tray_uuid": "00000000000000000000000000000000", "remain": -1, "tray_weight": "0"},
                    {"id": "2"},
                    {"id": "3", "tray_type": "PLA", "tray_color": "F77F00FF", "tag_uid": "0000000000000000",
                     "remain": -1, "tray_weight": "0"},
                ],
            }],
            "ams_exist_bits": "1", "tray_exist_bits": "b", "tray_now": "255",
        },
        "vt_tray": {"id": "254", "tray_type": "", "tray_color": "00000000"},
        "gcode_state": "IDLE",
        "mc_percent": 0,
        "command": "push_status",
    }
}


def report(**fields):
    return {"print": {"command": "push_status", **fields}}


class FakeFetcher:
    def __init__(self, content: bytes | None):
        self.content = content
        self.calls: list[dict] = []

    def __call__(self, host, code, subtask_name=None, gcode_file=None, url=None):
        self.calls.append({"subtask_name": subtask_name, "gcode_file": gcode_file, "url": url})
        return (self.content, f"/cache/{subtask_name}.gcode.3mf") if self.content else None


@pytest.fixture()
def spools(client):
    """Drie spoelen in de database (client-fixture maakt de tabellen aan)."""
    with SessionLocal() as db:
        black = Filament(material="PLA", color_name="Galaxy Black", r=27, g=27, b=34, price_per_kg=22.99,
                         weight_new=1000, weight_current=800, rfid="D5:3E:55:05")
        blue = Filament(material="PETG", color_name="Clear Blue", r=64, g=156, b=255, transparent=True,
                        price_per_kg=24.5, weight_new=1000, weight_current=640)
        orange = Filament(material="PLA", color_name="Signaal Oranje", r=247, g=127, b=0, price_per_kg=19.95,
                          weight_new=1000, weight_current=1000)
        db.add_all([black, blue, orange])
        db.commit()
        return {"black": black.id, "blue": blue.id, "orange": orange.id}


def make_service(fetcher, auto="true") -> BambuService:
    svc = BambuService(session_factory=SessionLocal, fetcher=fetcher, synchronous=True)
    with SessionLocal() as db:
        U.save_settings(db, {"bambu_enabled": "true", "bambu_host": "10.0.0.5", "bambu_serial": "01P00A000000001",
                             "bambu_access_code": "12345678", "bambu_auto_deduct": auto})
    svc.load_config()
    return svc


def weight(fid: int) -> int:
    with SessionLocal() as db:
        return db.get(Filament, fid).weight_current


def last_job() -> PrintJob:
    with SessionLocal() as db:
        job = db.query(PrintJob).order_by(PrintJob.id.desc()).first()
        _ = [u.filament_id for u in job.usages]
        return job


def run_print(svc, *, task="1001", name="Bracket", plate=1, tray_sequence=("3",), end="FINISH", end_progress=100,
              extra_running=None):
    svc.handle_report(report(gcode_state="PREPARE", task_id=task, subtask_name=name,
                             gcode_file=f"/data/Metadata/plate_{plate}.gcode", mc_percent=0))
    progress = 0
    for i, tray in enumerate(tray_sequence):
        progress = min(99, (i + 1) * 30)
        msg = {"gcode_state": "RUNNING", "mc_percent": progress, "ams": {"tray_now": tray}}
        if extra_running:
            msg.update(extra_running)
        svc.handle_report(report(**msg))
    if end == "FINISH":
        svc.handle_report(report(gcode_state="FINISH", mc_percent=100))
    else:
        svc.handle_report(report(gcode_state="RUNNING", mc_percent=end_progress))
        svc.handle_report(report(gcode_state=end, mc_percent=end_progress))


# ---------------------------------------------------------------- 3MF en pure logica

def test_read_3mf_plates():
    data = make_3mf({1: [(1, "PLA", "#FF0000", 0)], 2: [(1, "PLA", "#FF0000", 12.5), (2, "PETG", "#00FF00", 3.25)]})
    p = read_3mf(data)  # zonder plaat: eerste plaat mét verbruik
    assert p.index == 2 and [f.used_g for f in p.filaments] == [12.5, 3.25]
    assert read_3mf(data, 2).filaments[1].material == "PETG"
    assert plate_from_path("/data/Metadata/plate_7.gcode") == 7 and plate_from_path("x.gcode") is None
    with pytest.raises(ThreeMFError):
        read_3mf(b"geen zip")
    with pytest.raises(ThreeMFError):
        read_3mf(data, 9)


def test_mapping_decode_and_rfid():
    assert decode_mapping_value(3) == 3 and decode_mapping_value(-1) is None
    assert decode_mapping_value(254) == EXTERNAL_SLOT
    assert decode_mapping_value(0x0102) == 6  # AMS 2, slot 3
    assert decode_mapping_value(65024) == EXTERNAL_SLOT
    t = Tray(slot=0, present=True, tag_uid="D53E550500000100", tray_uuid="A1B2C3D4E5F60718293A4B5C6D7E8F90")
    assert rfid_matches("d5:3e:55:05", t)
    assert rfid_matches("D53E550500000100", t)
    assert rfid_matches("A1B2C3D4-E5F6-0718-293A-4B5C6D7E8F90", t)
    assert not rfid_matches("D53E", t) and not rfid_matches("FFFFFFFF", t)
    assert not rfid_matches("00000000", Tray(slot=1, present=True, tag_uid="0000000000000000"))


def test_state_merges_deltas():
    s = PrinterState()
    s.merge(AMS_FULL)
    s.merge({"print": {"ams": {"ams": [{"id": "0", "tray": [{"id": "0", "remain": 75}]}], "tray_now": "0"}}})
    trays = s.tray_map()
    assert trays[0].remain == 75 and trays[0].material == "PLA"  # rest behouden
    assert not trays[2].present and trays[3].color == "#F77F00"
    assert s.tray_now == 0 and EXTERNAL_SLOT in trays and not trays[EXTERNAL_SLOT].present


# ---------------------------------------------------------------- service: prints volgen

def test_rfid_auto_assignment(spools):
    svc = make_service(FakeFetcher(None))
    svc.handle_report(AMS_FULL)
    with SessionLocal() as db:
        a = U.assignments(db)
    assert a[0].filament_id == spools["black"] and a[0].source == "rfid"
    # Spoel eruit: koppeling via RFID vervalt
    svc.handle_report({"print": {"ams": {"ams": [{"id": "0", "tray": [{"id": "0"}]}]}}})
    with SessionLocal() as db:
        assert U.assignments(db)[0].filament_id is None


def test_single_color_print_deducts(spools):
    fetch = FakeFetcher(make_3mf({1: [(1, "PLA", "#F77F00", 14.21)]}))
    svc = make_service(fetch)
    svc.handle_report(AMS_FULL)
    with SessionLocal() as db:
        U.assign_slot(db, 3, spools["orange"])
    run_print(svc, tray_sequence=("3", "3"))
    job = last_job()
    assert job.status == "finished" and job.usage_source == "3mf" and job.plate == 1
    assert len(job.usages) == 1 and job.usages[0].slot == 3 and float(job.usages[0].grams) == 14.21
    assert weight(spools["orange"]) == 1000 - 14
    assert fetch.calls[0]["subtask_name"] == "Bracket"


def test_multicolor_with_ams_mapping_from_print_command(spools):
    fetch = FakeFetcher(make_3mf({1: [(1, "PLA", "#F77F00", 20.0), (2, "PLA", "#1B1B22", 5.5), (3, "PETG", "#409CFF", 0)]}))
    svc = make_service(fetch)
    svc.handle_report(AMS_FULL)
    with SessionLocal() as db:
        U.assign_slot(db, 3, spools["orange"])
    # Bambu Studio stuurt het printcommando (LAN) — wij lezen mee op het request-topic
    svc.handle_request({"print": {"command": "project_file", "param": "Metadata/plate_1.gcode",
                                  "url": "ftp:///Bracket.gcode.3mf", "ams_mapping": [3, 0, -1], "use_ams": True}})
    run_print(svc, tray_sequence=("3", "0", "3"))
    job = last_job()
    by_slot = {u.slot: u for u in job.usages}
    assert set(by_slot) == {3, 0}
    assert by_slot[3].filament_id == spools["orange"] and by_slot[0].filament_id == spools["black"]
    assert weight(spools["orange"]) == 980 and weight(spools["black"]) == 800 - 6  # 5,5 -> 6 (afgerond)
    assert fetch.calls[0]["url"] == "ftp:///Bracket.gcode.3mf"


def test_multicolor_without_mapping_uses_used_slots_and_colors(spools):
    fetch = FakeFetcher(make_3mf({1: [(1, "PLA", "#1B1B22", 8.0), (2, "PLA", "#F77F00", 4.0)]}))
    svc = make_service(fetch)
    svc.handle_report(AMS_FULL)
    with SessionLocal() as db:
        U.assign_slot(db, 3, spools["orange"])
    run_print(svc, tray_sequence=("0", "3"))
    by_slot = {u.slot: float(u.grams) for u in last_job().usages}
    assert by_slot == {0: 8.0, 3: 4.0}


def test_cancelled_print_deducts_printed_part(spools):
    svc = make_service(FakeFetcher(make_3mf({1: [(1, "PLA", "#F77F00", 50.0)]})))
    svc.handle_report(AMS_FULL)
    with SessionLocal() as db:
        U.assign_slot(db, 3, spools["orange"])
    run_print(svc, end="FAILED", end_progress=40)
    job = last_job()
    assert job.status == "failed" and job.progress == 40
    assert float(job.usages[0].grams) == 20.0 and weight(spools["orange"]) == 980


def test_fallback_on_ams_remain_when_no_3mf(spools):
    svc = make_service(FakeFetcher(None))
    svc.handle_report(AMS_FULL)  # slot 0 = 80%, tag -> zwarte spoel
    svc.handle_report(report(gcode_state="RUNNING", task_id="2002", subtask_name="Haak", mc_percent=5,
                             ams={"tray_now": "0"}))
    svc.handle_report({"print": {"ams": {"ams": [{"id": "0", "tray": [{"id": "0", "remain": 75}]}]}}})
    svc.handle_report(report(gcode_state="FINISH", mc_percent=100))
    job = last_job()
    assert job.usage_source == "ams_remain" and float(job.usages[0].grams) == 50.0
    assert weight(spools["black"]) == 750


def test_no_usage_info_then_manual_entry(client, spools):
    svc = make_service(FakeFetcher(None))
    svc.handle_report(AMS_FULL)
    run_print(svc, task="3003", name="Zonder bestand", tray_sequence=("3",))
    job = last_job()
    assert job.usage_source == "none" and not job.usages and "handmatig" in job.note
    r = client.post(f"/printer/jobs/{job.id}/usages", data={"filament_id": str(spools["blue"]), "grams": "12,4"},
                    headers={"HX-Request": "true"})
    assert r.status_code == 200 and "afgeschreven" in r.text
    assert weight(spools["blue"]) == 640 - 12


def test_no_auto_deduct_and_manual_actions(client, spools):
    svc = make_service(FakeFetcher(make_3mf({1: [(1, "PLA", "#F77F00", 30.0)]})), auto="false")
    svc.handle_report(AMS_FULL)
    run_print(svc, tray_sequence=("3",))
    job = last_job()
    u = job.usages[0]
    assert u.filament_id is None and weight(spools["orange"]) == 1000  # slot 3 had nog geen spoel
    h = {"HX-Request": "true"}
    # Spoel kiezen -> wordt meteen afgeschreven
    r = client.post(f"/printer/usages/{u.id}/assign", data={"filament_id": str(spools["orange"])}, headers=h)
    assert "30 g afgeschreven" in r.text and weight(spools["orange"]) == 970
    # Gram aanpassen past de afschrijving aan
    client.post(f"/printer/usages/{u.id}/grams", data={"grams": "25"}, headers=h)
    assert weight(spools["orange"]) == 975
    # Terugdraaien
    client.post(f"/printer/usages/{u.id}/undo", headers=h)
    assert weight(spools["orange"]) == 1000
    # Andere spoel
    client.post(f"/printer/usages/{u.id}/assign", data={"filament_id": str(spools["black"])}, headers=h)
    assert weight(spools["black"]) == 775 and weight(spools["orange"]) == 1000
    # Pagina en API
    page = client.get("/printer")
    assert page.status_code == 200 and "Printgeschiedenis" in page.text and "Bracket" in page.text
    jobs = client.get("/api/printer/jobs").json()
    assert jobs[0]["usages"][0]["deducted_g"] == 25.0
    # Print verwijderen laat gewicht staan
    client.post(f"/printer/jobs/{job.id}/delete", headers=h)
    assert weight(spools["black"]) == 775 and client.get("/api/printer/jobs").json() == []


def test_restart_during_print(spools):
    fetch = FakeFetcher(make_3mf({1: [(1, "PLA", "#F77F00", 10.0)]}))
    svc = make_service(fetch)
    svc.handle_report(AMS_FULL)
    with SessionLocal() as db:
        U.assign_slot(db, 3, spools["orange"])
    svc.handle_report(report(gcode_state="RUNNING", task_id="4004", subtask_name="Lang", mc_percent=10,
                             gcode_file="/data/Metadata/plate_1.gcode", ams={"tray_now": "3"}))
    # App herstart: nieuwe service, zelfde database
    svc2 = make_service(fetch)
    assert svc2.active and svc2.active["key"] == "task:4004"
    # Eerst een deltabericht zonder taakgegevens (komt na herstart soms vóór de volledige status)
    svc2.handle_report(report(gcode_state="RUNNING", mc_percent=55))
    svc2.handle_report({"print": {"ams": AMS_FULL["print"]["ams"]}})
    svc2.handle_report(report(gcode_state="RUNNING", task_id="4004", subtask_name="Lang", mc_percent=60))
    svc2.handle_report(report(gcode_state="FINISH", mc_percent=100))
    with SessionLocal() as db:
        assert db.query(PrintJob).count() == 1
    assert weight(spools["orange"]) == 990


def test_new_print_without_seeing_end(spools):
    svc = make_service(FakeFetcher(make_3mf({1: [(1, "PLA", "#F77F00", 10.0)]})))
    svc.handle_report(AMS_FULL)
    with SessionLocal() as db:
        U.assign_slot(db, 3, spools["orange"])
    svc.handle_report(report(gcode_state="RUNNING", task_id="5005", subtask_name="A", mc_percent=50, ams={"tray_now": "3"}))
    svc.handle_report(report(gcode_state="RUNNING", task_id="5006", subtask_name="B", mc_percent=1))
    with SessionLocal() as db:
        jobs = db.query(PrintJob).order_by(PrintJob.id).all()
        assert [j.status for j in jobs] == ["unknown", "running"]
        # Onbekend einde: wel berekend, niet automatisch afgeschreven
        assert float(jobs[0].usages[0].grams) == 5.0 and jobs[0].usages[0].deducted_at is None
    assert weight(spools["orange"]) == 1000


def test_printer_settings_page(client, monkeypatch):
    from app.bambu.service import service

    monkeypatch.setattr(service, "_connect", lambda: None)
    assert "Niet ingesteld" in client.get("/printer").text
    r = client.post("/printer/settings", data={"bambu_enabled": "on", "bambu_host": "10.0.0.9",
                                               "bambu_serial": "01p00a123", "bambu_access_code": "abcd1234",
                                               "bambu_auto_deduct": "on"}, follow_redirects=False)
    assert r.status_code == 303
    # Lege toegangscode bij opnieuw opslaan: bestaande blijft behouden
    client.post("/printer/settings", data={"bambu_enabled": "on", "bambu_host": "10.0.0.10",
                                           "bambu_serial": "01P00A123", "bambu_access_code": ""})
    with SessionLocal() as db:
        cfg = U.get_settings(db)
    assert cfg["bambu_access_code"] == "abcd1234" and cfg["bambu_host"] == "10.0.0.10"
    assert cfg["bambu_serial"] == "01P00A123" and cfg["bambu_auto_deduct"] == "false"
    page = client.get("/printer").text
    assert "abcd1234" not in page and "opgeslagen" in page
    r = client.post("/printer/test", data={"bambu_host": "", "bambu_serial": "", "bambu_access_code": ""})
    assert r.status_code == 200
    assert client.get("/api/printer/status").json()["configured"] is True
    service.stop()
