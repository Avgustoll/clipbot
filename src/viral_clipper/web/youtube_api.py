"""YouTube Data API + Analytics API: collegamento canali (OAuth), upload Shorts, statistiche ufficiali.

Serve un client OAuth di tipo "App desktop" creato su Google Cloud Console con le API
"YouTube Data API v3" e "YouTube Analytics API" attive. Il file JSON va caricato dalle
impostazioni dell'app (finisce in data/google_client_secret.json).
Ogni canale autorizzato ha il suo token in data/oauth/<channel_id>.json.
"""

import json
import os
import re
from datetime import date
from pathlib import Path

from . import db

CLIENT_SECRET = db.DATA_DIR / "google_client_secret.json"
TOKENS_DIR = db.DATA_DIR / "oauth"
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]

# Il redirect su http://127.0.0.1 è consentito da Google per i client desktop
os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

_pending_flows: dict = {}


REDIRECT_URI = "http://127.0.0.1:8000/api/youtube/callback"


def is_configured() -> bool:
    return CLIENT_SECRET.exists() and config_problem() is None


_probe_cache: dict = {}

GOOGLE_ERRORS = {
    "redirect_uri_mismatch": (
        "Google non riconosce l'indirizzo di ritorno per questo client (redirect_uri_mismatch). "
        f"Su Google Cloud → Credenziali apri il client \"{{client}}\" e aggiungi agli "
        f"\"URI di reindirizzamento autorizzati\" esattamente {REDIRECT_URI} (poi Salva; Google può "
        "impiegare qualche minuto). Oppure crea un client di tipo \"App desktop\" e carica quel JSON."
    ),
    "deleted_client": "Questo client OAuth è stato eliminato su Google Cloud: creane uno nuovo e caricalo qui.",
    "invalid_client": "Google non riconosce questo client OAuth: riscarica il JSON da Google Cloud e ricaricalo qui.",
    "disabled_client": "Questo client OAuth è disattivato su Google Cloud: riattivalo o creane uno nuovo.",
    "org_internal": (
        "La schermata di consenso è di tipo \"Interno\": impostala su \"Esterno\" e aggiungi il tuo account "
        "tra gli utenti di test."
    ),
}


def config_problem(data: dict | None = None, probe: bool = True) -> str | None:
    """Spiega perché il client OAuth non può funzionare, oppure None se è a posto.

    Per i client "Applicazione web" il JSON può essere vecchio rispetto a Google Cloud:
    la verifica che conta la fa Google, quindi la interroghiamo direttamente.
    """
    if data is None:
        if not CLIENT_SECRET.exists():
            return None
        data = json.loads(CLIENT_SECRET.read_text())
    if "installed" not in data and "web" not in data:
        return "Il file non sembra un client OAuth di Google (manca 'installed' o 'web')."
    if not probe:
        return None
    error = probe_google(data)
    if error:
        client_id = (data.get("web") or data.get("installed"))["client_id"]
        return GOOGLE_ERRORS.get(error, f"Google rifiuta il client OAuth ({error}).").format(client=client_id[:24] + "…")
    return None


def probe_google(data: dict, max_age: float = 30) -> str | None:
    """Chiede a Google se il client accetta il nostro indirizzo di ritorno. Restituisce il codice d'errore o None."""
    import time
    import urllib.error
    import urllib.request

    from google_auth_oauthlib.flow import Flow

    key = json.dumps(data, sort_keys=True)
    cached = _probe_cache.get(key)
    if cached and time.time() - cached[0] < max_age:
        return cached[1]
    flow = Flow.from_client_config(data, scopes=SCOPES, redirect_uri=REDIRECT_URI)
    url, _ = flow.authorization_url(access_type="offline")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "en"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            body = r.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "ignore")
    except OSError:
        return None  # senza rete non possiamo verificare: lasciamo provare
    error = next((code for code in GOOGLE_ERRORS if code in body), None)
    _probe_cache[key] = (time.time(), error)
    return error


def save_client_secret(data: dict) -> None:
    problem = config_problem(data, probe=False)
    if problem:
        raise ValueError(problem)
    db.DATA_DIR.mkdir(parents=True, exist_ok=True)
    CLIENT_SECRET.write_text(json.dumps(data))
    CLIENT_SECRET.chmod(0o600)
    problem = config_problem(data)
    if problem:
        raise ValueError(problem)


def recheck() -> str | None:
    """Ripete subito la verifica con Google (dopo una modifica su Google Cloud)."""
    _probe_cache.clear()
    return config_problem()


def auth_url() -> str:
    from google_auth_oauthlib.flow import Flow

    if not CLIENT_SECRET.exists():
        raise ValueError("Carica prima il file OAuth di Google nelle impostazioni.")
    problem = recheck()
    if problem:
        raise ValueError(problem)
    # Sempre lo stesso indirizzo di ritorno, anche se l'app è aperta come localhost
    flow = Flow.from_client_secrets_file(str(CLIENT_SECRET), scopes=SCOPES, redirect_uri=REDIRECT_URI)
    url, state = flow.authorization_url(access_type="offline", prompt="consent select_account",
                                        include_granted_scopes="true")
    _pending_flows[state] = flow
    return url


def finish_auth(state: str, code: str) -> list[str]:
    """Completa l'autorizzazione e registra il canale (o i canali) dell'account scelto."""
    from googleapiclient.discovery import build

    flow = _pending_flows.pop(state, None)
    if flow is None:
        raise ValueError("Sessione di autorizzazione scaduta: riprova da 'Collega canale'.")
    flow.fetch_token(code=code)
    creds = flow.credentials
    yt = build("youtube", "v3", credentials=creds, cache_discovery=False)
    items = yt.channels().list(part="snippet", mine=True).execute().get("items", [])
    if not items:
        raise ValueError("Questo account Google non ha un canale YouTube.")
    TOKENS_DIR.mkdir(parents=True, exist_ok=True)
    ids = []
    for ch in items:
        cid, sn = ch["id"], ch["snippet"]
        token = TOKENS_DIR / f"{cid}.json"
        token.write_text(creds.to_json())
        token.chmod(0o600)
        db.execute(
            "INSERT INTO yt_channels (channel_id, title, handle, thumbnail, connected, added_at) "
            "VALUES (?,?,?,?,1,?) ON CONFLICT(channel_id) DO UPDATE SET title=excluded.title, "
            "handle=excluded.handle, thumbnail=excluded.thumbnail, connected=1",
            (cid, sn.get("title"), sn.get("customUrl"), sn.get("thumbnails", {}).get("default", {}).get("url"),
             db.now()),
        )
        ids.append(cid)
    return ids


def disconnect(channel_id: str) -> None:
    (TOKENS_DIR / f"{channel_id}.json").unlink(missing_ok=True)
    db.execute("UPDATE yt_channels SET connected=0 WHERE channel_id=?", (channel_id,))


def _credentials(channel_id: str):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    token = TOKENS_DIR / f"{channel_id}.json"
    if not token.exists():
        raise ValueError("Canale non collegato: autorizzalo da 'Collega canale'.")
    creds = Credentials.from_authorized_user_file(str(token), SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        token.write_text(creds.to_json())
    return creds


def upload_short(channel_id: str, path: str, title: str, description: str, privacy: str) -> str:
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    if privacy not in ("private", "unlisted", "public"):
        raise ValueError("Privacy non valida")
    if not Path(path).exists():
        raise ValueError(f"File della clip non trovato: {path}")
    yt = build("youtube", "v3", credentials=_credentials(channel_id), cache_discovery=False)
    title = (title or "Short").strip()
    if "#shorts" not in (title + description).lower():
        description = f"{description}\n\n#Shorts".strip()
    tags = list(dict.fromkeys(re.findall(r"#(\w+)", description)))[:15]
    body = {
        "snippet": {"title": title[:100], "description": description[:5000], "tags": tags, "categoryId": "22"},
        "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False},
    }
    media = MediaFileUpload(path, mimetype="video/mp4", chunksize=8 * 1024 * 1024, resumable=True)
    request = yt.videos().insert(part="snippet,status", body=body, media_body=media)
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"  {int(status.progress() * 100)}%")
    return response["id"]


def channel_stats(channel_id: str, limit: int = 100) -> list[dict]:
    """Video del canale con statistiche pubbliche + retention ufficiale (YouTube Analytics)."""
    from googleapiclient.discovery import build

    creds = _credentials(channel_id)
    yt = build("youtube", "v3", credentials=creds, cache_discovery=False)
    ch = yt.channels().list(part="contentDetails", id=channel_id).execute()["items"][0]
    uploads = ch["contentDetails"]["relatedPlaylists"]["uploads"]

    ids, token = [], None
    while len(ids) < limit:
        page = yt.playlistItems().list(part="contentDetails", playlistId=uploads, maxResults=50,
                                       pageToken=token).execute()
        ids += [i["contentDetails"]["videoId"] for i in page.get("items", [])]
        token = page.get("nextPageToken")
        if not token:
            break
    ids = ids[:limit]

    videos = {}
    for i in range(0, len(ids), 50):
        resp = yt.videos().list(part="snippet,statistics,contentDetails", id=",".join(ids[i:i + 50])).execute()
        for v in resp.get("items", []):
            st = v.get("statistics", {})
            dur = _iso_duration(v["contentDetails"]["duration"])
            videos[v["id"]] = {
                "video_id": v["id"], "title": v["snippet"]["title"], "published": v["snippet"]["publishedAt"][:10],
                "duration": dur, "views": int(st.get("viewCount", 0)), "likes": _int(st.get("likeCount")),
                "comments": _int(st.get("commentCount")), "avg_view_pct": None, "avg_view_pct_source": None,
                "avg_view_duration": None, "is_short": dur <= 180,
            }

    try:
        analytics = build("youtubeAnalytics", "v2", credentials=creds, cache_discovery=False)
        report = analytics.reports().query(
            ids="channel==MINE", startDate="2005-01-01", endDate=date.today().isoformat(),
            metrics="views,averageViewPercentage,averageViewDuration", dimensions="video",
            sort="-views", maxResults=200,
        ).execute()
        for vid, _views, pct, dur in report.get("rows", []):
            if vid in videos:
                videos[vid].update(avg_view_pct=pct, avg_view_pct_source="youtube", avg_view_duration=dur)
    except Exception as e:  # noqa: BLE001 — Analytics può non essere attiva sul progetto Google: le altre metriche restano
        print(f"  retention non disponibile da YouTube Analytics: {e}")
    return list(videos.values())


def _iso_duration(value: str) -> int:
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", value)
    if not m:
        return 0
    d, h, mi, s = (int(x or 0) for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + s


def _int(x) -> int | None:
    return int(x) if x is not None else None
