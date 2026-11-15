"""Bestanden ophalen van de printer via impliciete FTPS (poort 990, gebruiker bblp, wachtwoord = toegangscode)."""

from __future__ import annotations

import ftplib
import io
import logging
import os
import socket
import ssl

log = logging.getLogger(__name__)

FTP_PORT = 990


class ImplicitFTP_TLS(ftplib.FTP_TLS):
    """FTP_TLS met impliciete TLS en hergebruik van de TLS-sessie (vereist door de Bambu-printers)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._sock = None

    @property
    def sock(self):  # type: ignore[override]
        return self._sock

    @sock.setter
    def sock(self, value):
        if value is not None and not isinstance(value, ssl.SSLSocket):
            value = self.context.wrap_socket(value, server_hostname=self.host)
        self._sock = value

    def ntransfercmd(self, cmd, rest=None):
        conn, size = ftplib.FTP.ntransfercmd(self, cmd, rest)
        if self._prot_p:
            conn = self.context.wrap_socket(conn, server_hostname=self.host, session=self.sock.session)
        return conn, size


def _context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    # De printer gebruikt een eigen (Bambu) certificaat dat niet op naam/IP valideert
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def connect(host: str, access_code: str, timeout: float = 15) -> ImplicitFTP_TLS:
    ftp = ImplicitFTP_TLS(context=_context(), timeout=timeout)
    ftp.connect(host=host, port=FTP_PORT, timeout=timeout)
    ftp.login("bblp", access_code)
    ftp.prot_p()
    return ftp


def _list(ftp: ftplib.FTP, path: str) -> list[str]:
    try:
        return ftp.nlst(path)
    except (ftplib.error_perm, ftplib.error_temp, OSError):
        return []


def candidate_names(subtask_name: str | None, gcode_file: str | None, url: str | None) -> list[str]:
    names: list[str] = []

    def add(n: str | None) -> None:
        if n:
            n = n.strip()
            if n and n not in names:
                names.append(n)

    if url:
        for prefix in ("ftp://", "file://"):
            if url.startswith(prefix):
                add(url[len(prefix):].lstrip("/").split("/")[-1])
    if gcode_file and gcode_file.lower().endswith(".3mf"):
        add(os.path.basename(gcode_file))
    if subtask_name:
        add(f"{subtask_name}.gcode.3mf")
        add(f"{subtask_name}.3mf")
    return names


def fetch_project_file(
    host: str,
    access_code: str,
    subtask_name: str | None = None,
    gcode_file: str | None = None,
    url: str | None = None,
    max_bytes: int = 200 * 1024 * 1024,
) -> tuple[bytes, str] | None:
    """Zoek het printbestand van de huidige/laatste print en download het. Geeft (inhoud, pad) of None."""
    names = candidate_names(subtask_name, gcode_file, url)
    dirs = ["/", "/cache", "/model", "/sdcard"]
    try:
        ftp = connect(host, access_code)
    except (OSError, ftplib.Error, ssl.SSLError) as exc:
        log.warning("FTPS-verbinding met %s mislukt: %s", host, exc)
        return None
    try:
        # 1. Directe namen proberen
        tried: list[str] = []
        listings = {d: _list(ftp, d) for d in dirs}
        for d, entries in listings.items():
            base_entries = {os.path.basename(e): e for e in entries}
            for n in names:
                if n in base_entries:
                    tried.append(f"{d.rstrip('/')}/{n}")
        # 2. Anders: .3mf-bestanden waarvan de naam de taaknaam bevat
        if not tried and subtask_name:
            key = subtask_name.lower()
            for d, entries in listings.items():
                for e in entries:
                    b = os.path.basename(e)
                    if b.lower().endswith(".3mf") and key in b.lower():
                        tried.append(f"{d.rstrip('/')}/{b}")
        for path in tried:
            buf = io.BytesIO()
            try:
                ftp.retrbinary(f"RETR {path}", buf.write)
            except (ftplib.Error, OSError) as exc:
                log.info("Kon %s niet downloaden: %s", path, exc)
                continue
            if buf.tell() > max_bytes:
                continue
            return buf.getvalue(), path
        log.info("Printbestand niet gevonden op de printer (gezocht: %s)", names)
        return None
    finally:
        try:
            ftp.quit()
        except Exception:  # noqa: BLE001
            try:
                ftp.close()
            except Exception:  # noqa: BLE001
                pass


def test_connection(host: str, access_code: str) -> tuple[bool, str]:
    try:
        ftp = connect(host, access_code, timeout=8)
    except (OSError, ftplib.Error, ssl.SSLError, socket.timeout) as exc:
        return False, str(exc) or exc.__class__.__name__
    try:
        n = len(_list(ftp, "/"))
        return True, f"FTPS ok ({n} items in de hoofdmap)"
    finally:
        try:
            ftp.quit()
        except Exception:  # noqa: BLE001
            pass
