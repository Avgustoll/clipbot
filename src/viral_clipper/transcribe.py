"""Step 2: trascrizione con timestamp per ogni parola (faster-whisper)."""

import json
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class Word:
    start: float
    end: float
    text: str


@dataclass
class Segment:
    id: int
    start: float
    end: float
    text: str
    words: list[Word]


def transcribe(wav: Path, job_dir: Path, model_size: str = "small", language: str | None = None) -> list[Segment]:
    cache = job_dir / "transcript.json"
    if cache.exists():
        return load_transcript(cache)

    from faster_whisper import WhisperModel

    model = WhisperModel(model_size, device="auto", compute_type="int8")
    words = _transcribe_words(model, wav, language, vad=True, batched=True)
    if not words:
        # Il filtro VAD scarta la voce sopra la musica (canzoni, intro): riprova senza
        print("  nessun parlato con il filtro VAD, riprovo senza…")
        words = _transcribe_words(model, wav, language, vad=False)
    segments = split_sentences(words)
    if not segments:
        return []

    cache.write_text(json.dumps([asdict(s) for s in segments], ensure_ascii=False, indent=1))
    return segments


BATCH_SIZE = 8


def _transcribe_words(model, wav: Path, language: str | None, vad: bool, batched: bool = False) -> list[Word]:
    if batched and vad:
        # Decodifica più finestre audio in parallelo: 2-4x più veloce, stessa precisione
        from faster_whisper import BatchedInferencePipeline

        raw_segments, info = BatchedInferencePipeline(model=model).transcribe(
            str(wav), language=language, word_timestamps=True, batch_size=BATCH_SIZE,
        )
    else:
        raw_segments, info = model.transcribe(str(wav), language=language, word_timestamps=True, vad_filter=vad)
    print(f"  lingua rilevata: {info.language} ({info.language_probability:.0%})")
    words = []
    for seg in raw_segments:
        words += [Word(w.start, w.end, w.word.strip()) for w in (seg.words or []) if w.word.strip()]
        print(f"  [{seg.start:7.1f}s] {seg.text.strip()[:80]}")
    return words


SENTENCE_END =(".", "?", "!", "…")
WEAK_BREAK = (",", ";", ":")


def split_sentences(words: list[Word], max_len: float = 12.0, pause: float = 0.7) -> list[Segment]:
    """Ricompone le parole in frasi, così le clip iniziano e finiscono su confini naturali.

    Whisper spezza i segmenti ogni ~5 secondi, spesso a metà frase. Qui si va a capo su
    punteggiatura forte o su una pausa lunga; se la frase supera max_len si spezza alla
    prima virgola utile.
    """
    segments: list[Segment] = []
    current: list[Word] = []

    def flush() -> None:
        if current:
            text = " ".join(w.text for w in current)
            segments.append(Segment(len(segments), current[0].start, current[-1].end, text, current.copy()))
            current.clear()

    for i, word in enumerate(words):
        current.append(word)
        nxt = words[i + 1] if i + 1 < len(words) else None
        gap = nxt.start - word.end if nxt else 0
        length = word.end - current[0].start
        if (
            word.text.endswith(SENTENCE_END)
            or gap > pause
            or (length > max_len and word.text.endswith(WEAK_BREAK))
            or length > max_len * 2
        ):
            flush()
    flush()
    return segments


def load_transcript(path: Path) -> list[Segment]:
    data = json.loads(path.read_text())
    return [
        Segment(s["id"], s["start"], s["end"], s["text"], [Word(**w) for w in s["words"]])
        for s in data
    ]
