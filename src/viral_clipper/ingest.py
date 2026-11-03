"""Step 1: scarica (o copia) il video sorgente ed estrae l'audio per l'analisi."""

import re
import shutil
import subprocess
from pathlib import Path

import static_ffmpeg

static_ffmpeg.add_paths()
FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")


def run_ffmpeg(args: list[str], cwd: Path | None = None) -> None:
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *args]
    result = subprocess.run(cmd, check=False, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg fallito:\n{result.stderr}")


def probe_duration(path: Path) -> float:
    out = subprocess.run(
        [FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


class DownloadFailed(Exception):
    pass


class _SilentLogger:
    """yt-dlp stampa gli errori da solo: li zittiamo e mostriamo un messaggio nostro."""

    def debug(self, msg: str) -> None:
        pass

    def warning(self, msg: str) -> None:
        pass

    def error(self, msg: str) -> None:
        pass


def is_url(source: str) -> bool:
    return source.startswith(("http://", "https://"))


def video_title(source: str) -> str:
    """Titolo del video: da YouTube (solo metadati, niente download) o dal nome del file."""
    if not is_url(source):
        return Path(source).expanduser().stem
    import yt_dlp

    opts = {"quiet": True, "no_warnings": True, "skip_download": True, "logger": _SilentLogger()}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(source, download=False)
    except yt_dlp.utils.DownloadError as e:
        reason = str(e).removeprefix("ERROR: ").strip()
        raise DownloadFailed(f"Download non riuscito: {reason}") from None
    return info.get("title") or info.get("id") or "video"


def safe_folder_name(title: str, max_len: int = 80) -> str:
    """Rende il titolo valido come nome di cartella su macOS, Windows e Linux."""
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', " ", title)
    name = re.sub(r"\s+", " ", name).strip(" .")
    return name[:max_len].rstrip(" .") or "video"


def fetch_source(source: str, job_dir: Path) -> Path:
    """Restituisce il percorso di job_dir/source.mp4, scaricandolo se `source` è un URL."""
    target = job_dir / "source.mp4"
    if target.exists():
        return target

    if is_url(source):
        import yt_dlp

        opts = {
            "format": "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080]/b",
            "merge_output_format": "mp4",
            "outtmpl": str(job_dir / "source.%(ext)s"),
            "ffmpeg_location": str(Path(FFMPEG).parent),
            "quiet": True,
            "noprogress": True,
            "no_warnings": True,
            "logger": _SilentLogger(),
        }
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([source])
        except yt_dlp.utils.DownloadError as e:
            reason = str(e).removeprefix("ERROR: ").strip()
            raise DownloadFailed(f"Download non riuscito: {reason}") from None
        if not target.exists():
            raise RuntimeError("Download completato ma source.mp4 non trovato")
    else:
        src = Path(source).expanduser().resolve()
        if not src.exists():
            raise FileNotFoundError(src)
        if src.suffix.lower() == ".mp4":
            shutil.copy(src, target)
        else:
            run_ffmpeg(["-i", str(src), "-c:v", "libx264", "-c:a", "aac", str(target)])
    return target


def extract_audio(video: Path, job_dir: Path) -> Path:
    """Audio mono 16 kHz: il formato che si aspetta Whisper."""
    wav = job_dir / "audio.wav"
    if not wav.exists():
        run_ffmpeg(["-i", str(video), "-vn", "-ac", "1", "-ar", "16000", str(wav)])
    return wav
