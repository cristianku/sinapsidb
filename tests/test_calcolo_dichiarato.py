"""Chi dichiara con quale modo si fanno le somme dei circuiti: chi scrive, quando scrive, e nel record di ogni decisione.

Completano le controprove della revisione sulla 0.9.5 (``test_revisione_calcoli_095.py``): quelle dicono che cosa non deve
succedere, queste fissano com'è fatta la risposta e i casi che il cambio ha aperto.
"""

import json
import shutil
from enum import Enum
from pathlib import Path

import pytest

from sinapsidb import lingua, motore
from sinapsidb.memoria import Memoria, SoloLettura
from sinapsidb.motore import Rete, SegnoGuasto
from test_decisioni import _record, _riscrivi
from test_revisione_circuiti_090 import aperta, circuito, evento


DATI = Path(__file__).parent / "dati"
INDISTINGUIBILE = DATI / "storico-092-093-indistinguibile"
NOTA = [{"tipo": "nota", "legami": {"cosa": "X"}}]


def fido(m):
    """Un fido che non può andare sotto zero, e un cliente che non ne ha: il suo primo ordine è un no."""
    r, n = m.rete, m.rete.n
    r.proponi_circuito("Saldo", "test", "fido", [
        {"osserva": n(t), "lega": n("cliente"), "somma": n("importo"), "peso": k} for t, k in (("accredito", 1), ("ordine", -1))])
    r.proponi_regola("Fido", "test", "limite", 1, "vieta", [{"nome": "negativo", "circuito": n("fido"), "limite": 0, "peso": 1}])


def ordine(m, cliente, importo, **altro):
    return m.racconta(f"Ordine di {cliente}", [{"tipo": "ordine", "legami": {"cliente": cliente, "importo": importo}}], **altro)


def senza_modo(cartella, *eventi):
    """Toglie il modo di calcolo dalle decisioni di quegli eventi: come le scrive un programma fino alla 0.9.5."""
    record = _record(cartella)
    for e in eventi:
        del record[e][7]["ricevuta"]["decisione"]["calcolo"]
    _riscrivi(cartella, record)


# --- chi mette il segno, e quando ------------------------------------------------------------------------------------

def test_aprire_provare_e_leggere_non_mettono_il_segno(tmp_path):
    cartella = tmp_path / "rete"
    with aperta(Memoria(cartella)) as m:  # sola lettura, giornale nuovo
        assert m.segno() is None
        assert m.racconta("Nota", NOTA, prova=True)["esito"] == "prova"
        with pytest.raises(SoloLettura):
            m.racconta("Nota", NOTA)
    assert not (cartella / "formato").exists()
    with aperta(Memoria(cartella, scritture=True)) as m:  # può scrivere, ma non scrive
        assert m.racconta("Nota", NOTA, prova=True)["decisione"]["calcolo"] == 4
        assert m.conteggi()["eventi"] == 0
    assert not (cartella / "formato").exists()
    with aperta(Memoria(cartella, scritture=True)) as m:
        assert m.racconta("Nota", NOTA)["esito"] == "applicato"
        assert m.segno() == {"formato": 2, "dal": 0, "calcoli": [[4, 0]], "calcolo_dal": 0}


def test_anche_un_no_e_un_evento_scritto_e_dichiara(tmp_path):
    cartella = tmp_path / "rete"
    with aperta(Memoria(cartella, scritture=True)) as m:
        fido(m)
        (cartella / "formato").unlink()  # come un giornale scritto finora solo da un programma di prima
    with aperta(Memoria(cartella, scritture=True)) as m:
        no = ordine(m, "B", "60")
        assert no["esito"] == "violazione_regola" and no["decisione"]["calcolo"] == 4
        assert m.segno() == {"formato": 2, "dal": 2, "calcoli": [[4, 2]], "calcolo_dal": 2}
        assert m.verifica(no["evento"])["calcolo"] == 4


def test_il_segno_e_sul_disco_prima_dell_evento(tmp_path, monkeypatch):
    cartella = tmp_path / "rete"
    with aperta(Memoria(cartella, scritture=True)) as m:
        scrivi = motore.os.write
        monkeypatch.setattr(motore.os, "write", lambda *a: (_ for _ in ()).throw(OSError("disco pieno")))
        with pytest.raises(OSError):
            m.racconta("Nota", NOTA)
        monkeypatch.setattr(motore.os, "write", scrivi)
        # l'evento non è arrivato sul disco, la frontiera sì: se arriva dopo, è già dichiarato
        assert Rete.segno(str(cartella)) == {"formato": 2, "dal": 0, "calcoli": [[4, 0]], "calcolo_dal": 0}
        assert (cartella / "giornale").read_bytes() == b""
    with aperta(Memoria(cartella, scritture=True)) as m:
        x = m.racconta("Nota", NOTA)
        assert evento(x) == 0 and m.verifica(0)["calcolo"] == 4
        assert m.segno() == {"formato": 2, "dal": 0, "calcoli": [[4, 0]], "calcolo_dal": 0}


# --- il modo sta nel record della decisione ------------------------------------------------------------------------

def test_ogni_decisione_dichiara_il_suo_modo_anche_senza_il_segno(tmp_path):
    cartella = tmp_path / "rete"
    with aperta(Memoria(cartella, scritture=True)) as m:
        fido(m)
        no = ordine(m, "B", "60")
    assert all(x[7]["ricevuta"]["decisione"]["calcolo"] == 4 for x in _record(cartella))
    (cartella / "formato").unlink()
    with aperta(Memoria(cartella)) as m:
        v = m.verifica(no["evento"])
        assert v["esito"] == "coerente" and (v["calcolo"], v["calcolo_da"]) == (4, "ricevuta") and "calcoli_compatibili" not in v
        assert m.riesamina(no["evento"])["calcolo"] == 4


@pytest.mark.parametrize("strano", [99, 0, "4", 4.0, True, None])
def test_un_modo_che_non_si_conosce_non_si_rifa_con_un_altro(tmp_path, strano):
    cartella = tmp_path / "rete"
    with aperta(Memoria(cartella, scritture=True)) as m:
        fido(m)
        no = evento(ordine(m, "B", "60"))
    record = _record(cartella)
    record[no][7]["ricevuta"]["decisione"]["calcolo"] = strano
    _riscrivi(cartella, record)
    prima = (cartella / "formato").read_bytes()
    with aperta(Memoria(cartella)) as m:
        v = m.verifica(no)
        assert v["esito"] == "non_supportato" and "non conosce" in v["spiegazione"] and "calcoli_compatibili" not in v
        with pytest.raises(ValueError, match="non conosce"):
            m.riesamina(no)
    assert (cartella / "formato").read_bytes() == prima


def test_un_programma_piu_vecchio_tornato_a_scrivere_non_si_attribuisce_al_segno(tmp_path):
    """Dopo ``calcolo_dal`` una decisione che non dichiara il suo modo non l'ha scritta un programma che dichiara. Il
    segno dice ancora «modo 4», ma non l'ha scritto lui per quell'evento: si dice con quali modi è compatibile."""
    cartella = tmp_path / "rete"
    with aperta(Memoria(cartella, scritture=True)) as m:
        fido(m)
        no = evento(ordine(m, "B", "60"))
        doppio = m.rete.racconta("Accredito", "test", [(m.rete.n("accredito"), [
            (m.rete.n("cliente"), m.rete.n("A")), (m.rete.n("cliente"), m.rete.n("A")), (m.rete.n("importo"), m.rete.n("70"))])])
        assert m.segno()["calcolo_dal"] == 0
    senza_modo(cartella, no, doppio)
    with aperta(Memoria(cartella)) as m:
        v = m.verifica(no)  # il no di B non distingue nessun modo
        assert v["esito"] == "coerente" and v["calcoli_compatibili"] == [4, 3, 2, 1] and "calcolo" not in v
        assert "programma più vecchio" in v["spiegazione"]
        # lo stesso cliente due volte: il modo 3 lo avrebbe contato doppio, e la ricevuta dice 70
        v = m.verifica(doppio)
        assert v["esito"] == "coerente" and v["calcoli_compatibili"] == [4, 2, 1]


# --- un segno che non si legge ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("guasto", [
    b"", b"{", b"[]", b"{}", b'{"formato": 2}', b'{"formato": true, "dal": 0}', b'{"formato": 2, "dal": -1}',
    b'{"formato": 2, "dal": 0, "calcoli": [[4]]}', b'{"formato": 2, "dal": 0, "calcoli": [[4, 2], [3, 1]]}',
    b'{"formato": 2, "dal": 0, "calcoli": {"4": 0}}', b'{"formato": 2, "dal": 0, "calcoli": [["4", 0]]}',
    b'{"formato": 2, "dal": 0, "calcolo_dal": "0"}', b"\xff\xfe",
])
def test_un_segno_guasto_ferma_scritture_e_verifiche_e_resta_com_e(tmp_path, guasto):
    cartella = tmp_path / "rete"
    with aperta(Memoria(cartella, scritture=True)) as m:
        fido(m)
        no = evento(ordine(m, "B", "60"))
    (cartella / "formato").write_bytes(guasto)
    giornale = (cartella / "giornale").read_bytes()
    with aperta(Memoria(cartella, scritture=True)) as m:
        neuroni = len(m.rete.nomi)
        assert m.chiedi("vocabolario")["righe"] and m.conteggi()["eventi"] == no + 1  # leggere si può ancora
        assert set(m.segno()) == {"guasto"}
        with pytest.raises(SegnoGuasto):
            ordine(m, "Nuovo Cliente", "5")
        with pytest.raises(SegnoGuasto):
            m.rete.racconta("Nota", "test", [(m.rete.n("nota"), [(m.rete.n("cosa"), m.rete.n("X"))])], neuroni_da=neuroni)
        assert len(m.rete.nomi) == neuroni and len(m.rete.e_frase) == no + 1  # niente è rimasto a metà
        assert m.racconta("Nota", NOTA, prova=True)["esito"] == "prova"  # provare non scrive: si può
        v = m.verifica(no)
        assert v["esito"] == "non_verificabile" and "formato" in v["spiegazione"]
        with pytest.raises(SegnoGuasto):
            m.riesamina(no)
    assert (cartella / "formato").read_bytes() == guasto and (cartella / "giornale").read_bytes() == giornale


def test_un_segno_che_parla_di_eventi_che_il_giornale_non_ha(tmp_path):
    cartella = tmp_path / "rete"
    with aperta(Memoria(cartella, scritture=True)) as m:
        fido(m)
    for segno in ({"formato": 2, "dal": 0, "calcoli": [[4, 50]]}, {"formato": 2, "dal": 9}, {"formato": 2, "dal": 0, "calcoli": [[4, 0]], "calcolo_dal": 3},
                  {"formato": 3, "dal": 0}):
        (cartella / "formato").write_text(json.dumps(segno))
        with aperta(Memoria(cartella, scritture=True)) as m:
            with pytest.raises(SegnoGuasto):
                ordine(m, "B", "60")
        assert json.loads((cartella / "formato").read_text()) == segno and len(_record(cartella)) == 2


def test_chi_torna_a_scrivere_dopo_un_programma_piu_nuovo_mette_la_sua_frontiera(tmp_path):
    cartella = tmp_path / "rete"
    with aperta(Memoria(cartella, scritture=True)) as m:
        fido(m)
        no = evento(ordine(m, "B", "60"))
    record = _record(cartella)
    record[no][7]["ricevuta"]["decisione"]["calcolo"] = 5  # come l'avrebbe scritta un programma di domani
    _riscrivi(cartella, record)
    (cartella / "formato").write_text(json.dumps({"formato": 2, "dal": 0, "calcoli": [[4, 0], [5, no]], "calcolo_dal": 0, "di_domani": "resta"}))
    with aperta(Memoria(cartella, scritture=True)) as m:
        dopo = ordine(m, "B", "30")
        # la sua frontiera, e il fatto che da lì scrive di nuovo un programma che dichiara il modo in ogni decisione
        assert m.segno() == {"formato": 2, "dal": 0, "calcoli": [[4, 0], [5, no], [4, no + 1]], "calcolo_dal": 0, "dichiarano_da": [no + 1],
                             "di_domani": "resta"}
        assert m.verifica(no)["esito"] == "non_supportato" and m.verifica(dopo["evento"])["calcolo"] == 4
        assert m.verifica(0)["calcolo"] == 4


# --- il riesame quando il giornale non dice il modo ------------------------------------------------------------------

def test_il_riesame_riporta_tutte_le_risposte_possibili_e_non_ne_sceglie_una(tmp_path):
    cartella = shutil.copytree(INDISTINGUIBILE, tmp_path / "rete")
    with aperta(Memoria(cartella)) as m:
        v = m.verifica(3)
        assert v["esito"] == "coerente" and v["calcoli_compatibili"] == [4, 3, 2, 1] and "calcolo" not in v
        x = m.riesamina(3, [4])
        assert x["calcoli_compatibili"] == [4, 3, 2, 1] and "calcolo" not in x
        assert x["cambia"] is None and x["letture_in_piu"] == [] and x["ipotetico"]["esito"] == "indeterminato" and x["ipotetico"]["letture"] == []
        alternative = {tuple(a["calcoli"]): (a["esito"], [y["valore"] for y in a["letture"]]) for a in x["ipotetico"]["alternative"]}
        assert alternative == {(4, 3, 1): ("applicato", ["70", "10"]), (2,): ("violazione_regola", ["0", "-60"])}
        assert "non danno la stessa risposta" in x["ipotetico"]["spiegazione"]
        # chi cerca le decisioni toccate da una rettifica lo vede, e non lo conta tra quelle cambiate
        toccate = m.candidate(4, riesamina=True)
        assert toccate["esiti_cambiati"] == [] and toccate["esiti_indeterminati"] == [
            {"evento": "evento:3", "allora": "violazione_regola", "possibili": ["applicato", "violazione_regola"]}]
        # senza rettifiche tutti i modi rifanno lo stesso no: la risposta è una
        y = m.riesamina(3)
        assert y["cambia"] is False and y["ipotetico"] == y["allora"] and "alternative" not in y["ipotetico"]
    assert not (cartella / "formato.nuovo").exists()


def test_la_frontiera_non_legata_della_095_non_identifica_lo_scrittore(tmp_path):
    """Lo stesso segno lo lasciava la 0.9.5 semplicemente aprendo. La scrittura di oggi non gli dà più peso."""
    cartella = shutil.copytree(INDISTINGUIBILE, tmp_path / "rete")
    (cartella / "formato").write_text(json.dumps({"formato": 2, "dal": 0, "calcoli": [[4, 0]]}))
    with aperta(Memoria(cartella, scritture=True)) as m:
        x = m.riesamina(3, [4])
        assert "calcolo" not in x and x["cambia"] is None and x["ipotetico"]["esito"] == "indeterminato"
        nuovo = m.racconta("Nota", NOTA)
        assert m.segno() == {"formato": 2, "dal": 0, "calcoli": [[4, 0]], "calcolo_dal": 5}
        assert m.verifica(nuovo["evento"])["calcolo_da"] == "ricevuta"
        assert m.verifica(3)["calcoli_compatibili"] == [4, 3, 2, 1]


# --- testi e letture preparate ---------------------------------------------------------------------------------------

def test_un_testo_di_un_enumerazione_resta_il_suo_valore_in_memoria_e_nel_giornale(tmp_path):
    class Parola(str, Enum):
        FRASE = "Una nota"
        NOME = "Ada"

    cartella = tmp_path / "rete"
    with aperta(Rete(str(cartella))) as r:
        ada = r.n(Parola.NOME)
        e = r.racconta(Parola.FRASE, Parola.NOME, [(r.n("nota"), [(r.n("di"), ada)])])
        assert (type(r.nomi[ada]), r.nomi[ada]) == (str, "Ada") and (type(r.e_frase[e]), r.e_frase[e]) == (str, "Una nota")
        assert (type(r.e_origine[e]), r.e_origine[e]) == (str, "Ada") and "Ada" in r.in_parole(0) and "Parola" not in r.in_parole(0)
    with aperta(Rete(str(cartella))) as riletta:
        assert (riletta.nomi, riletta.e_frase, riletta.e_origine) == (r.nomi, r.e_frase, r.e_origine)


def test_una_lettura_preparata_resta_di_chi_era_anche_se_nasce_un_omonimo(tmp_path):
    with aperta(Memoria(tmp_path / "rete", scritture=True)) as db:
        r, n = db.rete, db.rete.n
        c = circuito(r, "ricavi", per=None, lega="cliente")
        ada = n("Ada")
        r.racconta("Vendita", "test", [(n("vendita"), [(n("cliente"), ada), (n("totale"), n("90"))])])
        p = lingua.prepara(r, "circuito ricavi di Ada")
        assert p.esegui() == [("Ada", "90")]
        altra = r.nuovo("Ada")  # un'omonima, nata dopo
        r.racconta("Vendita all'omonima", "test", [(n("vendita"), [(n("cliente"), altra), (n("totale"), n("5"))])])
        f, = r.scatta(n("termine"), [(n("termine di"), c)])
        r.racconta("Raddoppia il peso", "test", [(n("termine"), [(k, n("2") if k == n("peso") else v) for k, v in r.legami(f)])], ritira=[f])
        assert p.esegui() == [("Ada", "180")]  # la stessa Ada di prima, con il circuito di adesso
        with pytest.raises(lingua.Ambigua):  # chi la prepara adesso deve dire quale
            lingua.prepara(r, "circuito ricavi di Ada")


@pytest.mark.parametrize("rinata", [False, True], ids=["numero-riusato", "omonima-rinata"])
def test_anche_una_domanda_preparata_non_cambia_la_persona_di_cui_parla(tmp_path, rinata):
    """Lo stesso difetto trovato in revisione nella lettura di un circuito, cercato nelle domande ordinarie: un nome
    nato in una prova e poi dimenticato lascia il suo numero a un altro."""
    with aperta(Rete(str(tmp_path / "rete"))) as r:
        n = r.n
        vendita = lambda chi, totale: [(n("vendita"), [(n("cliente"), chi), (n("totale"), n(totale))])]
        r.racconta("Vendita", "test", vendita(n("Zoe"), "1"))
        segno = len(r.nomi)
        ada = n("Ada")
        r.racconta("Prova", "test", vendita(ada, "1"), prova=True)
        p = lingua.prepara(r, "accendi cliente = Ada in vendita leggi totale")
        ferma = lingua.prepara(r, "accendi cliente = Zoe in vendita leggi totale")
        assert p.esegui() == [] and ferma.esegui() == [("1",)]
        r.dimentica(segno)
        bea = n("Bea")
        assert bea == ada
        r.racconta("Vendita a Bea", "test", vendita(bea, "90"))
        if rinata:
            r.racconta("Vendita a un'altra Ada", "test", vendita(n("Ada"), "5"))
        with pytest.raises(lingua.ErroreDomanda, match="non c'è più"):
            p.esegui()
        assert ferma.esegui() == [("1",)]  # chi è nel giornale non si dimentica: la sua domanda resta buona
        assert lingua.prepara(r, "accendi cliente = Ada in vendita leggi totale").esegui() == ([("5",)] if rinata else [])
