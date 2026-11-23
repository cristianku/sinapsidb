"""Controprova sulla candidata 0.10.4: una chiave usata per un racconto che non ha cambiato niente vive solo in RAM.

Contratto delle decisioni, paragrafo 5 e caso 13 del paragrafo 10: la stessa chiave, dopo il riavvio e dopo un cambio
di stato, riceve la ricevuta di allora e non crea un evento nuovo. ``Memoria.ricevuta_di`` promette la ricevuta
«durevole» di una chiave: quello che dice prima della riapertura deve essere quello che dice dopo.
"""

import pytest

from sinapsidb.memoria import Memoria
from test_revisione_circuiti_090 import aperta


def abita(dove):
    return [{"tipo": "abita", "legami": {"chi": "Greta", "dove": dove}}]


def storia(m):
    """Un fatto, lo stesso fatto ripetuto con un'altra chiave (non cambia niente), poi una correzione che lo ritira."""
    assert m.racconta("Greta abita a Lugano", abita("Lugano"), chiave="k1")["esito"] == "applicato"
    allora = m.racconta("Greta abita a Lugano", abita("Lugano"), chiave="k2")
    assert allora["esito"] == "nessun_cambiamento"
    corretto = m.racconta("Greta si è trasferita a Zurigo", abita("Zurigo"),
                          sostituisce=[{"tipo": "abita", "indizi": {"chi": "Greta"}}], chiave="k3")
    assert corretto["esito"] == "applicato" and len(corretto["ritirati"]) == 1  # il cambio di stato
    return allora


def test_la_ricevuta_di_una_chiave_e_la_stessa_prima_e_dopo_la_riapertura(tmp_path):
    c = tmp_path / "rete"
    with aperta(Memoria(c, scritture=True)) as m:
        storia(m)
        calda = m.ricevuta_di("k2")
    with aperta(Memoria(c)) as riaperta:
        assert riaperta.ricevuta_di("k2") == calda


@pytest.mark.parametrize("riavvio", [False, True], ids=["stessa-istanza", "dopo-il-riavvio"])
def test_la_stessa_chiave_dopo_un_cambio_di_stato_non_scrive_un_evento_nuovo(tmp_path, riavvio):
    c = tmp_path / "rete"
    m = Memoria(c, scritture=True)
    try:
        allora = storia(m)
        if riavvio:
            m.chiudi()
            m = Memoria(c, scritture=True)
        prima = m.conteggi()
        ancora = m.racconta("Greta abita a Lugano", abita("Lugano"), chiave="k2")
        # la ricevuta di allora, nessun evento nuovo: Greta non torna a Lugano perché una richiesta vecchia è stata ripetuta
        assert (ancora["esito"], m.conteggi()) == (allora["esito"], prima)
        assert ancora == {**allora, "ripetuta": True}
    finally:
        m.chiudi()
