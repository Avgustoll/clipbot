from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from viral_clipper.web import db, services


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "studio.db")
    monkeypatch.setattr(db, "_conn", None)
    from viral_clipper.web.app import app

    with TestClient(app) as c:
        yield c
    monkeypatch.setattr(db, "_conn", None)


def make_context(client, **kw):
    body = {"name": "Test", "queries": ["podcast"], **kw}
    return client.post("/api/contexts", json=body).json()["id"]


def test_crud_contesti(client):
    cid = make_context(client, queries=[" podcast business ", ""], channels=["@canale"])
    ctx = client.get("/api/contexts").json()[0]
    assert ctx["id"] == cid and ctx["queries"] == ["podcast business"] and ctx["channels"] == ["@canale"]
    client.put(f"/api/contexts/{cid}", json={"name": "Nuovo", "auto_freq": "weekly", "auto_weekday": 2})
    assert client.get("/api/contexts").json()[0]["auto_freq"] == "weekly"
    assert client.put(f"/api/contexts/{cid}", json={"name": "x", "auto_freq": "ogni ora"}).status_code == 400


def test_analisi_senza_ricerche_da_errore_leggibile(client, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cid = make_context(client, queries=[])
    r = client.post(f"/api/analyst/run/{cid}")
    assert r.status_code == 400 and "ricerca" in r.json()["detail"]


def test_scheduler_giornaliero_e_settimanale(client):
    daily = make_context(client, auto_freq="daily", auto_hour=8)
    weekly = make_context(client, auto_freq="weekly", auto_hour=8, auto_weekday=0)
    make_context(client, auto_freq="off")

    monday_7 = datetime(2026, 10, 26, 7, 0)
    monday_9 = datetime(2026, 10, 26, 9, 0)
    tuesday_9 = datetime(2026, 10, 27, 9, 0)
    assert services.due_contexts(monday_7) == []
    assert {c["id"] for c in services.due_contexts(monday_9)} == {daily, weekly}

    db.execute("UPDATE contexts SET last_auto_run=?", (monday_9.isoformat(),))
    assert services.due_contexts(datetime(2026, 10, 26, 18, 0)) == []          # già fatto oggi
    assert [c["id"] for c in services.due_contexts(tuesday_9)] == [daily]      # il settimanale aspetta lunedì


def test_coda_clipper_e_media_protetti(client):
    video = {"id": "abcdefghijk", "url": "https://www.youtube.com/watch?v=abcdefghijk", "titolo": "T",
             "canale": "C", "durata_min": 45}
    vid = client.post("/api/clipper/videos", json={"run_id": None, "video": video}).json()["id"]
    assert client.post("/api/clipper/videos", json={"run_id": None, "video": video}).json()["id"] == vid
    listed = client.get("/api/clipper/videos").json()
    assert listed[0]["duration"] == 45 * 60 and listed[0]["clips"] == []

    clip_id = db.execute("INSERT INTO clips (clip_video_id, idx, path) VALUES (?, 1, '/etc/passwd')", (vid,))
    assert client.get(f"/api/media/clip/{clip_id}").status_code == 404   # fuori da jobs/: non servito


def test_iso_week():
    assert services.iso_week(datetime(2026, 10, 27)) == "2026-W44"
    assert services.iso_week(datetime(2027, 1, 1)) == "2026-W53"
