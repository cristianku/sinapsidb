"""Terzo giro: la rete sul disco conta due volte un fatto che nomina due volte lo stesso neurone nello stesso ruolo.

Il contratto (paragrafo 9) lo prevede: «uno che nomina due volte lo stesso cliente vale una volta». Il motore in memoria
mette il fatto una volta sola sotto quel neurone (``Rete._fatto``), e lo stesso fa chi riapre dal giornale. Chi
costruisce la rete sul disco (``blocchi.costruisci``) scrive invece una coppia per legame, e la linea del ruolo tiene il
fatto due volte sotto lo stesso neurone. Le correzioni del secondo giro per i ruoli ripetuti tolgono i doppioni solo
dove si passa una linea intera o si entra da un passo precedente: da un indizio, da ``dove ruolo = nome`` e da due
indizi (``ComeRete.scatta``) il fatto esce due volte. La stessa domanda, sullo stesso giornale, dà un conto in memoria e
un altro sul disco: non è una differenza d'ordine, che per la rete sul disco è dichiarata, ma di righe.
"""

import pytest

from sinapsidb import blocchi, lingua
from sinapsidb.motore import Rete
from test_revisione_circuiti_090 import aperta

DOMANDE = [
    "accendi presente = Ada in riunione conta",
    "in riunione dove presente = Ada conta",
    "accendi presente = Ada e sala = S1 in riunione conta",
    "accendi presente = Ada in riunione leggi sala",
    "accendi presente = Ada in riunione conta per sala",
    "accendi presente = Ada conta",
]


def risposte(r):
    return {d: sorted(lingua.prepara(r, d).esegui()) for d in DOMANDE}


@pytest.fixture
def cartella(tmp_path):
    with aperta(Rete(str(tmp_path / "rete"))) as r:
        n = r.n
        r.racconta("Ada segnata due volte in S1", "test", [(n("riunione"), [(n("presente"), n("Ada")), (n("presente"), n("Ada")), (n("sala"), n("S1"))])])
        r.racconta("Ada e Bea in S2", "test", [(n("riunione"), [(n("presente"), n("Ada")), (n("presente"), n("Bea")), (n("sala"), n("S2"))])])
        assert r.conteggi()["fatti_in_vigore"] == 2
        assert risposte(r)["accendi presente = Ada in riunione conta"] == [("2",)]  # due riunioni: una per fatto
    return tmp_path


def test_la_rete_sul_disco_risponde_come_quella_riletta_dal_giornale(cartella):
    with aperta(Rete(str(cartella / "rete"), sola_lettura=True)) as riaperta:
        dal_giornale = risposte(riaperta)
    blocchi.costruisci(cartella / "rete" / "giornale", cartella / "disco")
    with aperta(blocchi.ComeRete(blocchi.ReteParziale(cartella / "disco"))) as disco:
        assert disco.conteggi()["fatti_in_vigore"] == 2
        sul_disco = risposte(disco)
    assert sul_disco == dal_giornale
