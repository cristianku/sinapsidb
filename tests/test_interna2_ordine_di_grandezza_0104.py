"""Una scrittura che non avviene non deve cambiare l'ordine delle righe: nemmeno dove il motore tiene i valori in
ordine di grandezza.

``in accredito dove importo > 0 leggi …`` non ha un neurone da cui partire: il motore tiene i valori di «importo» in
ordine di grandezza (``Rete.ordinata``) e le righe escono in quell'ordine. L'ordine si costruisce alla prima domanda; i
valori che entrano nella linea dopo aspettano in ``Ordinata.attesa`` ed escono in coda a tutti gli altri. Una scrittura
che non avviene e che nominava un importo nuovo fa buttare via quell'ordine (``Rete.dimentica`` tiene solo le ordinate
con ``massimo < da``): alla domanda dopo si rifà da capo, tutto in ordine di grandezza. La stessa domanda, prima e dopo
una prova, un no o un errore del disco, dà le righe in un altro ordine, e con un limite dà un'altra riga. E già prima la
rete viva rispondeva in un ordine che chi riapre dal giornale non ritrova.
"""

import pytest

from sinapsidb.memoria import Memoria
from test_revisione_circuiti_090 import aperta
from test_segno_e_disco import fido, guasta

DOMANDA = "in accredito dove importo > 0 leggi cliente, importo"


def accredito(m, cliente, importo, **altro):
    return m.racconta(f"Accredito di {cliente}: {importo}", [{"tipo": "accredito", "legami": {"cliente": cliente, "importo": importo}}], **altro)


def risposte(m):
    return m.chiedi(DOMANDA, limite=2)["righe"], m.chiedi(DOMANDA)["righe"]


@pytest.mark.parametrize("come", ["prova", "rifiuto", "disco"])
def test_una_scrittura_che_non_avviene_non_cambia_l_ordine_dei_valori_tenuti_in_ordine_di_grandezza(tmp_path, monkeypatch, come):
    c = tmp_path / "rete"
    with aperta(Memoria(c, scritture=True)) as m:
        fido(m)
        assert accredito(m, "A", "10")["esito"] == "applicato"
        assert accredito(m, "B", "30")["esito"] == "applicato"
        assert m.chiedi(DOMANDA)["righe"] == [("A", "10"), ("B", "30")]  # la prima domanda: da qui gli importi sono tenuti in ordine
        assert accredito(m, "C", "20")["esito"] == "applicato"
        prima, eventi = risposte(m), m.conteggi()["eventi"]
        if come == "prova":
            assert accredito(m, "D", "5", prova=True)["esito"] == "prova"
        elif come == "rifiuto":
            assert accredito(m, "A", "-500")["esito"] == "violazione_regola"  # il fido di A andrebbe sotto zero
            eventi += 1  # il no è un evento, senza fatti
        else:
            with monkeypatch.context() as mp:
                guasta(mp, m.rete, scrivi=0)
                with pytest.raises(OSError):
                    accredito(m, "D", "5")
            assert not m.rete.fuori_uso
        assert m.conteggi()["eventi"] == eventi  # niente è cambiato
        dopo = risposte(m)
    with aperta(Memoria(c)) as riaperta:
        dal_giornale = risposte(riaperta)
    assert dopo == prima, "la scrittura non è avvenuta: la stessa domanda deve dare le stesse righe nello stesso ordine"
    assert prima == dal_giornale, "la rete viva e quella riaperta dal giornale devono dare le righe nello stesso ordine"


def test_la_rete_viva_e_quella_riaperta_danno_le_righe_nello_stesso_ordine_anche_senza_scritture_mancate(tmp_path):
    c = tmp_path / "rete"
    with aperta(Memoria(c, scritture=True)) as m:
        accredito(m, "A", "10")
        accredito(m, "B", "30")
        m.chiedi(DOMANDA)  # una lettura, e basta
        accredito(m, "C", "20")
        viva = risposte(m)
    with aperta(Memoria(c)) as riaperta:
        assert viva == risposte(riaperta)
