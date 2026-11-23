"""Terzo giro di revisione interna della candidata 0.10.4, lente «mutazioni»: una protezione che nessuna
prova tiene ferma.

Un fatto può avere lo stesso ruolo due volte (una riunione con due presenti, una misura con due valori). Sulla linea di
quel ruolo sta sotto ognuno dei suoi nomi. Tra il secondo giro e la candidata ``Domanda._compila`` toglie i doppioni in
tre punti dove i fatti si prendono passando quella linea neurone per neurone:

    return list(dict.fromkeys(fatti)) if una_volta else fatti   ->   return fatti

Togliendone uno alla volta tutta la suite resta verde: ``test_interna2_ruolo_ripetuto_0104.py`` passa solo dal percorso
senza indizi (la scelta della linea non ripetuta) e dalla condizione «=». Restano scoperti: il passo che entra dal ruolo
ripetuto (``poi in riunione``), la condizione su un numero nel ruolo ripetuto (rete in memoria, valori in ordine di
grandezza) e la stessa condizione sulla rete letta dal disco. Senza la protezione ogni fatto esce una volta per nome:
le righe raddoppiano e ``conta`` conta il doppio.
"""

import pytest

from sinapsidb import lingua
from sinapsidb.blocchi import ComeRete, ReteParziale, costruisci
from sinapsidb.motore import Rete
from test_revisione_circuiti_090 import aperta


@pytest.fixture
def rete(tmp_path):
    with aperta(Rete(str(tmp_path / "rete"))) as r:
        n = r.n
        for sala in ("S1", "S2"):
            r.racconta(f"Riunione in {sala}", "test", [(n("riunione"), [(n("presente"), n("Ada")), (n("presente"), n("Bea")), (n("sala"), n(sala))])])
        r.racconta("Misura", "test", [(n("misura"), [(n("valore"), n("7")), (n("valore"), n("9")), (n("sensore"), n("T1"))])])
        for chi in ("Ada", "Bea"):
            r.racconta(f"Scheda di {chi}", "test", [(n("scheda"), [(n("persona"), n(chi)), (n("presente"), n(chi))])])
        yield r


def righe(r, domanda):
    return lingua.chiedi(r, domanda)["righe"]


def test_il_passo_che_entra_dal_ruolo_ripetuto_prende_ogni_fatto_una_volta(rete):
    # le due persone delle schede sono tutte e due presenti alle stesse due riunioni: le riunioni restano due
    assert rete.conteggi()["fatti_in_vigore"] == 5
    assert righe(rete, "in scheda leggi presente poi in riunione leggi sala") == [("S1",), ("S2",)]
    assert righe(rete, "in scheda leggi presente poi in riunione conta") == [("2",)]
    assert righe(rete, "in scheda leggi presente poi in riunione conta per sala") == [("S1", "1"), ("S2", "1")]


def test_una_condizione_su_un_numero_nel_ruolo_ripetuto_prende_ogni_fatto_una_volta(rete):
    # la misura ha due valori, e tutti e due passano la condizione: è una misura sola
    assert righe(rete, "in misura dove valore > 5 leggi sensore") == [("T1",)]
    assert righe(rete, "in misura dove valore > 5 conta") == [("1",)]
    assert righe(rete, "in misura dove valore > 8 conta") == [("1",)]  # e basta uno dei due


def test_la_stessa_condizione_sulla_rete_letta_dal_disco(rete, tmp_path):
    rete.scarica()
    costruisci(tmp_path / "rete" / "giornale", tmp_path / "blocchi")
    disco = ReteParziale(tmp_path / "blocchi", tetto=64 << 10)
    try:
        assert righe(ComeRete(disco), "in misura dove valore > 5 leggi sensore") == righe(rete, "in misura dove valore > 5 leggi sensore") == [("T1",)]
        assert righe(ComeRete(disco), "in misura dove valore > 5 conta") == [("1",)]
    finally:
        disco.chiudi()
