"""Verifica che tutte le dipendenze del progetto siano installate."""

import importlib
import shutil
import subprocess

import static_ffmpeg

MODULES = [
    "yt_dlp", "faster_whisper", "librosa", "soundfile", "cv2", "mediapipe",
    "scenedetect", "anthropic", "sklearn", "pandas", "fastapi",
]


def main() -> None:
    static_ffmpeg.add_paths()
    ffmpeg = shutil.which("ffmpeg")
    version = subprocess.run([ffmpeg, "-version"], check=False, capture_output=True, text=True).stdout.split()[2]
    print(f"ffmpeg          {version}")
    for name in MODULES:
        module = importlib.import_module(name)
        print(f"{name:15} {getattr(module, '__version__', 'ok')}")
    print("\nAmbiente pronto.")


if __name__ == "__main__":
    main()
