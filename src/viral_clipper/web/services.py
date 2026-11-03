"""Operazioni lanciate dall'interfaccia: ognuna gira come lavoro in background."""

import json
import math
import os
import urllib.parse
from datetime import date, datetime, timedelta
from pathlib import Path

from . import db, jobs

JOBS_ROOT = Path("jobs").resolve()
TRAINER_DIR = db.DATA_DIR / "trainer"
RESULTS_PER_RUN = 30


def has_api_key() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def iso_week(d: date | datetime) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


# --- Analyst ------------------------------------------------------------------

def start_analysis(context_id: int, trigger: str = "manuale") -> int:
    ctx = db.one("SELECT * FROM contexts WHERE id=?", (context_id,))
    if not ctx:
        raise ValueError("Contesto non trovato")
    queries = db.loads(ctx["queries"], [])
    channels = db.loads(ctx["channels"], [])
    use_agent = bool(ctx["use_agent"]) and has_api_key() and bool(ctx["brief"].strip())
    if not use_agent and not queries and not channels:
        from ..analyst.summary import queries_from_brief

        # Senza AI la descrizione diventa direttamente un elenco di ricerche
        queries = queries_from_brief(ctx["brief"])
        if not queries:
            raise ValueError("Scrivi una descrizione, oppure aggiungi almeno una ricerca o un canale al contesto.")

    run_id = db.execute(
        "INSERT INTO analyst_runs (context_id, context_name, week, started_at, status, trigger, mode) "
        "VALUES (?,?,?,?, 'in_corso', ?, ?)",
        (context_id, ctx["name"], iso_week(datetime.now()), db.now(), trigger, "agente" if use_agent else "ricerca"),
    )

    def work(job_id: int) -> None:
        db.execute("UPDATE analyst_runs SET job_id=? WHERE id=?", (job_id, run_id))
        try:
            _run_analysis(run_id, ctx, queries, channels, use_agent, trigger)
        except Exception:
            db.execute("UPDATE analyst_runs SET status='errore', finished_at=? WHERE id=?", (db.now(), run_id))
            raise

    return jobs.submit("analyst", f"Analisi: {ctx['name']}", work)


def _run_analysis(run_id, ctx, queries, channels, use_agent, trigger) -> None:
    from ..analyst.pipeline import Session, update_all

    if trigger == "automatico":
        print("Aggiorno lo storico delle views dei video già visti…")
        print(f"  {update_all()} video aggiornati")

    session = Session()
    commentary = None
    if use_agent:
        from ..analyst.agent import run_agent

        print("L'agente sta cercando e analizzando…")
        brief = ctx["brief"]
        if queries or channels:
            brief += "\n\nRicerche suggerite: " + "; ".join(queries) + "\nCanali suggeriti: " + "; ".join(channels)
        brief += f"\n\nAnalizza almeno {RESULTS_PER_RUN + 10} video in tutto."
        commentary = run_agent(brief, session)
    else:
        sources = max(len(queries) + len(channels), 1)
        per_source = max(10, math.ceil((RESULTS_PER_RUN + 15) / sources))
        if not has_api_key():
            print("Modalità senza AI: ricerca su YouTube e classifica calcolata dalle metriche.")
        print("Cerco i video…")
        ids = session.discover(queries, channels, per_source)
        print(f"Analizzo {len(ids)} video…")
        session.analyze_ids(ids)

    ranked = session.ranked()[:RESULTS_PER_RUN]
    results = [a.to_dict() for a in ranked]
    if commentary is None:
        from ..analyst.summary import build_summary

        commentary = build_summary(results, len(session.results), queries)
    session.write(commentary)
    print(f"Fatto: {len(session.results)} video analizzati, {sum(r['qualificato'] for r in results)} qualificati.")
    db.execute(
        "UPDATE analyst_runs SET status='completato', finished_at=?, results=?, commentary=?, total_analyzed=? "
        "WHERE id=?",
        (db.now(), db.dumps(results), commentary, len(session.results), run_id),
    )


def due_contexts(now: datetime) -> list[dict]:
    """Contesti con analisi automatica da lanciare adesso."""
    due = []
    for ctx in db.query("SELECT * FROM contexts WHERE auto_freq != 'off'"):
        last = datetime.fromisoformat(ctx["last_auto_run"]) if ctx["last_auto_run"] else None
        if now.hour < ctx["auto_hour"]:
            continue
        if ctx["auto_freq"] == "daily" and (last is None or last.date() < now.date()) or ctx["auto_freq"] == "weekly" and now.weekday() == ctx["auto_weekday"] and (
            last is None or now - last > timedelta(days=6)
        ):
            due.append(ctx)
    return due


# --- Clipper ------------------------------------------------------------------

def add_to_clipper(video: dict, run_id: int | None) -> int:
    existing = db.one("SELECT id FROM clip_videos WHERE video_id=?", (video["id"],))
    if existing:
        return existing["id"]
    return db.execute(
        "INSERT INTO clip_videos (video_id, url, title, channel, duration, stats, from_run_id, added_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (video["id"], video["url"], video["titolo"], video["canale"], (video.get("durata_min") or 0) * 60,
         db.dumps(video), run_id, db.now()),
    )


def start_clipping(clip_video_id: int, n_clips: int, min_len: int, max_len: int, instructions: str = "") -> int:
    cv = db.one("SELECT * FROM clip_videos WHERE id=?", (clip_video_id,))
    if not cv:
        raise ValueError("Video non trovato")
    if min_len >= max_len:
        raise ValueError("La durata minima deve essere inferiore alla massima")
    db.execute(
        "UPDATE clip_videos SET n_clips=?, min_len=?, max_len=?, instructions=?, status='in_corso', error=NULL "
        "WHERE id=?",
        (n_clips, min_len, max_len, instructions, clip_video_id),
    )

    def work(job_id: int) -> None:
        from ..pipeline import run_clipper

        db.execute("UPDATE clip_videos SET last_job_id=? WHERE id=?", (job_id, clip_video_id))
        references = trainer_references()
        if references:
            print(f"Il copywriter userà {len(references)} Shorts di riferimento dal Trainer.")
        if not has_api_key():
            print("ANTHROPIC_API_KEY non impostata: l'agente copywriter non è attivo, uso lo scoring euristico.")
        try:
            result = run_clipper(cv["url"], n_clips, min_len, max_len, jobs_root=JOBS_ROOT, references=references,
                                 instructions=instructions)
        except Exception as e:
            db.execute("UPDATE clip_videos SET status='errore', error=? WHERE id=?", (str(e)[:500], clip_video_id))
            raise
        with db.tx() as conn:
            conn.execute("DELETE FROM clips WHERE clip_video_id=?", (clip_video_id,))
            for i, c in enumerate(result["clips"], 1):
                conn.execute(
                    "INSERT INTO clips (clip_video_id, idx, path, start, end, score, title, hook, description, "
                    "reason, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (clip_video_id, i, c["path"], c["start"], c["end"], c["score"], c["title"], c["hook"],
                     c.get("description") or "", c["reason"], db.now()),
                )
            conn.execute("UPDATE clip_videos SET status='pronto', job_dir=? WHERE id=?",
                         (result["job_dir"], clip_video_id))

    return jobs.submit("clipper", f"Clip: {cv['title'][:60]}", work)


def _clip_job(clip_id: int, label: str, fn) -> int:
    """Lavoro su una singola clip: la segna "in_corso" e registra l'eventuale errore sulla clip."""
    clip = db.one("SELECT * FROM clips WHERE id=?", (clip_id,))
    if not clip:
        raise ValueError("Clip non trovata")
    if clip["status"] == "in_corso":
        raise ValueError("Questa clip è già in lavorazione")
    cv = db.one("SELECT * FROM clip_videos WHERE id=?", (clip["clip_video_id"],))
    if not cv or not cv["job_dir"]:
        raise ValueError("Video della clip non trovato: rigenera tutte le clip del video.")
    db.execute("UPDATE clips SET status='in_corso', error=NULL WHERE id=?", (clip_id,))

    def work(job_id: int) -> None:
        try:
            fn(clip, cv)
        except Exception as e:
            db.execute("UPDATE clips SET status='errore', error=? WHERE id=?", (str(e)[:500], clip_id))
            raise
        db.execute("UPDATE clips SET status='pronta', version=COALESCE(version, 1) + 1 WHERE id=?", (clip_id,))

    return jobs.submit("clipper", f"{label} · clip #{clip['idx']}", work)


def start_adjust(clip_id: int, edge: str, action: str, seconds: float = 0.0) -> int:
    """Allunga o accorcia la clip da un lato (a frasi intere o di qualche secondo) e la riesporta."""
    from ..clip_edit import adjust_bounds, load_job

    clip = db.one("SELECT * FROM clips WHERE id=?", (clip_id,))
    if not clip:
        raise ValueError("Clip non trovata")
    cv = db.one("SELECT job_dir FROM clip_videos WHERE id=?", (clip["clip_video_id"],))
    _, segments = load_job(cv["job_dir"])
    # Calcolo subito i nuovi estremi: un errore (es. inizio del video) arriva all'utente senza attese
    start, end = adjust_bounds(segments, clip["start"], clip["end"], edge, action, seconds)

    def apply(clip, cv):
        _rerender(clip, cv, start, end)

    side = "inizio" if edge == "start" else "fine"
    return _clip_job(clip_id, f"Modifica {side}", apply)


def start_regenerate(clip_id: int) -> int:
    """Sostituisce la clip con un altro momento del video, diverso da quelli già usati."""

    def apply(clip, cv):
        from ..audio import energy_per_second
        from ..clip_edit import load_job
        from ..scoring import find_clips

        source, segments = load_job(cv["job_dir"])
        others = db.query("SELECT start, end FROM clips WHERE clip_video_id=? AND id<>?", (cv["id"], clip["id"]))
        exclude = [(o["start"], o["end"]) for o in others] + [(clip["start"], clip["end"])]
        print(f"Cerco un nuovo momento (escludo {len(exclude)} intervalli già usati)…")
        wav = Path(cv["job_dir"]) / "audio.wav"
        energy = energy_per_second(wav) if wav.exists() else None
        found = find_clips(segments, 1, cv["min_len"], cv["max_len"], energy, trainer_references(),
                           cv["title"], cv["instructions"] or "", exclude)
        if not found:
            raise ValueError("Non ho trovato altri momenti adatti con questa durata: prova ad allungare la durata massima.")
        new = found[0]
        print(f"  nuovo momento: {new.start:.1f}-{new.end:.1f}s · {new.title}")
        db.execute("UPDATE clips SET score=?, title=?, hook=?, description=?, reason=? WHERE id=?",
                   (new.score, new.title, new.hook, new.description, new.reason, clip["id"]))
        clip = db.one("SELECT * FROM clips WHERE id=?", (clip["id"],))
        _rerender(clip, cv, new.start, new.end, source, segments)

    return _clip_job(clip_id, "Rigenera", apply)


def _rerender(clip: dict, cv: dict, start: float, end: float, source=None, segments=None) -> None:
    from ..clip_edit import clip_from_row, load_job
    from ..render import render_clip

    if source is None:
        source, segments = load_job(cv["job_dir"])
    row = {**clip, "start": start, "end": end}
    c = clip_from_row(row, segments)
    print(f"Riesporto la clip #{clip['idx']}: {start:.1f}-{end:.1f}s ({end - start:.0f}s)…")
    job_dir = Path(cv["job_dir"])
    path = render_clip(source, c, segments, job_dir / "clips", job_dir / "lavoro", clip["idx"])
    db.execute("UPDATE clips SET start=?, end=?, path=? WHERE id=?", (start, end, str(path), clip["id"]))
    print("Fatto.")


def trainer_references() -> list[dict]:
    rows = db.query(
        "SELECT title, views, hook, transcript, notes FROM trainer_shorts "
        "WHERE active=1 AND status='pronto' ORDER BY views DESC LIMIT 15"
    )
    return rows


# --- Upload YouTube -----------------------------------------------------------

def start_upload(clip_id: int, channel_ids: list[str], privacy: str) -> int:
    from . import youtube_api

    clip = db.one("SELECT * FROM clips WHERE id=?", (clip_id,))
    if not clip:
        raise ValueError("Clip non trovata")
    if not channel_ids:
        raise ValueError("Seleziona almeno un canale")
    upload_ids = [
        db.execute(
            "INSERT INTO uploads (clip_id, channel_id, privacy, status) VALUES (?,?,?, 'in_corso')",
            (clip_id, ch, privacy),
        )
        for ch in channel_ids
    ]

    def work(job_id: int) -> None:
        failed = 0
        for upload_id, ch in zip(upload_ids, channel_ids, strict=True):
            db.execute("UPDATE uploads SET job_id=? WHERE id=?", (job_id, upload_id))
            try:
                print(f"Carico su {ch}…")
                yt_id = youtube_api.upload_short(ch, clip["path"], clip["title"], clip["description"], privacy)
                db.execute("UPDATE uploads SET status='caricato', youtube_id=?, uploaded_at=? WHERE id=?",
                           (yt_id, db.now(), upload_id))
                print(f"  caricato: https://youtube.com/shorts/{yt_id}")
            except Exception as e:  # noqa: BLE001 — un canale che fallisce non deve bloccare gli altri
                failed += 1
                db.execute("UPDATE uploads SET status='errore', error=? WHERE id=?", (str(e)[:500], upload_id))
                print(f"  errore: {e}")
        if failed:
            raise RuntimeError(f"{failed} caricamenti non riusciti")

    return jobs.submit("upload", f"Upload: {clip['title'][:50]}", work)


# --- Trainer ------------------------------------------------------------------

def add_trainer_urls(urls: list[str]) -> int | None:
    new_ids = []
    for url in urls:
        url = url.strip()
        if not url.startswith("http"):
            continue
        if db.one("SELECT id FROM trainer_shorts WHERE url=?", (url,)):
            continue
        new_ids.append(db.execute("INSERT INTO trainer_shorts (url, added_at) VALUES (?,?)", (url, db.now())))
    if not new_ids:
        return None
    return analyze_trainer(new_ids)


def analyze_trainer(short_ids: list[int]) -> int:
    def work(job_id: int) -> None:
        failed = 0
        for sid in short_ids:
            row = db.one("SELECT * FROM trainer_shorts WHERE id=?", (sid,))
            db.execute("UPDATE trainer_shorts SET status='in_corso', error=NULL WHERE id=?", (sid,))
            try:
                print(f"Analizzo {row['url']}…")
                _analyze_short(sid, row["url"])
            except Exception as e:  # noqa: BLE001 — uno Short non valido non deve bloccare gli altri
                failed += 1
                db.execute("UPDATE trainer_shorts SET status='errore', error=? WHERE id=?", (str(e)[:500], sid))
                print(f"  errore: {e}")
        if failed:
            raise RuntimeError(f"{failed} Shorts non analizzati")

    return jobs.submit("trainer", f"Trainer: {len(short_ids)} Shorts", work)


def _analyze_short(sid: int, url: str) -> None:
    import yt_dlp

    from ..ingest import FFMPEG
    from ..transcribe import Word

    TRAINER_DIR.mkdir(parents=True, exist_ok=True)
    opts = {
        "quiet": True, "no_warnings": True, "format": "bestaudio/best",
        "outtmpl": str(TRAINER_DIR / "%(id)s.%(ext)s"),
        "ffmpeg_location": str(Path(FFMPEG).parent),
        "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "wav"}],
        "postprocessor_args": ["-ac", "1", "-ar", "16000"],
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
    wav = TRAINER_DIR / f"{info['id']}.wav"

    from faster_whisper import WhisperModel

    model = WhisperModel("base", device="auto", compute_type="int8")
    segs, _ = model.transcribe(str(wav), word_timestamps=True)
    words = [Word(w.start, w.end, w.word.strip()) for s in segs for w in (s.words or []) if w.word.strip()]
    transcript = " ".join(w.text for w in words)
    hook = " ".join(w.text for w in words if w.start < 3.5) or " ".join(w.text for w in words[:12])
    wav.unlink(missing_ok=True)
    print(f"  \"{info.get('title')}\" · {info.get('view_count')} views · hook: {hook[:80]}")

    notes = None
    if has_api_key() and transcript:
        notes = _short_notes(info.get("title") or "", info.get("view_count"), transcript)
    db.execute(
        "UPDATE trainer_shorts SET video_id=?, title=?, channel=?, views=?, likes=?, duration=?, hook=?, "
        "transcript=?, notes=?, status='pronto' WHERE id=?",
        (info.get("id"), info.get("title"), info.get("channel") or info.get("uploader"), info.get("view_count"),
         info.get("like_count"), int(info.get("duration") or 0), hook, transcript, notes, sid),
    )


def _short_notes(title: str, views: int | None, transcript: str) -> str:
    import anthropic

    client = anthropic.Anthropic()
    msg = client.beta.messages.create(
        model="claude-opus-5-5",
        max_tokens=2000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={"effort": "low"},
        system="Sei un copywriter esperto di YouTube Shorts. Rispondi in italiano, in 3 punti brevi.",
        messages=[{"role": "user", "content": (
            f"Short \"{title}\" ({views or '?'} views).\n<testo>\n{transcript[:3000]}\n</testo>\n\n"
            "Spiega perché funziona: tipo di hook, struttura (setup → svolta → payoff), leva emotiva."
        )}],
    )
    if msg.stop_reason == "refusal":
        return ""
    return "\n".join(b.text for b in msg.content if b.type == "text").strip()


# --- Statistics ---------------------------------------------------------------

def add_watched_channel(handle: str) -> str:
    """Aggiunge un canale pubblico da osservare (senza autorizzazione)."""
    import yt_dlp

    from ..analyst.youtube import BASE_OPTS

    handle = handle.strip()
    if handle.startswith("http"):
        url = handle.rstrip("/")
    elif handle.startswith("@"):
        url = f"https://www.youtube.com/{handle}"
    else:
        url = f"https://www.youtube.com/@{handle}"
    with yt_dlp.YoutubeDL({**BASE_OPTS, "extract_flat": True, "playlistend": 1}) as ydl:
        info = ydl.extract_info(url + "/videos", download=False)
    channel_id = info.get("channel_id") or info.get("id")
    thumbs = info.get("thumbnails") or []
    db.execute(
        "INSERT INTO yt_channels (channel_id, title, handle, thumbnail, connected, added_at) VALUES (?,?,?,?,0,?) "
        "ON CONFLICT(channel_id) DO UPDATE SET title=excluded.title, handle=excluded.handle",
        (channel_id, info.get("channel") or info.get("title"), info.get("uploader_id") or handle,
         thumbs[-1]["url"] if thumbs else None, db.now()),
    )
    return channel_id


def start_stats_refresh(channel_ids: list[str]) -> int:
    def work(job_id: int) -> None:
        from . import youtube_api

        for ch in channel_ids:
            row = db.one("SELECT * FROM yt_channels WHERE channel_id=?", (ch,))
            print(f"Aggiorno le statistiche di {row['title']}…")
            if row["connected"]:
                videos = youtube_api.channel_stats(ch)
            else:
                videos = _public_channel_stats(ch)
            with db.tx() as conn:
                for v in videos:
                    conn.execute(
                        "INSERT OR REPLACE INTO channel_videos VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (ch, v["video_id"], v["title"], v["published"], v["duration"], v["views"], v["likes"],
                         v["comments"], v["avg_view_pct"], v["avg_view_pct_source"], v["avg_view_duration"],
                         int(v["is_short"]), db.now()),
                    )
                conn.execute("UPDATE yt_channels SET stats_updated_at=? WHERE channel_id=?", (db.now(), ch))
            print(f"  {len(videos)} video")

    return jobs.submit("stats", f"Statistiche: {len(channel_ids)} canali", work)


def _public_channel_stats(channel_id: str, limit: int = 40) -> list[dict]:
    from ..analyst.metrics import estimate_retention
    from ..analyst.youtube import _flat_ids, fetch_many

    base = f"https://www.youtube.com/channel/{channel_id}"
    shorts = set(_safe_ids(_flat_ids, base + "/shorts", limit))
    longs = _safe_ids(_flat_ids, base + "/videos", limit)
    out = []
    for v in fetch_many(list(shorts) + longs):
        ret = estimate_retention(v.heatmap)
        out.append({
            "video_id": v.id, "title": v.title, "published": v.upload_date and v.upload_date.isoformat(),
            "duration": v.duration, "views": v.views, "likes": v.likes, "comments": v.comments,
            "avg_view_pct": ret * 100 if ret is not None else None,
            "avg_view_pct_source": "stima" if ret is not None else None,
            "avg_view_duration": None, "is_short": v.id in shorts or v.duration <= 180,
        })
    return out


def _safe_ids(fn, url: str, limit: int) -> list[str]:
    import yt_dlp

    try:
        return fn(url, limit)
    except yt_dlp.utils.DownloadError:
        return []  # il canale non ha la scheda Shorts o Video


def start_stats_report(channel_ids: list[str]) -> int:
    if not has_api_key():
        raise ValueError("L'agente statistiche usa Claude: inserisci ANTHROPIC_API_KEY nel file .env")
    scope = ", ".join(r["title"] for r in db.query(
        f"SELECT title FROM yt_channels WHERE channel_id IN ({','.join('?' * len(channel_ids))})", tuple(channel_ids)
    ))
    report_id = db.execute(
        "INSERT INTO stat_reports (created_at, scope, status) VALUES (?,?, 'in_corso')", (db.now(), scope)
    )

    def work(job_id: int) -> None:
        db.execute("UPDATE stat_reports SET job_id=? WHERE id=?", (job_id, report_id))
        try:
            content = _stats_agent(channel_ids)
        except Exception:
            db.execute("UPDATE stat_reports SET status='errore' WHERE id=?", (report_id,))
            raise
        db.execute("UPDATE stat_reports SET content=?, status='completato' WHERE id=?", (content, report_id))

    return jobs.submit("report", f"Analisi statistiche: {scope[:50]}", work)


STATS_SYSTEM = """Sei un analista di contenuti YouTube specializzato in Shorts e video lunghi. \
Ricevi le statistiche dei video di uno o più canali e, quando disponibili, le clip da cui \
provengono (hook, titolo, video sorgente).

Scrivi in italiano un'analisi in Markdown che risponda a: quali contenuti funzionano di più e perché.
Struttura:
## In sintesi — 3-5 punti con le conclusioni principali
## Cosa funziona — temi, formati, hook, durate e titoli dei video migliori, con esempi concreti
## Cosa non funziona — schemi ricorrenti nei video peggiori
## Per canale — una breve sezione per ogni canale
## Prossimi passi — 5 azioni concrete per i prossimi contenuti

Confronta i video con metriche normalizzate (views rispetto alla mediana del canale, like/views, \
retention). Indica se la retention è il dato ufficiale di YouTube o una stima. \
Non inventare numeri: usa solo quelli forniti."""


def _stats_agent(channel_ids: list[str]) -> str:
    import anthropic

    payload = []
    for ch in channel_ids:
        c = db.one("SELECT channel_id, title, connected FROM yt_channels WHERE channel_id=?", (ch,))
        videos = db.query(
            "SELECT cv.video_id, cv.title, cv.published, cv.duration, cv.views, cv.likes, cv.comments, "
            "cv.avg_view_pct, cv.avg_view_pct_source, cv.is_short, c.hook AS clip_hook, v.title AS fonte "
            "FROM channel_videos cv "
            "LEFT JOIN uploads u ON u.youtube_id = cv.video_id "
            "LEFT JOIN clips c ON c.id = u.clip_id "
            "LEFT JOIN clip_videos v ON v.id = c.clip_video_id "
            "WHERE cv.channel_id=? ORDER BY cv.views DESC", (ch,),
        )
        payload.append({"canale": c["title"], "collegato": bool(c["connected"]), "video": videos})
    print(f"L'agente analizza {sum(len(p['video']) for p in payload)} video…")

    client = anthropic.Anthropic()
    with client.beta.messages.stream(
        model="claude-opus-5-5",
        max_tokens=32000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={"effort": "high"},
        system=STATS_SYSTEM,
        messages=[{"role": "user", "content": "<statistiche>\n" + json.dumps(payload, ensure_ascii=False)
                   + "\n</statistiche>"}],
    ) as stream:
        msg = stream.get_final_message()
    if msg.stop_reason == "refusal":
        raise RuntimeError("Claude ha rifiutato la richiesta")
    return "\n".join(b.text for b in msg.content if b.type == "text")


def thumb(video_id: str) -> str:
    return f"https://i.ytimg.com/vi/{urllib.parse.quote(video_id)}/mqdefault.jpg"
