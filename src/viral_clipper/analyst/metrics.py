"""Metriche e filtri dell'analista.

Criteri:
1. durata > 30 minuti
2. engagement almeno medio: retention stimata >= 50%
3. views in crescita nelle ultime 3 settimane (non solo in base alla data di uscita)

Retention: YouTube la mostra solo al proprietario del canale. Qui la stimiamo dalla
heatmap pubblica "Più riprodotti": trattiamo la curva come una curva di pubblico,
normalizzata sul livello dei primi secondi, e ne prendiamo la media. È una stima,
utile per confrontare video tra loro, non il dato ufficiale di YouTube Studio.
Se la heatmap manca si ripiega sul rapporto like/views (>= 2% = engagement medio).
"""

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .store import Snapshot
from .youtube import VideoStats

MIN_DURATION = 30 * 60
MIN_RETENTION = 0.50
MIN_LIKE_RATE = 0.02         # ripiego quando non c'è la heatmap
WINDOW_DAYS = 21
GROWTH_MOMENTUM = 1.0        # views/giorno recenti >= media dall'uscita
MIN_SPAN_DAYS = 1.0          # distanza minima tra due rilevazioni per stimare un trend


@dataclass
class Trend:
    views_window: int | None      # views guadagnate nelle ultime 3 settimane (misurate o stimate)
    daily_recent: float | None    # views/giorno nel periodo osservato
    daily_lifetime: float | None  # views/giorno medie dall'uscita
    momentum: float | None        # daily_recent / daily_lifetime
    acceleration: float | None    # ultima settimana vs settimane precedenti
    label: str                    # "in crescita", "stabile", "in calo", "da misurare"
    source: str                   # come è stato calcolato


@dataclass
class Analysis:
    video: VideoStats
    retention_est: float | None
    like_rate: float | None
    comment_rate: float | None
    engagement: str
    trend: Trend
    checks: dict[str, bool | None] = field(default_factory=dict)
    score: float = 0.0

    @property
    def qualified(self) -> bool:
        return all(self.checks.values())

    def to_dict(self) -> dict:
        v, t = self.video, self.trend
        return {
            "id": v.id, "url": v.url, "titolo": v.title, "canale": v.channel,
            "iscritti_canale": v.channel_followers,
            "durata_min": round(v.duration / 60), "views": v.views,
            "uscito": v.upload_date and v.upload_date.isoformat(), "eta_giorni": v.age_days,
            "retention_stimata": _pct(self.retention_est), "like_rate": _pct(self.like_rate, 2),
            "comment_rate": _pct(self.comment_rate, 3), "engagement": self.engagement,
            "views_ultime_3_settimane": t.views_window, "views_giorno_recenti": _round(t.daily_recent),
            "views_giorno_dall_uscita": _round(t.daily_lifetime), "momentum": _round(t.momentum, 2),
            "accelerazione": _round(t.acceleration, 2), "trend": t.label, "fonte_trend": t.source,
            "criteri": self.checks, "qualificato": self.qualified, "punteggio": round(self.score, 1),
        }


def analyze(video: VideoStats, history: list[Snapshot], now: datetime | None = None) -> Analysis:
    now = now or datetime.now()
    retention = estimate_retention(video.heatmap)
    like_rate = video.likes / video.views if video.likes is not None and video.views else None
    comment_rate = video.comments / video.views if video.comments is not None and video.views else None
    trend = compute_trend(video, history, now)

    if retention is not None:
        engagement_ok = retention >= MIN_RETENTION
        level = "alto" if retention >= 0.6 else "medio" if retention >= MIN_RETENTION else "basso"
    elif like_rate is not None:
        engagement_ok = like_rate >= MIN_LIKE_RATE
        level = ("alto" if like_rate >= 0.04 else "medio" if engagement_ok else "basso") + " (da like)"
    else:
        engagement_ok, level = None, "sconosciuto"

    growth_ok = None if trend.label == "da misurare" else trend.label == "in crescita"
    a = Analysis(
        video, retention, like_rate, comment_rate, level, trend,
        checks={"durata_30min": video.duration > MIN_DURATION, "engagement_medio": engagement_ok,
                "views_in_crescita": growth_ok},
    )
    a.score = _score(a)
    return a


def estimate_retention(heatmap: list[float]) -> float | None:
    """Quota media di pubblico rimasta lungo il video, rispetto all'inizio (0-1).

    La heatmap ha picchi nei punti rivisti più volte: una mediana mobile li toglie e lascia
    l'andamento di fondo. Il riferimento è il livello mediano del primo 10% del video.
    """
    if len(heatmap) < 20:
        return None
    smooth = _rolling_median(heatmap, 7)
    head = sorted(smooth[: max(len(smooth) // 10, 3)])
    ref = head[len(head) // 2]
    if ref <= 0:
        return None
    return sum(min(v / ref, 1.0) for v in smooth) / len(smooth)


def _rolling_median(values: list[float], window: int) -> list[float]:
    half = window // 2
    out = []
    for i in range(len(values)):
        chunk = sorted(values[max(0, i - half): i + half + 1])
        out.append(chunk[len(chunk) // 2])
    return out


def compute_trend(video: VideoStats, history: list[Snapshot], now: datetime) -> Trend:
    age = video.age_days
    lifetime = video.views / max(age, 1) if age is not None else None

    # Video uscito da meno di 3 settimane: tutta la sua vita cade nella finestra
    if age is not None and age <= WINDOW_DAYS:
        recent = _rate_from_history(history, now)
        daily = recent if recent is not None else lifetime
        accel = _acceleration(history, now)
        label = "in crescita" if accel is None or accel >= 0.8 else "in calo"
        src = f"uscito {age} giorni fa: views tutte nella finestra"
        return Trend(video.views, daily, lifetime, 1.0 if lifetime else None, accel, label, src)

    start = now - timedelta(days=WINDOW_DAYS)
    in_window = [s for s in history if s.taken_at >= start - timedelta(days=1)]
    if len(in_window) >= 2:
        first, last = in_window[0], in_window[-1]
        span = (last.taken_at - first.taken_at).total_seconds() / 86400
        if span >= MIN_SPAN_DAYS:
            daily = (last.views - first.views) / span
            window = round(daily * WINDOW_DAYS)
            momentum = daily / lifetime if lifetime else None
            accel = _acceleration(history, now)
            if momentum is not None and momentum >= GROWTH_MOMENTUM:
                label = "in crescita"
            elif accel is not None and accel >= 1.2 and (momentum or 0) >= 0.5:
                label = "in crescita"   # sta accelerando rispetto alle settimane precedenti
            elif momentum is not None and momentum >= 0.4:
                label = "stabile"
            else:
                label = "in calo"
            src = "misurato" if span >= WINDOW_DAYS - 0.5 else f"stimato su {span:.1f} giorni di rilevazioni"
            return Trend(window, daily, lifetime, momentum, accel, label, src)

    return Trend(None, None, lifetime, None, None, "da misurare",
                 "serve una seconda rilevazione ad almeno 1 giorno di distanza (comando: aggiorna)")


def _rate_from_history(history: list[Snapshot], now: datetime, days: float = WINDOW_DAYS) -> float | None:
    pts = [s for s in history if s.taken_at >= now - timedelta(days=days)]
    if len(pts) < 2:
        return None
    span = (pts[-1].taken_at - pts[0].taken_at).total_seconds() / 86400
    return (pts[-1].views - pts[0].views) / span if span >= MIN_SPAN_DAYS else None


def _acceleration(history: list[Snapshot], now: datetime) -> float | None:
    """views/giorno dell'ultima settimana diviso views/giorno delle 2 settimane prima."""
    last_week = _rate_between(history, now - timedelta(days=7), now)
    before = _rate_between(history, now - timedelta(days=WINDOW_DAYS), now - timedelta(days=7))
    if last_week is None or not before:
        return None
    return last_week / before


def _rate_between(history: list[Snapshot], start: datetime, end: datetime) -> float | None:
    pts = [s for s in history if start - timedelta(hours=12) <= s.taken_at <= end + timedelta(hours=12)]
    if len(pts) < 2:
        return None
    span = (pts[-1].taken_at - pts[0].taken_at).total_seconds() / 86400
    return (pts[-1].views - pts[0].views) / span if span >= MIN_SPAN_DAYS else None


def _score(a: Analysis) -> float:
    """0-100: 45% engagement, 55% crescita. Con trend da misurare la crescita vale a metà."""
    ret = a.retention_est
    like = a.like_rate or 0
    eng = 0.7 * _clip((ret - 0.3) / 0.4) + 0.3 * _clip(like / 0.04) if ret is not None else _clip(like / 0.04)

    t = a.trend
    if t.momentum is None or t.views_window is None:
        growth = 0.25
    else:
        growth = 0.6 * _clip(t.momentum / 2) + 0.4 * _clip(math.log10(max(t.views_window, 1)) / 6)
    return 100 * (0.45 * eng + 0.55 * growth)


def _clip(x: float) -> float:
    return max(0.0, min(1.0, x))


def _pct(x: float | None, digits: int = 1) -> str | None:
    return None if x is None else f"{x * 100:.{digits}f}%"


def _round(x: float | None, digits: int = 0) -> float | None:
    return None if x is None else round(x, digits)
