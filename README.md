# ClipBot

Estrae automaticamente le clip più virali da un video lungo e le esporta in verticale 9:16 con sottotitoli animati.

## Installazione e avvio

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt && pip install -e .
cp .env.example .env        # opzionale: aggiungi ANTHROPIC_API_KEY
viral-studio                # interfaccia web su http://127.0.0.1:8000
```

Serve anche `ffmpeg` nel PATH (`brew install ffmpeg`).

## Uso

```bash
source .venv/bin/activate   # oppure usa .venv/bin/viral-clipper
viral-clipper "https://www.youtube.com/watch?v=..." --top 5
viral-clipper mio_video.mp4 --top 5 --min 15 --max 60 --whisper small --lang it
viral-clipper mio_video.mp4 --center   # crop centrale fisso, senza tracking
```

In VS Code: **Esegui → Avvia debug (F5)** → "Estrai clip", poi incolla URL o percorso.

Ogni video ha la sua cartella, con il titolo preso da YouTube (o il nome del file):

```
jobs/
└── <Titolo del video>/
    ├── clips/            ← solo le clip finali (clip_01.mp4, clip_02.mp4, …)
    ├── clips.json        ← punteggio, tempi, titolo, hook e motivazione di ogni clip
    ├── source.mp4        ← video originale
    ├── audio.wav         ← audio per l'analisi
    ├── transcript.json   ← trascrizione con timestamp
    ├── meta.json         ← titolo e link di origine
    └── lavoro/           ← sottotitoli (.ass) e movimenti del crop (.cmd)
```

Video, audio e trascrizione restano in cache: rilanciare sullo stesso video è veloce.
Una nuova esecuzione sostituisce le clip precedenti in `clips/`.

## Scoring

- **Con `ANTHROPIC_API_KEY`** in `.env`: Claude legge tutta la trascrizione e valuta hook,
  autonomia, emozione, payoff e condivisibilità (0–10 ciascuno → punteggio 0–100).
- **Senza chiave**: euristica su parole-hook, punteggiatura ed energia audio. Utile per provare, molto meno precisa.

## Pipeline

| File | Step |
|---|---|
| `ingest.py` | download (yt-dlp) / copia, estrazione audio |
| `transcribe.py` | faster-whisper con timestamp per parola, ri-segmentazione per frasi |
| `audio.py` | energia audio per secondo |
| `scoring.py` | scelta clip (Claude o euristica), durata, niente sovrapposizioni |
| `facetrack.py` | rileva i volti (MediaPipe) e calcola un crop 9:16 fluido che li segue |
| `render.py` | taglio, crop 9:16, sottotitoli karaoke, hook, loudness -14 LUFS |

## Prossimi passi (v1)

- Scegliere il volto di chi sta parlando (oggi segue il volto più grande/stabile)
- Split-screen quando parlano due persone
- Rimozione automatica dei silenzi
- Segnali audience (heatmap "Most Replayed", commenti con timestamp)

## Note sull'ambiente

`.venv` è relocatable: la cartella si può spostare o rinominare senza reinstallare nulla.
Il modello di rilevamento volti viene scaricato la prima volta in `models/`.

## Analista (scegliere i video da clippare)

Trova video YouTube adatti al clipping secondo tre criteri: **durata > 30 min**, **tenuta stimata ≥ 50%**,
**views in crescita nelle ultime 3 settimane**.

```bash
# Ricerche e canali scelti da te (non serve la chiave API)
.venv/bin/viral-analyst cerca "podcast business italiano" "intervista imprenditore" --canale @TheBSMT

# Agente: Claude sceglie le ricerche, analizza e commenta (serve ANTHROPIC_API_KEY)
.venv/bin/viral-analyst agente "podcast italiani di business e finanza personale"

# Nuova rilevazione delle views di tutti i video già visti (da lanciare ogni giorno)
.venv/bin/viral-analyst aggiorna

# Estrae subito le clip dai primi 3 video qualificati
.venv/bin/viral-analyst cerca "podcast business" --clip 3
```

Ogni analisi crea `analisi/<data_ora>/` con `report.md`, `risultati.csv` e `risultati.json`.
Lo storico delle rilevazioni è in `analisi/storico.db`.

**Perché serve `aggiorna`:** YouTube mostra solo il totale delle views, non come cambiano nel tempo.
Per i video usciti da più di 3 settimane il trend si misura confrontando rilevazioni fatte in giorni
diversi: dopo 1 giorno si ha una prima stima, dopo 21 giorni il dato completo.

| File | Ruolo |
|---|---|
| `analyst/youtube.py` | ricerca video e canali, metriche via yt-dlp |
| `analyst/store.py` | storico delle rilevazioni (SQLite) |
| `analyst/metrics.py` | tenuta stimata, trend 3 settimane, criteri, punteggio |
| `analyst/pipeline.py` | analisi completa e report |
| `analyst/agent.py` | agente Claude con gli strumenti di ricerca e analisi |

## ClipBot Studio (interfaccia visuale)

```bash
.venv/bin/viral-studio
```

Poi apri http://127.0.0.1:8000. Quattro schermate dalla barra laterale:

| Schermata | Cosa fa |
|---|---|
| **Analyst** | Contesti di ricerca (descrizione per l'agente, ricerche, canali), pulsante *Esegui analisi*, automazione giornaliera o settimanale. Ogni settimana ha la sua sottopagina; ogni analisi mostra 30 video con statistiche e link. *Manda al Clipper* sceglie i video da tagliare. |
| **Clipper** | Clip divise per video. Scegli numero e durata delle clip: l'agente copywriter (Claude) legge lo script e sceglie i momenti salienti, scrivendo titolo e descrizione. Upload su YouTube Shorts su uno o più canali collegati. |
| **Trainer** | Link di Shorts di riferimento: vengono trascritti e analizzati, e il copywriter li usa come esempi di hook e ritmo. |
| **Statistics** | Statistiche per canale e per video. I canali *collegati* (OAuth) hanno la retention ufficiale e permettono l'upload; quelli *osservati* usano i dati pubblici. L'agente analista scrive un report su cosa funziona. |

Dati dell'app in `data/` (database, token OAuth: non condividerla), clip in `jobs/`, report dell'analista in `analisi/`.

**Configurazione**
- `ANTHROPIC_API_KEY` nel file `.env`: attiva gli agenti (analista, copywriter, statistiche). Senza chiave l'app funziona con ricerca e scoring euristico.
- Upload e retention ufficiale: crea un client OAuth "App desktop" su Google Cloud Console (YouTube Data API v3 + YouTube Analytics API), caricalo da *Impostazioni*, poi *Collega canale* in Statistics.
- Le analisi automatiche partono solo mentre ClipBot Studio è aperto.
