"""Riga di comando: python -m viral_clipper <url-o-file> [opzioni]"""

import argparse
from pathlib import Path

from dotenv import load_dotenv


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(prog="viral-clipper", description="Estrae le clip più virali da un video lungo.")
    parser.add_argument("source", help="URL (YouTube, ecc.) o percorso di un file video")
    parser.add_argument("--top", type=int, default=5, help="numero di clip da esportare (default 5)")
    parser.add_argument("--min", dest="min_len", type=float, default=15, help="durata minima in secondi")
    parser.add_argument("--max", dest="max_len", type=float, default=60, help="durata massima in secondi")
    parser.add_argument("--whisper", default="small", help="modello Whisper: tiny, base, small, medium, large-v3")
    parser.add_argument("--lang", default=None, help="lingua del video (es. it, en); default: rilevamento automatico")
    parser.add_argument("--jobs", default="jobs", help="cartella dei lavori")
    parser.add_argument("--center", action="store_true", help="crop centrale fisso invece di seguire i volti")
    args = parser.parse_args()

    from .pipeline import ClipperError, run_clipper

    try:
        run_clipper(
            args.source, args.top, args.min_len, args.max_len, args.whisper, args.lang,
            args.center, Path(args.jobs),
        )
    except ClipperError as e:
        raise SystemExit(str(e)) from None


if __name__ == "__main__":
    main()
