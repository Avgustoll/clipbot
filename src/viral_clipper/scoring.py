"""Steps 4-5: sceglie i momenti più virali.

Con ANTHROPIC_API_KEY usa Claude, che legge tutta la trascrizione e sceglie le clip.
Senza chiave usa un'euristica (energia audio + parole-hook) così il programma funziona lo stesso.
"""

import json
import os
import re
from dataclasses import dataclass

import numpy as np

from .copy import finalize, make_description, make_title
from .transcribe import Segment

MODEL = "claude-opus-5-5"


@dataclass
class Clip:
    start: float
    end: float
    score: float          # 0-100
    title: str
    hook: str
    reason: str
    first_segment: int
    last_segment: int
    description: str = ""


SYSTEM_PROMPT = """Sei un copywriter e video editor specializzato in short-form \
(YouTube Shorts, TikTok, Reels). Il tuo lavoro: leggere la trascrizione di un video lungo \
come se fosse uno script e ricavarne clip verticali che funzionino da sole, per uno \
spettatore che non ha mai visto il video originale e decide in un secondo se fermarsi.

## Come scegli i momenti
Cerca i passaggi con una tensione chiara: una tesi controcorrente, una rivelazione, \
un numero che sorprende, una storia con una svolta, un errore confessato, uno scontro di \
opinioni, una frase che qualcuno vorrebbe citare o mandare a un amico. Le clip migliori \
contengono un'unica idea completa: ingresso, sviluppo, chiusura.

## Dove taglia l'inizio
Lo spettatore vede la prima frase prima di decidere. Quindi l'inizio deve essere la frase \
più forte possibile che regga da sola: entra direttamente nel vivo (cold open), saltando \
riempitivi ("allora", "sì, guarda", "come dicevo"), domande dell'intervistatore che si \
capiscono dalla risposta e premesse lunghe. Va bene iniziare a metà di una risposta se \
quella frase è autonoma. Non iniziare con riferimenti a cose dette prima ("questo", \
"lui", "quella cosa") che lo spettatore non può capire.

## Dove taglia la fine
Chiudi subito dopo il payoff: la battuta, la conclusione, il numero finale, la morale. \
Non lasciare la clip a metà ragionamento e non trascinarla oltre: quello che viene dopo \
il payoff abbassa la retention. Le pause lunghe nella trascrizione sono buoni punti di taglio.

## Durata
Usa la durata più corta che contiene l'idea intera, dentro l'intervallo richiesto: \
le clip compatte trattengono meglio. Usa la durata massima solo se la storia ne ha bisogno.

## Varietà
Le clip non si sovrappongono e trattano temi o angoli diversi: meglio cinque idee \
diverse che cinque varianti della stessa.

## Da evitare
Sponsor e pubblicità, saluti e presentazioni, momenti che dipendono da immagini che \
non si vedono ("guarda qui"), parti tecniche senza un punto chiaro.

## Punteggi
Valuta ogni criterio da 0 a 10 con onestà e calibrazione: un momento nella media del \
video vale 5, un 9-10 è raro. Un punteggio gonfiato fa scegliere clip peggiori.

## Testi (nella lingua del video)
- title: il titolo dello Short, massimo 70 caratteri. Deve far venire voglia di guardare \
nelle prime 3 parole: usa una domanda, un numero concreto, una promessa specifica o un \
contrasto ("Perché X sbaglia tutto su Y", "3 errori che costano il 40%"). Niente \
titoli generici ("Un consiglio importante"), niente clickbait falso: deve essere vero \
rispetto a ciò che si sente. Niente maiuscolo integrale, al massimo un'emoji.
- hook_text: la scritta in sovraimpressione nei primi secondi, massimo 8 parole. \
Completa la prima frase parlata invece di ripeterla (es. pone la domanda a cui la \
clip risponde).
- description: tre parti separate da una riga vuota. (1) una frase che anticipa il \
payoff senza spoilerarlo e finisce con un invito leggero a guardare; (2) una riga di \
contesto (chi parla / di cosa tratta il video); (3) da 3 a 5 hashtag pertinenti sulla \
stessa riga: i primi 2 su tema e nicchia ("#startup", "#marketing"), gli ultimi di \
portata più ampia. Solo lettere minuscole, nessuno spazio dentro l'hashtag, nessun \
doppione. Non aggiungere #Shorts: lo inserisce il programma.
- reason: in una o due frasi, perché funziona, citando la frase di apertura.

Se ricevi Shorts di riferimento, studiane hook, ritmo e temi e privilegia momenti che \
possano funzionare allo stesso modo, senza copiarne le frasi. Se ricevi istruzioni \
dell'utente, seguile: hanno la precedenza su queste preferenze generali."""

CLIP_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["clips"],
    "properties": {
        "clips": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "first_segment", "last_segment", "hook_score", "standalone_score",
                    "emotion_score", "payoff_score", "shareability_score",
                    "title", "hook_text", "description", "reason",
                ],
                "properties": {
                    "first_segment": {"type": "integer"},
                    "last_segment": {"type": "integer"},
                    "hook_score": {"type": "integer"},
                    "standalone_score": {"type": "integer"},
                    "emotion_score": {"type": "integer"},
                    "payoff_score": {"type": "integer"},
                    "shareability_score": {"type": "integer"},
                    "title": {"type": "string"},
                    "hook_text": {"type": "string", "description": "Testo in sovraimpressione nei primi 2 secondi, max 8 parole"},
                    "description": {"type": "string"},
                    "reason": {"type": "string"},
                },
            },
        }
    },
}

WEIGHTS = {
    "hook_score": 0.30,
    "standalone_score": 0.20,
    "emotion_score": 0.20,
    "payoff_score": 0.15,
    "shareability_score": 0.15,
}


def find_clips(
    segments: list[Segment], top: int, min_len: float, max_len: float, audio_energy: np.ndarray | None = None,
    references: list[dict] | None = None, video_title: str = "", instructions: str = "",
    exclude: list[tuple[float, float]] | None = None,
) -> list[Clip]:
    """Sceglie le clip migliori.

    references: Shorts di riferimento dal Trainer ({title, views, hook, transcript, notes}).
    instructions: indicazioni libere dell'utente per il copywriter.
    exclude: intervalli (inizio, fine) già usati da altre clip, da non riproporre.
    """
    exclude = exclude or []
    if os.environ.get("ANTHROPIC_API_KEY"):
        clips = _llm_clips(segments, top, min_len, max_len, references or [], video_title, instructions, exclude)
    else:
        print("  ANTHROPIC_API_KEY non impostata: uso lo scoring euristico")
        clips = _heuristic_clips(segments, top + len(exclude) * 3, min_len, max_len, audio_energy, video_title)
    clips = [c for c in clips if not _overlaps_any(c, exclude)]
    return sorted(clips, key=lambda c: c.score, reverse=True)[:top]


# --- Claude ---------------------------------------------------------------

def _llm_clips(
    segments: list[Segment], top: int, min_len: float, max_len: float, references: list[dict],
    video_title: str, instructions: str, exclude: list[tuple[float, float]],
) -> list[Clip]:
    import anthropic

    lines, prev_end = [], None
    for s in segments:
        if prev_end is not None and s.start - prev_end >= 1.5:
            lines.append(f"(pausa {s.start - prev_end:.1f}s)")
        lines.append(f"[{s.id}] {s.start:.1f}-{s.end:.1f}s: {s.text}")
        prev_end = s.end
    transcript = "\n".join(lines)

    parts = []
    if references:
        items = []
        for r in references[:15]:
            items.append(
                f"<short titolo=\"{r.get('title', '')}\" views=\"{r.get('views') or '?'}\">\n"
                f"hook: {r.get('hook') or ''}\n"
                f"testo: {(r.get('transcript') or '')[:600]}\n"
                + (f"perché funziona: {r['notes']}\n" if r.get("notes") else "")
                + "</short>"
            )
        parts.append("<shorts_di_riferimento>\n" + "\n".join(items) + "\n</shorts_di_riferimento>")
    if instructions.strip():
        parts.append(f"<istruzioni_utente>\n{instructions.strip()}\n</istruzioni_utente>")
    if exclude:
        ranges = ", ".join(f"{a:.0f}-{b:.0f}s" for a, b in exclude)
        parts.append(f"Questi intervalli sono già usati da altre clip: non sovrapporti a nessuno di essi: {ranges}.")
    parts.append(
        (f"Video: \"{video_title}\".\n" if video_title else "")
        + f"Trova fino a {top + 3} clip candidate, ciascuna lunga tra {min_len:.0f} e {max_len:.0f} secondi."
    )
    parts.append(f"<trascrizione>\n{transcript}\n</trascrizione>")
    request = "\n\n".join(parts)

    client = anthropic.Anthropic()
    with client.beta.messages.stream(
        model=MODEL,
        max_tokens=32000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        system=SYSTEM_PROMPT,
        output_config={"effort": "high", "format": {"type": "json_schema", "schema": CLIP_SCHEMA}},
        messages=[{"role": "user", "content": request}],
    ) as stream:
        response = stream.get_final_message()

    if response.stop_reason == "refusal":
        raise RuntimeError(f"Claude ha rifiutato la richiesta: {response.stop_details}")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("Risposta troncata (max_tokens): riduci --top o la lunghezza del video")

    text = next(b.text for b in response.content if b.type == "text")
    by_id = {s.id: s for s in segments}
    clips = []
    for c in json.loads(text)["clips"]:
        first, last = c["first_segment"], c["last_segment"]
        if first not in by_id or last not in by_id or last < first:
            continue
        first, last = _fit_length(segments, first, last, min_len, max_len)
        score = sum(c[k] * w for k, w in WEIGHTS.items()) * 10
        text = " ".join(s.text for s in segments[first:last + 1])
        title, description = finalize(c["title"], c["description"], text, video_title)
        clips.append(Clip(
            by_id[first].start, by_id[last].end, round(score, 1),
            title, c["hook_text"], c["reason"], first, last, description,
        ))
    return _drop_overlaps(clips)


# --- Euristica di riserva ---------------------------------------------------

HOOK_PATTERNS = re.compile(
    r"\b(nessuno|mai|segreto|verità|incredibile|pazzesco|errore|soldi|milioni?|"
    r"never|nobody|secret|truth|crazy|insane|mistake|money|million)\b|[?!]",
    re.IGNORECASE,
)


def _heuristic_clips(
    segments: list[Segment], top: int, min_len: float, max_len: float, audio_energy: np.ndarray | None,
    video_title: str = "",
) -> list[Clip]:
    candidates = []
    for i in range(len(segments)):
        for j in range(i, len(segments)):
            length = segments[j].end - segments[i].start
            if length > max_len:
                break
            if length < min_len:
                continue
            window = segments[i:j + 1]
            text = " ".join(s.text for s in window)
            hook_hits = len(HOOK_PATTERNS.findall(window[0].text))
            density = len(HOOK_PATTERNS.findall(text)) / max(len(text.split()), 1)
            energy = _window_energy(audio_energy, window[0].start, window[-1].end)
            score = 40 * min(hook_hits, 2) / 2 + 30 * min(density * 20, 1) + 30 * energy
            candidates.append(Clip(
                window[0].start, window[-1].end, round(score, 1),
                make_title(text), " ".join(window[0].text.split()[:8]),
                f"hook={hook_hits}, densità={density:.2f}, energia={energy:.2f}", i, j,
                finalize("", make_description(text, video_title), text, video_title)[1],
            ))
    return _drop_overlaps(sorted(candidates, key=lambda c: c.score, reverse=True))[: top]


def _window_energy(energy: np.ndarray | None, start: float, end: float) -> float:
    """Energia media normalizzata 0-1 della finestra (energy ha un valore per secondo)."""
    if energy is None or len(energy) == 0:
        return 0.5
    chunk = energy[int(start): max(int(end), int(start) + 1)]
    lo, hi = np.percentile(energy, [10, 95])
    return float(np.clip((chunk.mean() - lo) / (hi - lo + 1e-9), 0, 1))


# --- Utilità ----------------------------------------------------------------

def _fit_length(segments: list[Segment], first: int, last: int, min_len: float, max_len: float) -> tuple[int, int]:
    """Allarga o accorcia la clip, a segmenti interi, finché rientra nella durata richiesta."""
    while segments[last].end - segments[first].start > max_len and last > first:
        last -= 1
    while segments[last].end - segments[first].start < min_len and last + 1 < len(segments):
        last += 1
    return first, last


def _overlaps_any(clip: Clip, ranges: list[tuple[float, float]]) -> bool:
    return any(clip.start < b and clip.end > a for a, b in ranges)


def _drop_overlaps(clips: list[Clip]) -> list[Clip]:
    kept: list[Clip] = []
    for clip in sorted(clips, key=lambda c: c.score, reverse=True):
        if all(clip.end <= k.start or clip.start >= k.end for k in kept):
            kept.append(clip)
    return kept
