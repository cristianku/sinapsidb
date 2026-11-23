"""Un fatto che nomina due volte lo stesso ruolo è un fatto solo, e una condizione su quel ruolo vale per tutti e due i nomi.

Il motore li ammette (``Rete.racconta``, ``racconta_molti``; i circuiti hanno un modo dichiarato per dire di chi è un
fatto così) e il nucleo compilato si fa da parte quando la forma di un tipo ripete un ruolo. Ma il Python, che è il
riferimento, sceglie «la linea con meno neuroni» e ne passa i fatti senza togliere i doppioni: sulla linea del ruolo
ripetuto ogni fatto c'è una volta per nome. ``conta`` conta il doppio, ``leggi`` dà ogni riga due volte, ``conta per``
dà tutto al primo nome. E ``dove ruolo = nome``, guardata fatto per fatto, legge solo il primo dei due nomi: la stessa
condizione sullo stesso fatto è vera se la domanda parte da lì e falsa se c'è un indizio prima, mentre il percorso di
riferimento (``Rete.scatta``, i due indizi) lo trova.
"""

import pytest

from sinapsidb import lingua
from sinapsidb.motore import Rete
from test_revisione_circuiti_090 import aperta


@pytest.fixture
def rete(tmp_path):
    with aperta(Rete(str(tmp_path / "rete"))) as r:
        n = r.n
        for sala in ("S1", "S2", "S3"):
            r.racconta(f"Riunione in {sala}", "test", [(n("riunione"), [(n("presente"), n("Ada")), (n("presente"), n("Bea")), (n("sala"), n(sala))])])
        yield r


def righe(r, domanda):
    return lingua.chiedi(r, domanda)["righe"]


def test_tre_riunioni_sono_tre(rete):
    assert rete.conteggi()["fatti_in_vigore"] == 3
    assert righe(rete, "in riunione conta per sala") == [("S1", "1"), ("S2", "1"), ("S3", "1")]
    assert righe(rete, "in riunione conta") == [("3",)]
    assert righe(rete, "in riunione leggi sala") == [("S1",), ("S2",), ("S3",)]
    assert [x["fatti"] for x in lingua.vocabolario(rete) if x["tipo"] == "riunione"] == [3]


def test_dove_vale_come_i_due_indizi_anche_per_il_secondo_nome(rete):
    con_due_indizi = righe(rete, "accendi sala = S1 e presente = Bea in riunione leggi sala")  # il percorso di riferimento
    assert con_due_indizi == [("S1",)]
    assert righe(rete, "in riunione dove presente = Bea e sala = S1 leggi sala") == con_due_indizi
    assert righe(rete, "accendi sala = S1 in riunione dove presente = Bea leggi sala") == con_due_indizi
