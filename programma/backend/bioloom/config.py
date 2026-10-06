"""Configurazione: percorsi dell'installazione e impostazioni utente persistenti."""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from .core.storage import atomic_write_json, read_json

ROOT_DIR = Path(__file__).resolve().parents[2]   # programma/ (in Docker /app); i dati stanno accanto, in ../data


def _env_path(name: str, default: Path) -> Path:
    value = os.environ.get(name)
    return Path(value).expanduser().resolve() if value else default


@dataclass(frozen=True)
class Paths:
    """`data` è persistente (anche su chiavetta, lenta); `scratch` è l'area di lavoro veloce dei calcoli."""

    data: Path
    scratch_dir: Path | None = None
    local_cache_dir: Path | None = None

    @property
    def scratch(self) -> Path:
        return self.scratch_dir or self.data / ".scratch"

    @property
    def local_cache(self) -> Path:
        """Dati derivati e ricostruibili, locali a ogni computer (es. indice MMseqs2): non viaggiano con i dati."""
        return self.local_cache_dir or self.data / ".localcache"

    @property
    def scratch_tmp(self) -> Path:
        return self.scratch / "tmp"

    @property
    def licenses(self) -> Path:
        return self.data / "licenses"

    @property
    def db(self) -> Path:
        return self.data / "bioloom.db"

    @property
    def jobs(self) -> Path:
        return self.data / "jobs"

    @property
    def templates(self) -> Path:
        return self.data / "templates"

    @property
    def uploads(self) -> Path:
        return self.data / "uploads"

    @property
    def cache(self) -> Path:
        return self.data / "cache"

    @property
    def compute_cache(self) -> Path:
        """Risultati di ricerche e allineamenti (core/cache.py), dentro `cache`: stessa pulizia automatica."""
        return self.cache / "compute"

    @property
    def databases(self) -> Path:
        return self.data / "databases"

    @property
    def tools(self) -> Path:
        return self.data / "tools"

    @property
    def settings_file(self) -> Path:
        return self.data / "settings.json"

    def ensure(self) -> None:
        for p in (self.data, self.jobs, self.templates, self.uploads, self.cache, self.databases, self.tools,
                  self.licenses, self.scratch, self.scratch_tmp, self.local_cache):
            p.mkdir(parents=True, exist_ok=True)


def default_paths() -> Paths:
    scratch = os.environ.get("BIOLOOM_SCRATCH_DIR")
    cache = os.environ.get("BIOLOOM_CACHE_DIR")
    return Paths(data=_env_path("BIOLOOM_DATA_DIR", ROOT_DIR.parent / "data"),
                 scratch_dir=Path(scratch).expanduser().resolve() if scratch else None,
                 local_cache_dir=Path(cache).expanduser().resolve() if cache else None)


FRONTEND_DIST = _env_path("BIOLOOM_FRONTEND_DIST", ROOT_DIR / "frontend" / "dist")
MAX_UPLOAD_BYTES = int(os.environ.get("BIOLOOM_MAX_UPLOAD_MB", "50")) * 1024 * 1024


def _url_like(value: str) -> bool:
    """Forma minima di un URL http(s) senza spazi; la verifica completa è in engines/remote.validate_esmfold_url."""
    return value.lower().startswith(("http://", "https://")) and len(value) > len("https://") and \
        not any(c.isspace() for c in value)


class UserSettings(BaseModel):
    """Impostazioni modificabili dall'interfaccia (salvate in data/settings.json)."""

    contact_email: str = Field("", description="Email inviata a NCBI e SWISS-MODEL come contatto")
    modeller_python: str = Field("", description="Interprete Python con MODELLER; vuoto = rilevamento automatico")
    max_workers: int = Field(0, ge=0, le=64, description="Job di calcolo in parallelo; 0 = core-1")
    job_timeout_minutes: int = Field(120, ge=1, le=24 * 60)
    qmean_remote: bool = Field(False, description="Consente l'invio dei modelli a SWISS-MODEL per QMEAN")
    qmean_mode: Literal["local", "remote"] = Field(
        "local", description="Dove calcolare QMEAN: locale (UniClust30 sul disco esterno) o SWISS-MODEL")
    qmean_fallback_remote: bool = Field(
        False, description="Se QMEAN locale non è disponibile o fallisce, ripiega su SWISS-MODEL "
                           "(richiede il consenso all'invio e l'email)")
    qmean_uniclust: str = Field("", max_length=300,
                                description="Versione di UniClust30/UniRef30 da usare; vuoto = la più recente pronta")
    qmean_timeout_minutes: int = Field(60, ge=5, le=24 * 60)
    default_search_engine: Literal["rcsb", "ncbi", "local", "mmseqs"] = "rcsb"
    keep_intermediates: bool = Field(False, description="Conserva i file intermedi di MODELLER nei risultati")
    uploads_retention_days: int = Field(30, ge=0, le=3650, description="Giorni di conservazione degli upload; 0 = sempre")
    compute_cache: bool = Field(True, description="Riusa i risultati già calcolati di ricerche di template e "
                                                  "allineamenti multipli e strutturali (cache dei calcoli)")
    # ESMFold (F9.3): la sequenza lascia il computer, quindi solo con il consenso esplicito (spento per default)
    esmfold_remote: bool = Field(False, description="Consente l'invio delle sequenze al servizio ESMFold per la "
                                                    "predizione di struttura usata come controllo di plausibilità")
    esmfold_url: str = Field("", max_length=300, description="Indirizzo del servizio ESMFold (POST della sequenza, "
                                                             "risposta PDB); vuoto = API pubblica di ESM Atlas")

    @field_validator("esmfold_url")
    @classmethod
    def _esmfold_url(cls, value: str) -> str:
        value = value.strip()
        if value and not _url_like(value):
            raise ValueError("indirizzo del servizio ESMFold non valido (atteso un URL http o https)")
        return value

    @field_validator("contact_email")
    @classmethod
    def _email(cls, value: str) -> str:
        value = value.strip()
        if value and ("@" not in value or " " in value or len(value) > 200):
            raise ValueError("indirizzo email non valido")
        return value

    @field_validator("modeller_python", "qmean_uniclust")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip().strip('"')

    @model_validator(mode="before")
    @classmethod
    def _migrate(cls, data):
        """Impostazioni delle versioni precedenti.

        - 4.1.0: la modalità "auto" (locale, altrimenti SWISS-MODEL se consentito) diventa "local" con il
          fallback su SWISS-MODEL attivo se l'invio era consentito.
        - Cache dei calcoli: le impostazioni precedenti non hanno `compute_cache` e prendono il predefinito
          (acceso); un valore nullo (file modificato a mano) fa lo stesso invece di azzerare tutte le impostazioni.
        - ESMFold (F9.3): le impostazioni precedenti non hanno `esmfold_remote` né `esmfold_url` e prendono i
          predefiniti (invio spento, servizio pubblico). Il consenso vale solo come vero booleano: un valore diverso
          (nullo, testo, numero in un file modificato a mano) torna spento invece di essere interpretato; un indirizzo
          non valido torna vuoto. Così un file con un valore strano non azzera tutte le impostazioni.
        """
        if isinstance(data, dict) and data.get("qmean_mode") == "auto":
            data = {**data, "qmean_mode": "local"}
            data.setdefault("qmean_fallback_remote", bool(data.get("qmean_remote")))
        if isinstance(data, dict) and "compute_cache" in data and data["compute_cache"] is None:
            data = {k: v for k, v in data.items() if k != "compute_cache"}
        if isinstance(data, dict) and "esmfold_remote" in data and not isinstance(data["esmfold_remote"], bool):
            data = {k: v for k, v in data.items() if k != "esmfold_remote"}
        if isinstance(data, dict) and "esmfold_url" in data:
            url = data["esmfold_url"]
            if not isinstance(url, str) or len(url.strip()) > 300 or (url.strip() and not _url_like(url.strip())):
                data = {k: v for k, v in data.items() if k != "esmfold_url"}
        return data

    def remote_qmean_allowed(self) -> bool:
        return bool(self.qmean_remote and self.contact_email)

    def effective_workers(self) -> int:
        return self.max_workers or max(1, (os.cpu_count() or 2) - 1)


def server_esmfold_url() -> str:
    """Indirizzo di ESMFold scelto dall'amministratore per la modalità server (vuoto = API pubblica di ESM Atlas)."""
    return os.environ.get("BIOLOOM_ESMFOLD_URL", "").strip()


class SettingsStore:
    """Impostazioni salvate in settings.json.

    In modalità server (`server`, accesso con chiave: BIOLOOM_AUTH=on) l'indirizzo di ESMFold è quello
    dell'amministratore (`server_esmfold_url`), mai quello salvato: è l'unico indirizzo a cui il server invia richieste
    che si potrebbe scegliere dall'interfaccia, e chi ha la chiave d'accesso non deve poterlo puntare sulla rete interna
    del server. Il valore salvato (anche da una versione precedente) resta nel file e vale di nuovo in locale.
    """

    def __init__(self, path: Path, *, server: bool = False) -> None:
        self._path = path
        self._lock = threading.Lock()
        self.server = server
        raw = read_json(path, default={}) or {}
        try:
            self._settings = UserSettings(**raw)
        except ValueError:
            self._settings = UserSettings()

    def _effective(self) -> UserSettings:
        if self.server:
            return self._settings.model_copy(update={"esmfold_url": server_esmfold_url()})
        return self._settings.model_copy()

    def get(self) -> UserSettings:
        with self._lock:
            return self._effective()

    def update(self, changes: dict) -> UserSettings:
        with self._lock:
            merged = {**self._settings.model_dump(), **changes}
            self._settings = UserSettings(**merged)
            atomic_write_json(self._path, self._settings.model_dump())
            return self._effective()
