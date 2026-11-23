"""Revisione interna della candidata 0.10.4 (contratto §4 e §8).

§4: un segno che c'è ma non si legge non è un segno assente: «si può ancora chiedere, ma non si scrive e non si
verifica ... Lo stesso se sparisce dopo che il motore lo ha scritto». La memoria che lo ha scritto lo sa (rifiuta la
scrittura con «non c'è più: questo programma lo aveva scritto»), ma un attimo dopo verifica, riesamina e dice che il
segno «non c'è ancora».
"""

import pytest

from sinapsidb.memoria import Memoria
from sinapsidb.motore import SegnoGuasto
from test_revisione_circuiti_090 import aperta, evento
from test_segno_e_disco import fido, movimento, nota


def test_la_memoria_che_ha_scritto_il_segno_e_non_lo_trova_piu_non_verifica_e_non_riesamina(tmp_path):
    c = tmp_path / "rete"
    with aperta(Memoria(c, scritture=True)) as m:
        fido(m)
        no = evento(movimento(m, "ordine", "B", "60", "ordine-B"))
        assert m.verifica(no)["esito"] == "coerente" and m.segno()["dal"] == 0
        (c / "formato").unlink()
        with pytest.raises(SegnoGuasto, match="non c'è più"):  # questa memoria sa di averlo scritto: non scrive più
            nota(m, "dopo")
        assert m.chiedi("vocabolario")["righe"]  # chiedere si può ancora
        visti = {"verifica": m.verifica(no)["esito"], "segno": m.segno()}
        try:
            m.riesamina(no)
            visti["riesame"] = "risponde"
        except SegnoGuasto:
            visti["riesame"] = "si ferma"
        # la stessa memoria, un attimo dopo aver rifiutato la scrittura perché il segno è sparito
        assert visti["verifica"] == "non_verificabile" and visti["riesame"] == "si ferma" and visti["segno"] is not None, visti
    assert not (c / "formato").exists()
