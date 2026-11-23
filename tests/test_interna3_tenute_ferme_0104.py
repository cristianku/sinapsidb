"""Terzo giro di revisione interna della candidata 0.10.4, lente «mutazioni».

Ognuna di queste prove tiene ferma una protezione aggiunta tra il secondo giro e la candidata che nessuna
prova del ramo teneva: tolta la protezione in una copia, la suite restava verde. Qui sono verdi sulla candidata e
rosse sulla copia mutata. Accanto a ogni prova c'è la mutazione che scopre.
"""

import os
import shutil
import struct
import zlib

import pytest
from starlette.testclient import TestClient

from sinapsidb import lingua, memoria, motore, nucleo, portale
from sinapsidb.blocchi import ComeRete, ReteParziale, costruisci
from sinapsidb.memoria import Memoria
from sinapsidb.motore import GiornaleGuasto, Rete
from sinapsidb.portale import ConfigPortale
from test_decisioni import _record, _riscrivi
from test_revisione_circuiti_090 import aperta
from test_revisione_frontiera_096 import MISTA
from test_segno_e_disco import fido, movimento


def nota(m, nome):
    return m.racconta(f"Nota {nome}", [{"tipo": "nota", "legami": {"cosa": nome}}], chiave=nome)


def sei_scritture(c):
    with aperta(Memoria(c, scritture=True)) as m:
        for nome in "abcdef":
            assert nota(m, nome)["esito"] == "applicato"
    return (c / "giornale").read_bytes()


# --- il giornale ------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("scritture", [True, False], ids=["chi-scrive", "chi-legge"])
@pytest.mark.parametrize("corpo", [b"12", b"null", b'"testo"'], ids=["numero", "null", "testo"])
def test_un_record_con_il_controllo_giusto_che_non_e_una_lista_e_un_guasto_detto(tmp_path, scritture, corpo):
    """Mutazione: ``record_buono`` senza «corpo[:1] == b"["». Il record ha la lunghezza e il controllo giusti ma non è
    un evento: senza quel controllo l'apertura cade con TypeError invece di dire GiornaleGuasto."""
    c = tmp_path / "rete"
    intero = sei_scritture(c)
    guasto = intero + struct.pack("<II", len(corpo), zlib.crc32(corpo)) + corpo
    (c / "giornale").write_bytes(guasto)
    with pytest.raises(GiornaleGuasto):
        Memoria(c, scritture=scritture)
    assert (c / "giornale").read_bytes() == guasto


@pytest.mark.parametrize("scritture", [True, False], ids=["chi-scrive", "chi-legge"])
def test_un_resto_piu_lungo_della_finestra_non_e_una_coda(tmp_path, monkeypatch, scritture):
    """Mutazione: ``giudica_la_coda`` senza il caso «troppo lungo». Oltre la finestra non si è guardato se ci sono
    record buoni: non si può dire che sia una coda, e non si taglia."""
    c = tmp_path / "rete"
    intero = sei_scritture(c)
    guasto = intero + struct.pack("<II", 5000, 12345) + b"\x01" * 3000  # dichiara più di quello che c'è, e non è spazio a zero
    (c / "giornale").write_bytes(guasto)
    # la finestra contiene sempre una scrittura sola (un record lungo quanto il massimo più un gruppo a lotti): per
    # restare piccola la prova abbassa tutte e tre le misure, e il resto, tremila byte, è più lungo
    monkeypatch.setattr(motore, "FINESTRA_DI_CODA", 64)
    monkeypatch.setattr(motore, "MASSIMO_RECORD", 2048)
    monkeypatch.setattr(motore, "LOTTO", 64)
    with pytest.raises(GiornaleGuasto, match="troppo lungo"):
        Memoria(c, scritture=scritture)
    assert (c / "giornale").read_bytes() == guasto


# --- la scrittura -----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("percorso", ["racconta", "racconta_molti"])
def test_i_legami_dati_una_volta_sola_entrano_come_sono_stati_controllati(tmp_path, percorso):
    """Mutazione: ``racconta`` non rimette ``fatti[quale] = (tipo, legami)``. I legami arrivano come qualcosa che si
    scorre una volta sola: quello che è stato controllato è quello che entra nella rete e nel giornale."""
    c = tmp_path / "rete"
    with aperta(Rete(str(c))) as r:
        n = r.n
        coppie = [(n("cosa"), n("A")), (n("peso"), n("3"))]
        if percorso == "racconta":
            r.racconta("Nota A", "test", [(n("nota"), iter(coppie))])
        else:
            assert r.racconta_molti("test", [("Nota A", n("nota"), iter(coppie))]) == 1
        assert not r.fuori_uso
        viva = (r.conteggi(), [r.legami(f) for f in range(len(r.f_tipo))], list(r.nomi))
        assert viva[1] == [coppie]
    with aperta(Rete(str(c), sola_lettura=True)) as r:
        assert (r.conteggi(), [r.legami(f) for f in range(len(r.f_tipo))], list(r.nomi)) == viva


@pytest.mark.parametrize("percorso", ["racconta", "racconta_molti"])
def test_un_legame_e_una_coppia_se_preso_una_volta_da_due_pezzi(tmp_path, percorso):
    """I due percorsi prendono ogni legame una volta sola. Uno che si scorre una volta sola e dà due pezzi è una coppia,
    ed entra com'è stato preso; uno che ne dà uno, o tre, non lo è, ed è rifiutato senza toccare niente."""
    c = tmp_path / "rete"
    with aperta(Rete(str(c))) as r:
        n = r.n
        tipo, ruolo, a = n("nota"), n("cosa"), n("A")

        def scrivi(legami):
            if percorso == "racconta":
                r.racconta("Nota A", "test", [(tipo, legami)])
            else:
                r.racconta_molti("test", [("Nota A", tipo, legami)])

        for storto in ([iter((ruolo,))], [iter((ruolo, a, a))], [(ruolo, a), iter(())]):
            with pytest.raises(ValueError, match="coppia"):
                scrivi(storto)
            assert not r.fuori_uso and r.conteggi()["eventi"] == 0 and os.path.getsize(c / "giornale") == 0
        scrivi([iter((ruolo, a))])
        assert not r.fuori_uso and [r.legami(f) for f in range(len(r.f_tipo))] == [[(ruolo, a)]]
    with aperta(Rete(str(c), sola_lettura=True)) as riletta:
        assert [riletta.legami(f) for f in range(len(riletta.f_tipo))] == [[(ruolo, a)]]


def test_uscendo_dai_lotti_si_conferma_anche_quello_che_era_gia_stato_scaricato(tmp_path, monkeypatch):
    """Mutazione: ``sync`` che esce dai lotti solo se c'è qualcosa in attesa (``elif self._in_attesa``, come prima).
    Un gruppo già scaricato nel giornale non è ancora confermato sul disco: tornando a «frase» deve esserlo."""
    c = tmp_path / "rete"
    with aperta(Rete(str(c))) as r:
        tipo, ruolo, ada = (r.n(s) for s in ("nota", "cosa", "Ada"))
        vera, confermati = motore.os.fdatasync, []
        monkeypatch.setattr(motore.os, "fdatasync", lambda fd: (confermati.append(fd), vera(fd))[1])
        r.sync = "lotti"
        r.racconta("Prima", "test", [(tipo, [(ruolo, ada)])])
        r.scarica()  # nel giornale, ma nessuno ha ancora chiesto al disco di confermarlo
        assert not confermati and os.path.getsize(c / "giornale") > 0
        r.sync = "frase"
        assert confermati == [r._fd]




def test_attesta_legge_il_segno_quando_ha_gia_preso_il_giornale(tmp_path, monkeypatch):
    """Mutazione: ``attesta`` che legge il segno prima di prendere il giornale. Tra la lettura e il blocco un altro
    attesta un altro tratto e finisce: il segno letto prima non lo porta, e riscrivendolo lo si cancellerebbe."""
    c = shutil.copytree(MISTA, tmp_path / "rete")
    vero, gia = motore.fcntl.flock, []

    def flock(fd, come):
        if not gia:  # il primo che chiede il giornale: un attimo prima che lo ottenga, un altro attesta e chiude
            gia.append(fd)
            Rete.attesta(str(c), 2, 1, 3, "un altro, arrivato un attimo prima")
        return vero(fd, come)

    monkeypatch.setattr(motore.fcntl, "flock", flock)
    Rete.attesta(str(c), 2, 3, 6, "registro degli aggiornamenti")
    monkeypatch.undo()
    assert [(x["da"], x["a"]) for x in Rete.segno(str(c))["attestati"]] == [(1, 3), (3, 6)]


# --- la memoria delle decisioni ----------------------------------------------------------------------------------------

def memoria_del_fido(c):
    with aperta(Memoria(c, scritture=True)) as m:
        fido(m)  # eventi 0 e 1: il circuito e la regola
        assert movimento(m, "accredito", "Rossi", "100", "a1")["esito"] == "applicato"  # evento 2
        assert movimento(m, "ordine", "Rossi", "150", "o1")["esito"] == "violazione_regola"  # evento 3
        assert movimento(m, "ordine", "Rossi", "60", "o2")["esito"] == "applicato"  # evento 4
    return _record(c)


ROVINATE = {
    "regola-lista": lambda d: d["regole"][0].__setitem__("regola_id", []),
    "nuovo-numero": lambda d: d["proposta"]["nuovi"].__setitem__(0, 7),
    "nuovi-vuoti": lambda d: d["proposta"].__setitem__("nuovi", []),
    "fatto-fuori-misura": lambda d: d["proposta"]["fatti"][0].__setitem__(0, 10 ** 400),
    "senza-nuovi": lambda d: d.__setitem__("proposta", {}),
}


@pytest.mark.parametrize("cambia", ROVINATE.values(), ids=ROVINATE.keys())
def test_una_ricevuta_rovinata_non_si_riesamina_e_lo_si_dice(tmp_path, cambia):
    """Mutazione: ``Memoria.riesamina`` senza la conversione in ValueError. Contratto, paragrafo 8: «Una ricevuta
    guasta non si riesamina»; sulla copia mutata escono TypeError, AttributeError, IndexError, OverflowError, KeyError."""
    c = tmp_path / "rete"
    record = memoria_del_fido(c)
    cambia(record[3][7]["ricevuta"]["decisione"])
    _riscrivi(c, record)
    with aperta(Memoria(c)) as m:
        with pytest.raises(ValueError):
            m.riesamina(3)


def test_una_memoria_che_non_finisce_di_aprirsi_lascia_libero_il_giornale(tmp_path, monkeypatch):
    """Mutazione: ``Memoria.__init__`` non chiude la rete se fallisce dopo averla aperta. Con un giornale vero oggi
    non succede (la forma delle note è controllata): si forza l'errore, e si guarda che il giornale non resti preso."""
    c = tmp_path / "rete"
    sei_scritture(c)

    class Finita(dict):
        def values(self):
            raise MemoryError("memoria finita")

        items = values

    class Rotta(Rete):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.e_nota = Finita(self.e_nota)

    monkeypatch.setattr(memoria, "Rete", Rotta)
    with pytest.raises(MemoryError) as errore:
        Memoria(c, scritture=True)
    monkeypatch.undo()
    assert errore.value is not None  # l'errore, e con lui la memoria rimasta a metà, è ancora in mano a qualcuno
    with aperta(Memoria(c, scritture=True)) as m:  # «uno solo scrive»: se la rete di prima tiene ancora il giornale non si apre
        assert m.conteggi()["eventi"] == 6


# --- la lingua --------------------------------------------------------------------------------------------------------

DOMANDA = "accendi cliente = Ada e canale = web in vendita leggi totale"


def test_dopo_una_preparazione_fallita_la_domanda_torna_a_rispondere_come_una_fresca(tmp_path):
    """Mutazione: ``Domanda._prepara`` lega i nomi direttamente in ``self._legati``. Il nome trovato in una preparazione
    fallita resterebbe legato alla domanda: dimenticato quello, la domanda direbbe «non c'è più» per sempre, per un
    neurone a cui non è mai stata preparata."""
    with aperta(Rete(str(tmp_path / "rete"))) as r:
        n = r.n
        r.racconta("Vendita a Zoe", "test", [(n("vendita"), [(n("cliente"), n("Zoe")), (n("totale"), n("1")), (n("canale"), n("negozio"))])])
        p = lingua.prepara(r, DOMANDA)
        assert p.esegui() == []
        segno = len(r.nomi)
        n("Ada")  # nominata per una scrittura che poi non avviene
        r.nuovo("web"), r.nuovo("web")
        with pytest.raises(lingua.ErroreDomanda, match="indica 2 cose"):
            p.esegui()  # si prepara di nuovo, trova Ada, e si ferma su «web»
        r.dimentica(segno)
        assert lingua.prepara(r, DOMANDA).esegui() == []
        assert p.esegui() == []  # la preparazione fallita non le ha lasciato niente: risponde come la fresca
        assert p.esegui() == []


def test_un_tipo_con_piu_forme_esce_in_ordine_di_nascita(tmp_path):
    """Mutazione: ``scelti`` su più linee con ``list`` di un insieme al posto di ``sorted``. I fatti 1 e 8 di un tipo
    che non ha una forma sola: un insieme li dà nell'ordine 8, 1."""
    c = tmp_path / "rete"
    with aperta(Rete(str(c))) as r:
        n = r.n
        r.racconta("Altro", "test", [(n("altro"), [(n("x"), n("zero"))])])  # fatto 0
        r.racconta("Appunto A", "test", [(n("appunto"), [(n("cosa"), n("A"))])])  # fatto 1
        for i in range(6):
            r.racconta("Altro", "test", [(n("altro"), [(n("x"), n(f"a{i}"))])])  # fatti 2..7
        r.racconta("Appunto B", "test", [(n("appunto"), [(n("cosa"), n("B")), (n("dove"), n("qui"))])])  # fatto 8
        assert not r.forma.get(n("appunto"))  # due forme diverse: nessuna linea porta tutti i fatti
        viva = lingua.chiedi(r, "in appunto leggi cosa")["righe"]
        assert viva == [("A",), ("B",)]
    with aperta(Rete(str(c), sola_lettura=True)) as r:
        assert lingua.chiedi(r, "in appunto leggi cosa")["righe"] == viva


@pytest.fixture
def ripetuti(tmp_path):
    with aperta(Rete(str(tmp_path / "rete"))) as r:
        n = r.n
        for sala in ("S1", "S2"):
            r.racconta(f"Riunione in {sala}", "test", [(n("riunione"), [(n("presente"), n("Ada")), (n("presente"), n("Bea")), (n("sala"), n(sala))])])
        r.racconta("Misura", "test", [(n("misura"), [(n("valore"), n("7")), (n("valore"), n("9")), (n("sensore"), n("T1"))])])
        for chi in ("Ada", "Bea"):
            r.racconta(f"Scheda di {chi}", "test", [(n("scheda"), [(n("persona"), n(chi)), (n("presente"), n(chi))])])
        yield r


def test_il_nucleo_non_conta_un_tipo_che_ripete_un_ruolo(ripetuti):
    """Mutazione: il nucleo lasciato anche con la forma che ripete un ruolo. Si vede solo con il nucleo acceso: con
    ``SINAPSIDB_NUCLEO=0`` questa prova è saltata."""
    if not nucleo.attivo():
        pytest.skip("il nucleo compilato è spento")
    p = lingua.prepara(ripetuti, "in riunione conta per presente")
    righe = p.esegui()
    assert not p.nucleo  # si è fatto da parte
    assert lingua.prepara(ripetuti, "in riunione conta").esegui() == [("2",)]
    assert righe == [("Ada", "2")]  # come il Python, che del ruolo ripetuto legge il primo nome


def test_il_ruolo_ripetuto_letto_dal_disco_da_ogni_fatto_una_volta(ripetuti, tmp_path):
    """Mutazione: senza ``dict.fromkeys`` sulla linea scorsa neurone per neurone, che è il percorso della rete sul disco."""
    ripetuti.scarica()
    costruisci(tmp_path / "rete" / "giornale", tmp_path / "blocchi")
    disco = ReteParziale(tmp_path / "blocchi", tetto=64 << 10)
    try:
        assert lingua.chiedi(ComeRete(disco), "in misura dove valore > 5 leggi sensore")["righe"] == [("T1",)]
        assert lingua.chiedi(ComeRete(disco), "in misura dove valore > 5 conta")["righe"] == [("1",)]
    finally:
        disco.chiudi()


# --- il portale -------------------------------------------------------------------------------------------------------

BASE = "http://127.0.0.1:8419"
LOCALE = ("127.0.0.1", 5000)
AZIONE = {"x-sinapsidb-azione": "1"}


@pytest.mark.parametrize("op, corpo", [
    ("verifica", {"evento": True}), ("verifica", {"evento": 1.9}), ("verifica", {"evento": "1"}), ("verifica", {"evento": -1}),
    ("candidate", {"rettifica": True}), ("riesamina", {"evento": 2, "rettifiche": [True]}), ("riesamina", {"evento": 2, "rettifiche": [1.9]}),
], ids=["vero", "decimale", "testo", "negativo", "rettifica-vera", "rettifiche-vero", "rettifiche-decimale"])
def test_il_numero_di_un_evento_e_un_intero_scritto_come_intero(tmp_path, op, corpo):
    """Mutazioni: il portale che torna a ``int(corpo[campo])`` e a ``int(x)`` per le rettifiche. «vero» diventerebbe
    l'evento 1 e 1.9 l'evento 1: la pagina risponderebbe per un evento che nessuno ha chiesto."""
    config = ConfigPortale(metriche_db=tmp_path / "m.db", sonda_intervallo=0, prova_pg=f"host={tmp_path}/nessuno port=1 dbname=x")
    with TestClient(portale.crea_app(config), base_url=BASE, client=LOCALE, raise_server_exceptions=False) as c:
        assert c.post("/api/decisioni/accorda", json={"cliente": "Rossi", "importo": "800"}, headers=AZIONE).status_code == 200
        assert c.post("/api/decisioni/ordine", json={"cliente": "Rossi", "importo": "100"}, headers=AZIONE).status_code == 200
        risposta = c.post(f"/api/decisioni/{op}", json=corpo, headers=AZIONE)
        assert risposta.status_code == 400, (risposta.status_code, risposta.text)
        assert "non è scritta bene" in risposta.json()["error"]  # respinta perché scritta male, non perché l'operazione dice no


def test_una_richiesta_annidata_oltre_misura_e_scritta_male_non_un_500(tmp_path):
    """Accanto alla mutazione «il portale senza RecursionError tra gli errori di una richiesta scritta male»: con
    Python 3.13 un annidamento che sta negli 8192 byte ammessi non arriva a RecursionError, e questa prova resta verde
    anche sulla copia mutata. Tiene ferma solo la risposta: 400, non 500."""
    config = ConfigPortale(metriche_db=tmp_path / "m.db", sonda_intervallo=0, prova_pg=f"host={tmp_path}/nessuno port=1 dbname=x")
    corpo = b'{"evento": ' + b"[" * 3500 + b"]" * 3500 + b"}"
    with TestClient(portale.crea_app(config), base_url=BASE, client=LOCALE, raise_server_exceptions=False) as c:
        risposta = c.post("/api/decisioni/verifica", content=corpo, headers={**AZIONE, "content-type": "application/json"})
        assert risposta.status_code == 400, (risposta.status_code, risposta.text[:200])
        assert c.get("/api/decisioni").status_code == 200
