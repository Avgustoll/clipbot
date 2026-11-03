"""Riga di comando dell'analista: viral-analyst {cerca, agente, aggiorna}"""

import argparse
import os
import subprocess
import sys

from dotenv import load_dotenv


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(prog="viral-analyst", description="Trova video YouTube adatti al clipping.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_search = sub.add_parser("cerca", help="analizza i risultati di ricerche e canali che indichi tu")
    p_search.add_argument("query", nargs="*", help="una o più ricerche, tra virgolette")
    p_search.add_argument("--canale", action="append", default=[], help="@handle o URL di un canale (ripetibile)")
    p_search.add_argument("--per-fonte", type=int, default=20, help="video per ricerca o canale (default 20)")

    p_agent = sub.add_parser("agente", help="Claude sceglie le ricerche, analizza e scrive il report")
    p_agent.add_argument("brief", help='cosa cerchi, es. "podcast italiani di business e finanza"')
    p_agent.add_argument("--max-passi", type=int, default=30, help="limite di passaggi dell'agente")

    for p in (p_search, p_agent):
        p.add_argument("--clip", type=int, default=0, metavar="N", help="estrae le clip dai primi N video qualificati")

    sub.add_parser("aggiorna", help="nuova rilevazione delle views di tutti i video già analizzati")
    args = parser.parse_args()

    from .pipeline import Session, update_all

    if args.cmd == "aggiorna":
        n = update_all()
        print(f"Rilevazione salvata per {n} video.")
        return

    session = Session()
    commentary = None
    if args.cmd == "cerca":
        if not args.query and not args.canale:
            parser.error("indica almeno una ricerca o un --canale")
        print("1/3 Cerco i video…")
        ids = session.discover(args.query, args.canale, args.per_fonte)
        print(f"2/3 Analizzo {len(ids)} video…")
        session.analyze_ids(ids)
    else:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise SystemExit("L'agente usa Claude: inserisci ANTHROPIC_API_KEY nel file .env "
                             "(oppure usa `viral-analyst cerca` senza agente).")
        print("1/3 L'agente sta cercando e analizzando…")
        commentary = run_agent_safe(args.brief, session, args.max_passi)

    print("3/3 Scrivo il report…")
    report = session.write(commentary)
    qualified = [a for a in session.ranked() if a.qualified]
    print(f"\n{len(session.results)} video analizzati, {len(qualified)} qualificati.")
    for i, a in enumerate(qualified[:10], 1):
        print(f"  #{i} [{a.score:5.1f}] {a.video.title[:70]}")
    print(f"\nReport: {report}")

    for a in qualified[: args.clip]:
        print(f"\n▶ Clip da: {a.video.title}")
        subprocess.run([sys.executable, "-m", "viral_clipper", a.video.url, "--top", "5"], check=False)


def run_agent_safe(brief: str, session, max_iterations: int) -> str:
    import anthropic

    from .agent import run_agent

    try:
        return run_agent(brief, session, max_iterations)
    except (anthropic.APIError, RuntimeError) as e:  # l'analisi fatta finora resta valida: la salviamo comunque
        print(f"  L'agente si è interrotto: {e}")
        return f"_L'agente si è interrotto prima di concludere ({e}). Le metriche raccolte sono comunque sotto._"


if __name__ == "__main__":
    main()
