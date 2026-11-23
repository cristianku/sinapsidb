"""Revisione interna della candidata 0.10.4 (contratto §7 e §8): una ricevuta guasta nel giornale deve dare un esito.

``verifica`` ha quattro esiti e «ogni differenza è un guasto segnalato»; la ricerca delle candidate, dove un evento non
ha le letture, «non può vedere, e non presenta la sua lista come completa». Qui invece sollevano ValueError,
AttributeError e KeyError. Il giornale è quello storico vero ``storico-096-093-096``: l'evento 4 è il no della 0.9.3,
l'evento 5 la rettifica, l'evento 6 una scrittura della 0.9.6.
"""

import shutil
from pathlib import Path

import pytest

from sinapsidb.memoria import Memoria
from test_decisioni import _record, _riscrivi
from test_revisione_circuiti_090 import aperta


MISTA = Path(__file__).parent / "dati" / "storico-096-093-096"


def _manomessa(tmp_path, cambia):
    c = shutil.copytree(MISTA, tmp_path / "rete")
    record = _record(c)
    assert record[4][3] == "rifiuto" and "proposta" in record[4][7]["ricevuta"]["decisione"]
    cambia(record[4][7]["ricevuta"])
    _riscrivi(c, record)
    return c


@pytest.mark.parametrize("cambia", [
    lambda ricevuta: ricevuta["decisione"].pop("proposta"),  # la richiesta negata non c'è più: il no non si può rifare
    lambda ricevuta: ricevuta.__setitem__("decisione", "x"),  # al posto della decisione c'è un testo
    lambda ricevuta: ricevuta.__setitem__("decisione", []),  # o una lista vuota
], ids=["no-senza-proposta", "decisione-testo", "decisione-lista"])
def test_una_ricevuta_guasta_e_un_guasto_segnalato_non_un_eccezione(tmp_path, cambia):
    c = _manomessa(tmp_path, cambia)
    with aperta(Memoria(c)) as m:
        v = m.verifica(4)  # §8: gli esiti sono quattro, e ogni differenza è un guasto segnalato
        assert v["esito"] == "guasto" and v["differenze"]
        assert m.verifica(0)["esito"] == "coerente"  # il resto del giornale si verifica ancora


def test_una_decisione_senza_letture_non_fa_cadere_la_ricerca_delle_candidate(tmp_path):
    c = _manomessa(tmp_path, lambda ricevuta: ricevuta["decisione"].pop("regole"))  # il no non dice più che cosa ha letto
    with aperta(Memoria(c)) as m:
        assert m.verifica(4)["esito"] == "guasto"  # la verifica lo sa dire
        # §7: dove un evento non ha le letture la ricerca non può vedere, e non presenta la sua lista come completa.
        # Vale per la rettifica 5, che tocca il cliente del no, e per qualunque altra scrittura dello stesso giornale.
        for rettifica in (5, 6):
            trovate = m.candidate(rettifica, riesamina=True)
            assert trovate["copertura"]["completa"] is False and trovate["copertura"]["eventi_senza_letture"] >= 1
