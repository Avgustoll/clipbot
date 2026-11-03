from viral_clipper.analyst.summary import build_summary, queries_from_brief


def test_descrizione_divisa_in_ricerche_con_il_formato():
    q = queries_from_brief("podcast di business, finanza personale e interviste a imprenditori")
    assert q == ["podcast di business", "podcast finanza personale", "interviste a imprenditori"]


def test_descrizione_semplice_e_vuota():
    assert queries_from_brief("storie di sport") == ["storie di sport"]
    assert queries_from_brief("   ") == []


def result(i, canale="Canale A", qualificato=False, momentum=None, trend="da misurare", titolo="Soldi e business"):
    return {
        "id": f"v{i:010d}", "url": f"https://youtu.be/v{i}", "titolo": f"{titolo} {i}", "canale": canale,
        "durata_min": 60, "retention_stimata": "70.0%", "punteggio": 60 - i, "qualificato": qualificato,
        "momentum": momentum, "trend": trend, "fonte_trend": "misurato",
        "criteri": {"durata_30min": True, "engagement_medio": True, "views_in_crescita": qualificato or None},
    }


def test_sintesi_con_qualificati_canali_e_temi():
    results = [result(1, qualificato=True, momentum=2.5, trend="in crescita"), result(2), result(3, canale="B")]
    md = build_summary(results, 40, ["podcast business"])
    assert "Analizzati **40**" in md and "**1** rispettano" in md
    assert "Soldi e business 1" in md                      # il migliore da clippare
    assert "×2.5" in md                                    # in accelerazione
    assert "**Canale A** (2)" in md                        # canale ricorrente
    assert "*soldi*" in md and '"soldi"' in md             # tema ricorrente e ricerca nuova


def test_video_appena_usciti_non_sono_in_accelerazione():
    r = result(1, momentum=1.0, trend="in crescita")
    r["fonte_trend"] = "uscito 5 giorni fa: views tutte nella finestra"
    assert "accelerazione" not in build_summary([r], 1, [])


def test_nome_del_canale_non_e_un_tema():
    results = [result(i, canale="The Bull ", titolo="The Bull: investire") for i in range(3)]
    md = build_summary(results, 3, [])
    assert "*investire*" in md and "*bull*" not in md and "**The Bull** (3)" in md


def test_sintesi_senza_risultati():
    assert "Nessun video" in build_summary([], 0, [])
