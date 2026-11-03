from datetime import date, datetime, timedelta

from viral_clipper.analyst.metrics import analyze, compute_trend, estimate_retention
from viral_clipper.analyst.pipeline import render_report
from viral_clipper.analyst.store import Snapshot, Store
from viral_clipper.analyst.youtube import VideoStats

NOW = datetime(2026, 10, 27, 12, 0)


def video(views=1_000_000, age_days=200, duration=60 * 60, heatmap=None, likes=30_000):
    return VideoStats(
        id="abcdefghijk", title="Test", channel="Canale", channel_followers=100_000,
        duration=duration, views=views, likes=likes, comments=500,
        upload_date=date.today() - timedelta(days=age_days),
        heatmap=heatmap if heatmap is not None else [0.5] * 100,
    )


def history(daily_views: list[int], end_views: int) -> list[Snapshot]:
    """Una rilevazione al giorno; daily_views[i] = views guadagnate il giorno i."""
    snaps, views = [], end_views - sum(daily_views)
    start = NOW - timedelta(days=len(daily_views))
    snaps.append(Snapshot(start, views, None, None))
    for i, d in enumerate(daily_views, 1):
        views += d
        snaps.append(Snapshot(start + timedelta(days=i), views, None, None))
    return snaps


# --- Retention -------------------------------------------------------------

def test_tenuta_piatta_e_alta_quella_in_calo_e_bassa():
    assert estimate_retention([0.5] * 100) == 1.0
    falling = [1.0 - i / 100 for i in range(100)]
    assert 0.4 < estimate_retention(falling) < 0.6


def test_i_picchi_di_replay_non_falsano_la_tenuta():
    base = [0.2] * 100
    spiky = base.copy()
    spiky[2] = spiky[40] = spiky[41] = 1.0
    assert abs(estimate_retention(spiky) - estimate_retention(base)) < 0.05


def test_senza_heatmap_niente_stima():
    assert estimate_retention([]) is None


# --- Trend -----------------------------------------------------------------

def test_video_vecchio_senza_storico_e_da_misurare():
    t = compute_trend(video(age_days=200), [], NOW)
    assert t.label == "da misurare" and t.views_window is None


def test_video_vecchio_che_riparte_e_in_crescita():
    # 200 giorni, 1M views → media 5000/giorno; ultime 3 settimane 8000/giorno
    t = compute_trend(video(age_days=200), history([8000] * 21, 1_000_000), NOW)
    assert t.label == "in crescita"
    assert t.views_window == 168_000 and t.source == "misurato"
    assert t.momentum > 1.5


def test_video_vecchio_che_cala_non_e_in_crescita():
    t = compute_trend(video(age_days=200), history([500] * 21, 1_000_000), NOW)
    assert t.label == "in calo"


def test_accelerazione_recente_conta_come_crescita():
    # media dall'uscita 5000/g; prime 2 settimane 2000/g, ultima settimana 6000/g
    t = compute_trend(video(age_days=200), history([2000] * 14 + [6000] * 7, 1_000_000), NOW)
    assert t.acceleration > 2.5
    assert t.label == "in crescita"


def test_storico_breve_da_una_stima_dichiarata():
    t = compute_trend(video(age_days=200), history([9000] * 3, 1_000_000), NOW)
    assert t.label == "in crescita"
    assert t.source.startswith("stimato su 3.0 giorni")


def test_video_recente_usa_tutte_le_views():
    t = compute_trend(video(views=50_000, age_days=10), [], NOW)
    assert t.views_window == 50_000 and t.label == "in crescita"


# --- Criteri e report ------------------------------------------------------

def test_video_corto_viene_scartato():
    a = analyze(video(duration=20 * 60), history([8000] * 21, 1_000_000), NOW)
    assert a.checks["durata_30min"] is False and not a.qualified


def test_video_che_rispetta_tutto_e_qualificato():
    a = analyze(video(), history([8000] * 21, 1_000_000), NOW)
    assert a.qualified and a.score > 60


def test_report_divide_qualificati_da_misurare_e_scartati():
    ok = analyze(video(), history([8000] * 21, 1_000_000), NOW)
    pending = analyze(video(), [], NOW)
    short = analyze(video(duration=10 * 60), [], NOW)
    md = render_report([ok, pending, short], None)
    assert "## Qualificati" in md and "## Da misurare" in md and "## Scartati" in md
    assert "durata 10′" in md


def test_storico_sqlite(tmp_path):
    store = Store(tmp_path / "s.db")
    v = video()
    store.save([v], NOW - timedelta(days=1))
    v.views += 5000
    store.save([v], NOW)
    h = store.history(v.id)
    assert [s.views for s in h] == [1_000_000, 1_005_000]
    assert store.tracked_ids() == [v.id]
