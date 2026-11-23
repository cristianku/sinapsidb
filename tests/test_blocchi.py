"""La rete su disco attiva solo in parte, confrontata con il motore su una rete piccola."""

from __future__ import annotations

import random

import pytest

from sinapsidb import blocchi
from sinapsidb.blocchi import ReteParziale, costruisci
from sinapsidb.motore import BIT, Rete


@pytest.fixture(scope="module")
def reti(tmp_path_factory: pytest.TempPathFactory):
    """Una rete del motore con ordini, righe, correzioni e una regola, e la stessa rete costruita a blocchi dal suo giornale."""
    base = tmp_path_factory.mktemp("reti")
    rete = Rete(str(base / "motore"), sync="os")
    n = rete.n
    rng = random.Random(7)
    riga, ordine, prodotto, quantita, scheda, nome = n("riga d'ordine"), n("ordine"), n("prodotto"), n("quantità"), n("scheda prodotto"), n("nome")
    for p in range(60):
        rete.racconta(f"P{p:03d} si chiama Prodotto {p}", "prova", [(scheda, [(prodotto, n(f"P{p:03d}")), (nome, n(f"Prodotto {p % 7}"))])])
    for o in range(1500):
        for p in rng.sample(range(60), rng.randint(1, 5)):
            rete.racconta(f"L'ordine O{o:05d} chiede {p % 9 + 1} pezzi di P{p:03d}", "prova",
                          [(riga, [(ordine, n(f"O{o:05d}")), (prodotto, n(f"P{p:03d}")), (quantita, n(str(p % 9 + 1)))])])
    # correzioni: il fatto di prima viene ritirato dall'evento che scrive quello nuovo
    for o in rng.sample(range(1500), 200):
        vecchio = sorted(rete.scatta(riga, [(ordine, n(f"O{o:05d}"))]))[0]
        p = rete.leggi(vecchio, prodotto)
        rete.racconta(f"Porta a 20 i pezzi di {rete.nomi[p]} nell'ordine O{o:05d}", "correzione",
                      [(riga, [(ordine, n(f"O{o:05d}")), (prodotto, p), (quantita, n("20"))])], ritira=[vecchio])
    # una regola scritta nella rete, e un fatto con una forma diversa dagli altri del suo tipo
    rete.proponi_regola("Un ordine confermato non può essere annullato", "prova", "confermato non annullabile", 2, "vieta", [
        {"nome": "confermato", "modo": "presenza", "osserva": n("stato dell'ordine"), "lega": ordine, "peso": 1, "con_ruolo": n("stato"), "con_valore": n("confermato")},
        {"nome": "annullato", "modo": "presenza", "osserva": n("annullamento"), "lega": ordine, "peso": 1}])
    rete.racconta("Una riga senza quantità", "prova", [(riga, [(prodotto, n("P001")), (ordine, n("O00001"))])])
    rete.chiudi()
    numeri = costruisci(base / "motore" / "giornale", base / "blocchi")
    riaperta = Rete(str(base / "motore"), sync="os")
    yield riaperta, base / "blocchi", numeri
    riaperta.chiudi()


def test_stessi_neuroni_fatti_ed_eventi_del_motore(reti) -> None:
    rete, cartella, numeri = reti
    r = ReteParziale(cartella, tetto=64 << 10)  # un tetto piccolo: quasi ogni lettura espelle qualcosa
    assert (r.n_fatti, r.n_neuroni, r.n_eventi) == (len(rete.f_tipo), len(rete.nomi), len(rete.e_frase))
    assert numeri["fatti"] == r.n_fatti and numeri["sinapsi"] == len(rete.s_a) and r.testa["ritirati"] == 200
    for i in random.Random(1).sample(range(r.n_neuroni), 400):
        assert r.nome(i) == rete.nomi[i]
        assert r.trova(rete.nomi[i]) == rete.trova(rete.nomi[i])
    assert r.trova("un nome che non c'è") == [] and r.trova("PRODOTTO 3") == rete.trova("prodotto 3")  # maiuscole e accenti come nel motore
    for f in random.Random(2).sample(range(r.n_fatti), 600):
        tipo, nato, morto, sinapsi = r.fatto(f)
        assert (tipo, nato, morto) == (rete.f_tipo[f], rete.f_nato[f], rete.f_morto[f])
        assert list(zip(sinapsi[0::2], sinapsi[1::2])) == rete.legami(f)
    for e in random.Random(3).sample(range(r.n_eventi), 300):
        assert r.evento(e) == (rete.e_frase[e], rete.e_origine[e], rete.e_quando[e], rete.e_tipo[e])
    assert r.blocchi.espulsi > 100  # la prova è passata davvero per l'espulsione dei blocchi
    with pytest.raises(IndexError):
        r.fatto(r.n_fatti)
    r.chiudi()


def test_stesse_linee_del_motore_in_vigore_e_ritirate(reti) -> None:
    rete, cartella, _ = reti
    r = ReteParziale(cartella, tetto=128 << 10)
    linee = 0
    for linea, d in rete.vivi.items():
        ruolo, tipo = linea >> BIT, linea & ((1 << BIT) - 1)
        for neurone, x in d.items():
            assert sorted(r.accesi(tipo, ruolo, neurone)) == sorted([x] if type(x) is int else x)
        # passare tutta la linea dà gli stessi neuroni e gli stessi fatti
        assert {n: sorted(f) for n, f in r.scorri(tipo, ruolo)} == {n: sorted([x] if type(x) is int else x) for n, x in d.items()}
        linee += bool(d)
    for linea, d in rete.morti.items():
        ruolo, tipo = linea >> BIT, linea & ((1 << BIT) - 1)
        for neurone, x in d.items():
            assert sorted(r.accesi(tipo, ruolo, neurone, ritirati=True)) == sorted(x)
    assert linee == len(r.linea)
    # un neurone che non è su quella linea, e una linea che non esiste: nessun fatto, non un errore
    assert r.accesi(r.id("riga d'ordine"), r.id("ordine"), r.id("prodotto")) == () and r.accesi(r.id("ordine"), r.id("ordine"), 5) == ()
    r.chiudi()


def test_la_storia_resta_recuperabile_dopo_l_espulsione(reti) -> None:
    rete, cartella, _ = reti
    r = ReteParziale(cartella, tetto=48 << 10)
    riga, ordine, quantita = r.id("riga d'ordine"), r.id("ordine"), r.id("quantità")
    corretti = [n for n, x in rete.morti[(rete.n("ordine") << BIT) | rete.n("riga d'ordine")].items()][:40]
    for giro in range(2):  # al secondo giro i blocchi del primo sono stati espulsi da tempo
        for n in corretti:
            assert r.storia(riga, ordine, n, quantita) == rete.storia(rete.n("riga d'ordine"), rete.n("ordine"), n, rete.n("quantità"))
    valore, nato, morto = r.storia(riga, ordine, corretti[0], quantita)[0]
    assert morto >= 0 and r.evento(morto)[0].startswith("Porta a 20") and r.evento(morto)[1] == "correzione"  # l'evento che lega il prima al dopo
    assert r.blocchi.espulsi > 0
    r.chiudi()


def test_il_tetto_vale_e_i_blocchi_servono_domande_diverse(reti) -> None:
    rete, cartella, _ = reti
    r = ReteParziale(cartella, tetto=256 << 10)
    riga, ordine, prodotto, quantita = r.id("riga d'ordine"), r.id("ordine"), r.id("prodotto"), r.id("quantità")
    o = r.trova("O00700")[0]
    prodotti, uno = r.misura(lambda: [r.nome(r.leggi(f, prodotto)) for f in r.accesi(riga, ordine, o)])
    assert sorted(prodotti) == sorted(rete.nomi[rete.leggi(f, rete.n("prodotto"))] for f in rete.scatta(rete.n("riga d'ordine"), [(rete.n("ordine"), o)]))
    assert uno["caricati"] > 0 and uno["byte_letti"] > 0
    # una domanda diversa sullo stesso ordine trova in RAM i blocchi della prima: carica solo quello che le manca
    frasi, due = r.misura(lambda: [(r.nome(r.leggi(f, quantita)), r.evento(r.fatto(f)[1])[0]) for f in r.accesi(riga, ordine, o)])
    assert len(frasi) == len(prodotti) and all(f"O00700" in frase for _, frase in frasi)
    assert due["gia_in_ram"] >= 2 and due["caricati"] < uno["caricati"] + 3
    # tante altre domande: il tetto non viene mai superato e i blocchi della prima escono
    for i in range(1500):
        for f in r.accesi(riga, ordine, r.trova(f"O{i:05d}")[0]):
            r.nome(r.leggi(f, prodotto))
            r.evento(r.fatto(f)[1])
        assert r.blocchi.usati <= r.blocchi.tetto
    assert not any(b in r.blocchi.blocchi for b in uno["blocchi"] if b[0] in (blocchi.FATTI, blocchi.EVENTI))
    di_nuovo, tre = r.misura(lambda: [r.nome(r.leggi(f, prodotto)) for f in r.accesi(riga, ordine, o)])
    assert di_nuovo == prodotti and tre["caricati"] > 0  # stessa risposta, ricaricata dal disco
    r.chiudi()


def test_la_lingua_da_le_stesse_risposte_sui_due_motori(reti) -> None:
    """Le stesse frasi della lingua di SinapsiDB, sul motore attuale e sulla rete a blocchi."""
    from sinapsidb import lingua
    from sinapsidb.blocchi import ComeRete

    rete, cartella, _ = reti
    r = ReteParziale(cartella, tetto=96 << 10)
    a = ComeRete(r)
    domande = [
        "accendi ordine = O00700 in riga d'ordine leggi prodotto",
        "accendi ordine = O00700 in riga d'ordine leggi prodotto, quantità, frase, id_fatto, id_evento",
        "accendi ordine = O00700 e quantità = 20 in riga d'ordine leggi prodotto",
        "accendi prodotto = P007 in riga d'ordine leggi ordine poi in riga d'ordine leggi distinti prodotto",
        "accendi ordine = O00012 in riga d'ordine leggi prodotto poi in scheda prodotto leggi distinti nome",
        "accendi prodotto = P001 leggi tipo, ordine, nome",
        "accendi ordine = O00700 in riga d'ordine conta",
        "accendi ordine = O00700 in riga d'ordine somma quantità",
        "in riga d'ordine conta per quantità",
        "in riga d'ordine somma quantità per prodotto",
        "in riga d'ordine dove quantità > 8 leggi ordine, prodotto",
        "in scheda prodotto leggi distinti nome",
        "accendi ordine = O99999 in riga d'ordine leggi prodotto",
        "vocabolario",
        "percorsi da ordine a nome",
    ]
    for domanda in domande:
        sul_motore, sui_blocchi = lingua.chiedi(rete, domanda, limite=100000), lingua.chiedi(a, domanda, limite=100000)
        assert sorted(sui_blocchi["righe"]) == sorted(sul_motore["righe"]), domanda
        assert sui_blocchi["colonne"] == sul_motore["colonne"], domanda
        if " dove quantità > " in domanda:
            # il motore in memoria tiene i valori in ordine di grandezza e parte dal punto giusto; la rete letta dal
            # disco li guarda tutti. La risposta è la stessa, la strada no, e il piano lo dice
            assert "in ordine di grandezza" in sul_motore["piano"][0] and "guardo i valori" in sui_blocchi["piano"][0]
        else:
            assert sui_blocchi["piano"] == sul_motore["piano"], domanda
    # le domande preparate, con valori diversi a ogni esecuzione
    preparate = [lingua.prepara(x, "accendi ordine = ? in riga d'ordine leggi prodotto, quantità, frase") for x in (rete, a)]
    for i in random.Random(5).sample(range(1500), 300):
        assert sorted(preparate[0].esegui(f"O{i:05d}")) == sorted(preparate[1].esegui(f"O{i:05d}"))
    # anche gli errori sono gli stessi: un tipo o un ruolo che non c'è non diventa una risposta vuota
    for sbagliata in ("accendi ordine = O00700 in riga d'ordne leggi prodotto", "accendi ordine = O00700 in riga d'ordine leggi colore"):
        with pytest.raises(lingua.ErroreDomanda) as sul_motore:
            lingua.chiedi(rete, sbagliata)
        with pytest.raises(lingua.ErroreDomanda) as sui_blocchi:
            lingua.chiedi(a, sbagliata)
        assert str(sui_blocchi.value) == str(sul_motore.value)
    assert r.blocchi.espulsi > 100 and r.blocchi.usati <= r.blocchi.tetto
    r.chiudi()
