"""Una scrittura che non avviene (una prova, un no, un errore del disco) non deve cambiare le risposte.

``Rete._annulla`` rimette in vigore il fatto ritirato, ma lo riaccende in fondo alla sua linea e in fondo alle somme
del circuito: l'entità che era prima diventa l'ultima. Le righe di una domanda escono in quell'ordine, e con un limite
la stessa domanda dà un'altra riga. Chi riapre dal giornale ritrova l'ordine di prima."""

import pytest

from sinapsidb.memoria import Memoria
from test_revisione_circuiti_090 import aperta
from test_segno_e_disco import fido, guasta, movimento

DOMANDE = ("circuito fido", "in accredito somma importo per cliente")


def risposte(m):
    return {d: (m.chiedi(d, limite=1)["righe"], m.chiedi(d)["righe"]) for d in DOMANDE}


def rettifica(m, importo, **altro):
    """Sostituisce l'unico accredito di A con uno nuovo."""
    return m.racconta("Rettifica dell'accredito di A", [{"tipo": "accredito", "legami": {"cliente": "A", "importo": importo}}],
                      sostituisce=[{"tipo": "accredito", "indizi": {"cliente": "A"}}], **altro)


@pytest.mark.parametrize("come", ["prova", "rifiuto", "disco"])
def test_una_scrittura_che_non_avviene_non_cambia_le_righe_di_una_domanda(tmp_path, monkeypatch, come):
    c = tmp_path / "rete"
    with aperta(Memoria(c, scritture=True)) as m:
        fido(m)
        assert movimento(m, "accredito", "A", "100", "a")["esito"] == "applicato"
        assert movimento(m, "accredito", "B", "50", "b")["esito"] == "applicato"
        prima, eventi = risposte(m), m.conteggi()["eventi"]
        assert prima["circuito fido"][0] == [("A", "100")]
        if come == "prova":
            assert rettifica(m, "80", prova=True)["esito"] == "prova"
        elif come == "rifiuto":
            assert rettifica(m, "-5")["esito"] == "violazione_regola"  # il fido di A andrebbe sotto zero
            eventi += 1  # il no è un evento, senza fatti
        else:
            with monkeypatch.context() as mp:
                guasta(mp, m.rete, scrivi=0)
                with pytest.raises(OSError):
                    rettifica(m, "80")
            assert not m.rete.fuori_uso
        assert m.conteggi()["eventi"] == eventi and m.valore("fido", "A")["valore"] == "100"  # niente è cambiato
        dopo = risposte(m)
    with aperta(Memoria(c)) as riaperta:
        dal_giornale = risposte(riaperta)
    assert dal_giornale == prima
    assert dopo == dal_giornale, "la scrittura non è avvenuta: la stessa domanda deve dare le stesse righe, come dopo una riapertura"
