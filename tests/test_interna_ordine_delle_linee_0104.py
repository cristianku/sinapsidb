"""Revisione della candidata 0.10.4: una lettura, una prova o un no cambiano l'ordine delle linee in memoria.

``Rete._annulla`` rimette in vigore un fatto ritirato accodandolo alla sua linea (``_accendi``), non dove stava. Lo
usano la prova e il rifiuto di una sostituzione, e ``Memoria.chiedi`` senza clausola, che mette da parte per il tempo
della domanda i fatti che adesso non valgono. Nel giornale non va niente, ma la memoria non è più quella di prima: la
stessa domanda dà le righe in un altro ordine, con un limite dà altre righe, e chi riapre ritrova l'ordine di prima.
"""

import pytest

from sinapsidb import nucleo
from sinapsidb.memoria import Memoria
from test_revisione_circuiti_090 import aperta

REGISTRATO = "accendi cliente = A in vendita leggi totale come è registrato"


@pytest.fixture(autouse=True)
def senza_nucleo(monkeypatch):
    monkeypatch.setenv("SINAPSIDB_NUCLEO", "0")  # il difetto è nel motore: con il nucleo acceso è lo stesso
    nucleo.ricarica()
    yield
    nucleo.ricarica()


def vendita(m, cliente, totale, da):
    esito = m.racconta(f"Vendita a {cliente}: {totale}", [{"tipo": "vendita", "legami": {"cliente": cliente, "totale": totale, "vale da": da}}])
    assert esito["esito"] == "applicato"


def tre_vendite(m):
    vendita(m, "A", "10", "2999-01-01")  # registrata, vale dal 2999
    vendita(m, "B", "20", "2000-01-01")
    vendita(m, "A", "30", "2000-01-01")


def letture(m):
    return m.chiedi(REGISTRATO)["righe"], m.chiedi(REGISTRATO, limite=1)["righe"]


def test_una_domanda_senza_clausola_non_cambia_le_risposte_di_dopo(tmp_path):
    with aperta(Memoria(tmp_path / "rete", scritture=True)) as m:
        tre_vendite(m)
        prima = letture(m)
        assert prima == ([("10",), ("30",)], [("10",)])
        assert m.chiedi("in vendita conta per cliente")["righe"] == [("A", "1"), ("B", "1")]  # una lettura, di quello che vale adesso
        dopo = letture(m)
    with aperta(Memoria(tmp_path / "rete")) as riaperta:
        dal_giornale = letture(riaperta)
    assert dal_giornale == prima  # il giornale non è cambiato
    assert dopo == dal_giornale  # e la memoria che ha solo letto deve dire lo stesso


def test_una_prova_che_sostituisce_non_lascia_tracce(tmp_path):
    with aperta(Memoria(tmp_path / "rete", scritture=True)) as m:
        vendita(m, "A", "10", "2000-01-01")
        vendita(m, "A", "30", "2000-01-01")
        prima = letture(m)
        assert prima == ([("10",), ("30",)], [("10",)])
        prova = m.racconta("E se la prima fosse da 11?", [{"tipo": "vendita", "legami": {"cliente": "A", "totale": "11", "vale da": "2000-01-01"}}],
                           sostituisce=[{"tipo": "vendita", "indizi": {"cliente": "A", "totale": "10"}}], prova=True)
        assert prova["esito"] == "prova"
        dopo = letture(m)
    with aperta(Memoria(tmp_path / "rete")) as riaperta:
        dal_giornale = letture(riaperta)
    assert dal_giornale == prima
    assert dopo == dal_giornale  # una prova non scrive niente: la memoria deve restare quella del giornale
