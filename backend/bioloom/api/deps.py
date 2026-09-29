"""Dipendenze condivise dalle rotte."""

from __future__ import annotations

from fastapi import Request, UploadFile

from ..config import MAX_UPLOAD_BYTES
from ..core.container import Core
from ..core.errors import AppError


def get_core(request: Request) -> Core:
    return request.app.state.core


async def read_upload(file: UploadFile, limit: int = MAX_UPLOAD_BYTES) -> bytes:
    chunks, size = [], 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        size += len(chunk)
        if size > limit:
            raise AppError(f"File troppo grande: massimo {limit // (1024 * 1024)} MB.", status=413)
        chunks.append(chunk)
    return b"".join(chunks)


def is_loopback(request: Request) -> bool:
    host = request.client.host if request.client else ""
    return host in ("127.0.0.1", "::1", "localhost", "testclient")
