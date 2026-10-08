"""Licenze dei programmi esterni: chiave MODELLER, file LicenseDB di ProSA II, stato di PROCHECK.

Le licenze stanno in `data/licenses/` (permessi 0600) e quindi viaggiano con la cartella dati:
chiavetta, cartella del server o volume. Non entrano mai nell'immagine Docker né nelle risposte API
(la chiave è sempre mascherata).

- MODELLER: `modeller.key`, in alternativa la variabile d'ambiente MODELLER_KEY (server). La chiave
  viene passata ai sottoprocessi MODELLER con BIOLOOM_MODELLER_KEY e iniettata dal preambolo degli
  script (engines.tools.LICENSE_PREAMBLE), senza modificare l'installazione di MODELLER.
- ProSA II 2003: `prosa/LicenseDB` (righe "nome scadenza MAC codice"). La licenza è legata al MAC
  della scheda di rete: l'avviatore imposta quel MAC sul container.
"""

from __future__ import annotations

import os
import re
import threading
from pathlib import Path
from typing import Callable, Optional

from ..core.errors import AppError
from ..core.jobs import set_subprocess_env
from ..core.storage import atomic_write_bytes
from ..engines import tools

_KEY = re.compile(r"^[A-Za-z0-9_\-]{4,64}$")
_MAC_FIELD = re.compile(r"^[0-9A-Fa-f]{12}$")


def mask(key: str) -> str:
    if not key:
        return ""
    return key[:3] + "•" * max(3, len(key) - 5) + key[-2:] if len(key) > 6 else "•" * len(key)


def format_mac(raw: str) -> str:
    clean = re.sub(r"[^0-9A-Fa-f]", "", raw).lower()
    return ":".join(clean[i:i + 2] for i in range(0, 12, 2)) if len(clean) == 12 else ""


def parse_prosa_license(text: str) -> list[dict]:
    """Righe valide di LicenseDB: programma, scadenza, MAC (12 cifre esadecimali), codice."""
    entries = []
    for line in text.splitlines():
        fields = line.split()
        if not fields:
            continue
        if len(fields) < 4 or not _MAC_FIELD.match(fields[2]):
            raise AppError("File LicenseDB non valido: ogni riga deve avere programma, scadenza, MAC e codice.",
                           code="invalid_license")
        entries.append({"name": fields[0], "expires": fields[1], "mac": format_mac(fields[2])})
    if not entries:
        raise AppError("Il file LicenseDB è vuoto.", code="invalid_license")
    return entries


def container_mac() -> Optional[str]:
    """MAC della scheda di rete del container (Linux); None fuori da Linux."""
    for iface in ("eth0", "ens3", "enp0s3"):
        path = Path("/sys/class/net") / iface / "address"
        try:
            return path.read_text().strip().lower()
        except OSError:
            continue
    return None


def _write_private(path: Path, data: bytes) -> None:
    atomic_write_bytes(path, data)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


class LicenseService:
    def __init__(self, directory: Path, settings_provider: Callable) -> None:
        self.dir = directory
        self.settings_provider = settings_provider
        self.extra_secrets: Optional[Callable[[], list[str]]] = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ MODELLER
    @property
    def _modeller_file(self) -> Path:
        return self.dir / "modeller.key"

    def modeller_key(self) -> tuple[str, Optional[str]]:
        try:
            key = self._modeller_file.read_text(encoding="utf-8").strip()
            if key:
                return key, "file"
        except OSError:
            pass
        env = os.environ.get("MODELLER_KEY", "").strip()
        return (env, "env") if env else ("", None)

    def apply(self) -> None:
        """Rende attive le licenze per i sottoprocessi (all'avvio e dopo ogni modifica)."""
        key, _source = self.modeller_key()
        set_subprocess_env("BIOLOOM_MODELLER_KEY", key or None)
        tools.invalidate_modeller_cache()

    def set_modeller_key(self, key: str) -> dict:
        key = (key or "").strip().strip("'\"")
        if not _KEY.match(key):
            raise AppError("Chiave MODELLER non valida: usa solo lettere e cifre (es. MODELXXXXX).",
                           code="invalid_license", status=422)
        with self._lock:
            probe = tools.detect_modeller(self.settings_provider().modeller_python, license_key=key)
            if not probe["available"]:
                # MODELLER ripete la chiave nel messaggio d'errore: non va restituita in chiaro
                attempts = [{**a, "error": (a.get("error") or "").replace(key, mask(key))}
                            for a in probe.get("attempts", [])]
                message = next((a["error"] for a in attempts if a["error"]), "")
                raise AppError("MODELLER ha rifiutato la chiave: " + (message or "licenza non valida."),
                               code="invalid_license", status=422, details=attempts)
            _write_private(self._modeller_file, (key + "\n").encode("utf-8"))
            self.apply()
        return self.status()

    def remove_modeller_key(self) -> dict:
        with self._lock:
            self._modeller_file.unlink(missing_ok=True)
            self.apply()
        return self.status()

    # ------------------------------------------------------------------ ProSA
    @property
    def _prosa_file(self) -> Path:
        return self.dir / "prosa" / "LicenseDB"

    def set_prosa_license(self, data: bytes) -> dict:
        try:
            text = data.decode("ascii")
        except UnicodeDecodeError:
            raise AppError("Il file LicenseDB deve essere un file di testo.", code="invalid_license")
        parse_prosa_license(text)
        with self._lock:
            _write_private(self._prosa_file, data)
        return self.status()

    def remove_prosa_license(self) -> dict:
        with self._lock:
            self._prosa_file.unlink(missing_ok=True)
        return self.status()

    def prosa_status(self) -> dict:
        entries: list[dict] = []
        error = None
        if self._prosa_file.exists():
            try:
                entries = parse_prosa_license(self._prosa_file.read_text(encoding="ascii", errors="replace"))
            except AppError as exc:
                error = exc.message
        current = container_mac()
        required = entries[0]["mac"] if entries else None
        return {
            "installed": tools.find("prosa") is not None,
            "license": bool(entries),
            "error": error,
            "program": entries[0]["name"] if entries else None,
            "expires": entries[0]["expires"] if entries else None,
            "mac_required": required,
            "mac_current": current,
            "mac_ok": bool(required and current and required == current),
        }

    # ------------------------------------------------------------------ segreti
    def secrets(self) -> list[str]:
        """Valori da non esportare mai (pacchetti di riproducibilità, F9.4): chiave MODELLER (file e ambiente) e
        codici delle licenze di ProSA. Solo per il codice che li oscura: mai in una risposta né nel log."""
        out = [self.modeller_key()[0], os.environ.get("MODELLER_KEY", "").strip()]
        try:
            text = self._prosa_file.read_text(encoding="ascii", errors="replace")
        except OSError:
            text = ""
        out += [fields[3] for fields in (line.split() for line in text.splitlines()) if len(fields) >= 4]
        if self.extra_secrets is not None:  # password della posta (services/notify.py)
            out += self.extra_secrets()
        return [s for s in dict.fromkeys(out) if s]

    # ------------------------------------------------------------------ riepilogo
    def status(self) -> dict:
        key, source = self.modeller_key()
        info = tools.detect_modeller(self.settings_provider().modeller_python)
        message = None
        if not info["available"]:
            message = next((a.get("error") for a in info.get("attempts", []) if a.get("error")), None)
            if message and key:
                message = message.replace(key, mask(key))
        return {
            "modeller": {
                "configured": bool(key),
                "source": source,
                "masked": mask(key),
                "available": info["available"],
                "version": info.get("version"),
                "message": message,
            },
            "prosa": self.prosa_status(),
            "procheck": {"installed": tools.find("procheck") is not None},
        }
