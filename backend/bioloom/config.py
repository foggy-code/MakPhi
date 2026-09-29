"""Configurazione: percorsi dell'installazione e impostazioni utente persistenti."""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from .core.storage import atomic_write_json, read_json

ROOT_DIR = Path(__file__).resolve().parents[2]


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
    return Paths(data=_env_path("BIOLOOM_DATA_DIR", ROOT_DIR / "data"),
                 scratch_dir=Path(scratch).expanduser().resolve() if scratch else None,
                 local_cache_dir=Path(cache).expanduser().resolve() if cache else None)


FRONTEND_DIST = _env_path("BIOLOOM_FRONTEND_DIST", ROOT_DIR / "frontend" / "dist")
MAX_UPLOAD_BYTES = int(os.environ.get("BIOLOOM_MAX_UPLOAD_MB", "50")) * 1024 * 1024


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
        """Impostazioni della 4.1.0: la modalità "auto" (locale, altrimenti SWISS-MODEL se consentito)
        diventa "local" con il fallback su SWISS-MODEL attivo se l'invio era consentito."""
        if isinstance(data, dict) and data.get("qmean_mode") == "auto":
            data = {**data, "qmean_mode": "local"}
            data.setdefault("qmean_fallback_remote", bool(data.get("qmean_remote")))
        return data

    def remote_qmean_allowed(self) -> bool:
        return bool(self.qmean_remote and self.contact_email)

    def effective_workers(self) -> int:
        return self.max_workers or max(1, (os.cpu_count() or 2) - 1)


class SettingsStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        raw = read_json(path, default={}) or {}
        try:
            self._settings = UserSettings(**raw)
        except ValueError:
            self._settings = UserSettings()

    def get(self) -> UserSettings:
        with self._lock:
            return self._settings.model_copy()

    def update(self, changes: dict) -> UserSettings:
        with self._lock:
            merged = {**self._settings.model_dump(), **changes}
            self._settings = UserSettings(**merged)
            atomic_write_json(self._path, self._settings.model_dump())
            return self._settings.model_copy()
