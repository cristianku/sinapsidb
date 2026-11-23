"""Il nucleo compilato deve dare le stesse righe del Python, nello stesso ordine, oppure farsi da parte.

Il Python è il riferimento: ogni prova fa la stessa domanda due volte sulla stessa rete, con il nucleo spento e con il
nucleo acceso, e confronta righe e conteggi. La libreria si compila in una cartella temporanea; se sulla macchina non
c'è un compilatore queste prove si saltano, e il resto della suite gira comunque senza nucleo.
"""

import random
import shutil
from pathlib import Path

import pytest

from sinapsidb import lingua, nucleo
from sinapsidb.blocchi import ComeRete, ReteParziale, costruisci
from sinapsidb.memoria import Memoria
from sinapsidb.motore import Rete, Vietato
from test_revisione_circuiti_090 import aperta


@pytest.fixture(scope="module")
def libreria(tmp_path_factory):
    if shutil.which("cc") is None:
        pytest.skip("su questa macchina non c'è un compilatore C")
    return nucleo.compila(uscita=tmp_path_factory.mktemp("nucleo") / "_nucleo.so")


@pytest.fixture
def acceso(libreria, monkeypatch):
    """Una funzione che fa la stessa domanda senza nucleo e con il nucleo, e restituisce le due domande preparate."""
    def due_volte(rete, frase, usato=True):
        monkeypatch.setenv("SINAPSIDB_NUCLEO", "0")
        nucleo.ricarica()
        lenta = lingua.prepara(rete, frase)
        attese, conteggi = lenta.esegui(), lenta.conteggi
        assert not lenta.nucleo
        monkeypatch.setenv("SINAPSIDB_NUCLEO", str(libreria))
        nucleo.ricarica()
        assert nucleo.attivo()
        veloce = lingua.prepara(rete, frase)
        for _ in range(2):  # la seconda volta le tabelle sono già pronte
            assert veloce.esegui() == attese, frase
            assert veloce.conteggi == conteggi and veloce.nucleo is usato, frase
        return attese

    yield due_volte
    nucleo.ricarica()


FRASI = ("in vendita somma totale per mese(quando)", "in vendita somma totale per anno(quando)", "in vendita somma totale per cliente",
         "in vendita somma totale per quando", "in vendita somma totale", "in vendita conta per mese(quando)", "in vendita conta per cliente",
         "in vendita conta", "in vendita somma sconto per cliente", "in vendita somma sconto")
NUMERI = ("0", "7", "12.5", "12.50", "-3.25", "-0.00", "0.000001", "999999.999999", "5E+2", "1_000", " 42 ", "+8.10", "00012.3400")
NON_NUMERI = ("n/d", "", "NaN", "Infinity", "dodici", "1,5", "--3")


def rete_a_caso(seme, vendite=400):
    rng = random.Random(seme)
    r = Rete()
    n = r.n
    t, cl, q, tot, sc = n("vendita"), n("cliente"), n("quando"), n("totale"), n("sconto")
    giorni = [f"{anno}-{mese:02d}-{giorno:02d}" for anno in (2025, 2026) for mese in (1, 2, 11, 12) for giorno in (1, 9, 28)]
    scritti = []
    for i in range(vendite):
        totale = rng.choice(NUMERI) if rng.random() < 0.3 else f"{rng.randint(-500, 9000)}.{rng.randint(0, 99):02d}"
        sconto = rng.choice(NON_NUMERI + NUMERI)
        e = r.racconta(f"V{i}", "test", [(t, [(cl, n(f"C{rng.randrange(25)}")), (q, n(rng.choice(giorni))), (tot, n(totale)), (sc, n(sconto))])])
        scritti.append(e)
        if rng.random() < 0.15:  # un fatto di un altro tipo in mezzo, e ogni tanto un ritiro
            r.racconta("Altro", "test", [(n("nota"), [(n("cosa"), n(f"X{i}"))])])
        if rng.random() < 0.1:
            vivi = [f for f in range(len(r.f_tipo)) if r.f_tipo[f] == t and r.f_morto[f] < 0]
            r.racconta("Storno", "test", ritira=[rng.choice(vivi)])
    return r


@pytest.mark.parametrize("seme", range(6))
def test_le_stesse_righe_nello_stesso_ordine(acceso, seme):
    r = rete_a_caso(seme)
    for frase in FRASI:
        assert acceso(r, frase)


def test_resta_uguale_mentre_la_rete_cambia(acceso):
    r = rete_a_caso(41, vendite=120)
    n = r.n
    t, cl, q, tot, sc = n("vendita"), n("cliente"), n("quando"), n("totale"), n("sconto")
    vendi = lambda chi, giorno, totale: r.racconta("V", "test", [(t, [(cl, n(chi)), (q, n(giorno)), (tot, n(totale)), (sc, n("0"))])])
    for passo in range(5):
        for frase in FRASI:
            acceso(r, frase)
        if passo == 0:  # gruppi e numeri che il nucleo non ha ancora visto
            vendi("Cliente Nuovo", "2030-07-04", "123456.789")
        elif passo == 1:  # un gruppo che resta senza fatti, e poi li riprende: l'ordine della linea cambia
            suoi = [f for f in range(len(r.f_tipo)) if r.f_tipo[f] == t and r.f_morto[f] < 0 and r.riceve(f, cl, n("C3"))]
            r.racconta("Via tutti quelli di C3", "test", ritira=suoi)
        elif passo == 2:
            vendi("C3", "2025-01-01", "1.5")
        elif passo == 3:  # una prova e un neurone dimenticato: non devono lasciare traccia nelle tabelle
            segno = len(r.nomi)
            r.racconta("Prova", "test", [(t, [(cl, n("Fantasma")), (q, n("2031-01-01")), (tot, n("77.77")), (sc, n("0"))])], prova=True, neuroni_da=segno)
            assert len(r.nomi) == segno and len(r._tabelle_del_nucleo.scala) <= segno
            vendi("Altro Nome", "2031-02-02", "9")  # il numero di «Fantasma» tocca a un altro


def test_una_scrittura_rifiutata_e_una_vista_del_passato(acceso, tmp_path):
    with aperta(Memoria(tmp_path / "rete", scritture=True)) as m:
        r, n = m.rete, m.rete.n
        r.proponi_circuito("Saldo", "test", "fido", [
            {"osserva": n(t), "lega": n("cliente"), "somma": n("importo"), "peso": k} for t, k in (("accredito", 1), ("ordine", -1))])
        r.proponi_regola("Fido", "test", "limite", 1, "vieta", [{"nome": "negativo", "circuito": n("fido"), "limite": 0, "peso": 1}])
        muovi = lambda tipo, chi, quanto: r.racconta(tipo, "test", [(n(tipo), [(n("cliente"), n(chi)), (n("importo"), n(quanto))])])
        for chi, quanto in (("A", "100"), ("B", "40.50"), ("A", "0.25")):
            muovi("accredito", chi, quanto)
        muovi("ordine", "A", "60")
        with pytest.raises(Vietato):
            muovi("ordine", "B", "41")
        for frase in ("in accredito somma importo per cliente", "in ordine somma importo per cliente", "in ordine conta", "in accredito somma importo"):
            acceso(r, frase)
        passato = Rete.vista(str(tmp_path / "rete"), 4)  # com'era dopo quattro eventi: una rete rifatta dal giornale
        assert acceso(passato, "in accredito somma importo per cliente") == [("A", "100"), ("B", "40.50")]


@pytest.mark.parametrize("valore,perche", [("0.1234567", "più di sei decimali"), ("9" * 25, "troppo grande per i milionesimi"),
                                           ("1E-9", "più di sei decimali")])
def test_un_numero_che_non_si_rappresenta_lo_somma_il_python(acceso, valore, perche):
    r = rete_a_caso(7, vendite=30)
    n = r.n
    r.racconta("Fuori misura", "test", [(n("vendita"), [(n("cliente"), n("C1")), (n("quando"), n("2025-01-01")), (n("totale"), n(valore)), (n("sconto"), n("0"))])])
    for frase in ("in vendita somma totale per cliente", "in vendita somma totale"):
        acceso(r, frase, usato=False)
    acceso(r, "in vendita conta per cliente")  # contare non guarda i numeri: lo fa ancora il nucleo
    acceso(r, "in vendita somma sconto per cliente")


def test_una_somma_che_non_sta_in_64_bit_la_fa_il_python(acceso):
    r = Rete()
    n = r.n
    for i in range(6):  # ognuno sta nei milionesimi, la somma no
        r.racconta("Grande", "test", [(n("vendita"), [(n("cliente"), n("C")), (n("numero"), n(f"V{i}")), (n("totale"), n("4000000000000"))])])
    assert acceso(r, "in vendita somma totale per cliente", usato=False) == [("C", "24000000000000")]
    assert acceso(r, "in vendita somma totale", usato=False) == [("24000000000000",)]
    assert acceso(r, "in vendita conta") == [("6",)]


def test_dove_i_ruoli_non_stanno_sempre_allo_stesso_posto_il_nucleo_si_fa_da_parte(acceso):
    r = Rete()
    n = r.n
    t, cl, tot = n("vendita"), n("cliente"), n("totale")
    r.racconta("Una", "test", [(t, [(cl, n("A")), (tot, n("10"))])])
    r.racconta("Con un ruolo in più", "test", [(t, [(cl, n("B")), (tot, n("5")), (n("nota"), n("x"))])])
    assert r.forma[t] is None
    assert acceso(r, "in vendita somma totale per cliente", usato=False) == [("A", "10"), ("B", "5")]
    # lo stesso ruolo due volte nello stesso fatto: il posto non basta a dire di chi è
    doppio = Rete()
    n = doppio.n
    t, cl, tot = n("accredito"), n("cliente"), n("totale")
    doppio.racconta("Per due", "test", [(t, [(cl, n("A")), (cl, n("B")), (tot, n("70"))])])
    doppio.racconta("Per due", "test", [(t, [(cl, n("B")), (cl, n("B")), (tot, n("1"))])])
    for frase in ("in accredito somma totale per cliente", "in accredito somma totale", "in accredito conta"):
        acceso(doppio, frase, usato=False)


def test_le_domande_con_un_punto_di_partenza_o_una_condizione_restano_al_python(acceso):
    r = rete_a_caso(3, vendite=80)
    for frase in ("accendi cliente = C1 in vendita somma totale", "in vendita dove totale > 100 somma totale per cliente",
                  "in vendita dove cliente = C2 conta", "accendi cliente = C1 in vendita leggi quando poi in vendita conta"):
        acceso(r, frase, usato=False)


def test_la_rete_sul_disco_non_usa_il_nucleo(acceso, tmp_path):
    with aperta(Rete(str(tmp_path / "rete"))) as r:
        n = r.n
        for i in range(20):
            r.racconta("V", "test", [(n("vendita"), [(n("cliente"), n(f"C{i % 3}")), (n("totale"), n(str(i)))])])
    costruisci(str(tmp_path / "rete" / "giornale"), str(tmp_path / "blocchi"))
    disco = ComeRete(ReteParziale(str(tmp_path / "blocchi")))
    try:
        assert acceso(disco, "in vendita somma totale per cliente", usato=False) == [("C0", "63"), ("C1", "70"), ("C2", "57")]
    finally:
        disco.chiudi()


def test_come_si_scrive_un_numero():
    for nome, atteso in (("12.50", (12500000, 2)), ("-0.00", (0, 2)), ("5E+2", (500000000, 0)), (" 42 ", (42000000, 0)), ("1_000", (1000000000, 0)),
                         ("0.000001", (1, 6)), ("0.0000001", (0, nucleo.FUORI_MISURA)), ("NaN", (0, nucleo.NON_NUMERO)), ("dodici", (0, nucleo.NON_NUMERO)),
                         ("1" + "0" * 31, (0, nucleo.FUORI_MISURA)), ("4611686018428", (0, nucleo.FUORI_MISURA)), ("4611686018427", (4611686018427000000, 0))):
        assert nucleo.numero_di(nome) == atteso, nome
    assert [nucleo.in_testo(*x) for x in ((13700000, 2), (0, 2), (-12500000, 2), (500000000, 0), (1, 6), (0, 0))] == [
        "13.70", "0.00", "-12.50", "500", "0.000001", "0"]


def test_una_libreria_che_non_e_di_questo_sorgente_non_si_usa(libreria, monkeypatch, tmp_path):
    try:
        monkeypatch.setenv("SINAPSIDB_NUCLEO", "0")
        nucleo.ricarica()
        assert nucleo.stato() == {"attivo": False, "perche": "è spento da SINAPSIDB_NUCLEO"}
        monkeypatch.setenv("SINAPSIDB_NUCLEO", str(tmp_path / "non-c-e.so"))
        nucleo.ricarica()
        assert nucleo.stato() == {"attivo": False, "perche": "la libreria non è stata compilata su questa macchina"}
        monkeypatch.setenv("SINAPSIDB_NUCLEO", str(libreria))
        nucleo.ricarica()
        assert nucleo.stato() == {"attivo": True}
        monkeypatch.setattr(nucleo, "impronta_del_sorgente", lambda: "un altro sorgente")
        nucleo.ricarica()
        assert nucleo.stato() == {"attivo": False, "perche": "la libreria è stata compilata da un altro sorgente: va compilata di nuovo"}
        (tmp_path / "rotta.so").write_bytes(b"non sono una libreria")
        monkeypatch.setenv("SINAPSIDB_NUCLEO", str(tmp_path / "rotta.so"))
        nucleo.ricarica()
        assert not nucleo.attivo() and "non si carica" in nucleo.stato()["perche"]
        r = rete_a_caso(1, vendite=10)  # e senza nucleo tutto funziona come sempre
        assert lingua.prepara(r, "in vendita conta").esegui() and not lingua.prepara(r, "in vendita conta").nucleo
    finally:
        nucleo.ricarica()


def test_senza_compilatore_lo_si_dice(monkeypatch, tmp_path):
    with pytest.raises(RuntimeError, match="non c'è un compilatore"):
        nucleo.compila(compilatore=str(tmp_path / "nessuno"), uscita=tmp_path / "x.so")
    assert not (tmp_path / "x.so").exists()
    assert Path(nucleo.SORGENTE).exists()
