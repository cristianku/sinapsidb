"""Controesempi sulle istantanee: una prova, o un no, lascia in memoria la «forma» di un tipo di fatto diversa da
quella che si rilegge dal giornale; l'istantanea la salva com'è, e chi riapre da lì risponde a «vocabolario» e a
«percorsi» in modo diverso da chi rilegge il giornale.

``Rete._annulla`` toglie i fatti proposti ma non rimette ``forma[tipo]`` com'era; ``Rete.istantanea`` salva
``forma`` senza rifarla dai fatti.
"""

import pytest

from sinapsidb.memoria import Memoria
from test_revisione_circuiti_090 import aperta
from test_segno_e_disco import fido


def _ordini(m):
    """Un cliente con la sua scheda, due testate d'ordine e le loro righe: da un cliente ai prodotti si arriva per il
    numero dell'ordine, che è il primo ruolo della testata."""
    m.racconta("Scheda di Rossi", [{"tipo": "scheda", "legami": {"cliente": "Rossi", "città": "Verona"}}])
    for numero, prodotti in (("T1", ("vite", "dado")), ("T2", ("rondella", "bullone"))):
        m.racconta(f"Testata {numero}", [{"tipo": "testata", "legami": {"numero": numero, "cliente": "Rossi"}}])
        for prodotto in prodotti:
            m.racconta(f"Riga di {numero}", [{"tipo": "riga", "legami": {"numero": numero, "prodotto": prodotto}}])


def _risposte(m):
    return {domanda: m.chiedi(domanda)["righe"] for domanda in ("vocabolario", "percorsi da cliente a prodotto")}


def _proposta_senza_seguito(m, come):
    """Una testata con un ruolo in più, che nella rete non resta: una prova, oppure un no della regola del fido."""
    strana = [{"tipo": "testata", "legami": {"numero": "T9", "cliente": "Rossi", "urgenza": "alta"}}]
    if come == "prova":
        assert m.racconta("Testata di prova", strana, prova=True)["esito"] == "prova"
    else:
        ordine = [{"tipo": "ordine", "legami": {"cliente": "Rossi", "importo": "60"}}]
        assert m.racconta("Testata con un ordine senza fido", strana + ordine)["esito"] == "violazione_regola"


@pytest.mark.parametrize("come", ["prova", "rifiuto"])
def test_una_proposta_senza_seguito_non_cambia_le_risposte(tmp_path, come):
    with aperta(Memoria(tmp_path / "rete", scritture=True)) as m:
        fido(m)
        _ordini(m)
        prima = _risposte(m)
        assert ("testata", "numero, cliente", "2") in prima["vocabolario"] and prima["percorsi da cliente a prodotto"]
        fatti = m.conteggi()["fatti"]
        _proposta_senza_seguito(m, come)
        assert m.conteggi()["fatti"] == fatti
        assert _risposte(m) == prima


@pytest.mark.parametrize("come", ["prova", "rifiuto"])
def test_chi_riapre_dall_istantanea_risponde_come_chi_rilegge_il_giornale(tmp_path, come):
    c = tmp_path / "rete"
    with aperta(Memoria(c, scritture=True)) as m:
        fido(m)
        _ordini(m)
        _proposta_senza_seguito(m, come)
        m.rete.istantanea()
    with aperta(Memoria(c)) as dalla_foto:
        assert not dalla_foto.rete.istantanea_scartata
        con = _risposte(dalla_foto)
    (c / "istantanea").unlink()
    with aperta(Memoria(c)) as dal_giornale:
        senza = _risposte(dal_giornale)
    assert senza["percorsi da cliente a prodotto"], "dal giornale la strada c'è"
    assert con == senza
