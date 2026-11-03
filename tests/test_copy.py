from viral_clipper.copy import finalize, hashtag, keywords, make_description, make_title

TEXT = (
    "Allora, guarda, il vero errore che fanno tutti con i soldi è non investire mai. "
    "Ho perso 40 mila euro in startup perché ascoltavo i consigli sbagliati sugli investimenti. "
    "Gli investimenti vanno diversificati e le startup sono rischiose."
)


def test_titolo_corto_senza_riempitivi():
    t = make_title(TEXT)
    assert len(t) <= 70
    assert not t.lower().startswith(("allora", "guarda"))


def test_descrizione_con_hashtag():
    d = make_description(TEXT, "Intervista sul denaro")
    tags = [w for w in d.split() if w.startswith("#")]
    assert 3 <= len(tags) <= 5
    assert "Dal video: Intervista sul denaro" in d


def test_finalize_aggiunge_hashtag_e_rimuove_doppioni_e_shorts():
    title, desc = finalize("x" * 100, "Bella clip #Startup #startup #Shorts", TEXT)
    assert len(title) <= 70
    tags = [w for w in desc.split() if w.startswith("#")]
    assert len(tags) == len(set(tags)) >= 3
    assert "#shorts" not in desc.lower()


def test_keywords_ignora_stopword():
    assert "investimenti" in keywords(TEXT)
    assert "perché" not in keywords(TEXT)
    assert hashtag("Start-up") == "#startup"
