"""Pipeline completa del clipper, riutilizzabile dalla riga di comando e dall'interfaccia web."""

import json
from dataclasses import asdict
from pathlib import Path

RENDER_WORKERS = 3  # l'encoder hardware del Mac regge poche sessioni in parallelo


class ClipperError(Exception):
    pass


def run_clipper(
    source: str,
    top: int = 5,
    min_len: float = 15,
    max_len: float = 60,
    whisper: str = "small",
    lang: str | None = None,
    center: bool = False,
    jobs_root: Path = Path("jobs"),
    references: list[dict] | None = None,
    instructions: str = "",
) -> dict:
    """Scarica, trascrive, sceglie e rende le clip. Restituisce titolo, cartella e clip esportate."""
    from .audio import energy_per_second
    from .ingest import DownloadFailed, extract_audio, fetch_source, is_url, video_title
    from .render import render_clip
    from .scoring import find_clips
    from .transcribe import transcribe

    if not is_url(source) and not Path(source).expanduser().is_file():
        raise ClipperError(
            f"File non trovato: {Path(source).expanduser()}\n"
            "Controlla il percorso: puoi trascinare il video nel Terminale per incollarlo."
        )

    print("1/5 Recupero il video…")
    try:
        title = video_title(source)
        job_dir = job_folder(Path(jobs_root).resolve(), title, source)
        print(f"  \"{title}\" -> {job_dir}")
        video = fetch_source(source, job_dir)
    except DownloadFailed as e:
        raise ClipperError(f"{e}\nControlla che il link sia completo e che il video sia pubblico.") from None
    wav = extract_audio(video, job_dir)

    print(f"2/5 Trascrivo con Whisper ({whisper})…")
    segments = transcribe(wav, job_dir, whisper, lang)
    if not segments:
        raise ClipperError("Nessun parlato trovato nel video.")

    print("3/5 Analizzo l'audio…")
    energy = energy_per_second(wav)

    print("4/5 Cerco i momenti virali…")
    clips = find_clips(segments, top, min_len, max_len, energy, references, title, instructions)
    if not clips:
        raise ClipperError("Nessuna clip trovata: il video potrebbe essere più corto della durata minima.")

    print(f"5/5 Esporto {len(clips)} clip…")
    clips_dir = job_dir / "clips"
    # Una nuova esecuzione sostituisce le clip precedenti, così clips/ non mescola risultati diversi
    for old in clips_dir.glob("clip_*.mp4"):
        old.unlink()
    from concurrent.futures import ThreadPoolExecutor

    def render(item):
        i, clip = item
        return render_clip(video, clip, segments, clips_dir, job_dir / "lavoro", i, track_faces=not center)

    # ffmpeg e il rilevamento volti non bloccano il GIL: più clip insieme sfruttano tutti i core
    with ThreadPoolExecutor(max_workers=min(RENDER_WORKERS, len(clips))) as pool:
        paths = list(pool.map(render, enumerate(clips, 1)))
    report = []
    for i, (clip, path) in enumerate(zip(clips, paths), 1):
        print(f"  #{i} [{clip.score:5.1f}] {clip.start:7.1f}-{clip.end:7.1f}s  {clip.title}")
        report.append({"file": path.name, "path": str(path), **asdict(clip)})
    (job_dir / "clips.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\nFatto. Clip in {clips_dir}")
    return {"title": title, "job_dir": str(job_dir), "clips": report}


def job_folder(root: Path, title: str, source: str) -> Path:
    """jobs/<titolo del video>/. Se il nome è già usato da un altro video aggiunge (2), (3)…"""
    from .ingest import safe_folder_name

    base = safe_folder_name(title)
    for n in range(1, 100):
        job_dir = root / (base if n == 1 else f"{base} ({n})")
        meta = job_dir / "meta.json"
        if not job_dir.exists() or (
            meta.exists() and _same_source(json.loads(meta.read_text()).get("source", ""), source)
        ):
            job_dir.mkdir(parents=True, exist_ok=True)
            meta.write_text(json.dumps({"title": title, "source": source}, ensure_ascii=False, indent=2))
            return job_dir
    raise ClipperError(f"Troppe cartelle con il nome \"{base}\" in {root}")


def _same_source(a: str, b: str) -> bool:
    """Stesso video anche se il link è scritto in forme diverse (youtu.be, watch?v=, shorts/…)."""
    import re

    def key(s: str) -> str:
        m = re.search(r"(?:v=|youtu\.be/|shorts/|live/|embed/)([A-Za-z0-9_-]{11})", s)
        return m.group(1) if m else s

    return key(a) == key(b)
