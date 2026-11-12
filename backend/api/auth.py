"""Loopback-only API guarded by a per-run random token.

The token and port are written to <runtime_dir>/backend.json; only the Electron main process reads
it and attaches the token, so neither renderer pages nor other local websites can call the API.
"""

import json
import os
import secrets
from pathlib import Path

from fastapi import Header, HTTPException, Request, status

ENDPOINT_FILE = "backend.json"


def new_token() -> str:
    return secrets.token_urlsafe(32)


def write_endpoint_file(directory: Path, port: int, token: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / ENDPOINT_FILE
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"port": port, "token": token, "pid": os.getpid()}), encoding="utf-8")
    os.replace(tmp, path)
    return path


def remove_endpoint_file(path: Path, token: str) -> None:
    """Only removes the file if it still belongs to this run (a newer instance may have replaced it)."""
    try:
        if json.loads(path.read_text(encoding="utf-8")).get("token") == token:
            path.unlink()
    except (OSError, ValueError):
        pass


def require_token(request: Request, authorization: str | None = Header(default=None)) -> None:
    expected: str = request.app.state.token
    scheme, _, value = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not secrets.compare_digest(value.encode(), expected.encode()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "unauthorized")
