"""Revisione della candidata 0.10.4: una domanda preparata che non riesce a prepararsi di nuovo resta a metà.

``Domanda._prepara`` rinnova il contrassegno di validità (``_valida``, ``_ignoti``, ``_neuroni``) prima di sapere se
la nuova preparazione riesce. Se solleva, il piano compilato resta quello di prima, con i numeri di neuroni di prima,
ma il contrassegno dice che è buono: la chiamata dopo non prepara più niente ed esegue il piano vecchio. Con il nucleo
acceso quel piano legge le colonne della rete di adesso con i numeri di allora: risponde con i fatti di un altro tipo.
"""

import pytest

from sinapsidb import lingua, nucleo
from sinapsidb.motore import Rete
from test_nucleo import libreria

VENDITE = "in vendita somma totale per cliente"


@pytest.fixture
def modo(libreria, monkeypatch):
    def scegli(compilato):
        monkeypatch.setenv("SINAPSIDB_NUCLEO", str(libreria) if compilato else "0")
        nucleo.ricarica()
        assert nucleo.attivo() is compilato
    yield scegli
    nucleo.ricarica()


def rete_con_una_vendita_tornata_indietro():
    """Una vendita proposta e non scritta (la nota non è pronta): dentro la decisione qualcuno prepara la domanda sulle
    vendite. Poi i numeri lasciati liberi toccano a un altro tipo di fatto, i resi, con altri ruoli."""
    r = Rete()
    n = r.n
    r.racconta("Ordine", "test", [(n("ordine"), [(n("chi"), n("A")), (n("quanto"), n("10"))])])
    posto = len(r.nomi)
    tenute = []

    def nota_non_pronta(decisione):
        p = lingua.prepara(r, VENDITE)
        assert p.esegui() == [("C", "100")]  # dentro la decisione la vendita proposta c'è
        tenute.append(p)
        raise ValueError("nota non pronta")

    with pytest.raises(ValueError, match="nota non pronta"):
        r.racconta("Vendita", "test", [(n("vendita"), [(n("cliente"), n("C")), (n("totale"), n("100"))])],
                   neuroni_da=posto, nota_della_scrittura=nota_non_pronta)
    assert len(r.nomi) == posto and not r.trova("vendita")  # la rete è tornata com'era
    r.racconta("Reso", "test", [(n("reso"), [(n("negozio"), n("N1")), (n("importo"), n("30"))])])
    return r, tenute[0]


@pytest.mark.parametrize("compilato", [False, True], ids=["python", "nucleo-c"])
def test_la_domanda_preparata_dentro_una_scrittura_tornata_indietro_non_risponde_per_un_altro_tipo(modo, compilato):
    modo(compilato)
    r, tenuta = rete_con_una_vendita_tornata_indietro()
    with pytest.raises(lingua.ErroreDomanda, match="vendita"):
        lingua.prepara(r, VENDITE)  # il riferimento: nella rete le vendite non ci sono
    with pytest.raises(lingua.ErroreDomanda, match="vendita"):
        tenuta.esegui()  # la prima volta la domanda tenuta lo dice
    # La seconda volta no: la rete è la stessa, la domanda è la stessa.
    try:
        righe = tenuta.esegui()
    except lingua.ErroreDomanda:
        return
    pytest.fail(f"la domanda sulle vendite ha risposto {righe} (nucleo: {tenuta.nucleo}, fatti passati: {tenuta.conteggi}): "
                "nella rete non c'è nessuna vendita, e una domanda fresca lo dice")


def test_c_e_python_danno_la_stessa_risposta_alla_stessa_domanda_tenuta(modo):
    risposte = {}
    for compilato in (False, True):
        modo(compilato)
        r, tenuta = rete_con_una_vendita_tornata_indietro()
        with pytest.raises(lingua.ErroreDomanda):
            tenuta.esegui()
        try:
            risposte[compilato] = tenuta.esegui()
        except lingua.ErroreDomanda as e:
            risposte[compilato] = f"errore: {e}"
    assert risposte[True] == risposte[False]


def test_un_nome_diventato_ambiguo_resta_ambiguo_anche_alla_seconda_esecuzione(modo):
    """Lo stesso difetto senza funzioni dentro la decisione e senza nucleo: la domanda nomina chi non c'è ancora."""
    modo(False)
    r = Rete()
    n = r.n
    vendi = lambda chi, quanto: r.racconta("Vendita", "test", [(n("vendita"), [(n("cliente"), chi), (n("totale"), n(quanto))])])
    vendi(n("Ada"), "10")
    tenuta = lingua.prepara(r, "accendi cliente = Bea in vendita somma totale")
    assert tenuta.esegui() == [("0",)]  # Bea non c'è ancora
    prima, seconda = r.nuovo("Bea"), r.nuovo("Bea")
    vendi(prima, "5")
    vendi(seconda, "7")
    for volta in ("prima", "seconda"):
        with pytest.raises(lingua.ErroreDomanda, match="indica 2 cose diverse"):
            lingua.prepara(r, "accendi cliente = Bea in vendita somma totale")  # il riferimento
        try:
            righe = tenuta.esegui()
        except lingua.ErroreDomanda as e:
            assert "indica 2 cose diverse" in str(e)
            continue
        pytest.fail(f"alla {volta} esecuzione la domanda tenuta ha risposto {righe}: di Bea ce ne sono due, con 5 e con 7")
