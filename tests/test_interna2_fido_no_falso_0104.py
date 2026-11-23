"""Secondo giro di revisione interna sulla candidata 0.10.4: il servizio del fido e l'errore che ferma la memoria.

``Fido.esegui`` guarda se la memoria si è fermata solo quando l'errore non è un ``KeyError`` o un ``ValueError``: quei due
li rilancia prima di guardare, come «la richiesta non sta in piedi». Se a fermare la memoria dopo il commit è proprio
uno di loro (una voce che manca in un dizionario mentre la memoria si allinea; un ``NonNumerico``, che è un
``ValueError`` e che ``Memoria`` trasforma in ``Rifiutato``), chi aveva chiesto l'operazione riceve un no di tipo
«richiesta» o «rifiutato», mentre nel giornale c'è la sua ricevuta «applicato», e la memoria ferma resta lì fino alla
richiesta dopo.

Contratto, paragrafo 9: «Il servizio del fido lo fa da solo, e a chi aveva chiesto l'operazione rimasta a metà risponde
che l'esito lo dice il giornale: ripetendo con la stessa chiave». Con ``MemoryError`` lo fa
(``test_interna_zone_del_critico_0104``); qui lo stesso guasto, nello stesso punto, con un altro tipo di errore.
"""

import pytest

from sinapsidb.fido import Fido
from sinapsidb.motore import NonNumerico


def ferma_al_prossimo_evento(monkeypatch, rete, errore):
    """L'evento arriva nel giornale, ma la memoria non riesce a contarlo: una volta sola."""
    vera, fatto = rete._in_memoria, []

    def in_memoria(*a, **k):
        if not fatto:
            fatto.append(True)
            raise errore
        return vera(*a, **k)

    monkeypatch.setattr(rete, "_in_memoria", in_memoria)


@pytest.mark.parametrize("errore", [MemoryError("la memoria non riesce ad allinearsi"), KeyError("una voce che manca mentre la memoria si allinea"),
                                    ValueError("un valore che non torna mentre la memoria si allinea"),
                                    NonNumerico("una somma che non si rifà dopo il commit")],
                         ids=["MemoryError-controllo", "KeyError", "ValueError", "NonNumerico"])
def test_chi_ha_chiesto_l_operazione_che_ha_fermato_la_memoria_non_riceve_un_no(tmp_path, monkeypatch, errore):
    f = Fido(tmp_path / "fido")
    try:
        assert f.esegui({"op": "accorda", "cliente": "A", "importo": "100", "chiave": "fido"})["esito"] == "applicato"
        ferma = f.memoria
        ferma_al_prossimo_evento(monkeypatch, ferma.rete, errore)
        risposta = f.esegui({"op": "ordine", "cliente": "A", "importo": "60", "chiave": "ordine-1"})  # non solleva
        riaperta_subito = f.memoria is not ferma
        assert ferma.rete.fuori_uso and ferma.rete.memoria_guasta  # l'operazione ha fermato la memoria: l'evento è nel giornale e non in lei
        # il giornale dice che l'ordine è entrato: con la stessa chiave si ritrova la ricevuta «applicato»
        stato = f.esegui({"op": "stato", "chiave": "ordine-1"})
        assert stato["ok"] and stato["presente"] and stato["esito"] == "applicato"
        assert f.esegui({"op": "fido", "cliente": "A"})["valore"] == "40"
        # quindi a chi lo aveva chiesto non si può aver detto che la richiesta non stava in piedi, o che è stata rifiutata
        assert risposta["ok"] is False and risposta["tipo"] == "da_ripetere" and "stessa chiave" in risposta["errore"], risposta
        assert riaperta_subito, "la memoria ferma è rimasta al suo posto fino alla richiesta dopo"
    finally:
        f.chiudi()
