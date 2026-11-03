"""Analyst senza AI: ricerche ricavate dalla descrizione e sintesi calcolata dai dati."""

import re
from collections import Counter

STOPWORDS = set("""
a ad al alla alle agli ai allo anche che chi ci con come da dal dalla dei del della delle di e ed gli
ha hanno i il in io la le lo ma mi ne nel nella non o per più può se si sono su sua suo tra tu un una uno
vs w with the and of to in on for is are you your how what why this that from it my me we our at by an a
video podcast ep episodio puntata official ft feat con passa dal questo questa questi quello quella perché
cosa come quando dove sempre tutto tutti fare fatto essere stato oggi ecco nuovo nuova parte prima dopo
""".split())  # noqa: SIM905 — più leggibile come testo

MAX_QUERIES = 6


def queries_from_brief(brief: str) -> list[str]:
    """Trasforma una descrizione libera in ricerche YouTube.

    "podcast di business, finanza personale e interviste a imprenditori" diventa
    ["podcast di business", "finanza personale", "interviste a imprenditori"].
    Un elemento generico ("podcast") viene aggiunto alle altre ricerche come contesto.
    """
    parts = re.split(r"[,;\n/]|\s+(?:e|ed|o|oppure|and|or)\s+", brief.strip(), flags=re.IGNORECASE)
    parts = [re.sub(r"\s+", " ", p).strip(" .:-") for p in parts]
    parts = [p for p in parts if len(p) >= 3]
    if not parts:
        return []
    # Il formato ("podcast", "interviste") si porta dietro anche nelle parti che non lo dicono
    formats = [w for w in ("podcast", "intervista", "interviste", "talk", "documentario")
               if re.search(rf"\b{w}\b", brief, re.IGNORECASE)]
    queries = []
    for p in parts:
        if formats and not any(re.search(rf"\b{f}\b", p, re.IGNORECASE) for f in formats):
            p = f"{formats[0]} {p}"
        queries.append(p)
    return list(dict.fromkeys(queries))[:MAX_QUERIES]


def build_summary(results: list[dict], total_analyzed: int, queries: list[str]) -> str:
    """Sintesi in Markdown dei risultati di un'analisi, senza modello linguistico."""
    if not results:
        return "Nessun video trovato: prova ricerche più ampie o aggiungi dei canali."
    qualified = [r for r in results if r["qualificato"]]
    pending = [r for r in results if r["trend"] == "da misurare" and r["criteri"]["durata_30min"]
               and r["criteri"]["engagement_medio"]]
    # Solo video con storico vero: per quelli usciti da poco il momentum vale 1 per definizione
    growing = sorted((r for r in results if r["momentum"] is not None and r["momentum"] > 1.05
                      and not r.get("fonte_trend", "").startswith("uscito")), key=lambda r: -r["momentum"])

    lines = ["**Sintesi automatica** (senza AI, calcolata dalle metriche).", ""]
    lines.append(
        f"- Analizzati **{total_analyzed}** video, mostrati i migliori {len(results)}: "
        f"**{len(qualified)}** rispettano tutti i criteri, **{len(pending)}** passano durata ed engagement "
        "ma aspettano lo storico delle views."
    )

    pick = qualified or pending or results
    best = pick[0]
    lines.append(f"- Da clippare per primo: **[{best['titolo']}]({best['url']})** di {best['canale'].strip()} "
                 f"({best['durata_min']}′, tenuta {best['retention_stimata'] or '—'}, punteggio {best['punteggio']}).")

    if growing:
        names = ", ".join(f"[{r['titolo'][:50].strip()}]({r['url']}) (×{r['momentum']})" for r in growing[:3])
        lines.append(f"- In accelerazione rispetto alla loro media: {names}.")

    channels = Counter(r["canale"].strip() for r in results[:15])
    top_channels = [(c, n) for c, n in channels.most_common(5) if n >= 2]
    if top_channels:
        lines.append("- Canali che ricorrono tra i migliori: "
                     + ", ".join(f"**{c}** ({n})" for c, n in top_channels)
                     + ". Aggiungili ai canali del contesto per seguirne i nuovi video.")

    # Le parole del nome del canale ("The Bull") non sono un tema
    channel_words = {w for r in results for w in re.findall(r"[a-zàèéìòù0-9']+", r["canale"].lower())}
    words = Counter()
    for r in results[:15]:
        seen = set()
        for w in re.findall(r"[a-zàèéìòù0-9']{4,}", r["titolo"].lower()):
            if w not in STOPWORDS and w not in channel_words and w not in seen:
                words[w] += 1
                seen.add(w)
    themes = [w for w, n in words.most_common(8) if n >= 2]
    if themes:
        lines.append("- Parole ricorrenti nei titoli migliori: " + ", ".join(f"*{w}*" for w in themes) + ".")
        new_queries = [w for w in themes if not any(w in q.lower() for q in queries)][:3]
        if new_queries:
            lines.append("- Ricerche da provare: " + ", ".join(f"\"{w}\"" for w in new_queries) + ".")
    return "\n".join(lines)
