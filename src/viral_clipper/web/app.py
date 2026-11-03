"""ClipBot Studio: interfaccia web locale per Analyst, Clipper, Trainer e Statistics."""

import threading
import time
import urllib.parse
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

load_dotenv()

from . import db, jobs, services, youtube_api

STATIC = Path(__file__).parent / "static"


def _scheduler() -> None:
    """Lancia le analisi automatiche dei contesti quando sono dovute (controlla ogni minuto)."""
    while True:
        try:
            for ctx in services.due_contexts(datetime.now()):
                db.execute("UPDATE contexts SET last_auto_run=? WHERE id=?", (db.now(), ctx["id"]))
                services.start_analysis(ctx["id"], trigger="automatico")
        except Exception as e:  # noqa: BLE001 — lo scheduler non deve mai fermarsi per un errore
            print(f"[scheduler] {e}")
        time.sleep(60)


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.connect()
    db.reset_interrupted()
    jobs.install_stdout()
    threading.Thread(target=_scheduler, daemon=True, name="scheduler").start()
    yield


app = FastAPI(title="ClipBot Studio", lifespan=lifespan)


def _phone_key() -> str:
    """Chiave segreta per aprire lo Studio da altri dispositivi della rete (es. il telefono)."""
    import secrets

    path = Path("data") / "phone_key"
    if not path.exists():
        path.parent.mkdir(exist_ok=True)
        path.write_text(secrets.token_urlsafe(16))
    return path.read_text().strip()


PHONE_KEY = _phone_key()


@app.middleware("http")
async def lan_guard(request: Request, call_next):
    # Dal computer stesso si entra sempre; dalla rete serve il link con la chiave (poi resta in un cookie)
    if request.client and request.client.host in ("127.0.0.1", "::1"):
        return await call_next(request)
    if request.query_params.get("k") == PHONE_KEY:
        response = RedirectResponse(request.url.remove_query_params("k"))
        response.set_cookie("clipbot_key", PHONE_KEY, max_age=365 * 86400, httponly=True, samesite="lax")
        return response
    if request.cookies.get("clipbot_key") != PHONE_KEY:
        return HTMLResponse("Apri lo Studio dal link per il telefono stampato nel terminale.", status_code=403)
    return await call_next(request)


app.mount("/static", StaticFiles(directory=STATIC), name="static")


def _ui_version() -> int:
    """Cambia quando cambiano JS o CSS: le schede aperte capiscono che devono ricaricarsi."""
    return max(int((STATIC / n).stat().st_mtime) for n in ("app.js", "style.css", "index.html"))


@app.get("/", response_class=HTMLResponse)
def index():
    # Versione nei link di CSS/JS: dopo un aggiornamento il browser non usa file vecchi dalla cache
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    for name in ("app.js", "style.css"):
        html = html.replace(f"/static/{name}", f"/static/{name}?v={int((STATIC / name).stat().st_mtime)}")
    return HTMLResponse(html)


@app.exception_handler(ValueError)
async def value_error(_: Request, exc: ValueError):
    from fastapi.responses import JSONResponse

    return JSONResponse({"detail": str(exc)}, status_code=400)


# --- Stato e impostazioni -----------------------------------------------------

@app.get("/api/status")
def status():
    problem = youtube_api.config_problem()
    return {
        "api_key": services.has_api_key(),
        "google_configured": youtube_api.CLIENT_SECRET.exists() and problem is None,
        "google_problem": problem,
        "redirect_uri": youtube_api.REDIRECT_URI,
        "connected_channels": len(db.query("SELECT 1 FROM yt_channels WHERE connected=1")),
        "ui_version": _ui_version(),
    }


ENV_FILE = Path(".env").resolve()


@app.post("/api/settings/anthropic-key")
def anthropic_key(data: dict):
    """Verifica la chiave con Anthropic, la salva in .env e la attiva subito (senza riavvio)."""
    import os
    import re

    import anthropic

    key = (data.get("key") or "").strip()
    if not key.startswith("sk-ant-"):
        raise ValueError("Non sembra una chiave API di Anthropic: deve iniziare con sk-ant-")
    try:
        anthropic.Anthropic(api_key=key, max_retries=1, timeout=20).models.list(limit=1)
    except anthropic.AuthenticationError:
        raise ValueError("Anthropic ha rifiutato la chiave: controlla di averla copiata per intero.") from None
    except anthropic.PermissionDeniedError:
        raise ValueError("La chiave è valida ma non ha i permessi per usare l'API.") from None
    except anthropic.APIConnectionError:
        raise ValueError("Impossibile raggiungere Anthropic: controlla la connessione.") from None

    lines = ENV_FILE.read_text().splitlines() if ENV_FILE.exists() else []
    lines = [ln for ln in lines if not re.match(r"\s*ANTHROPIC_API_KEY\s*=", ln)]
    lines.insert(0, f"ANTHROPIC_API_KEY={key}")
    ENV_FILE.write_text("\n".join(lines) + "\n")
    ENV_FILE.chmod(0o600)
    os.environ["ANTHROPIC_API_KEY"] = key
    return {"ok": True}


@app.post("/api/youtube/recheck")
def youtube_recheck():
    return {"problem": youtube_api.recheck()}


@app.post("/api/settings/google-secret")
def google_secret(data: dict):
    youtube_api.save_client_secret(data)
    return {"ok": True}


# --- Lavori ---------------------------------------------------------------------

@app.get("/api/jobs")
def list_jobs():
    return db.query("SELECT id, kind, label, status, error, created_at, finished_at, "
                    "substr(log, -1500) AS log FROM jobs ORDER BY id DESC LIMIT 15")


@app.get("/api/jobs/{job_id}")
def get_job(job_id: int):
    job = db.one("SELECT * FROM jobs WHERE id=?", (job_id,))
    if not job:
        raise HTTPException(404)
    return job


# --- Analyst --------------------------------------------------------------------

class ContextIn(BaseModel):
    name: str
    brief: str = ""
    queries: list[str] = []
    channels: list[str] = []
    use_agent: bool = True
    auto_freq: str = "off"
    auto_hour: int = 8
    auto_weekday: int = 0


@app.get("/api/contexts")
def list_contexts():
    rows = db.query("SELECT * FROM contexts ORDER BY id")
    for r in rows:
        r["queries"], r["channels"] = db.loads(r["queries"], []), db.loads(r["channels"], [])
    return rows


@app.post("/api/contexts")
def create_context(c: ContextIn):
    cid = db.execute(
        "INSERT INTO contexts (name, brief, queries, channels, use_agent, auto_freq, auto_hour, auto_weekday) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (c.name, c.brief, db.dumps(_clean(c.queries)), db.dumps(_clean(c.channels)), int(c.use_agent),
         _freq(c.auto_freq), c.auto_hour, c.auto_weekday),
    )
    return {"id": cid}


@app.put("/api/contexts/{cid}")
def update_context(cid: int, c: ContextIn):
    db.execute(
        "UPDATE contexts SET name=?, brief=?, queries=?, channels=?, use_agent=?, auto_freq=?, auto_hour=?, "
        "auto_weekday=? WHERE id=?",
        (c.name, c.brief, db.dumps(_clean(c.queries)), db.dumps(_clean(c.channels)), int(c.use_agent),
         _freq(c.auto_freq), c.auto_hour, c.auto_weekday, cid),
    )
    return {"ok": True}


@app.delete("/api/contexts/{cid}")
def delete_context(cid: int):
    db.execute("DELETE FROM contexts WHERE id=?", (cid,))
    return {"ok": True}


@app.post("/api/analyst/run/{cid}")
def run_analyst(cid: int):
    return {"job_id": services.start_analysis(cid)}


@app.get("/api/analyst/weeks")
def analyst_weeks():
    return db.query("SELECT week, COUNT(*) AS runs, MAX(started_at) AS last FROM analyst_runs "
                    "GROUP BY week ORDER BY week DESC")


@app.get("/api/analyst/runs")
def analyst_runs(week: str):
    rows = db.query("SELECT * FROM analyst_runs WHERE week=? ORDER BY id DESC", (week,))
    queued = {r["video_id"] for r in db.query("SELECT video_id FROM clip_videos")}
    for r in rows:
        r["results"] = db.loads(r["results"], [])
        for v in r["results"]:
            v["thumb"] = services.thumb(v["id"])
            v["in_clipper"] = v["id"] in queued
    return rows


@app.delete("/api/analyst/runs/{run_id}")
def delete_run(run_id: int):
    db.execute("DELETE FROM analyst_runs WHERE id=?", (run_id,))
    return {"ok": True}


# --- Clipper --------------------------------------------------------------------

class QueueIn(BaseModel):
    run_id: int | None = None
    video: dict


class GenerateIn(BaseModel):
    n_clips: int = 5
    min_len: int = 20
    max_len: int = 60
    instructions: str = ""


class AdjustIn(BaseModel):
    edge: str               # start | end
    action: str             # extend_sentence | shrink_sentence | nudge
    seconds: float = 0.0


class ClipEdit(BaseModel):
    title: str
    description: str


class UploadIn(BaseModel):
    channels: list[str]
    privacy: str = "private"


@app.post("/api/clipper/videos")
def queue_video(q: QueueIn):
    return {"id": services.add_to_clipper(q.video, q.run_id)}


@app.post("/api/clipper/videos/url")
def queue_url(data: dict):
    """Aggiunge al clipper un video incollato a mano (link YouTube)."""
    from ..analyst.youtube import fetch_video

    url = (data.get("url") or "").strip()
    vid = _youtube_id(url)
    if not vid:
        raise ValueError("Link YouTube non valido")
    v = fetch_video(vid)
    if v is None:
        raise ValueError("Video non disponibile")
    video = {"id": v.id, "url": v.url, "titolo": v.title, "canale": v.channel, "durata_min": round(v.duration / 60),
             "views": v.views}
    return {"id": services.add_to_clipper(video, None)}


@app.get("/api/clipper/videos")
def clipper_videos():
    videos = db.query("SELECT * FROM clip_videos ORDER BY id DESC")
    clips = db.query("SELECT * FROM clips ORDER BY clip_video_id, idx")
    uploads = db.query("SELECT u.*, c.title AS channel_title FROM uploads u "
                       "LEFT JOIN yt_channels c ON c.channel_id = u.channel_id ORDER BY u.id")
    by_clip: dict[int, list] = {}
    for u in uploads:
        by_clip.setdefault(u["clip_id"], []).append(u)
    by_video: dict[int, list] = {}
    for c in clips:
        c["uploads"] = by_clip.get(c["id"], [])
        c["exists"] = Path(c["path"]).exists()
        by_video.setdefault(c["clip_video_id"], []).append(c)
    for v in videos:
        v["stats"] = db.loads(v["stats"], {})
        v["thumb"] = services.thumb(v["video_id"])
        v["clips"] = by_video.get(v["id"], [])
    return videos


@app.delete("/api/clipper/videos/{vid}")
def remove_video(vid: int):
    with db.tx() as conn:
        conn.execute("DELETE FROM clips WHERE clip_video_id=?", (vid,))
        conn.execute("DELETE FROM clip_videos WHERE id=?", (vid,))
    return {"ok": True}


@app.post("/api/clipper/videos/{vid}/generate")
def generate(vid: int, g: GenerateIn):
    if not (1 <= g.n_clips <= 20):
        raise ValueError("Numero di clip tra 1 e 20")
    return {"job_id": services.start_clipping(vid, g.n_clips, g.min_len, g.max_len, g.instructions)}


@app.post("/api/clips/{clip_id}/adjust")
def adjust_clip(clip_id: int, a: AdjustIn):
    return {"job_id": services.start_adjust(clip_id, a.edge, a.action, a.seconds)}


@app.post("/api/clips/{clip_id}/regenerate")
def regenerate_clip(clip_id: int):
    return {"job_id": services.start_regenerate(clip_id)}


@app.put("/api/clips/{clip_id}")
def edit_clip(clip_id: int, e: ClipEdit):
    db.execute("UPDATE clips SET title=?, description=? WHERE id=?", (e.title, e.description, clip_id))
    return {"ok": True}


@app.post("/api/clips/{clip_id}/upload")
def upload_clip(clip_id: int, u: UploadIn):
    return {"job_id": services.start_upload(clip_id, u.channels, u.privacy)}


@app.get("/api/media/clip/{clip_id}")
def clip_media(clip_id: int):
    clip = db.one("SELECT path FROM clips WHERE id=?", (clip_id,))
    if not clip:
        raise HTTPException(404)
    path = Path(clip["path"]).resolve()
    if not path.is_relative_to(services.JOBS_ROOT) or not path.exists():
        raise HTTPException(404)
    return FileResponse(path, media_type="video/mp4")


# --- Trainer --------------------------------------------------------------------

@app.get("/api/trainer")
def trainer_list():
    rows = db.query("SELECT * FROM trainer_shorts ORDER BY COALESCE(views, -1) DESC, id DESC")
    for r in rows:
        r["thumb"] = services.thumb(r["video_id"]) if r["video_id"] else None
    return rows


@app.post("/api/trainer")
def trainer_add(data: dict):
    urls = [u for u in (data.get("urls") or []) if u.strip()]
    if not urls:
        raise ValueError("Incolla almeno un link")
    return {"job_id": services.add_trainer_urls(urls)}


@app.put("/api/trainer/{sid}")
def trainer_toggle(sid: int, data: dict):
    db.execute("UPDATE trainer_shorts SET active=? WHERE id=?", (int(bool(data.get("active"))), sid))
    return {"ok": True}


@app.post("/api/trainer/{sid}/retry")
def trainer_retry(sid: int):
    return {"job_id": services.analyze_trainer([sid])}


@app.delete("/api/trainer/{sid}")
def trainer_delete(sid: int):
    db.execute("DELETE FROM trainer_shorts WHERE id=?", (sid,))
    return {"ok": True}


# --- Statistics ------------------------------------------------------------------

@app.get("/api/channels")
def channels():
    return db.query(
        "SELECT c.*, COUNT(v.video_id) AS videos, COALESCE(SUM(v.views), 0) AS views, "
        "AVG(v.avg_view_pct) AS avg_view_pct FROM yt_channels c "
        "LEFT JOIN channel_videos v ON v.channel_id = c.channel_id GROUP BY c.channel_id ORDER BY c.added_at"
    )


@app.post("/api/channels/watch")
def watch_channel(data: dict):
    handle = (data.get("handle") or "").strip()
    if not handle:
        raise ValueError("Indica @handle o link del canale")
    cid = services.add_watched_channel(handle)
    return {"channel_id": cid, "job_id": services.start_stats_refresh([cid])}


@app.delete("/api/channels/{cid}")
def delete_channel(cid: str):
    youtube_api.disconnect(cid)
    with db.tx() as conn:
        conn.execute("DELETE FROM channel_videos WHERE channel_id=?", (cid,))
        conn.execute("DELETE FROM yt_channels WHERE channel_id=?", (cid,))
    return {"ok": True}


@app.post("/api/channels/refresh")
def refresh_channels(data: dict):
    ids = data.get("channel_ids") or [r["channel_id"] for r in db.query("SELECT channel_id FROM yt_channels")]
    if not ids:
        raise ValueError("Nessun canale da aggiornare")
    return {"job_id": services.start_stats_refresh(ids)}


@app.get("/api/channels/{cid}/videos")
def channel_videos(cid: str):
    rows = db.query(
        "SELECT cv.*, c.title AS clip_title, c.hook AS clip_hook, v.title AS source_title "
        "FROM channel_videos cv LEFT JOIN uploads u ON u.youtube_id = cv.video_id "
        "LEFT JOIN clips c ON c.id = u.clip_id LEFT JOIN clip_videos v ON v.id = c.clip_video_id "
        "WHERE cv.channel_id=? ORDER BY cv.views DESC", (cid,),
    )
    for r in rows:
        r["thumb"] = services.thumb(r["video_id"])
    return rows


@app.get("/api/youtube/connect")
def youtube_connect():
    try:
        return RedirectResponse(youtube_api.auth_url())
    except ValueError as e:
        return RedirectResponse(f"/#statistics?errore={urllib.parse.quote(str(e))}")


@app.get("/api/youtube/callback", name="youtube_callback")
def youtube_callback(state: str = "", code: str = "", error: str = ""):
    if error:
        return RedirectResponse(f"/#statistics?errore={urllib.parse.quote(error)}")
    try:
        youtube_api.finish_auth(state, code)
    except ValueError as e:
        return RedirectResponse(f"/#statistics?errore={urllib.parse.quote(str(e))}")
    return RedirectResponse("/#statistics?collegato=1")


@app.post("/api/reports")
def create_report(data: dict):
    ids = data.get("channel_ids") or [r["channel_id"] for r in db.query("SELECT channel_id FROM yt_channels")]
    if not ids:
        raise ValueError("Aggiungi almeno un canale")
    return {"job_id": services.start_stats_report(ids)}


@app.get("/api/reports")
def reports():
    return db.query("SELECT id, created_at, scope, status FROM stat_reports ORDER BY id DESC")


@app.get("/api/reports/{rid}")
def report(rid: int):
    r = db.one("SELECT * FROM stat_reports WHERE id=?", (rid,))
    if not r:
        raise HTTPException(404)
    return r


# --- Utilità --------------------------------------------------------------------

def _clean(items: list[str]) -> list[str]:
    return [s.strip() for s in items if s.strip()]


def _freq(value: str) -> str:
    if value not in ("off", "daily", "weekly"):
        raise ValueError("Frequenza non valida")
    return value


def _youtube_id(url: str) -> str | None:
    import re

    m = re.search(r"(?:v=|youtu\.be/|shorts/|live/|embed/)([A-Za-z0-9_-]{11})", url)
    return m.group(1) if m else None


URL = "http://127.0.0.1:8000"


def _phone_url() -> str | None:
    """Indirizzo dello Studio nella rete locale, da aprire sul telefono (stessa Wi-Fi)."""
    import socket

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))  # nessun pacchetto inviato: serve solo a scegliere l'interfaccia
            ip = s.getsockname()[0]
    except OSError:
        return None
    return f"http://{ip}:8000/?k={PHONE_KEY}"


def main() -> None:
    import socket
    import urllib.request
    import webbrowser

    import uvicorn

    with socket.socket() as s:
        busy = s.connect_ex(("127.0.0.1", 8000)) == 0
    if busy:
        try:
            urllib.request.urlopen(f"{URL}/api/status", timeout=3)
        except OSError:
            raise SystemExit("La porta 8000 è occupata da un altro programma: chiudilo e riprova.") from None
        print(f"ClipBot Studio è già acceso → {URL} (lo apro nel browser)")
        webbrowser.open(URL)
        return

    print(f"ClipBot Studio → {URL}   (Ctrl+C per fermarlo)")
    if phone := _phone_url():
        print(f"Dal telefono (stessa Wi-Fi) → {phone}")
    threading.Timer(1.5, webbrowser.open, args=(URL,)).start()
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="warning")


if __name__ == "__main__":
    main()
