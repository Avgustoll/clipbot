"""Titolo, descrizione e hashtag delle clip, anche senza Claude (modalità euristica).

Con Claude i testi li scrive il modello; qui sotto c'è `finalize`, che li rifinisce in ogni caso
(lunghezza del titolo, almeno 3 hashtag, niente duplicati).
"""

import re
from collections import Counter

TITLE_MAX = 70
MIN_TAGS, MAX_TAGS = 3, 5

FILLER_START = re.compile(
    r"^(allora|sì|si|no|ok|okay|beh|ecco|cioè|guarda|senti|dunque|quindi|e|ma|però|"
    r"well|so|yeah|yes|right|um|uh|and|but)\b[\s,.:;-]*", re.IGNORECASE,
)
HOOKY = re.compile(
    r"\b(nessuno|mai|segreto|verità|incredibile|pazzesco|errore|errori|soldi|milioni?|miliardi?|"
    r"problema|paura|storia|motivo|perché|come|devi|smettila|never|nobody|secret|truth|mistake|"
    r"money|million|why|how|stop)\b|[?!]|\d",
    re.IGNORECASE,
)
STOPWORDS = set("""
il lo la i gli le un uno una di a da in con su per tra fra e ed o ma però anche che chi cui non si
è sono era ho ha hanno abbiamo avere essere fare fatto fa come più meno molto poi già ancora quando
dove quello quella questo questa questi queste quelli cosa cose così quindi perché allora ecco
mi ti ci vi ne lo li lei lui noi voi loro mio mia tuo tua suo sua nostro vostro
the a an and or but if of to in on at for with from by as is are was were be been it its this that
these those i you he she we they me my your his her our their not no so do does did have has had
just very more most can will would could should about into than then there here what which who
""".split())


def _words(text: str) -> list[str]:
    return re.findall(r"[A-Za-zÀ-ÿ0-9']+", text)


def keywords(text: str, n: int = 6, extra: str = "") -> list[str]:
    """Parole più ricorrenti e significative (nomi/temi), le più frequenti per prime."""
    counts = Counter(
        w.lower() for w in _words(text)
        if len(w) > 3 and w.lower() not in STOPWORDS and not w.isdigit()
    )
    boost = {w.lower() for w in _words(extra)}
    ranked = sorted(counts, key=lambda w: (counts[w] + (1 if w in boost else 0), len(w)), reverse=True)
    return ranked[:n]


def hashtag(word: str) -> str:
    return "#" + re.sub(r"[^\wÀ-ÿ]", "", word.lower().replace("'", ""))


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?…])\s+", text.strip()) if len(s.strip()) > 12]


def _trim(text: str, limit: int) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    room = text[:limit - 1]  # un carattere è per "…"
    cut = (room.rsplit(" ", 1)[0] if " " in room else room).rstrip(",;:-– ")
    return cut + "…"


def _clean(sentence: str) -> str:
    s = FILLER_START.sub("", sentence).strip(" -–,;:")
    s = FILLER_START.sub("", s)  # "e allora, ..." → due riempitivi di fila
    return (s[:1].upper() + s[1:]) if s else s


def make_title(text: str) -> str:
    """La frase più forte della clip (domanda, numero, parola-hook), ripulita e accorciata."""
    sentences = [_clean(s) for s in _sentences(text)] or [_clean(text)]
    sentences = [s for s in sentences if s] or [text.strip()]

    def strength(s: str) -> float:
        hits = len(HOOKY.findall(s))
        length_pen = abs(len(s) - 55) / 55  # titoli di ~55 caratteri
        return hits - length_pen + (0.5 if s.endswith("?") else 0)

    best = max(sentences[:6], key=strength)
    return _trim(best.rstrip(".,;:"), TITLE_MAX)


def make_description(text: str, video_title: str = "") -> str:
    sentences = [_clean(s) for s in _sentences(text)]
    title = make_title(text).rstrip("…").lower()
    # la prima riga non deve ripetere il titolo: prende la frase successiva più significativa
    others = [s for s in sentences if not s.lower().startswith(title)] or sentences
    lead = _trim(max(others[:4], key=lambda s: len(HOOKY.findall(s))), 110) if others else ""
    kws = keywords(text, MAX_TAGS, extra=video_title)
    tags = " ".join(dict.fromkeys(hashtag(k) for k in kws[:MAX_TAGS]))
    parts = [lead] if lead else []
    if video_title:
        parts.append(f"Dal video: {_trim(video_title, 80)}")
    if tags:
        parts.append(tags)
    return "\n\n".join(parts)


def finalize(title: str, description: str, transcript: str = "", video_title: str = "") -> tuple[str, str]:
    """Titolo ≤ 70 caratteri e descrizione con 3-5 hashtag unici (#Shorts lo aggiunge l'upload)."""
    title = _trim(title.strip().strip('"'), TITLE_MAX)
    found = list(dict.fromkeys(t.lower() for t in re.findall(r"#\w+", description)))
    found = [t for t in found if t != "#shorts"]
    if len(found) < MIN_TAGS:
        for k in keywords(transcript or description, 8, extra=video_title):
            t = hashtag(k)
            if t not in found and len(t) > 3:
                found.append(t)
            if len(found) >= MIN_TAGS:
                break
    body = re.sub(r"\s*#\w+", "", description).strip()
    tags = " ".join(found[:MAX_TAGS])
    return title, "\n\n".join(p for p in (body, tags) if p)
