"""Controprova sulla candidata 0.10.4: il servizio del fido e la richiesta ripetuta senza un riferimento suo.

``Fido`` accetta un ordine, un fido o un pagamento senza numero, atto o causale e gliene dà uno fatto con il numero
dell'evento che sta per scrivere. Ripetendo la stessa richiesta con la stessa chiave quel numero è cambiato: il
racconto non è più lo stesso e la risposta è «conflitto». Contratto, paragrafo 5, e ``docs/prova-del-fido.md``, punto
6: la stessa richiesta con la stessa chiave riceve la risposta di allora, anche dopo un riavvio e anche se era un no.
"""

import pytest

from sinapsidb.fido import Fido

RICHIESTE = [{"op": "accorda", "cliente": "Rossi", "importo": "800", "chiave": "a1"},
             {"op": "ordine", "cliente": "Rossi", "importo": "1000", "chiave": "o1"},  # oltre il fido: un no
             {"op": "ordine", "cliente": "Rossi", "importo": "100", "chiave": "o2"},
             {"op": "pagamento", "cliente": "Rossi", "importo": "50", "chiave": "p1"}]


@pytest.mark.parametrize("riavvio", [False, True], ids=["stessa-istanza", "dopo-il-riavvio"])
def test_la_stessa_richiesta_con_la_stessa_chiave_riceve_la_risposta_di_allora(tmp_path, riavvio):
    f = Fido(tmp_path / "rete")
    try:
        allora = [f.esegui(r) for r in RICHIESTE]
        assert [x["esito"] for x in allora] == ["applicato", "violazione_regola", "applicato", "applicato"]
        if riavvio:
            f.chiudi()
            f = Fido(tmp_path / "rete")
        ultimo = f.esegui({"op": "stato"})["ultimo_evento"]
        ancora = [f.esegui(r) for r in RICHIESTE]
        assert f.esegui({"op": "stato"})["ultimo_evento"] == ultimo  # ripetere non scrive
        assert ancora == [{**x, "ripetuta": True} for x in allora]
    finally:
        f.chiudi()
