"""
Pequeño gestor de locks por sitio/operación para evitar concurrencia
entre scraping y predicciones. Usa lockfiles en disco y limpia locks
obsoletos para resiliencia ante cierres abruptos.
"""

import os
import json
import time
from pathlib import Path
from contextlib import contextmanager

from config import DATA_DIR

LOCKS_DIR = Path(DATA_DIR) / "locks"
LOCKS_DIR.mkdir(parents=True, exist_ok=True)


def _lock_path(site: str, operation: str) -> Path:
    safe_site = site.replace(".", "_").replace("/", "_")
    safe_op = operation.replace("/", "_")
    return LOCKS_DIR / f"{safe_site}_{safe_op}.lock"


@contextmanager
def site_operation_lock(
    site: str,
    operation: str,
    timeout_seconds: int = 30,
    stale_seconds: int = 60 * 60 * 4,
    poll_seconds: float = 1.0,
):
    """
    Adquiere un lock por sitio/operación. Si ya existe, espera hasta timeout;
    si es obsoleto, lo elimina. Lanza RuntimeError si no puede adquirir.
    """
    path = _lock_path(site, operation)
    start = time.time()
    pid = os.getpid()
    owns = False
    try:
        while True:
            try:
                fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump({"pid": pid, "created_at": time.time()}, f)
                owns = True
                break
            except FileExistsError:
                # Si el lock es viejo, eliminarlo
                try:
                    mtime = path.stat().st_mtime
                    if (time.time() - mtime) > stale_seconds:
                        path.unlink(missing_ok=True)
                        continue
                except FileNotFoundError:
                    continue
                if (time.time() - start) > timeout_seconds:
                    raise RuntimeError(
                        f"Operación concurrente en curso para '{site}' ({operation}). Intenta de nuevo en unos segundos."
                    )
                time.sleep(poll_seconds)
        yield
    finally:
        if owns:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass


def is_operation_locked(site: str, operation: str, stale_seconds: int = 60 * 60 * 4) -> bool:
    """
    Verifica si existe un lock activo para un sitio/operación.
    Elimina locks obsoletos.
    """
    path = _lock_path(site, operation)
    if not path.exists():
        return False
    try:
        age = time.time() - path.stat().st_mtime
        if age > stale_seconds:
            path.unlink(missing_ok=True)
            return False
        return True
    except FileNotFoundError:
        return False

