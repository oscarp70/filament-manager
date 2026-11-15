"""Achtergrondservice: verbindt met de printer via MQTT, volgt prints en schrijft verbruik af."""

from __future__ import annotations

import json
import logging
import ssl
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import SessionLocal
from ..models import Filament, PrintJob, PrintUsage
from . import usage as U
from .ftp import fetch_project_file
from .state import ACTIVE_STATES, END_STATES, PrinterState, Tray, resolve_slots, rfid_matches
from .threemf import FilamentUse, ThreeMFError, plate_from_path, read_3mf

log = logging.getLogger("filament.bambu")

MQTT_PORT = 8883

STATUS_LABELS = {
    "running": "Bezig",
    "finished": "Voltooid",
    "failed": "Mislukt/afgebroken",
    "cancelled": "Geannuleerd",
    "unknown": "Onbekend einde",
}
GCODE_STATE_LABELS = {
    "IDLE": "Inactief",
    "PREPARE": "Voorbereiden",
    "RUNNING": "Printen",
    "PAUSE": "Gepauzeerd",
    "FINISH": "Klaar",
    "FAILED": "Mislukt",
    "SLICING": "Slicen",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


class BambuService:
    def __init__(
        self,
        session_factory: Callable[[], Session] = SessionLocal,
        fetcher: Callable[..., tuple[bytes, str] | None] = fetch_project_file,
        synchronous: bool = False,
    ) -> None:
        self.session_factory = session_factory
        self.fetcher = fetcher
        self.synchronous = synchronous
        self.lock = threading.RLock()
        self.state = PrinterState()
        self.client = None
        self.config: dict[str, str] = {}
        self.connected = False
        self.last_error = ""
        self.last_message_at: datetime | None = None
        self.active: dict[str, Any] | None = None  # {"id": int, "key": str, "progress": int, "slots": [..]}
        self.last_project_cmd: dict[str, Any] | None = None
        self._tray_signature: str | None = None
        self._executor = None if synchronous else ThreadPoolExecutor(max_workers=1, thread_name_prefix="bambu")

    # ------------------------------------------------------------------ levenscyclus

    @property
    def enabled(self) -> bool:
        return U.is_true(self.config.get("bambu_enabled"))

    @property
    def configured(self) -> bool:
        c = self.config
        return bool(c.get("bambu_host") and c.get("bambu_serial") and c.get("bambu_access_code"))

    def load_config(self) -> None:
        with self.session_factory() as db:
            self.config = U.get_settings(db)
            running = db.scalars(
                select(PrintJob).where(PrintJob.status == "running").order_by(PrintJob.id.desc())
            ).first()
            if running is not None:
                data = U.job_data(running)
                self.active = {
                    "id": running.id,
                    "key": running.job_key,
                    "progress": running.progress,
                    "slots": list(data.get("used_slots", [])),
                }

    def start(self) -> None:
        try:
            self.load_config()
        except Exception as exc:  # noqa: BLE001 - bv. tabellen nog niet gemigreerd
            log.warning("Printerinstellingen niet geladen: %s", exc)
            return
        if not (self.enabled and self.configured):
            return
        self._connect()

    def stop(self) -> None:
        client, self.client = self.client, None
        if client is not None:
            try:
                client.disconnect()
                client.loop_stop()
            except Exception:  # noqa: BLE001
                pass
        self.connected = False

    def reload(self) -> None:
        self.stop()
        self.state = PrinterState()
        self._tray_signature = None
        self.last_error = ""
        self.start()

    def _connect(self) -> None:
        import paho.mqtt.client as mqtt

        c = self.config
        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"filament-manager-{uuid.uuid4().hex[:8]}",
            protocol=mqtt.MQTTv311,
        )
        client.username_pw_set("bblp", c["bambu_access_code"])
        client.tls_set(cert_reqs=ssl.CERT_NONE)
        client.tls_insecure_set(True)
        client.reconnect_delay_set(min_delay=2, max_delay=60)
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        self.client = client
        try:
            client.connect_async(c["bambu_host"], MQTT_PORT, keepalive=60)
            client.loop_start()
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"Verbinden mislukt: {exc}"
            log.warning(self.last_error)

    # ------------------------------------------------------------------ MQTT-callbacks

    def _topic(self, kind: str) -> str:
        return f"device/{self.config.get('bambu_serial', '').strip()}/{kind}"

    def _on_connect(self, client, userdata, flags, reason_code, properties=None) -> None:  # noqa: ANN001
        if getattr(reason_code, "is_failure", False):
            self.connected = False
            text = str(reason_code)
            if "authori" in text.lower() or "password" in text.lower():
                text += " — controleer de toegangscode (en of LAN-only + Developer Mode aan staan)"
            self.last_error = f"Verbinding geweigerd: {text}"
            log.warning(self.last_error)
            return
        self.connected = True
        self.last_error = ""
        client.subscribe(self._topic("report"))
        client.subscribe(self._topic("request"))
        client.publish(
            self._topic("request"),
            json.dumps({"pushing": {"sequence_id": "0", "command": "pushall"}}),
        )
        log.info("Verbonden met printer %s", self.config.get("bambu_host"))

    def _on_disconnect(self, client, userdata, flags, reason_code, properties=None) -> None:  # noqa: ANN001
        self.connected = False
        if getattr(reason_code, "is_failure", False):
            self.last_error = f"Verbinding verbroken: {reason_code}"

    def _on_message(self, client, userdata, msg) -> None:  # noqa: ANN001
        try:
            payload = json.loads(msg.payload.decode("utf-8", errors="replace"))
        except ValueError:
            return
        try:
            if msg.topic.endswith("/request"):
                self.handle_request(payload)
            else:
                self.handle_report(payload)
        except Exception:  # noqa: BLE001
            log.exception("Fout bij verwerken printerbericht")

    # ------------------------------------------------------------------ berichtverwerking

    def handle_request(self, payload: dict[str, Any]) -> None:
        p = payload.get("print")
        if isinstance(p, dict) and p.get("command") == "project_file":
            with self.lock:
                self.last_project_cmd = {**p, "_at": _now().isoformat()}
                if self.active is not None:
                    self._update_job_data(self.active["id"], {"project_cmd": self.last_project_cmd})

    def handle_report(self, payload: dict[str, Any]) -> None:
        with self.lock:
            self.state.merge(payload)
            self.last_message_at = _now()
            p = payload.get("print") or {}
            if "ams" in p or "vt_tray" in p:
                self._sync_rfid_assignments()
            self._track()

    # ------------------------------------------------------------------ RFID -> slot

    def _sync_rfid_assignments(self) -> None:
        trays = self.state.trays()
        sig = json.dumps([(t.slot, t.present, t.tag_uid, t.tray_uuid) for t in trays])
        if sig == self._tray_signature:
            return
        self._tray_signature = sig
        self.refresh_assignments(trays)

    def refresh_assignments(self, trays: list[Tray] | None = None) -> None:
        """Koppel sloten automatisch aan spoelen waarvan het RFID-veld overeenkomt met de tag in de AMS."""
        trays = trays if trays is not None else self.state.trays()
        if not trays:
            return
        with self.session_factory() as db:
            spools = list(db.scalars(select(Filament).where(Filament.rfid.is_not(None))))
            current = U.assignments(db)
            for t in trays:
                match = next((f for f in spools if t.present and t.has_tag and rfid_matches(f.rfid, t)), None)
                a = current.get(t.slot)
                if match is not None:
                    if a is None or a.filament_id != match.id:
                        U.assign_slot(db, t.slot, match.id, "rfid")
                elif a is not None and a.source == "rfid" and a.filament_id is not None:
                    # Spoel met tag is eruit gehaald of vervangen
                    U.assign_slot(db, t.slot, None, "rfid")

    # ------------------------------------------------------------------ printjobs volgen

    def _track(self) -> None:
        st = self.state.gcode_state
        if not st:
            return
        key = self.state.job_key
        if st in ACTIVE_STATES:
            if not self.state.has_identity:
                if self.active is not None:
                    self._update_running()
                return
            if self.active is not None and self.active["key"] != key:
                self._end_job("unknown")
            if self.active is None:
                self._start_job(key)
            else:
                self._update_running()
        elif st in END_STATES and self.active is not None:
            self._end_job("finished" if st == "FINISH" else "failed")
        elif st == "IDLE" and self.active is not None:
            self._end_job("cancelled")

    def _start_job(self, key: str) -> None:
        s = self.state
        trays = s.tray_map()
        cmd = self.last_project_cmd
        if cmd is not None:
            try:
                age = _now() - datetime.fromisoformat(cmd["_at"])
            except (KeyError, ValueError):
                age = timedelta(hours=1)
            if age > timedelta(minutes=30):
                cmd = None
        plate = plate_from_path(s.gcode_file) or plate_from_path((cmd or {}).get("param"))
        slots = [s.tray_now] if s.tray_now is not None else []
        data = {
            "subtask_name": s.subtask_name,
            "gcode_file": s.gcode_file,
            "project_cmd": cmd,
            "status_mapping": s.mapping,
            "used_slots": slots,
            "remain_start": {str(k): t.remain for k, t in trays.items() if t.present},
            "tray_weight": {str(k): t.weight for k, t in trays.items() if t.present},
            "trays_start": {str(k): asdict(t) for k, t in trays.items() if t.present},
        }
        with self.session_factory() as db:
            job = PrintJob(job_key=key, name=s.subtask_name or "Onbekende print", plate=plate,
                           status="running", progress=s.progress)
            U.set_job_data(job, data)
            db.add(job)
            db.commit()
            job_id = job.id
        self.active = {"id": job_id, "key": key, "progress": s.progress, "slots": slots}
        log.info("Print gestart: %s (job %s)", s.subtask_name, job_id)
        self._submit(self._fetch_3mf, job_id)

    def _update_running(self) -> None:
        a = self.active
        s = self.state
        changes: dict[str, Any] = {}
        if s.gcode_state == "RUNNING" and s.progress != a["progress"]:
            a["progress"] = s.progress
            changes["progress"] = s.progress
        tn = s.tray_now
        if tn is not None and tn not in a["slots"]:
            a["slots"].append(tn)
            changes["used_slots"] = list(a["slots"])
        if s.mapping:
            changes["status_mapping"] = s.mapping
        if changes:
            progress = changes.pop("progress", None)
            self._update_job_data(a["id"], changes, progress=progress)

    def _update_job_data(self, job_id: int, changes: dict[str, Any], progress: int | None = None) -> None:
        with self.session_factory() as db:
            job = db.get(PrintJob, job_id)
            if job is None:
                return
            if changes:
                data = U.job_data(job)
                data.update(changes)
                U.set_job_data(job, data)
            if progress is not None:
                job.progress = progress
            db.commit()

    def _end_job(self, status: str) -> None:
        a, self.active = self.active, None
        if a is None:
            return
        # Bij FINISH meldt de printer 100%; bij afbreken de laatst bekende voortgang
        progress = 100 if status == "finished" else max(a.get("progress", 0), 0)
        snapshot = {
            "remain_end": {str(k): t.remain for k, t in self.state.tray_map().items() if t.present},
            "trays_end": {str(k): asdict(t) for k, t in self.state.tray_map().items() if t.present},
            "used_slots": list(a.get("slots", [])),
            "status_mapping_end": self.state.mapping,
        }
        log.info("Print beëindigd (%s): job %s", status, a["id"])
        self._submit(self._finalize, a["id"], status, progress, snapshot)

    def _submit(self, fn: Callable[..., Any], *args: Any) -> None:
        if self._executor is None:
            fn(*args)
        else:
            self._executor.submit(self._safe, fn, *args)

    @staticmethod
    def _safe(fn: Callable[..., Any], *args: Any) -> None:
        try:
            fn(*args)
        except Exception:  # noqa: BLE001
            log.exception("Fout in printerverwerking")

    # ------------------------------------------------------------------ 3MF ophalen en afrekenen

    def _fetch_3mf(self, job_id: int) -> bool:
        with self.session_factory() as db:
            job = db.get(PrintJob, job_id)
            if job is None:
                return False
            data = U.job_data(job)
            if data.get("threemf"):
                return True
            cmd = data.get("project_cmd") or {}
            try:
                result = self.fetcher(
                    self.config.get("bambu_host", ""),
                    self.config.get("bambu_access_code", ""),
                    subtask_name=data.get("subtask_name") or job.name,
                    gcode_file=data.get("gcode_file"),
                    url=cmd.get("url"),
                )
            except Exception as exc:  # noqa: BLE001
                result = None
                data["threemf_error"] = f"Downloaden mislukt: {exc}"
            if result is None:
                data.setdefault("threemf_error", "Printbestand niet gevonden op de printer")
                U.set_job_data(job, data)
                db.commit()
                return False
            content, path = result
            plate = job.plate or plate_from_path(cmd.get("param"))
            try:
                info = read_3mf(content, plate)
            except ThreeMFError as exc:
                data["threemf_error"] = str(exc)
                U.set_job_data(job, data)
                db.commit()
                return False
            data.pop("threemf_error", None)
            data["threemf"] = {
                "path": path,
                "plate": info.index,
                "weight_g": info.weight_g,
                "prediction_s": info.prediction_s,
                "filaments": [asdict(f) for f in info.filaments],
            }
            job.plate = info.index
            U.set_job_data(job, data)
            db.commit()
            return True

    def _finalize(self, job_id: int, status: str, progress: int, snapshot: dict[str, Any]) -> None:
        # Nog geen 3MF? Nu nogmaals proberen.
        if not self._has_3mf(job_id):
            self._fetch_3mf(job_id)
        with self.session_factory() as db:
            job = db.get(PrintJob, job_id)
            if job is None or job.status != "running":
                return
            data = U.job_data(job)
            data.update({k: v for k, v in snapshot.items() if v is not None})
            usages = self._compute_usages(db, job, data, status, progress)
            for u in usages:
                job.usages.append(u)
            job.status = status
            job.progress = progress
            job.ended_at = _now()
            job.total_g = sum((u.grams for u in usages), Decimal(0))
            U.set_job_data(job, data)
            db.flush()
            auto = U.is_true(self.config.get("bambu_auto_deduct", "true"))
            if auto and status != "unknown":
                for u in job.usages:
                    U.deduct(db, u, commit=False)
            db.commit()

    def _compute_usages(
        self, db: Session, job: PrintJob, data: dict[str, Any], status: str, progress: int
    ) -> list[PrintUsage]:
        used_slots = list(dict.fromkeys(data.get("used_slots") or []))
        assignments = U.assignments(db)
        factor = 1.0 if status == "finished" else max(0, min(100, progress)) / 100
        trays = self.state.tray_map()
        if not trays and data.get("trays_end"):
            trays = {int(k): Tray(**v) for k, v in data["trays_end"].items()}
        usages: list[PrintUsage] = []
        job.note = None

        tm = data.get("threemf")
        if tm:
            filaments = [FilamentUse(**f) for f in tm.get("filaments", [])]
            cmd = data.get("project_cmd") or {}
            mapping = cmd.get("ams_mapping") or data.get("status_mapping") or data.get("status_mapping_end")
            mapping = [int(x) for x in mapping] if isinstance(mapping, list) else None
            slot_map = resolve_slots(filaments, mapping, used_slots, trays)
            for f in filaments:
                if f.used_g <= 0:
                    continue
                slot = slot_map.get(f.index)
                a = assignments.get(slot) if slot is not None else None
                usages.append(PrintUsage(
                    filament_id=a.filament_id if a else None,
                    slot=slot,
                    filament_index=f.index,
                    material=f.material,
                    color=f.color,
                    grams=U.q2(f.used_g * factor),
                ))
            job.usage_source = "3mf"
        else:
            start = data.get("remain_start") or {}
            end = data.get("remain_end") or {}
            weights = data.get("tray_weight") or {}
            for slot in used_slots or [int(k) for k in start.keys()]:
                s0, s1 = start.get(str(slot), -1), end.get(str(slot), -1)
                w = weights.get(str(slot)) or 1000
                if s0 is None or s1 is None or s0 < 0 or s1 < 0 or s1 >= s0:
                    continue
                a = assignments.get(slot)
                t = (data.get("trays_end") or {}).get(str(slot)) or {}
                usages.append(PrintUsage(
                    filament_id=a.filament_id if a else None,
                    slot=slot,
                    material=t.get("material"),
                    color=t.get("color"),
                    grams=U.q2((s0 - s1) / 100 * w),
                ))
            job.usage_source = "ams_remain" if usages else "none"
            if not usages:
                job.note = (
                    "Geen verbruik bekend: het printbestand kon niet van de printer worden gehaald. "
                    "Voer het verbruik handmatig in."
                )
        return usages

    def recompute(self, job_id: int) -> tuple[bool, str]:
        """Printbestand opnieuw ophalen en het verbruik opnieuw berekenen (alleen als er nog niets is afgeschreven)."""
        with self.session_factory() as db:
            job = db.get(PrintJob, job_id)
            if job is None or job.status == "running":
                return False, "Deze print is nog bezig of bestaat niet"
            if any(u.deducted_at is not None for u in job.usages):
                return False, "Draai eerst de afschrijvingen van deze print terug"
            data = U.job_data(job)
            data.pop("threemf", None)
            U.set_job_data(job, data)
            db.commit()
        ok = self._fetch_3mf(job_id)
        with self.session_factory() as db:
            job = db.get(PrintJob, job_id)
            data = U.job_data(job)
            if not ok:
                return False, data.get("threemf_error") or "Printbestand niet gevonden"
            job.usages.clear()
            db.flush()
            for u in self._compute_usages(db, job, data, job.status, job.progress):
                job.usages.append(u)
            job.total_g = sum((u.grams for u in job.usages), Decimal(0))
            db.commit()
        return True, "Verbruik opnieuw berekend uit het printbestand"

    def _has_3mf(self, job_id: int) -> bool:
        with self.session_factory() as db:
            job = db.get(PrintJob, job_id)
            return bool(job and U.job_data(job).get("threemf"))

    def retry_fetch(self, job_id: int) -> bool:
        return self._fetch_3mf(job_id)

    # ------------------------------------------------------------------ status voor de UI

    def status(self) -> dict[str, Any]:
        s = self.state
        layer, total = s.layer
        return {
            "enabled": self.enabled,
            "configured": self.configured,
            "connected": self.connected,
            "last_error": self.last_error,
            "last_message_at": self.last_message_at.isoformat() if self.last_message_at else None,
            "gcode_state": s.gcode_state,
            "gcode_state_label": GCODE_STATE_LABELS.get(s.gcode_state, s.gcode_state or "—"),
            "progress": s.progress,
            "remaining_min": s.remaining_min,
            "layer": layer,
            "total_layers": total,
            "subtask_name": s.subtask_name,
            "tray_now": s.tray_now,
            "active_job_id": self.active["id"] if self.active else None,
        }


service = BambuService()


def test_mqtt(host: str, serial: str, access_code: str, timeout: float = 10) -> tuple[bool, str]:
    """Probeer kort te verbinden en een statusbericht te ontvangen."""
    import paho.mqtt.client as mqtt

    result: dict[str, Any] = {"ok": False, "msg": "Geen antwoord van de printer (time-out)"}
    done = threading.Event()

    def on_connect(client, userdata, flags, reason_code, properties=None):  # noqa: ANN001
        if getattr(reason_code, "is_failure", False):
            result["msg"] = f"Verbinding geweigerd: {reason_code}"
            done.set()
            return
        client.subscribe(f"device/{serial}/report")
        client.publish(f"device/{serial}/request", json.dumps({"pushing": {"sequence_id": "0", "command": "pushall"}}))
        result["msg"] = "Verbonden, maar nog geen statusbericht ontvangen (klopt het serienummer?)"

    def on_message(client, userdata, msg):  # noqa: ANN001
        try:
            p = json.loads(msg.payload).get("print", {})
        except ValueError:
            p = {}
        if p:
            state = p.get("gcode_state") or "?"
            result.update(ok=True, msg=f"MQTT ok — printerstatus: {GCODE_STATE_LABELS.get(state, state)}")
            done.set()

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"fm-test-{uuid.uuid4().hex[:6]}",
                         protocol=mqtt.MQTTv311)
    client.username_pw_set("bblp", access_code)
    client.tls_set(cert_reqs=ssl.CERT_NONE)
    client.tls_insecure_set(True)
    client.on_connect = on_connect
    client.on_message = on_message
    try:
        client.connect(host, MQTT_PORT, keepalive=30)
    except Exception as exc:  # noqa: BLE001
        return False, f"Kan {host}:{MQTT_PORT} niet bereiken: {exc}"
    client.loop_start()
    done.wait(timeout)
    client.loop_stop()
    try:
        client.disconnect()
    except Exception:  # noqa: BLE001
        pass
    return bool(result["ok"]), str(result["msg"])
