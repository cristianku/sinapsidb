"""Sulla candidata 0.10.4: il contratto del giornale che registra le decisioni vale anche fuori dai lotti.

Dalla 0.10.2 una rete aperta senza ``decisioni`` su un giornale che le registra rifiuta i lotti, perché «il contratto
è del giornale, e chi lo riapre lo trova nel suo segno». Con ``sync="frase"`` e ``sync="os"`` quella stessa rete
scrive invece un evento senza ricevuta dopo la frontiera, da ``racconta`` e dal caso semplice di ``racconta_molti``.
"""

import pytest

from sinapsidb.memoria import Memoria
from sinapsidb.motore import Rete
from test_revisione_circuiti_090 import aperta
from test_velocita import _uno_alla_volta


VIE = (("uno", _uno_alla_volta), ("molti", Rete.racconta_molti))


@pytest.mark.parametrize("sync", ["frase", "os"])
@pytest.mark.parametrize("via,scrivi", VIE, ids=[v for v, _ in VIE])
def test_senza_lotti_non_resta_un_evento_senza_decisione_dopo_la_frontiera(tmp_path, via, scrivi, sync):
    c = tmp_path / "memoria"
    with aperta(Memoria(c, scritture=True)) as m:
        a = m.racconta("A", [{"tipo": "nota", "legami": {"cosa": "A"}}], chiave="A")
        assert m.segno()["dal"] == 0
    prima = (c / "giornale").read_bytes()
    try:
        # Una nuova istanza, con il default decisioni=False, sul giornale il cui segno è al suo posto. Un solo scrittore.
        with aperta(Rete(str(c), sync=sync)) as r:
            n = r.n
            scrivi(r, "test", [("B", n("nota"), [(n("cosa"), n("B"))])])
    except (ValueError, RuntimeError):
        assert (c / "giornale").read_bytes() == prima  # lecito rifiutare: allora non scrive niente
    with aperta(Memoria(c)) as m:
        assert m.ricevuta_di("A") == a
        # Lecito fermarsi o registrare una decisione vera: mai un evento senza ricevuta dopo la frontiera.
        for e in range(m.conteggi()["eventi"]):
            v = m.verifica(e)
            assert v["esito"] == "coerente", v.get("differenze")
