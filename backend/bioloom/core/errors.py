"""Errori applicativi con codice e stato HTTP, serializzati in modo uniforme dall'API."""

from __future__ import annotations

from typing import Any, Optional


class AppError(Exception):
    status = 400
    code = "bad_request"

    def __init__(self, message: str, *, code: Optional[str] = None, status: Optional[int] = None,
                 details: Any = None) -> None:
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        if status:
            self.status = status
        self.details = details

    def to_dict(self) -> dict:
        payload = {"code": self.code, "message": self.message}
        if self.details is not None:
            payload["details"] = self.details
        return payload


class NotFound(AppError):
    status = 404
    code = "not_found"


class Conflict(AppError):
    status = 409
    code = "conflict"


class ToolUnavailable(AppError):
    """Un motore esterno (MODELLER, DSSP, BLAST+...) non è installato o non risponde."""

    status = 503
    code = "tool_unavailable"


class RemoteServiceError(AppError):
    """Errore di un servizio remoto (RCSB, UniProt, NCBI, SWISS-MODEL)."""

    status = 502
    code = "remote_error"
