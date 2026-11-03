"""Database dell'app (SQLite in data/studio.db)."""

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

DATA_DIR = Path("data").resolve()
DB_PATH = DATA_DIR / "studio.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS contexts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    brief TEXT DEFAULT '',
    queries TEXT DEFAULT '[]',
    channels TEXT DEFAULT '[]',
    use_agent INTEGER DEFAULT 1,
    auto_freq TEXT DEFAULT 'off',          -- off | daily | weekly
    auto_hour INTEGER DEFAULT 8,
    auto_weekday INTEGER DEFAULT 0,        -- 0 = lunedì
    last_auto_run TEXT
);
CREATE TABLE IF NOT EXISTS analyst_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    context_id INTEGER,
    context_name TEXT,
    week TEXT,                             -- ISO: 2026-W44
    started_at TEXT,
    finished_at TEXT,
    status TEXT,                           -- in_corso | completato | errore
    trigger TEXT,                          -- manuale | automatico
    mode TEXT,                             -- agente | ricerca
    commentary TEXT,
    results TEXT DEFAULT '[]',
    total_analyzed INTEGER DEFAULT 0,
    job_id INTEGER
);
CREATE TABLE IF NOT EXISTS clip_videos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id TEXT UNIQUE,
    url TEXT,
    title TEXT,
    channel TEXT,
    duration INTEGER,
    stats TEXT DEFAULT '{}',
    from_run_id INTEGER,
    added_at TEXT,
    n_clips INTEGER DEFAULT 5,
    min_len INTEGER DEFAULT 20,
    max_len INTEGER DEFAULT 60,
    status TEXT DEFAULT 'in_attesa',       -- in_attesa | in_corso | pronto | errore
    job_dir TEXT,
    last_job_id INTEGER,
    error TEXT
);
CREATE TABLE IF NOT EXISTS clips (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    clip_video_id INTEGER,
    idx INTEGER,
    path TEXT,
    start REAL,
    end REAL,
    score REAL,
    title TEXT,
    hook TEXT,
    description TEXT,
    reason TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS yt_channels (
    channel_id TEXT PRIMARY KEY,
    title TEXT,
    handle TEXT,
    thumbnail TEXT,
    connected INTEGER DEFAULT 0,           -- 1 = autorizzato via OAuth (può caricare)
    added_at TEXT,
    stats_updated_at TEXT
);
CREATE TABLE IF NOT EXISTS uploads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    clip_id INTEGER,
    channel_id TEXT,
    youtube_id TEXT,
    privacy TEXT,
    status TEXT,                           -- in_corso | caricato | errore
    error TEXT,
    uploaded_at TEXT,
    job_id INTEGER
);
CREATE TABLE IF NOT EXISTS trainer_shorts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT UNIQUE,
    video_id TEXT,
    title TEXT,
    channel TEXT,
    views INTEGER,
    likes INTEGER,
    duration INTEGER,
    hook TEXT,
    transcript TEXT,
    notes TEXT,
    active INTEGER DEFAULT 1,
    status TEXT DEFAULT 'in_attesa',       -- in_attesa | in_corso | pronto | errore
    error TEXT,
    added_at TEXT
);
CREATE TABLE IF NOT EXISTS channel_videos (
    channel_id TEXT,
    video_id TEXT,
    title TEXT,
    published TEXT,
    duration INTEGER,
    views INTEGER,
    likes INTEGER,
    comments INTEGER,
    avg_view_pct REAL,                     -- retention ufficiale (solo canali collegati) o stimata
    avg_view_pct_source TEXT,              -- youtube | stima
    avg_view_duration REAL,
    is_short INTEGER,
    fetched_at TEXT,
    PRIMARY KEY (channel_id, video_id)
);
CREATE TABLE IF NOT EXISTS stat_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT,
    scope TEXT,
    content TEXT,
    status TEXT,
    job_id INTEGER
);
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT,
    label TEXT,
    status TEXT,                           -- in_coda | in_corso | completato | errore
    log TEXT DEFAULT '',
    error TEXT,
    created_at TEXT,
    finished_at TEXT
);
"""

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None


def connect() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.executescript(SCHEMA)
        _migrate(_conn)
        _conn.commit()
    return _conn


def reset_interrupted() -> None:
    """All'avvio dell'app: i lavori rimasti "in corso" da una chiusura precedente non finiranno mai.

    Va chiamata solo dal server all'avvio, non da altri processi che leggono il database.
    """
    with tx() as _conn:
        # Lavori rimasti "in corso" da una chiusura precedente non verranno mai completati
        _conn.execute("UPDATE jobs SET status='errore', error='interrotto alla chiusura dell''app' "
                      "WHERE status IN ('in_coda','in_corso')")
        _conn.execute("UPDATE analyst_runs SET status='errore' WHERE status='in_corso'")
        _conn.execute("UPDATE clip_videos SET status='errore', error='interrotto' WHERE status='in_corso'")
        _conn.execute("UPDATE trainer_shorts SET status='errore', error='interrotto' WHERE status='in_corso'")
        _conn.execute("UPDATE uploads SET status='errore', error='interrotto' WHERE status='in_corso'")
        _conn.execute("UPDATE stat_reports SET status='errore' WHERE status='in_corso'")
        _conn.execute("UPDATE clips SET status='errore' WHERE status='in_corso'")


MIGRATIONS = [
    "ALTER TABLE clip_videos ADD COLUMN instructions TEXT DEFAULT ''",
    "ALTER TABLE clips ADD COLUMN status TEXT DEFAULT 'pronta'",  # pronta / in_corso / errore
    "ALTER TABLE clips ADD COLUMN error TEXT",
    "ALTER TABLE clips ADD COLUMN version INTEGER DEFAULT 1",
]


def _migrate(conn: sqlite3.Connection) -> None:
    """Aggiunge le colonne nuove ai database creati da versioni precedenti dell'app."""
    for sql in MIGRATIONS:
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass  # colonna già presente


@contextmanager
def tx():
    with _lock:
        conn = connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def query(sql: str, params: tuple = ()) -> list[dict]:
    with _lock:
        return [dict(r) for r in connect().execute(sql, params).fetchall()]


def one(sql: str, params: tuple = ()) -> dict | None:
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql: str, params: tuple = ()) -> int:
    with tx() as conn:
        return conn.execute(sql, params).lastrowid


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def loads(value: str | None, default=None):
    if not value:
        return default
    return json.loads(value)


def dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False)
