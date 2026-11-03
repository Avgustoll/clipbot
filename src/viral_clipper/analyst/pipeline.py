"""Analisi deterministica: ricerca → metriche → classifica → report. Usata dal comando e dall'agente."""

import csv
import json
from datetime import datetime
from pathlib import Path

from .metrics import MIN_DURATION, Analysis, analyze
from .store import Store
from .youtube import channel_videos, fetch_many, search

ROOT = Path("analisi")


class Session:
    """Una sessione di analisi: tiene lo storico e tutti i video analizzati."""

    def __init__(self, root: Path = ROOT):
        self.root = root.resolve()
        self.store = Store(self.root / "storico.db")
        self.results: dict[str, Analysis] = {}
        self.out_dir = self.root / datetime.now().strftime("%Y-%m-%d_%H-%M")

    def discover(self, queries: list[str], channels: list[str], per_source: int) -> list[str]:
        ids: list[str] = []
        for q in queries:
            found = search(q, per_source)
            print(f"  ricerca \"{q}\": {len(found)} video")
            ids += found
        for c in channels:
            found = channel_videos(c, per_source)
            print(f"  canale {c}: {len(found)} video")
            ids += found
        return list(dict.fromkeys(ids))

    def analyze_ids(self, ids: list[str]) -> list[Analysis]:
        videos = fetch_many([i for i in ids if i not in self.results])
        self.store.save(videos)
        now = datetime.now()
        for v in videos:
            self.results[v.id] = analyze(v, self.store.history(v.id), now)
        return [self.results[i] for i in ids if i in self.results]

    def ranked(self) -> list[Analysis]:
        return sorted(self.results.values(), key=lambda a: (a.qualified, a.score), reverse=True)

    def write(self, commentary: str | None = None) -> Path:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        rows = [a.to_dict() for a in self.ranked()]
        (self.out_dir / "risultati.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2))
        with open(self.out_dir / "risultati.csv", "w", newline="", encoding="utf-8") as f:
            flat = [{k: (json.dumps(v) if isinstance(v, dict) else v) for k, v in r.items()} for r in rows]
            if flat:
                writer = csv.DictWriter(f, fieldnames=list(flat[0]))
                writer.writeheader()
                writer.writerows(flat)
        report = self.out_dir / "report.md"
        report.write_text(render_report(self.ranked(), commentary), encoding="utf-8")
        return report


def update_all(root: Path = ROOT) -> int:
    """Nuova rilevazione di tutti i video già visti: è ciò che costruisce il trend a 3 settimane."""
    store = Store(root.resolve() / "storico.db")
    ids = store.tracked_ids()
    videos = fetch_many(ids)
    store.save(videos)
    return len(videos)


def render_report(results: list[Analysis], commentary: str | None) -> str:
    qualified = [a for a in results if a.qualified]
    pending = [a for a in results if not a.qualified and None in a.checks.values()
               and False not in a.checks.values()]
    rejected = [a for a in results if False in a.checks.values()]

    lines = [f"# Analisi video YouTube — {datetime.now():%d/%m/%Y %H:%M}", ""]
    lines += [
        (f"Video analizzati: **{len(results)}** · qualificati: **{len(qualified)}** · "
        f"da misurare: **{len(pending)}** · scartati: **{len(rejected)}**"), "",
        "Criteri: durata > 30 min · tenuta stimata ≥ 50% · views in crescita nelle ultime 3 settimane.", "",
    ]
    if commentary:
        lines += ["## Analisi dell'agente", "", commentary.strip(), ""]

    lines += ["## Qualificati", ""]
    lines += _table(qualified) if qualified else ["Nessun video soddisfa tutti i criteri.", ""]

    if pending:
        lines += [
            "## Da misurare", "",
            ("Passano durata ed engagement, ma il video ha più di 3 settimane e non c'è ancora uno storico "
            "delle views. Rilancia `viral-analyst aggiorna` tra almeno 1 giorno per misurare il trend."), "",
        ]
        lines += _table(pending)

    if rejected:
        lines += ["## Scartati", "", "| Video | Motivo |", "|---|---|"]
        for a in rejected:
            reasons = [k for k, v in a.checks.items() if v is False]
            lines.append(f"| [{_esc(a.video.title[:70])}]({a.video.url}) | {_reasons(a, reasons)} |")
        lines.append("")

    lines += [
        "## Note sui dati", "",
        ("- **Tenuta stimata**: YouTube mostra la retention solo al proprietario del canale. Qui è stimata dalla "
        "heatmap pubblica \"Più riprodotti\" (andamento di fondo rispetto all'inizio del video): è adatta a "
        "confrontare video tra loro, e tende a essere più alta della retention ufficiale di YouTube Studio."),
        ("- **Trend 3 settimane**: per video usciti da più di 21 giorni serve lo storico delle rilevazioni "
        "(`analisi/storico.db`). *Momentum* = views/giorno recenti ÷ views/giorno medie dall'uscita: "
        "≥ 1 significa che il video sta andando meglio della sua media storica."),
        "",
    ]
    return "\n".join(lines)


def _table(items: list[Analysis]) -> list[str]:
    out = [
        "| # | Video | Canale | Durata | Views | Tenuta | Like | Views 3 sett. | Momentum | Trend | Punti |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for i, a in enumerate(items, 1):
        d, t = a.to_dict(), a.trend
        out.append(
            f"| {i} | [{_esc(a.video.title[:60])}]({a.video.url}) | {_esc(a.video.channel)} | {d['durata_min']}′ | "
            f"{_n(a.video.views)} | {d['retention_stimata'] or '—'} | {d['like_rate'] or '—'} | "
            f"{_n(t.views_window)} | {d['momentum'] if d['momentum'] is not None else '—'} | {t.label} | {d['punteggio']} |"
        )
    out.append("")
    return out


def _reasons(a: Analysis, keys: list[str]) -> str:
    text = []
    for k in keys:
        if k == "durata_30min":
            text.append(f"durata {a.video.duration // 60}′ (min {MIN_DURATION // 60}′)")
        elif k == "engagement_medio":
            text.append(f"engagement {a.engagement} (tenuta {a.to_dict()['retention_stimata'] or '—'})")
        elif k == "views_in_crescita":
            text.append(f"trend {a.trend.label} (momentum {a.to_dict()['momentum']})")
    return "; ".join(text)


def _n(x: int | None) -> str:
    if x is None:
        return "—"
    if x >= 1_000_000:
        return f"{x / 1_000_000:.1f}M"
    if x >= 1_000:
        return f"{x / 1_000:.0f}k"
    return str(x)


def _esc(s: str) -> str:
    return s.replace("|", "/").replace("[", "(").replace("]", ")")
