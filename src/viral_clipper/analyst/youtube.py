"""Raccolta dati da YouTube tramite yt-dlp (nessuna API key necessaria)."""

import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime

import yt_dlp

# Filtro di ricerca YouTube "Durata: oltre 20 minuti"
LONG_FILTER = "EgIYAg%253D%253D"


class _SilentLogger:
    def debug(self, msg: str) -> None:
        pass

    def warning(self, msg: str) -> None:
        pass

    def error(self, msg: str) -> None:
        pass


BASE_OPTS = {"quiet": True, "no_warnings": True, "skip_download": True, "logger": _SilentLogger()}


@dataclass
class VideoStats:
    id: str
    title: str
    channel: str
    channel_followers: int | None
    duration: int            # secondi
    views: int
    likes: int | None
    comments: int | None
    upload_date: date | None
    heatmap: list[float] = field(default_factory=list)  # intensità "Most replayed", 0-1, in ordine temporale

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.id}"

    @property
    def age_days(self) -> int | None:
        return (date.today() - self.upload_date).days if self.upload_date else None


def search(query: str, limit: int = 20, long_only: bool = True) -> list[str]:
    """ID dei video trovati per una ricerca (per rilevanza)."""
    url = "https://www.youtube.com/results?search_query=" + urllib.parse.quote(query)
    if long_only:
        url += "&sp=" + LONG_FILTER
    return _flat_ids(url, limit)


def channel_videos(channel: str, limit: int = 20) -> list[str]:
    """Ultimi video di un canale. Accetta @handle, URL del canale o ID UC…"""
    if channel.startswith("http"):
        url = channel.rstrip("/")
    elif channel.startswith("@"):
        url = f"https://www.youtube.com/{channel}"
    else:
        url = f"https://www.youtube.com/channel/{channel}"
    if not url.endswith("/videos"):
        url += "/videos"
    return _flat_ids(url, limit)


def _flat_ids(url: str, limit: int) -> list[str]:
    opts = {**BASE_OPTS, "extract_flat": True, "playlistend": limit}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    ids = []
    for entry in info.get("entries") or []:
        if entry and entry.get("id") and len(entry["id"]) == 11:
            ids.append(entry["id"])
        if len(ids) >= limit:
            break
    return ids


def fetch_video(video_id: str) -> VideoStats | None:
    try:
        with yt_dlp.YoutubeDL(BASE_OPTS) as ydl:
            info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
    except yt_dlp.utils.DownloadError:
        return None  # video privato, rimosso o con restrizioni d'età
    upload = info.get("upload_date")
    return VideoStats(
        id=info["id"],
        title=info.get("title") or "",
        channel=info.get("channel") or info.get("uploader") or "",
        channel_followers=info.get("channel_follower_count"),
        duration=int(info.get("duration") or 0),
        views=int(info.get("view_count") or 0),
        likes=info.get("like_count"),
        comments=info.get("comment_count"),
        upload_date=datetime.strptime(upload, "%Y%m%d").date() if upload else None,
        heatmap=[float(p["value"]) for p in info.get("heatmap") or []],
    )


def fetch_many(video_ids: list[str], workers: int = 6) -> list[VideoStats]:
    unique = list(dict.fromkeys(video_ids))
    with ThreadPoolExecutor(workers) as pool:
        return [v for v in pool.map(fetch_video, unique) if v is not None]
