"""Step 6: crop 9:16 che segue il volto principale.

1. Campiona il video a SAMPLE_FPS e rileva i volti con MediaPipe (BlazeFace).
2. Sceglie un volto bersaglio per frame: resta su quello attuale finché un altro
   volto non è nettamente più grande per almeno SWITCH_HOLD secondi (evita ping-pong).
3. Liscia il centro con una zona morta + media esponenziale, così l'inquadratura
   si muove solo quando serve e senza scatti.
4. Scrive un file `sendcmd` che ffmpeg usa per spostare il crop nel tempo.
"""

import urllib.request
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/face_detector/"
    "blaze_face_short_range/float16/latest/blaze_face_short_range.tflite"
)
MODEL_PATH = Path(__file__).resolve().parents[2] / "models" / "blaze_face_short_range.tflite"

SAMPLE_FPS = 5
MIN_CONFIDENCE = 0.5
SWITCH_RATIO = 1.4      # un altro volto deve essere il 40% più grande per "rubare" l'inquadratura
SWITCH_HOLD = 1.0       # ...per almeno questo numero di secondi
DEAD_ZONE = 0.06        # frazione della larghezza del crop entro cui non ci si muove
SMOOTHING = 0.25        # 0 = fermo, 1 = segue istantaneamente


@dataclass
class Face:
    cx: float   # centro x in pixel del video sorgente
    cy: float
    size: float  # area relativa del box


@dataclass
class CropPlan:
    src_w: int
    src_h: int
    crop_w: int
    crop_h: int
    xs: list[tuple[float, int]]  # (secondo relativo alla clip, x del crop)
    faces_found: bool


def plan_crop(video: Path, start: float, end: float, aspect: float = 9 / 16) -> CropPlan:
    cap = cv2.VideoCapture(str(video))
    src_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    src_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    crop_h = src_h
    crop_w = min(src_w, round(src_h * aspect / 2) * 2)

    if crop_w >= src_w:  # sorgente già verticale: niente da seguire
        cap.release()
        return CropPlan(src_w, src_h, src_w, src_h, [(0.0, 0), (end - start, 0)], False)

    detections = _detect(cap, start, end, src_w, src_h)
    cap.release()

    centers = _choose_targets(detections)
    xs = _smooth(centers, src_w, crop_w)
    return CropPlan(src_w, src_h, crop_w, crop_h, xs, any(detections.values()))


def write_sendcmd(plan: CropPlan, path: Path, duration: float, fps: int = 30) -> None:
    """Interpola le posizioni campionate a ogni frame, così il movimento è fluido."""
    ts = np.array([t for t, _ in plan.xs])
    xs = np.array([x for _, x in plan.xs], dtype=float)
    lines, last = [], None
    for t in np.arange(0, duration, 1 / fps):
        x = int(np.interp(t, ts, xs))
        x -= x % 2
        if x != last:
            lines.append(f"{t:.3f} crop x {x};")
            last = x
    path.write_text("\n".join(lines) + "\n")


# --- Rilevamento --------------------------------------------------------------

def _detector():
    from mediapipe.tasks.python import BaseOptions, vision

    if not MODEL_PATH.exists():
        MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
        print("  scarico il modello di rilevamento volti…")
        urllib.request.urlretrieve(MODEL_URL, MODEL_PATH)
    options = vision.FaceDetectorOptions(
        base_options=BaseOptions(model_asset_path=str(MODEL_PATH)),
        min_detection_confidence=MIN_CONFIDENCE,
    )
    return vision.FaceDetector.create_from_options(options)


def _detect(cap: cv2.VideoCapture, start: float, end: float, src_w: int, src_h: int) -> dict[float, list[Face]]:
    import mediapipe as mp

    detections: dict[float, list[Face]] = {}
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    step = max(1, round(fps / SAMPLE_FPS))  # un frame ogni `step`
    scale = 640 / max(src_w, src_h)  # BlazeFace short-range lavora meglio su immagini piccole
    k = 1 / scale if scale < 1 else 1
    n_samples = int((end - start) * SAMPLE_FPS)

    # Un solo seek all'inizio, poi lettura sequenziale: il seek per ogni campione è molto più lento
    cap.set(cv2.CAP_PROP_POS_MSEC, start * 1000)
    with _detector() as detector:
        for i in range(n_samples):
            ok, frame = cap.read()
            if not ok:
                break
            small = cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else frame
            rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
            result = detector.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb))
            faces = []
            for d in result.detections:
                b = d.bounding_box
                faces.append(Face(
                    cx=(b.origin_x + b.width / 2) * k,
                    cy=(b.origin_y + b.height / 2) * k,
                    size=(b.width * b.height * k * k) / (src_w * src_h),
                ))
            detections[round(i / SAMPLE_FPS, 3)] = faces
            for _ in range(step - 1):  # salta i frame intermedi (grab non li converte)
                if not cap.grab():
                    break
    return detections


# --- Scelta del bersaglio e smoothing ----------------------------------------

def _choose_targets(detections: dict[float, list[Face]]) -> list[tuple[float, float | None]]:
    """Per ogni istante restituisce la x del volto da inquadrare (None = nessun volto)."""
    out: list[tuple[float, float | None]] = []
    current: Face | None = None
    challenger_since: float | None = None

    for t, faces in sorted(detections.items()):
        if not faces:
            out.append((t, current.cx if current else None))
            continue
        biggest = max(faces, key=lambda f: f.size)
        if current is None:
            current = biggest
        else:
            # Il volto "attuale" in questo frame è quello più vicino alla posizione precedente
            same = min(faces, key=lambda f: abs(f.cx - current.cx))
            if biggest is not same and biggest.size > same.size * SWITCH_RATIO:
                challenger_since = challenger_since if challenger_since is not None else t
                if t - challenger_since >= SWITCH_HOLD:
                    same, challenger_since = biggest, None
            else:
                challenger_since = None
            current = same
        out.append((t, current.cx))
    return out


def _smooth(centers: list[tuple[float, float | None]], src_w: int, crop_w: int) -> list[tuple[float, int]]:
    max_x = src_w - crop_w
    default = src_w / 2
    known = [c for _, c in centers if c is not None]
    pos = known[0] if known else default
    dead = DEAD_ZONE * crop_w

    xs: list[tuple[float, int]] = []
    for t, target in centers:
        target = pos if target is None else target
        if abs(target - pos) > dead:
            pos += (target - pos) * SMOOTHING
        xs.append((t, int(np.clip(pos - crop_w / 2, 0, max_x))))
    return xs or [(0.0, int(max_x / 2))]
