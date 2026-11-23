"""Test della lingua di SinapsiDB su una piccola rete costruita a mano: non serve PostgreSQL."""

from __future__ import annotations

import pytest

from sinapsidb import lingua
from sinapsidb.lingua import ErroreDomanda, analizza, chiedi, percorsi, prepara, vocabolario
from sinapsidb.motore import Rete


def _racconta(rete: Rete, frase: str, tipo: str, *legami: tuple[str, str]) -> int:
    n = rete.n
    rete.racconta(frase, "prova", [(n(tipo), [(n(r), n(v)) for r, v in legami])])
    return len(rete.f_tipo) - 1


@pytest.fixture()
def rete() -> Rete:
    r = Rete()
    for codice, nome, citta in (("C1", "Rossi Strumenti", "Verona"), ("C2", "Bianchi Audio", "Milano"), ("C3", "Verdi Note", "Verona")):
        _racconta(r, f"{nome} ({codice}) è un cliente di {citta}", "scheda cliente", ("cliente", codice), ("nome", nome), ("città", citta))
    for codice, fornitore, prezzo in (("P1", "S1", "10.00"), ("P2", "S1", "25.50"), ("P3", "S2", "99.90")):
        _racconta(r, f"{codice} è un prodotto di {fornitore} a {prezzo}", "scheda prodotto", ("prodotto", codice), ("fornitore", fornitore), ("prezzo", prezzo))
    fatture = (("F1", "C1", "2026-01-10", "120.00", (("P1", "2"), ("P3", "1"))),
               ("F2", "C1", "2026-01-25", "25.50", (("P2", "1"),)),
               ("F3", "C2", "2026-02-03", "51.00", (("P2", "2"),)),
               ("F4", "C1", "2026-02-03", "10.00", (("P1", "1"),)))
    for numero, cliente, giorno, totale, righe in fatture:
        _racconta(r, f"Fattura {numero} a {cliente} del {giorno}, totale {totale}", "fattura emessa",
                  ("fattura", numero), ("cliente", cliente), ("quando", giorno), ("totale", totale))
        for prodotto, quantita in righe:
            _racconta(r, f"La fattura {numero} contiene {quantita} pezzi di {prodotto}", "riga di fattura",
                      ("fattura", numero), ("prodotto", prodotto), ("quantità", quantita))
    for numero, cliente, prodotto, giorno, stato in (("T1", "C1", "P3", "2026-02-03", "aperto"), ("T2", "C1", "P1", "2026-02-20", "chiuso"),
                                                     ("T3", "C2", "P2", "2026-02-21", "in lavorazione")):
        _racconta(r, f"Ticket {numero} di {cliente} su {prodotto}: {stato}", "ticket di assistenza",
                  ("ticket", numero), ("cliente", cliente), ("prodotto", prodotto), ("quando", giorno), ("stato", stato))
    return r


def _righe(rete: Rete, testo: str, *valori: str) -> list[tuple[str, ...]]:
    return sorted(prepara(rete, testo).esegui(*valori))


# --- la frase ---------------------------------------------------------------------


def test_la_frase_si_divide_in_passi() -> None:
    passi = analizza('accendi cliente = C1 e stato = "in lavorazione" in ticket di assistenza dove quando >= 2026 leggi distinti ticket, prodotto'
                     " poi da prodotto in scheda prodotto come prodotto leggi fornitore")
    primo, secondo = passi
    assert primo.indizi == [("cliente", "C1"), ("stato", "in lavorazione")] and primo.tipo == "ticket di assistenza"
    assert primo.filtri == [("quando", ">=", "2026")] and primo.azione == ("leggi", ["ticket", "prodotto"], True)
    assert secondo.da == "prodotto" and secondo.come == "prodotto" and secondo.tipo == "scheda prodotto"
    assert secondo.azione == ("leggi", ["fornitore"], False)
    assert analizza("in fattura emessa somma totale per mese(quando)")[0].azione == ("somma", "totale", ("mese", "quando"))
    assert analizza("IN fattura emessa CONTA per cliente")[0].azione == ("conta", None, ("", "cliente"))  # le parole valgono anche maiuscole
    assert analizza('accendi nome = "Rossi \\"Strumenti\\" e figli" in scheda cliente leggi cliente')[0].indizi == [("nome", 'Rossi "Strumenti" e figli')]
    assert analizza("accendi cliente = ? in fattura emessa dove totale > ? leggi fattura")[0].filtri == [("totale", ">", "?")]


@pytest.mark.parametrize(
    ("testo", "dice"),
    [
        ("", "vuota"),
        ("leggi fattura", "comincia con"),
        ("accendi cliente C1 in fattura emessa leggi fattura", "«=»"),
        ("accendi cliente = C1 in fattura emessa", "finisce troppo presto"),
        ("accendi cliente = C1 in fattura emessa leggi", "che cosa leggere"),
        ("accendi cliente = C1 in fattura emessa leggi fattura e totale", "la fine della domanda"),
        ("accendi stato = in lavorazione in ticket di assistenza leggi ticket", "virgolette"),
        ("accendi cliente = C1 in fattura emessa conta poi in riga di fattura leggi prodotto", "chiudono la domanda"),
        ("accendi cliente = C1 in fattura emessa leggi fattura poi leggi prodotto", "«in <tipo di fatto>»"),
        ("in fattura emessa somma totale per mese(quando", "«)»"),
        ('accendi cliente = "C1 in fattura emessa leggi fattura', "non capisco"),
    ],
)
def test_frasi_sbagliate_dicono_dove(testo: str, dice: str) -> None:
    with pytest.raises(ErroreDomanda) as e:
        analizza(testo)
    assert dice in str(e.value)


# --- le risposte ------------------------------------------------------------------


def test_un_indizio_piu_indizi_e_valori_che_non_ci_sono(rete: Rete) -> None:
    assert _righe(rete, "accendi cliente = C1 in fattura emessa leggi fattura, totale") == [("F1", "120.00"), ("F2", "25.50"), ("F4", "10.00")]
    assert _righe(rete, "accendi fattura = f1 in fattura emessa leggi cliente, quando") == [("C1", "2026-01-10")]  # i nomi non badano alle maiuscole
    assert _righe(rete, "accendi città = verona in scheda cliente leggi nome") == [("Rossi Strumenti",), ("Verdi Note",)]  # né agli accenti: «citta» è «città»
    assert _righe(rete, "accendi citta = Verona in scheda cliente leggi cliente") == [("C1",), ("C3",)]
    assert _righe(rete, "accendi cliente = C1 e quando = 2026-02-03 in fattura emessa leggi fattura") == [("F4",)]
    assert _righe(rete, 'accendi cliente = C2 e stato = "in lavorazione" in ticket di assistenza leggi ticket') == [("T3",)]
    assert _righe(rete, "accendi cliente = C9 in fattura emessa leggi fattura") == []
    assert _righe(rete, "accendi cliente = C1 e quando = 1999-01-01 in fattura emessa leggi fattura") == []
    # con i segnaposto la stessa domanda si riusa
    d = prepara(rete, "accendi cliente = ? e quando = ? in fattura emessa leggi fattura")
    assert d.parametri == 2 and d.colonne == ["fattura"]
    assert d.esegui("C1", "2026-02-03") == [("F4",)] and d.esegui("C2", "2026-02-03") == [("F3",)] and d.esegui("C9", "x") == []
    with pytest.raises(ErroreDomanda, match="2 segnaposto"):
        d.esegui("C1")


def test_frase_e_tipo_si_leggono_sempre(rete: Rete) -> None:
    assert _righe(rete, "accendi fattura = F2 in riga di fattura leggi prodotto, frase") == [("P2", "La fattura F2 contiene 1 pezzi di P2")]
    assert _righe(rete, "accendi fattura = F2 in fattura emessa leggi tipo, totale") == [("fattura emessa", "25.50")]


def test_fatti_di_qualsiasi_tipo(rete: Rete) -> None:
    # il cliente e il giorno accesi insieme: scattano una fattura e un ticket, due tipi di fatto diversi
    assert _righe(rete, "accendi cliente = C1 e quando = 2026-02-03 leggi tipo, fattura, ticket") == [
        ("fattura emessa", "F4", ""), ("ticket di assistenza", "", "T1")]
    assert _righe(rete, "accendi quando = 2026-02-03 e cliente = C2 leggi tipo, frase") == [("fattura emessa", "Fattura F3 a C2 del 2026-02-03, totale 51.00")]
    # un solo indizio, in tutti i tipi che hanno quel ruolo
    assert _righe(rete, "accendi prodotto = P3 leggi tipo") == [("riga di fattura",), ("scheda prodotto",), ("ticket di assistenza",)]
    assert _righe(rete, "accendi cliente = C9 e quando = 2026-02-03 leggi tipo") == []
    with pytest.raises(ErroreDomanda, match="in nessun tipo di fatto"):
        prepara(rete, "accendi colore = rosso leggi tipo")


def test_piu_passi(rete: Rete) -> None:
    assert _righe(rete, "accendi cliente = C1 in fattura emessa leggi fattura poi in riga di fattura leggi prodotto, quantità") == [
        ("P1", "1"), ("P1", "2"), ("P2", "1"), ("P3", "1")]
    quattro = "accendi cliente = ? in fattura emessa leggi fattura poi in riga di fattura leggi prodotto poi in scheda prodotto leggi distinti fornitore"
    assert _righe(rete, quattro, "C1") == [("S1",), ("S2",)] and _righe(rete, quattro, "C2") == [("S1",)] and _righe(rete, quattro, "C3") == []
    d = prepara(rete, quattro)
    d.esegui("C1")
    assert d.conteggi == [3, 4, 3]  # tre fatture, quattro righe, tre schede: P1 compare in due righe ma si accende una volta
    # da quale ruolo proseguire, e con quale ruolo entrare
    assert _righe(rete, "accendi cliente = C1 in ticket di assistenza leggi ticket, prodotto poi da prodotto in scheda prodotto leggi prezzo") == [
        ("10.00",), ("99.90",)]
    assert _righe(rete, "accendi fattura = F1 in riga di fattura leggi prodotto poi in ticket di assistenza come prodotto leggi ticket") == [("T1",), ("T2",)]
    with pytest.raises(ErroreDomanda, match="da quale proseguire"):
        prepara(rete, "accendi cliente = C1 in ticket di assistenza leggi ticket, prodotto poi in scheda prodotto leggi prezzo")
    with pytest.raises(ErroreDomanda, match="non è tra le cose lette"):
        prepara(rete, "accendi cliente = C1 in ticket di assistenza leggi ticket poi da prodotto in scheda prodotto leggi prezzo")
    with pytest.raises(ErroreDomanda, match="non c'è nei fatti «scheda prodotto»"):
        prepara(rete, "accendi cliente = C1 in fattura emessa leggi fattura poi in scheda prodotto leggi prezzo")


def test_contare_sommare_raggruppare(rete: Rete) -> None:
    assert _righe(rete, "accendi cliente = C1 in fattura emessa conta") == [("3",)]
    assert _righe(rete, "accendi cliente = C1 in fattura emessa somma totale") == [("155.50",)]
    assert _righe(rete, "accendi cliente = C9 in fattura emessa somma totale") == [("0",)]
    assert _righe(rete, "in fattura emessa somma totale") == [("206.50",)]
    assert _righe(rete, "in fattura emessa somma totale per mese(quando)") == [("2026-01", "145.50"), ("2026-02", "61.00")]
    assert _righe(rete, "in fattura emessa somma totale per anno(quando)") == [("2026", "206.50")]
    assert _righe(rete, "in fattura emessa conta per cliente") == [("C1", "3"), ("C2", "1")]
    assert _righe(rete, "in riga di fattura somma quantità per prodotto") == [("P1", "3"), ("P2", "3"), ("P3", "1")]
    assert prepara(rete, "in fattura emessa somma totale per mese(quando)").colonne == ["mese(quando)", "somma di totale"]
    assert prepara(rete, "in fattura emessa conta per cliente").colonne == ["cliente", "quanti"]
    # si somma solo quello che è un numero
    assert _righe(rete, "in fattura emessa somma cliente") == [("0",)]


def test_condizioni(rete: Rete) -> None:
    assert _righe(rete, "in fattura emessa dove totale > 30 leggi fattura") == [("F1",), ("F3",)]
    assert _righe(rete, "in fattura emessa dove totale >= 25.50 e totale < 100 leggi fattura") == [("F2",), ("F3",)]
    assert _righe(rete, "in fattura emessa dove totale <= ? leggi fattura", "25.5") == [("F2",), ("F4",)]
    assert _righe(rete, "accendi cliente = C1 in fattura emessa dove totale > 20 leggi fattura") == [("F1",), ("F2",)]
    assert _righe(rete, "in ticket di assistenza dove stato = aperto leggi ticket") == [("T1",)]
    assert _righe(rete, "accendi cliente = C1 in ticket di assistenza dove stato = ? leggi ticket", "chiuso") == [("T2",)]
    assert _righe(rete, "accendi cliente = C1 in fattura emessa leggi fattura poi in riga di fattura dove quantità > 1 leggi prodotto") == [("P1",)]
    assert _righe(rete, "in fattura emessa dove cliente > 5 leggi fattura") == []  # un codice non è un numero: nessun fatto passa
    with pytest.raises(ErroreDomanda, match="serve un numero"):
        prepara(rete, "in fattura emessa dove totale > tanto leggi fattura")
    with pytest.raises(ErroreDomanda, match="serve un numero"):
        prepara(rete, "in fattura emessa dove totale > ? leggi fattura").esegui("tanto")


def test_distinti_e_limite(rete: Rete) -> None:
    assert _righe(rete, "in riga di fattura leggi prodotto") == [("P1",), ("P1",), ("P2",), ("P2",), ("P3",)]
    assert _righe(rete, "in riga di fattura leggi distinti prodotto") == [("P1",), ("P2",), ("P3",)]
    d = prepara(rete, "in riga di fattura leggi prodotto")
    assert len(d.esegui(limite=2)) == 2 and d.troncata
    assert len(d.esegui(limite=50)) == 5 and not d.troncata
    d = prepara(rete, "in riga di fattura leggi distinti prodotto")
    assert len(d.esegui(limite=2)) == 2 and d.troncata


def test_nomi_che_non_ci_sono_o_che_sono_due(rete: Rete) -> None:
    with pytest.raises(ErroreDomanda) as e:
        prepara(rete, "accendi cliente = C1 in fatura emessa leggi totale")
    assert "non è un tipo di fatto" in str(e.value) and "«fattura emessa»" in str(e.value)
    with pytest.raises(ErroreDomanda) as e:
        prepara(rete, "accendi cliente = C1 in fattura emessa leggi importo")
    assert "Ci sono: fattura, cliente, quando, totale" in str(e.value)
    # due neuroni con lo stesso nome: la domanda va precisata, e l'errore dice che cosa li distingue
    omonimo = rete.nuovo("C1")
    rete.racconta("C1 è anche una categoria", "prova", [(rete.n("scheda categoria"), [(rete.n("categoria"), omonimo)])])
    with pytest.raises(ErroreDomanda) as e:
        prepara(rete, "accendi cliente = C1 in fattura emessa leggi fattura")
    assert "indica 2 cose diverse" in str(e.value) and "scheda categoria" in str(e.value)
    with pytest.raises(ErroreDomanda, match="indica 2 cose diverse"):
        prepara(rete, "accendi cliente = ? in fattura emessa leggi fattura").esegui("C1")


def test_la_rete_cambia_e_la_domanda_resta_giusta(rete: Rete) -> None:
    d = prepara(rete, "accendi cliente = ? in fattura emessa leggi fattura, totale")
    assert sorted(d.esegui("C2")) == [("F3", "51.00")]
    # una fattura nuova si vede subito
    _racconta(rete, "Fattura F5 a C2", "fattura emessa", ("fattura", "F5"), ("cliente", "C2"), ("quando", "2026-03-01"), ("totale", "7.00"))
    assert sorted(d.esegui("C2")) == [("F3", "51.00"), ("F5", "7.00")]
    # una fattura raccontata con i ruoli in un altro ordine e uno in meno: i posti non sono più fissi, e la domanda se ne accorge
    _racconta(rete, "Fattura F6, senza data", "fattura emessa", ("totale", "3.00"), ("cliente", "C2"), ("fattura", "F6"))
    assert sorted(d.esegui("C2")) == [("F3", "51.00"), ("F5", "7.00"), ("F6", "3.00")]
    assert _righe(rete, "accendi fattura = F6 in fattura emessa leggi cliente, quando, totale") == [("C2", "", "3.00")]  # il ruolo che manca resta vuoto
    assert _righe(rete, "in fattura emessa somma totale per mese(quando)") == [("", "3.00"), ("2026-01", "145.50"), ("2026-02", "61.00"), ("2026-03", "7.00")]
    assert _righe(rete, "in fattura emessa conta") == [("6",)]
    # la fattura senza data non ha il ruolo «quando»: non è un numero da sommare né da confrontare, qualunque sia l'ultimo neurone della rete
    rete.nuovo("1000")
    assert _righe(rete, "in fattura emessa somma quando") == [("0",)] and _righe(rete, "in fattura emessa dove quando > 5 leggi fattura") == []
    # un fatto ritirato non scatta più
    ritirata = next(f for f in rete.scatta(rete.n("fattura emessa"), [(rete.n("fattura"), rete.n("F5"))]))
    rete.racconta("La fattura F5 era un errore", "prova", ritira=[ritirata])
    assert sorted(d.esegui("C2")) == [("F3", "51.00"), ("F6", "3.00")]
    assert _righe(rete, "accendi cliente = C2 e quando = 2026-03-01 leggi tipo") == []
    assert _righe(rete, "in fattura emessa conta") == [("5",)]


# --- vocabolario, percorsi, chiedi ------------------------------------------------


def test_vocabolario(rete: Rete) -> None:
    v = {x["tipo"]: x for x in vocabolario(rete)}
    assert list(v) == ["fattura emessa", "riga di fattura", "scheda cliente", "scheda prodotto", "ticket di assistenza"]
    assert v["fattura emessa"] == {"tipo": "fattura emessa", "ruoli": ["fattura", "cliente", "quando", "totale"], "fatti": 4}
    assert v["riga di fattura"]["fatti"] == 5 and v["ticket di assistenza"]["ruoli"] == ["ticket", "cliente", "prodotto", "quando", "stato"]
    assert vocabolario(Rete()) == []


def test_percorsi(rete: Rete) -> None:
    strade = percorsi(rete, "cliente", "fornitore")
    per = [s["per"] for s in strade]
    assert per[0] == ["ticket di assistenza", "scheda prodotto"] and ["fattura emessa", "riga di fattura", "scheda prodotto"] in per
    # non si passa da una data, da un totale o da una città: collegherebbero cose che non c'entrano
    assert all("quando" not in s["domanda"] and "totale" not in s["domanda"] and "città" not in s["domanda"] for s in strade)
    # ogni strada proposta è una domanda che si può eseguire
    for s in strade:
        assert s["passi"] == len(s["per"])
        assert prepara(rete, s["domanda"]).esegui("C1")
    lunga = next(s for s in strade if s["per"] == ["fattura emessa", "riga di fattura", "scheda prodotto"])
    assert lunga["domanda"] == ("accendi cliente = ? in fattura emessa leggi fattura poi in riga di fattura leggi prodotto"
                                " poi in scheda prodotto leggi distinti fornitore")
    assert [s["per"] for s in percorsi(rete, "fattura", "cliente")][0] == ["fattura emessa"]
    with pytest.raises(ErroreDomanda, match="non è un ruolo"):
        percorsi(rete, "cliente", "colore")
    with pytest.raises(ErroreDomanda, match="stesso ruolo"):
        percorsi(rete, "cliente", "cliente")


def test_chiedi(rete: Rete) -> None:
    r = chiedi(rete, "  accendi cliente = C1   in fattura emessa leggi fattura, totale ")
    assert r["domanda"] == "accendi cliente = C1 in fattura emessa leggi fattura, totale" and r["colonne"] == ["fattura", "totale"]
    assert sorted(r["righe"]) == [("F1", "120.00"), ("F2", "25.50"), ("F4", "10.00")] and r["quante"] == 3 and not r["troncata"]
    assert r["fatti_scattati"] == [3] and r["esecuzione_us"] > 0 and r["preparazione_us"] > 0
    assert r["piano"] == ["Accendo cliente = C1 nei fatti «fattura emessa»; leggo fattura, totale."]
    r = chiedi(rete, "in riga di fattura leggi prodotto", limite=2)
    assert r["quante"] == 2 and r["troncata"]
    assert "Nessun neurone da cui partire" in chiedi(rete, "in fattura emessa conta")["piano"][0]
    assert "i valori di «quantità» nei fatti «riga di fattura» sono tenuti in ordine di grandezza" in \
        chiedi(rete, "in riga di fattura dove quantità > 1 leggi prodotto")["piano"][0]
    assert "parto dall'indizio che accende meno fatti" in chiedi(rete, "accendi cliente = C1 e stato = aperto in ticket di assistenza leggi ticket")["piano"][0]
    v = chiedi(rete, "vocabolario")
    assert v["colonne"] == ["tipo di fatto", "ruoli", "fatti"] and ("fattura emessa", "fattura, cliente, quando, totale", "4") in v["righe"]
    p = chiedi(rete, "percorsi da cliente a fornitore")
    assert p["colonne"] == ["passi", "passando per", "domanda"] and p["righe"][0][0] == "2"
    with pytest.raises(ErroreDomanda, match="segnaposto"):
        chiedi(rete, "accendi cliente = ? in fattura emessa leggi fattura")


def test_nomi_tra_virgolette_solo_se_serve() -> None:
    assert lingua._virgolette("fattura emessa") == "fattura emessa" and lingua._virgolette("ordine d'acquisto") == "ordine d'acquisto"
    assert lingua._virgolette("in lavorazione") == '"in lavorazione"' and lingua._virgolette("Rossi & Figli") == "Rossi & Figli"
    assert lingua._virgolette("a = b") == '"a = b"' and lingua._virgolette('dice "sì"') == '"dice \\"sì\\""'
    for nome in ("fattura emessa", "in lavorazione", "a = b", 'dice "sì"', "ordine d'acquisto", "Magazzino  Verona"):
        assert analizza(f"accendi x = {lingua._virgolette(nome)} in y leggi z")[0].indizi == [("x", nome)]


# --- i valori tenuti in ordine di grandezza ---------------------------------------


def _da_tutti(rete: Rete, tipo: str, ruolo: str, confronto: str, soglia: float, leggi: str) -> list[str]:
    """La stessa domanda fatta nel modo lungo: si guardano tutti i fatti in vigore, uno per uno."""
    n = rete.n
    passa = {">": lambda a, b: a > b, ">=": lambda a, b: a >= b, "<": lambda a, b: a < b, "<=": lambda a, b: a <= b}[confronto]
    out = []
    for f in range(len(rete.f_tipo)):
        if rete.f_tipo[f] != n(tipo) or rete.f_morto[f] >= 0:
            continue
        valore = rete.leggi(f, n(ruolo))
        try:
            v = float(rete.nomi[valore]) if valore >= 0 else None
        except ValueError:
            v = None
        if v is not None and v == v and passa(v, soglia):
            out.append(rete.nomi[rete.leggi(f, n(leggi))])
    return sorted(out)


def test_i_valori_in_ordine_danno_le_stesse_risposte_del_modo_lungo() -> None:
    import random

    rete = Rete()
    rng = random.Random(11)
    importi = ["0", "0.00", "-5", "-5.50", "10", "10.0", "10.00", "99.99", "100", "1e3", "inf", "-inf", "nan", "molti", "", "12 "]
    for i in range(400):
        _racconta(rete, f"Fattura F{i}", "fattura emessa", ("fattura", f"F{i}"), ("totale", rng.choice(importi + [f"{rng.randint(-50, 150)}.{rng.randint(0, 99):02d}"])))
    _racconta(rete, "Una fattura senza totale", "fattura emessa", ("fattura", "F-senza"))
    assert not rete._ordinate  # finché nessuno lo chiede, nessuna linea è tenuta in ordine
    soglie = [-1e9, -50, -5.5, -5, 0, 9.99, 10, 10.01, 99.99, 100, 1000, 1e9, float("inf"), float("-inf")]
    for confronto in (">", ">=", "<", "<="):
        for soglia in soglie:
            trovate = sorted(x[0] for x in prepara(rete, f"in fattura emessa dove totale {confronto} ? leggi fattura").esegui(repr(soglia)))
            assert trovate == _da_tutti(rete, "fattura emessa", "totale", confronto, soglia, "fattura"), (confronto, soglia)
    assert len(rete._ordinate) == 1  # una linea sola, costruita una volta
    # la soglia scritta nella frase, e due condizioni insieme
    assert sorted(x[0] for x in chiedi(rete, "in fattura emessa dove totale > 100 leggi fattura", limite=10000)["righe"]) == \
        _da_tutti(rete, "fattura emessa", "totale", ">", 100, "fattura")
    tra = sorted(x[0] for x in chiedi(rete, "in fattura emessa dove totale >= 10 e totale < 100 leggi fattura", limite=10000)["righe"])
    assert tra == sorted(set(_da_tutti(rete, "fattura emessa", "totale", ">=", 10, "fattura")) & set(_da_tutti(rete, "fattura emessa", "totale", "<", 100, "fattura")))
    assert tra


def test_i_valori_in_ordine_seguono_le_scritture() -> None:
    """Dopo che la linea è stata messa in ordine la rete continua a cambiare: valori nuovi, tanti da essere fusi con gli
    altri, fatti ritirati, valori che tornano. La domanda resta giusta, e nessun fatto esce due volte."""
    rete = Rete()
    for i in range(50):
        _racconta(rete, f"Fattura F{i}", "fattura emessa", ("fattura", f"F{i}"), ("totale", f"{i}.00"))
    domanda = prepara(rete, "in fattura emessa dove totale > ? leggi fattura")

    def giusta(soglia: float) -> None:
        trovate = [x[0] for x in domanda.esegui(str(soglia))]
        assert len(trovate) == len(set(trovate)), "un fatto è uscito due volte"
        assert sorted(trovate) == _da_tutti(rete, "fattura emessa", "totale", ">", soglia, "fattura"), soglia

    giusta(40)
    linea = next(iter(rete._ordinate.values()))
    assert len(linea.valori) == 50 and not linea.attesa
    # un valore nuovo, uno già visto, e uno senza numero: solo il nuovo aspetta
    nuovo = _racconta(rete, "Fattura N1", "fattura emessa", ("fattura", "N1"), ("totale", "45.50"))
    _racconta(rete, "Fattura N2", "fattura emessa", ("fattura", "N2"), ("totale", "45.00"))
    _racconta(rete, "Fattura N3", "fattura emessa", ("fattura", "N3"), ("totale", "da definire"))
    assert list(linea.attesa.values()) == [45.5]
    for soglia in (40, 45, 45.5, 46):
        giusta(soglia)
    # il fatto viene ritirato: il suo valore resta nell'ordine, ma la linea non lo ha più e la domanda non lo trova
    rete.racconta("Annulla N1", "prova", [], ritira=[nuovo])
    giusta(45)
    assert "N1" not in [x[0] for x in domanda.esegui("45")]
    # lo stesso valore torna con un'altra fattura: una volta sola
    _racconta(rete, "Fattura N4", "fattura emessa", ("fattura", "N4"), ("totale", "45.50"))
    assert list(linea.attesa.values()) == [45.5]
    giusta(45)
    # tanti valori nuovi: vengono fusi con gli altri, e l'ordine resta buono
    for i in range(1500):
        _racconta(rete, f"Fattura G{i}", "fattura emessa", ("fattura", f"G{i}"), ("totale", f"{1000 + i * 0.25:.2f}"))
    assert len(linea.valori) > 1000 and len(linea.attesa) < 1025 and list(linea.valori) == sorted(linea.valori)
    for soglia in (-1, 45, 999.99, 1000, 1200.25, 1374.75, 5000):
        giusta(soglia)
    # le altre linee non sono state toccate: in ordine c'è solo quella a cui è stato chiesto
    assert len(rete._ordinate) == 1


# --- leggere un circuito ----------------------------------------------------------


def test_la_frase_che_legge_un_circuito(rete: Rete) -> None:
    n = rete.n
    with pytest.raises(ErroreDomanda, match="non è un circuito della rete"):
        chiedi(rete, "circuito fatturato per mese")
    rete.proponi_circuito("Il fatturato di ogni mese", "prova", "fatturato per mese",
                          [{"osserva": n("fattura emessa"), "lega": n("quando"), "somma": n("totale"), "peso": 1, "per": "mese"}])
    rete.proponi_circuito("Il fatturato di ogni cliente", "prova", "fatturato del cliente",
                          [{"osserva": n("fattura emessa"), "lega": n("cliente"), "somma": n("totale"), "peso": 1}])
    r = chiedi(rete, "circuito fatturato per mese")
    assert r["colonne"] == ["mese", "fatturato per mese"] and sorted(r["righe"]) == [("2026-01", "145.5"), ("2026-02", "61")]
    assert "tenuta pronta" in r["piano"][0] and r["fatti_scattati"] == [0] and not r["troncata"]
    # è la stessa somma che la lingua calcola passando le fatture
    calcolata = chiedi(rete, "in fattura emessa somma totale per mese(quando)")["righe"]
    assert sorted((m, float(v)) for m, v in r["righe"]) == sorted((m, float(v)) for m, v in calcolata)
    # un'entità sola, il circuito legato a un ruolo, maiuscole e virgolette
    assert chiedi(rete, "circuito fatturato per mese di 2026-02")["righe"] == [("2026-02", "61")]
    assert chiedi(rete, 'CIRCUITO "Fatturato del cliente" di C1')["righe"] == [("C1", "155.5")]
    assert chiedi(rete, "circuito fatturato del cliente")["colonne"] == ["cliente", "fatturato del cliente"]
    assert chiedi(rete, "circuito fatturato del cliente di C3")["righe"] == [("C3", "0")]  # un cliente senza fatture: zero, non un errore
    assert chiedi(rete, "circuito fatturato per mese", limite=1)["troncata"]
    with pytest.raises(ErroreDomanda, match="Forse: «fatturato per mese»"):
        chiedi(rete, "circuito fatturato per mesi")
    with pytest.raises(ErroreDomanda, match="non conosco «Nessuno»"):
        chiedi(rete, "circuito fatturato del cliente di Nessuno")
    # preparata, segue le scritture senza prepararla di nuovo
    pronta = prepara(rete, "circuito fatturato per mese")
    _racconta(rete, "Fattura F9 a C2 del 2026-02-20, totale 9.00", "fattura emessa", ("fattura", "F9"), ("cliente", "C2"), ("quando", "2026-02-20"), ("totale", "9.00"))
    assert sorted(pronta.esegui()) == [("2026-01", "145.5"), ("2026-02", "70")]
    with pytest.raises(ErroreDomanda, match="segnaposto"):
        pronta.esegui("x")
