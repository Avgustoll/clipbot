"""Storico delle rilevazioni (SQLite).

YouTube mostra solo il totale attuale delle views, non la loro evoluzione nel tempo.
Per sapere come sta andando un video nelle ultime 3 settimane salviamo una "fotografia"
(snapshot) a ogni analisi: con più rilevazioni a giorni di distanza si ricava il trend reale.
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .youtube import VideoStats

SCHEMA = """
CREATE TABLE IF NOT EXISTS videos (
    id TEXT PRIMARY KEY, title TEXT, channel TEXT, duration INTEGER,
    upload_date TEXT, first_seen TEXT
);
CREATE TABLE IF NOT EXISTS snapshots (
    video_id TEXT, taken_at TEXT, views INTEGER, likes INTEGER, comments INTEGER,
    PRIMARY KEY (video_id, taken_at)
);
"""


@dataclass
class Snapshot:
    taken_at: datetime
    views: int
    likes: int | None
    comments: int | None


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.executescript(SCHEMA)

    def save(self, videos: list[VideoStats], taken_at: datetime | None = None) -> None:
        ts = (taken_at or datetime.now()).isoformat(timespec="seconds")
        for v in videos:
            self.db.execute(
                "INSERT INTO videos VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                "title=excluded.title, channel=excluded.channel, duration=excluded.duration",
                (v.id, v.title, v.channel, v.duration, v.upload_date and v.upload_date.isoformat(), ts),
            )
            self.db.execute(
                "INSERT OR REPLACE INTO snapshots VALUES (?,?,?,?,?)",
                (v.id, ts, v.views, v.likes, v.comments),
            )
        self.db.commit()

    def history(self, video_id: str) -> list[Snapshot]:
        rows = self.db.execute(
            "SELECT taken_at, views, likes, comments FROM snapshots WHERE video_id=? ORDER BY taken_at",
            (video_id,),
        ).fetchall()
        return [Snapshot(datetime.fromisoformat(t), v, lk, c) for t, v, lk, c in rows]

    def tracked_ids(self) -> list[str]:
        return [r[0] for r in self.db.execute("SELECT id FROM videos ORDER BY first_seen")]
