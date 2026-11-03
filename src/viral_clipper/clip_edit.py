"""Modifiche a una singola clip: allungare/accorciare un lato, rigenerarla con un altro momento."""

from pathlib import Path

from .scoring import Clip
from .transcribe import Segment, load_transcript

MIN_CLIP = 3.0       # secondi
MAX_CLIP = 180.0     # limite di durata degli Shorts

ACTIONS = ("extend_sentence", "shrink_sentence", "nudge")


def adjust_bounds(
    segments: list[Segment], start: float, end: float, edge: str, action: str, seconds: float = 0.0,
) -> tuple[float, float]:
    """Nuovi estremi della clip, sempre allineati a frasi o parole.

    edge: "start" | "end"
    action:
      extend_sentence  aggiunge la frase prima (start) o dopo (end)
      shrink_sentence  toglie la prima (start) o l'ultima (end) frase
      nudge            sposta il lato di `seconds` (+/-) e lo aggancia alla parola più vicina
    """
    if edge not in ("start", "end") or action not in ACTIONS:
        raise ValueError("Modifica non valida")
    if not segments:
        raise ValueError("Trascrizione non disponibile")

    if action == "nudge":
        words = [w for s in segments for w in s.words]
        if edge == "start":
            target = start + seconds
            new = min((w.start for w in words), key=lambda t: abs(t - target), default=start)
            # Lo spostamento deve avere effetto: se l'aggancio riporta al punto di partenza, prendi la parola successiva
            if new == start:
                later = [w.start for w in words if (w.start > start if seconds > 0 else w.start < start)]
                new = (min(later) if seconds > 0 else max(later)) if later else start
            start = new
        else:
            target = end + seconds
            new = min((w.end for w in words), key=lambda t: abs(t - target), default=end)
            if new == end:
                later = [w.end for w in words if (w.end > end if seconds > 0 else w.end < end)]
                new = (min(later) if seconds > 0 else max(later)) if later else end
            end = new
    elif edge == "start":
        if action == "extend_sentence":
            before = [s.start for s in segments if s.start < start - 0.05]
            if not before:
                raise ValueError("La clip inizia già dalla prima frase del video")
            start = max(before)
        else:
            after = [s.start for s in segments if s.start > start + 0.05]
            if not after:
                raise ValueError("Non ci sono frasi da togliere all'inizio")
            start = min(after)
    else:
        if action == "extend_sentence":
            after = [s.end for s in segments if s.end > end + 0.05]
            if not after:
                raise ValueError("La clip finisce già con l'ultima frase del video")
            end = min(after)
        else:
            before = [s.end for s in segments if s.end < end - 0.05]
            if not before:
                raise ValueError("Non ci sono frasi da togliere alla fine")
            end = max(before)

    if end - start < MIN_CLIP:
        raise ValueError(f"La clip non può durare meno di {MIN_CLIP:.0f} secondi")
    if end - start > MAX_CLIP:
        raise ValueError(f"Gli Shorts durano al massimo {MAX_CLIP:.0f} secondi")
    return round(start, 3), round(end, 3)


def clip_from_row(row: dict, segments: list[Segment]) -> Clip:
    """Ricostruisce la Clip (con gli indici dei segmenti) da una riga del database."""
    inside = [s.id for s in segments if s.end > row["start"] and s.start < row["end"]]
    first, last = (inside[0], inside[-1]) if inside else (0, 0)
    return Clip(row["start"], row["end"], row["score"] or 0, row["title"] or "", row["hook"] or "",
                row["reason"] or "", first, last, row.get("description") or "")


def load_job(job_dir: str) -> tuple[Path, list[Segment]]:
    job = Path(job_dir)
    source, transcript = job / "source.mp4", job / "transcript.json"
    if not source.exists() or not transcript.exists():
        raise ValueError("Video sorgente o trascrizione non trovati: rigenera tutte le clip del video.")
    return source, load_transcript(transcript)
