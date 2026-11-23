"""Secondo giro interno sulla candidata 0.10.4: il nome che il fido fa con la chiave non distingue due chiavi diverse.

``Fido._riferimento`` dà a un ordine senza numero il nome «O-<chiave>». Per la memoria due chiavi sono diverse se sono
diverse come valori («ORD-7» e «ord-7», 7 e «7»); un nome invece si cerca senza maiuscole, accenti e spazi doppi
(``piana``), e un numero diventa il suo testo. Due richieste con due chiavi diverse danno così lo stesso numero
d'ordine: la seconda trova «il fatto c'è già, identico», riceve «nessun_cambiamento» e il fido residuo non scende.
Con la candidata di prima (il numero dell'evento) ognuna era un ordine deciso.

Contratto, paragrafo 5: riprovare è una richiesta nuova, con una chiave nuova e una ricevuta nuova; solo la stessa
chiave è la stessa richiesta. Il nome serve a «distinguere un fatto dagli altri» (``Fido._riferimento``), e nel modello
di riferimento della prova (``prove/fido/riferimento.py``) ogni ordine è deciso: un sì o un no.
"""

import pytest

from sinapsidb.fido import Fido


@pytest.mark.parametrize("prima,seconda", [("ORD-7", "ord-7"), (7, "7"), ("ordine  7", "ordine 7")],
                         ids=["maiuscole", "numero-e-testo", "spazi"])
def test_due_ordini_con_due_chiavi_diverse_sono_due_ordini_decisi(tmp_path, prima, seconda):
    f = Fido(tmp_path / "rete")
    try:
        assert f.esegui({"op": "accorda", "cliente": "Rossi", "importo": "800", "chiave": "a1"})["esito"] == "applicato"
        primo = f.esegui({"op": "ordine", "cliente": "Rossi", "importo": "100", "chiave": prima})
        assert primo["esito"] == "applicato"
        # per la memoria la seconda chiave è un'altra chiave: non ha una ricevuta
        assert f.esegui({"op": "stato", "chiave": seconda})["presente"] is False
        secondo = f.esegui({"op": "ordine", "cliente": "Rossi", "importo": "100", "chiave": seconda})
        assert secondo["ripetuta"] is False
        assert secondo["esito"] == "applicato", secondo  # «nessun_cambiamento»: il secondo ordine non è stato deciso
        assert f.esegui({"op": "fido", "cliente": "Rossi"})["valore"] == "600"  # resta 700
    finally:
        f.chiudi()
