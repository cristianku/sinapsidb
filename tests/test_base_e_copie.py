"""Quello che il settimo giro di revisione ha aperto, oltre ai suoi casi: chi legge non tocca il
giornale, una scrittura torna alla base qualunque cosa vada storta, e quello che si consegna è una copia."""

import pytest

from sinapsidb import motore
from sinapsidb.memoria import Memoria
from sinapsidb.motore import Rete, Vietato
from test_revisione_circuiti_090 import aperta
from test_segno_e_disco import fido, movimento, nota, stato


def test_una_rete_aperta_per_leggere_non_scrive_niente_sul_disco(tmp_path):
    c = tmp_path / "rete"
    with aperta(Rete(str(c), sola_lettura=True)) as r:  # nemmeno la cartella
        assert not c.exists() and r.conteggi()["eventi"] == 0
    with aperta(Memoria(c, scritture=True)) as m:
        nota(m, "A")
    intero = (c / "giornale").read_bytes()
    (c / "giornale").write_bytes(intero + b"\x40\x00\x00\x00mezzo")  # un record che qualcuno sta ancora scrivendo
    prima = sorted(p.name for p in c.iterdir()), (c / "giornale").read_bytes()
    with aperta(Memoria(c)) as lettore:
        r = lettore.rete
        assert lettore.conteggi()["eventi"] == 1 and lettore.verifica(0)["esito"] == "coerente"
        assert lettore.racconta("Prova", [{"tipo": "nota", "legami": {"cosa": "B"}}], prova=True)["esito"] == "prova"
        for scrive in (lambda: r.racconta("B", "test", [(r.n("nota"), [(r.n("cosa"), r.n("B"))])]), r.istantanea):
            with pytest.raises(RuntimeError, match="sola lettura"):
                scrive()
    assert (sorted(p.name for p in c.iterdir()), (c / "giornale").read_bytes()) == prima
    with aperta(Memoria(c, scritture=True)) as m:  # chi apre per scrivere è uno solo, e la coda a metà la taglia lui
        assert (c / "giornale").read_bytes() == intero and nota(m, "B")["evento"] == "evento:1"


@pytest.mark.parametrize("dove", ["_valuta", "_leggi_definizioni", "_porta_a"])
def test_qualunque_cosa_vada_storta_decidendo_la_rete_torna_alla_base(tmp_path, monkeypatch, dove):
    c = tmp_path / "rete"
    with aperta(Memoria(c, scritture=True)) as m:
        r, n = m.rete, m.rete.n
        fido(m)
        movimento(m, "accredito", "A", "100", "a1")
        prima, giornale = stato(m), (c / "giornale").read_bytes()
        chiamate = []

        def rotto(*a, **k):
            chiamate.append(1)
            if dove == "_porta_a" and len(chiamate) == 1:
                return vero(*a, **k)  # la prima chiamata è prima dei fatti: si rompe la seconda, a fatti già nella rete
            raise OSError("un errore che nessuno si aspettava")

        vero = getattr(r, dove)
        segno = len(r.nomi)
        # un ordine che passerebbe, insieme a un termine nuovo: tocca dati, definizioni e regole nella stessa scrittura
        suo, = r.scatta(n("termine"), [(n("termine di"), n("fido")), (n("osserva"), n("ordine"))])
        fatti = [(n("ordine"), [(n("cliente"), n("A")), (n("importo"), n("30"))]),
                 (n("termine"), [(k, n("-2") if k == n("peso") else v) for k, v in r.legami(suo)])]
        with monkeypatch.context() as mp:
            mp.setattr(r, dove, rotto)
            with pytest.raises(OSError):
                r.racconta("Ordine e nuovo peso", "test", fatti, ritira=[suo], neuroni_da=segno, quando=r.e_scritto[-1] + 5, scritto=r.e_scritto[-1] + 9)
        assert chiamate and stato(m) == prima and (c / "giornale").read_bytes() == giornale and not r._decidendo
        assert r.f_morto[suo] < 0 and m.valore("fido", "A")["valore"] == "100" and m.verifica_circuiti()["esito"] == "coerente"
        assert movimento(m, "ordine", "A", "30", "o1")["esito"] == "applicato" and m.valore("fido", "A")["valore"] == "70"


def test_mentre_si_decide_non_si_fotografa_e_non_si_comincia_un_altra_scrittura(tmp_path):
    c = tmp_path / "rete"
    with aperta(Rete(str(c))) as r:
        n = r.n
        r.racconta("Prima", "test", [(n("nota"), [(n("cosa"), n("A"))])])
        prima = (c / "giornale").read_bytes()
        for dentro in (r.istantanea, lambda: r.racconta("Annidata", "test", [(n("nota"), [(n("cosa"), n("Z"))])]),
                       lambda: r.racconta("Annidata in prova", "test", [(n("nota"), [(n("cosa"), n("Z"))])], prova=True)):
            segno = len(r.nomi)

            def nota_che_fa_altro(decisione, dentro=dentro):
                dentro()

            with pytest.raises(RuntimeError, match="decidendo"):
                r.racconta("Seconda", "test", [(n("nota"), [(n("cosa"), n("B"))])], neuroni_da=segno, nota_della_scrittura=nota_che_fa_altro)
            assert (c / "giornale").read_bytes() == prima and r.conteggi()["fatti"] == 1 and not (c / "istantanea").exists()
        r.istantanea()  # fuori da una decisione si può
        assert r.racconta("Seconda", "test", [(n("nota"), [(n("cosa"), n("B"))])]) == 1


def test_un_fatto_si_ritira_una_volta_sola(tmp_path):
    with aperta(Rete(str(tmp_path / "rete"))) as r:
        n = r.n
        a = r.racconta("A", "test", [(n("nota"), [(n("cosa"), n("A"))])])
        r.racconta("B", "test", [(n("nota"), [(n("cosa"), n("B"))])])
        r.racconta("Via A", "test", ritira=[0])
        prima = (list(r.f_morto), len(r.e_frase))
        for ritira, perche in (([0], "non lo è"), ([7], "non lo è"), ([1, 1], "due volte")):
            for prova in (False, True):  # nemmeno una prova rimette in vigore quello che era già stato ritirato
                with pytest.raises(ValueError, match=perche):
                    r.racconta("Di nuovo", "test", ritira=ritira, prova=prova)
                assert (list(r.f_morto), len(r.e_frase)) == prima
        assert a == 0 and r.racconta("Via B", "test", ritira=[1]) == 3


def test_in_una_rete_che_registra_le_decisioni_la_decisione_nella_nota_e_quella_del_motore(tmp_path):
    c = tmp_path / "rete"
    with aperta(Memoria(c, scritture=True)) as m:
        r, n = m.rete, m.rete.n
        fido(m)
        prima = (c / "giornale").read_bytes()
        for nota_sbagliata in (lambda *a: None, lambda *a: {"chiave": "k"}, lambda *a: {"ricevuta": "applicato"}):
            segno = len(r.nomi)
            with pytest.raises(ValueError, match="ricevuta"):  # senza ricevuta l'evento non si potrebbe verificare: non si scrive
                r.racconta("Accredito", "test", [(n("accredito"), [(n("cliente"), n("A")), (n("importo"), n("70"))])],
                           neuroni_da=segno, nota_della_scrittura=nota_sbagliata)
            assert (c / "giornale").read_bytes() == prima and not r.trova("A")
        inventata = lambda *a: {"ricevuta": {"evento": f"evento:{len(r.e_frase)}", "esito": "applicato" if len(a) == 1 else "violazione_regola",
                                             "decisione": {"formato": 2, "calcolo": 1, "base": 40, "istante": 0, "regole": []}}}
        r.racconta("Accredito", "test", [(n("accredito"), [(n("cliente"), n("A")), (n("importo"), n("70"))])], nota_della_scrittura=inventata)
        with pytest.raises(Vietato):
            r.racconta("Ordine", "test", [(n("ordine"), [(n("cliente"), n("A")), (n("importo"), n("90"))])], nota_del_rifiuto=inventata)
        assert [m.verifica(e)["esito"] for e in (2, 3)] == ["coerente", "coerente"]
        assert r.e_nota[3]["ricevuta"]["decisione"]["base"] == 2 and "proposta" in r.e_nota[3]["ricevuta"]["decisione"]
    with aperta(Memoria(c)) as riletta:  # in memoria c'era la stessa cosa che c'è nel giornale
        assert riletta.rete.e_nota == r.e_nota


def test_dove_le_decisioni_non_si_registrano_la_nota_resta_di_chi_chiama(tmp_path):
    with aperta(Rete(str(tmp_path / "rete"))) as r:
        n = r.n
        r.racconta("Senza nota", "test", [(n("nota"), [(n("cosa"), n("A"))])])
        r.racconta("Con una nota qualsiasi", "test", [(n("nota"), [(n("cosa"), n("B"))])], nota_della_scrittura=lambda d: {"mia": 1})
        r.racconta("Con una decisione", "test", [(n("nota"), [(n("cosa"), n("C"))])],
                   nota_della_scrittura=lambda d: {"ricevuta": {"decisione": {"base": d["base"]}}})
        assert r.e_nota == {1: {"mia": 1}, 2: {"ricevuta": {"decisione": {"base": 1, "calcolo": motore.CALCOLO}}}}


def test_quello_che_la_memoria_consegna_e_una_copia(tmp_path):
    with aperta(Memoria(tmp_path / "rete", scritture=True)) as m:
        fido(m)
        consegnate = [movimento(m, "accredito", "A", "100", "a1"), m.ricevuta_di("a1"), movimento(m, "accredito", "A", "100", "a1"),
                      movimento(m, "ordine", "A", "500", "o1"), m.ricevuta_di("o1")]
        custodita = m.rete.e_nota[2]["ricevuta"]
        for x in consegnate:
            assert x is not custodita and x["decisione"] is not custodita["decisione"]
            x["decisione"]["regole"].append("aggiunta da chi l'ha ricevuta")
            x["esito"] = "cambiato"
        assert [m.verifica(e)["esito"] for e in (2, 3)] == ["coerente", "coerente"]
        assert m.ricevuta_di("a1")["esito"] == "applicato" and m.ricevuta_di("o1")["esito"] == "violazione_regola"
        assert movimento(m, "accredito", "A", "100", "a1")["decisione"]["regole"] == custodita["decisione"]["regole"]
