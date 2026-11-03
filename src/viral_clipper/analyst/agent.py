"""Agente analista: Claude decide cosa cercare, analizza i video con gli strumenti e scrive il report."""

import json

import anthropic
import yt_dlp
from anthropic import beta_tool

from .pipeline import Session
from .youtube import channel_videos, search

MODEL = "claude-opus-5-5"

SYSTEM_PROMPT = """Sei un analista di contenuti YouTube. Trovi video lunghi da cui ricavare clip \
virali per TikTok, Reels e Shorts.

Un video è un buon candidato se rispetta tutti e tre i criteri:
1. dura più di 30 minuti;
2. ha engagement almeno medio: tenuta stimata ≥ 50%;
3. le sue views sono in crescita nelle ultime 3 settimane. Non basta che sia uscito di recente: \
conta come sta andando adesso (momentum = views/giorno recenti ÷ views/giorno medie dall'uscita).

Come lavori:
- Parti dalla richiesta dell'utente e formula ricerche mirate (anche in più lingue se ha senso) \
e, se utili, canali specifici. Usa `cerca_video` e `video_canale` per trovare candidati, \
poi `analizza_video` per ottenere le metriche: analizza solo gli ID che ti sembrano pertinenti.
- Se i qualificati sono pochi, prova altre ricerche (sinonimi, nicchie vicine, canali emersi). \
Fermati quando hai una shortlist solida o dopo circa 80 video analizzati.
- I video con trend "da misurare" hanno più di 3 settimane e nessuno storico: non scartarli, \
segnalali come da ricontrollare.

Dati: la tenuta è una stima dalla heatmap pubblica, non la retention ufficiale. \
Il trend è misurato solo dove c'è storico. Dillo quando pesa sulle conclusioni.

Risposta finale, in italiano e in Markdown (senza tabelle: le metriche le aggiunge il programma):
- una shortlist ordinata dei migliori video, con link e 1-2 frasi sul perché sono adatti al clipping;
- cosa accomuna i video che funzionano (temi, formati, ospiti, canali);
- le ricerche o i canali da monitorare nelle prossime analisi."""


def run_agent(brief: str, session: Session, max_iterations: int = 30) -> str:
    @beta_tool
    def cerca_video(query: str, max_risultati: int = 20) -> str:
        """Cerca su YouTube video lunghi (oltre 20 minuti) per una query. Restituisce gli ID.

        Args:
            query: testo da cercare, come lo scriveresti nella barra di ricerca di YouTube.
            max_risultati: quanti video restituire (massimo 40).
        """
        ids = search(query, min(max_risultati, 40))
        print(f"  [agente] cerca \"{query}\": {len(ids)} video")
        return json.dumps({"query": query, "video_ids": ids})

    @beta_tool
    def video_canale(canale: str, max_risultati: int = 20) -> str:
        """Elenca gli ultimi video di un canale YouTube. Restituisce gli ID.

        Args:
            canale: @handle, URL del canale o ID che inizia con UC.
            max_risultati: quanti video restituire (massimo 40).
        """
        try:
            ids = channel_videos(canale, min(max_risultati, 40))
        except yt_dlp.utils.DownloadError as e:  # canale inesistente o URL non valido
            return json.dumps({"errore": str(e)})
        print(f"  [agente] canale {canale}: {len(ids)} video")
        return json.dumps({"canale": canale, "video_ids": ids})

    @beta_tool
    def analizza_video(video_ids: list[str]) -> str:
        """Scarica le metriche dei video e valuta i tre criteri. Salva anche una rilevazione nello storico.

        Restituisce per ogni video: durata, views, tenuta stimata, like rate, views nelle ultime
        3 settimane, momentum, trend, esito di ogni criterio e un punteggio 0-100.

        Args:
            video_ids: ID YouTube di 11 caratteri (massimo 25 per chiamata).
        """
        results = session.analyze_ids(video_ids[:25])
        print(f"  [agente] analizzati {len(results)} video (totale {len(session.results)})")
        keep = ["id", "titolo", "canale", "durata_min", "views", "eta_giorni", "retention_stimata",
                "like_rate", "views_ultime_3_settimane", "momentum", "trend", "criteri", "qualificato", "punteggio"]
        return json.dumps([{k: a.to_dict()[k] for k in keep} for a in results], ensure_ascii=False)

    client = anthropic.Anthropic()
    runner = client.beta.messages.tool_runner(
        model=MODEL,
        max_tokens=16000,
        max_iterations=max_iterations,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={"effort": "high"},
        system=SYSTEM_PROMPT,
        tools=[cerca_video, video_canale, analizza_video],
        messages=[{"role": "user", "content": brief}],
    )

    final = None
    for message in runner:
        final = message
    if final is None:
        raise RuntimeError("L'agente non ha prodotto risposte")
    if final.stop_reason == "refusal":
        raise RuntimeError(f"Claude ha rifiutato la richiesta: {final.stop_details}")
    return "\n".join(b.text for b in final.content if b.type == "text")
