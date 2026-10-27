"""Limite della dimensione del corpo delle richieste, prima che Starlette lo legga.

I limiti dei singoli caricamenti (api/deps.read_upload, pacchetti di riproducibilità) valgono quando l'endpoint legge
il file, cioè dopo che Starlette ha già scritto tutto il multipart su disco: un corpo di qualche GB riempirebbe la
cartella temporanea prima del 413. Questo middleware rifiuta subito i corpi oltre il più grande caricamento
legittimo dell'app:

- con Content-Length dichiarato oltre il limite, senza leggere un byte del corpo;
- senza Content-Length (corpo a blocchi, chunked), contando i byte mentre l'app li legge: oltre il limite smette di
  passarli e risponde 413 al posto della risposta dell'app (FastAPI trasformerebbe il corpo interrotto in un 400).

Il limite è il più grande fra quelli esistenti (`request_limit`) più un margine per l'intestazione multipart e i campi
del modulo: un caricamento accettato oggi dall'endpoint non viene mai fermato qui.
"""

from __future__ import annotations

from typing import Optional

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

MB = 1024 * 1024
# confini, intestazioni delle parti e campi di testo del modulo (project_id, template_id, ...) oltre al file
MULTIPART_MARGIN = 1 * MB


def request_limit() -> int:
    """Corpo più grande accettato: il più grande dei limiti dei caricamenti più il margine del multipart.

    Oggi è il pacchetto di riproducibilità (256 MB, domain/reproducibility.MAX_PACKAGE_BYTES); gli upload di strutture
    e template (MAKPHI_MAX_UPLOAD_MB, 50 MB predefiniti) contano se la variabile li porta oltre. Le sequenze (20 MB)
    e i corpi JSON restano sotto.
    """
    from ..config import MAX_UPLOAD_BYTES
    from ..domain.fileformats import MAX_BYTES as SEQUENCE_FILE_BYTES
    from ..domain.reproducibility import MAX_PACKAGE_BYTES

    return max(MAX_UPLOAD_BYTES, MAX_PACKAGE_BYTES, SEQUENCE_FILE_BYTES) + MULTIPART_MARGIN


class _TooLarge(Exception):
    """Interrompe la lettura del corpo nell'app (dal `receive` contato)."""


def _content_length(scope: Scope) -> Optional[int]:
    for name, value in scope.get("headers") or ():
        if name == b"content-length":
            try:
                return int(value)
            except ValueError:  # intestazione non valida: la rifiuta già il server HTTP
                return None
    return None


class BodyLimit:
    """Middleware ASGI puro (come core/guard): nessun livello in più sulle risposte in streaming."""

    def __init__(self, app: ASGIApp, limit: int) -> None:
        self.app = app
        self.limit = int(limit)

    def refusal(self) -> JSONResponse:
        # Connection: close, perché il resto del corpo non si legge: il server chiude invece di aspettarlo
        size = f"{self.limit // MB} MB" if self.limit >= MB else f"{self.limit // 1024} kB"
        message = f"Richiesta troppo grande: MakPhi accetta al massimo {size} per richiesta."
        return JSONResponse(status_code=413, headers={"connection": "close"},
                            content={"error": {"code": "too_large", "message": message}})

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        declared = _content_length(scope)
        if declared is not None and declared > self.limit:
            await self.refusal()(scope, receive, send)  # il corpo non si legge
            return

        received = 0
        exceeded = False
        started = False

        async def counted_receive() -> Message:
            nonlocal received, exceeded
            if exceeded:
                raise _TooLarge()
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.limit:
                    exceeded = True
                    raise _TooLarge()
            return message

        async def tracked_send(message: Message) -> None:
            nonlocal started
            if exceeded and not started:
                return  # la risposta dell'app al corpo interrotto (il 400 di FastAPI) lascia il posto al 413
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, counted_receive, tracked_send)
        except Exception:
            if not exceeded:
                raise
        if exceeded and not started:
            await self.refusal()(scope, receive, send)
