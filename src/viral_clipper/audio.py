"""Step 3 (parziale): energia audio per secondo, usata dallo scoring euristico."""

from pathlib import Path

import librosa
import numpy as np


def energy_per_second(wav: Path) -> np.ndarray:
    y, sr = librosa.load(str(wav), sr=16000, mono=True)
    rms = librosa.feature.rms(y=y, frame_length=sr, hop_length=sr, center=False)[0]
    return librosa.amplitude_to_db(rms, ref=np.max)
