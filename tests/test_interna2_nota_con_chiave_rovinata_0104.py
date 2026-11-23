"""Secondo giro interno sulla candidata 0.10.4: una nota con chiave rovinata nel giornale impedisce di aprire la memoria.

``Memoria.__init__`` ricostruisce le chiavi da ``e_nota`` con ``nota["impronta"]`` e ``nota["ricevuta"]``, senza
guardare come è fatta la nota. Se a un evento scritto con una chiave manca la ricevuta, o l'impronta, o al posto della
chiave c'è una lista, l'apertura solleva KeyError o TypeError: non si apre nemmeno per leggere, e non si può più
verificare niente. Lo stesso evento, scritto senza chiave, si apre e ``verifica`` lo dichiara «guasto».

Contratto, paragrafo 8: gli esiti di ``verifica`` sono quattro e «ogni differenza è un guasto segnalato». È la regola
di ``tests/test_interna_ricevuta_guasta_0104.py``: una ricevuta guasta dà un esito, e il resto del giornale si verifica
ancora.
"""

import pytest

from sinapsidb.memoria import Memoria
from test_decisioni import _record, _riscrivi
from test_revisione_circuiti_090 import aperta
from test_segno_e_disco import nota

ESITI = ("coerente", "guasto", "non_supportato", "non_verificabile")


@pytest.mark.parametrize("con_chiave", [False, True], ids=["senza-chiave", "con-chiave"])
def test_un_evento_senza_ricevuta_e_un_guasto_segnalato_anche_se_porta_una_chiave(tmp_path, con_chiave):
    c = tmp_path / "rete"
    with aperta(Memoria(c, scritture=True)) as m:
        nota(m, "A")  # evento 0, con la chiave «A»
        if con_chiave:
            nota(m, "B")  # evento 1, con la chiave «B»
        else:
            m.racconta("Nota B", [{"tipo": "nota", "legami": {"cosa": "B"}}])  # evento 1, senza chiave
    record = _record(c)
    assert ("chiave" in record[1][7]) is con_chiave
    del record[1][7]["ricevuta"]  # la ricevuta dell'evento 1 non c'è più
    _riscrivi(c, record)
    with aperta(Memoria(c)) as m:  # con la chiave: KeyError 'ricevuta', la memoria non si apre
        assert m.verifica(1)["esito"] == "guasto"
        assert m.verifica(0)["esito"] == "coerente"  # il resto del giornale si verifica ancora
        assert m.ricevuta_di("A")["evento"] == "evento:0"


@pytest.mark.parametrize("cambia", [
    lambda n: n.pop("impronta"),  # la chiave c'è, l'impronta del racconto no
    lambda n: n.__setitem__("chiave", ["B"]),  # al posto della chiave c'è una lista
], ids=["senza-impronta", "chiave-lista"])
def test_una_nota_con_chiave_rovinata_non_impedisce_di_aprire_e_di_verificare_il_resto(tmp_path, cambia):
    c = tmp_path / "rete"
    with aperta(Memoria(c, scritture=True)) as m:
        nota(m, "A")
        nota(m, "B")
    record = _record(c)
    cambia(record[1][7])
    _riscrivi(c, record)
    with aperta(Memoria(c)) as m:  # KeyError 'impronta' / TypeError: unhashable type
        assert m.verifica(1)["esito"] in ESITI
        assert m.verifica(0)["esito"] == "coerente"
        assert m.ricevuta_di("A")["evento"] == "evento:0"
