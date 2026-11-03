import itertools

from viral_clipper.facetrack import CropPlan, Face, _choose_targets, _smooth, write_sendcmd

SRC_W, CROP_W = 1920, 608


def frames(seq):
    return {round(i / 5, 3): faces for i, faces in enumerate(seq)}


def test_resta_sul_volto_attuale_se_l_altro_e_simile():
    a, b = Face(400, 500, 0.02), Face(1500, 500, 0.022)
    targets = _choose_targets(frames([[a]] + [[a, b]] * 20))
    assert all(x == 400 for _, x in targets)


def test_passa_a_volto_molto_piu_grande_dopo_l_attesa():
    a, big = Face(400, 500, 0.02), Face(1500, 500, 0.05)
    targets = _choose_targets(frames([[a]] + [[a, big]] * 10))
    xs = [x for _, x in targets]
    assert xs[3] == 400        # 0.4 s: ancora sul primo volto
    assert xs[-1] == 1500      # dopo >1 s: passa al volto grande


def test_mantiene_la_posizione_quando_il_volto_sparisce():
    a = Face(1500, 500, 0.02)
    targets = _choose_targets(frames([[a], [], []]))
    assert [x for _, x in targets] == [1500, 1500, 1500]


def test_smoothing_resta_nei_bordi_e_si_muove_gradualmente():
    centers = [(0.0, 300.0)] + [(i / 5, 1800.0) for i in range(1, 30)]
    xs = [x for _, x in _smooth(centers, SRC_W, CROP_W)]
    assert xs[0] == 0                       # bloccato al bordo sinistro
    assert xs[-1] == SRC_W - CROP_W         # arriva al bordo destro
    assert all(b >= a for a, b in itertools.pairwise(xs))  # movimento monotono, niente scatti indietro
    assert xs[1] - xs[0] < SRC_W - CROP_W   # non salta in un colpo solo


def test_sendcmd_interpola_a_ogni_frame(tmp_path):
    plan = CropPlan(SRC_W, 1080, CROP_W, 1080, [(0.0, 0), (1.0, 300)], True)
    out = tmp_path / "c.cmd"
    write_sendcmd(plan, out, duration=1.0)
    lines = out.read_text().splitlines()
    assert lines[0] == "0.000 crop x 0;"
    assert len(lines) > 20
    assert all(int(line.split()[-1].rstrip(";")) % 2 == 0 for line in lines)
