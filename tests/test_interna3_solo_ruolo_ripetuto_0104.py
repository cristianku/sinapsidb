"""Terzo giro: un tipo di fatto che ha SOLO un ruolo ripetuto (un'amicizia tra due amici) viene ancora contato doppio.

La correzione del secondo giro (``tests/test_interna2_ruolo_ripetuto_0104.py``) sceglie, per passare tutti i fatti di un
tipo, «una linea su cui ogni fatto sta una volta sola»: ``una_sola`` in ``Domanda._compila``. Se il tipo non ne ha
nessuna, perché tutti i suoi ruoli sono ripetuti, ``linee`` resta quella di prima; e se il ruolo è uno solo la linea è
una sola, e il ramo ``len(linee) == 1`` la passa neurone per neurone senza togliere i doppioni. Ogni amicizia sta sotto
tutti e due gli amici: ``conta`` dice il doppio, ``leggi`` dà ogni riga due volte, ``conta per`` conta due volte lo
stesso fatto. ``vocabolario`` (``_Schema.quanti``, corretto nello stesso giro) e ``Rete.conteggi`` dicono il numero vero.

La regola è quella scritta nella prova del secondo giro: un fatto che nomina due volte lo stesso ruolo è un fatto solo.
"""

import pytest

from sinapsidb import lingua
from sinapsidb.motore import Rete
from test_revisione_circuiti_090 import aperta


@pytest.fixture
def rete(tmp_path):
    with aperta(Rete(str(tmp_path / "rete"))) as r:
        n = r.n
        for a, b in (("Ada", "Bea"), ("Ada", "Cia")):
            r.racconta(f"{a} e {b} sono amiche", "test", [(n("amicizia"), [(n("amico"), n(a)), (n("amico"), n(b))])])
        yield r


def righe(r, domanda):
    return lingua.prepara(r, domanda).esegui()


def test_due_amicizie_sono_due(rete):
    assert rete.conteggi()["fatti_in_vigore"] == 2
    assert [x["fatti"] for x in lingua.vocabolario(rete) if x["tipo"] == "amicizia"] == [2]
    assert righe(rete, "in amicizia conta") == [("2",)]


def test_ogni_amicizia_si_legge_una_volta(rete):
    assert len(righe(rete, "in amicizia leggi amico")) == 2
    assert len(righe(rete, "in amicizia leggi id_fatto")) == 2
    assert sum(int(quante) for _, quante in righe(rete, "in amicizia conta per amico")) == 2
