import pytest

from viral_clipper.clip_edit import adjust_bounds, clip_from_row
from viral_clipper.scoring import Clip, _overlaps_any
from viral_clipper.transcribe import Segment, Word


def make_segments() -> list[Segment]:
    """4 frasi da 5 secondi, una parola al secondo, pausa di 1s tra le frasi."""
    segs = []
    for i in range(4):
        start = i * 6.0
        words = [Word(start + k, start + k + 0.8, f"w{i}{k}") for k in range(5)]
        segs.append(Segment(i, start, start + 4.8, " ".join(w.text for w in words), words))
    return segs


SEGS = make_segments()   # frasi: 0-4.8, 6-10.8, 12-16.8, 18-22.8


def test_aggiunge_e_toglie_frasi_all_inizio():
    assert adjust_bounds(SEGS, 6.0, 16.8, "start", "extend_sentence") == (0.0, 16.8)
    assert adjust_bounds(SEGS, 6.0, 16.8, "start", "shrink_sentence") == (12.0, 16.8)


def test_aggiunge_e_toglie_frasi_alla_fine():
    assert adjust_bounds(SEGS, 6.0, 16.8, "end", "extend_sentence") == (6.0, 22.8)
    assert adjust_bounds(SEGS, 6.0, 16.8, "end", "shrink_sentence") == (6.0, 10.8)


def test_spostamento_di_un_secondo_si_aggancia_alle_parole():
    assert adjust_bounds(SEGS, 6.0, 16.8, "start", "nudge", -1) == (4.0, 16.8)   # parola che inizia a 4.0
    assert adjust_bounds(SEGS, 6.0, 16.8, "start", "nudge", 1) == (7.0, 16.8)
    assert adjust_bounds(SEGS, 6.0, 16.8, "end", "nudge", 1) == (6.0, 18.8)      # fine parola più vicina a 17.8
    assert adjust_bounds(SEGS, 6.0, 16.8, "end", "nudge", -1) == (6.0, 15.8)


def test_lo_spostamento_ha_sempre_effetto_anche_nelle_pause():
    # da 10.8 (fine frase) +1s cadrebbe nella pausa: deve comunque andare avanti
    _, end = adjust_bounds(SEGS, 6.0, 10.8, "end", "nudge", 1)
    assert end > 10.8


def test_limiti_del_video_e_durata_minima():
    with pytest.raises(ValueError, match="prima frase"):
        adjust_bounds(SEGS, 0.0, 10.8, "start", "extend_sentence")
    with pytest.raises(ValueError, match="ultima frase"):
        adjust_bounds(SEGS, 6.0, 22.8, "end", "extend_sentence")
    with pytest.raises(ValueError, match="meno di"):
        adjust_bounds(SEGS, 6.0, 10.8, "end", "shrink_sentence")


def test_clip_ricostruita_dagli_estremi():
    c = clip_from_row({"start": 6.0, "end": 16.8, "score": 50, "title": "t", "hook": "h", "reason": "r"}, SEGS)
    assert (c.first_segment, c.last_segment) == (1, 2)


def test_esclusione_degli_intervalli_gia_usati():
    clip = Clip(10, 20, 50, "", "", "", 0, 0)
    assert _overlaps_any(clip, [(15, 25)])
    assert not _overlaps_any(clip, [(0, 10), (20, 30)])
