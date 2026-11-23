"""Dopo scritture vere, tutte confermate, la lettura di un circuito dà le righe in un ordine nella rete viva e in un
altro a chi riapre, dal giornale o dall'istantanea. Con un limite è un'altra riga.

La rete viva tiene le somme nell'ordine in cui le entità ci sono entrate (``Rete._contributo``); chi riapre le rifà
dai fatti (``Rete._compila_regole`` → ``Rete._somme``), un termine alla volta e nell'ordine delle linee. I due ordini
non coincidono appena un'entità entra nel circuito dal secondo termine prima che un'altra entri dal primo, oppure, in
un circuito raggruppato per mese, appena viene ritirata la vendita che aveva aperto un mese.

Il contratto (paragrafo 9) promette che dopo una scrittura che non avviene «la stessa domanda dà le stesse righe nello
stesso ordine, come a chi riapre dal giornale»: qui la rete viva non risponde come chi riapre già prima, e la prova,
il no e l'errore del disco la lasciano così."""

import pytest

from sinapsidb.memoria import Memoria
from test_revisione_circuiti_090 import aperta, circuito, vendita
from test_segno_e_disco import fido, guasta, movimento

DOMANDA = "circuito fido"


def risposte(m, domanda=DOMANDA):
    return m.chiedi(domanda, limite=1)["righe"], m.chiedi(domanda)["righe"]


def storia(m):
    """Due scritture vere: C entra nel circuito dal secondo termine (un ordine da zero), poi D dal primo."""
    fido(m)
    assert movimento(m, "ordine", "C", "0", "c")["esito"] == "applicato"
    assert movimento(m, "accredito", "D", "10", "d")["esito"] == "applicato"


@pytest.mark.parametrize("da", ["giornale", "istantanea"])
def test_la_lettura_di_un_circuito_esce_nello_stesso_ordine_dalla_rete_viva_e_da_chi_riapre(tmp_path, da):
    c = tmp_path / "rete"
    with aperta(Memoria(c, scritture=True)) as m:
        storia(m)
        viva = risposte(m)
        assert m.verifica_circuiti()["esito"] == "coerente"
        if da == "istantanea":
            m.rete.istantanea()
    with aperta(Memoria(c)) as riaperta:
        assert risposte(riaperta) == viva, "le scritture sono tutte nel giornale: chi riapre deve leggere le stesse righe, nello stesso ordine"


@pytest.mark.parametrize("come", ["prova", "rifiuto", "disco"])
def test_dopo_una_scrittura_che_non_avviene_il_circuito_si_legge_come_da_chi_riapre(tmp_path, monkeypatch, come):
    c = tmp_path / "rete"
    with aperta(Memoria(c, scritture=True)) as m:
        storia(m)
        if come == "prova":
            assert m.racconta("Ordine di D", [{"tipo": "ordine", "legami": {"cliente": "D", "importo": "3"}}], prova=True)["esito"] == "prova"
        elif come == "rifiuto":
            assert movimento(m, "ordine", "D", "30", "no")["esito"] == "violazione_regola"
        else:
            with monkeypatch.context() as mp:
                guasta(mp, m.rete, scrivi=0)
                with pytest.raises(OSError):
                    movimento(m, "ordine", "D", "3", "o")
            assert not m.rete.fuori_uso
        dopo = risposte(m)
    with aperta(Memoria(c)) as riaperta:
        assert dopo == risposte(riaperta), "la scrittura non è avvenuta: la stessa domanda deve dare le stesse righe, come dopo una riapertura"


def test_un_circuito_per_mese_si_legge_nello_stesso_ordine_dopo_il_ritiro_della_vendita_che_apriva_il_mese(tmp_path):
    c = tmp_path / "rete"
    with aperta(Memoria(c, scritture=True)) as m:
        circuito(m.rete)
        for giorno, totale in (("2026-01-05", "10"), ("2026-02-03", "20"), ("2026-01-20", "30")):
            assert vendita(m, giorno, totale)["esito"] == "applicato"
        ritiro = m.racconta("La vendita del 5 gennaio non c'è stata", [], sostituisce=[{"tipo": "vendita", "indizi": {"quando": "2026-01-05"}}])
        assert ritiro["esito"] == "applicato"
        viva = risposte(m, "circuito vendite mensili")
        assert sorted(viva[1]) == [("2026-01", "30"), ("2026-02", "20")]
    with aperta(Memoria(c)) as riaperta:
        assert risposte(riaperta, "circuito vendite mensili") == viva
