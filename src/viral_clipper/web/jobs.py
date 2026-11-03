"""Lavori in background (analisi, clip, trainer, upload, report) con log visibile nell'interfaccia.

I moduli della pipeline scrivono i progressi con print(): un sys.stdout per-thread
li indirizza nel log del lavoro che li ha generati.
"""

import sys
import threading
import traceback
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from . import db

_local = threading.local()
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="job")


class _JobStdout:
    def __init__(self, original):
        self.original = original

    def write(self, text: str) -> int:
        job_id = getattr(_local, "job_id", None)
        if job_id is None:
            return self.original.write(text)
        append_log(job_id, text)
        return len(text)

    def flush(self) -> None:
        self.original.flush()

    def __getattr__(self, name):
        return getattr(self.original, name)


def install_stdout() -> None:
    if not isinstance(sys.stdout, _JobStdout):
        sys.stdout = _JobStdout(sys.stdout)


def append_log(job_id: int, text: str) -> None:
    if not text:
        return
    with db.tx() as conn:
        conn.execute("UPDATE jobs SET log = substr(log || ?, -20000) WHERE id = ?", (text, job_id))


def submit(kind: str, label: str, fn: Callable[[int], None]) -> int:
    """Mette in coda fn(job_id). Restituisce l'id del lavoro."""
    job_id = db.execute(
        "INSERT INTO jobs (kind, label, status, created_at) VALUES (?, ?, 'in_coda', ?)",
        (kind, label, db.now()),
    )
    _pool.submit(_run, job_id, fn)
    return job_id


def _run(job_id: int, fn: Callable[[int], None]) -> None:
    _local.job_id = job_id
    db.execute("UPDATE jobs SET status='in_corso' WHERE id=?", (job_id,))
    try:
        fn(job_id)
        db.execute("UPDATE jobs SET status='completato', finished_at=? WHERE id=?", (db.now(), job_id))
    except Exception as e:  # noqa: BLE001 — qualsiasi errore va mostrato all'utente nel pannello lavori
        append_log(job_id, "\n" + "".join(traceback.format_exception_only(type(e), e)))
        db.execute(
            "UPDATE jobs SET status='errore', error=?, finished_at=? WHERE id=?",
            (str(e)[:2000], db.now(), job_id),
        )
    finally:
        _local.job_id = None
