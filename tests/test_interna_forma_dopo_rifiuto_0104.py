"""Sulla candidata 0.10.4: una scrittura che non avviene non cambia le risposte di dopo, né quello che finisce nell'istantanea.

Un fatto che torna indietro (il record non si compone, il disco non lo prende, è solo una prova) lascia segnato
che il suo tipo «non ha una forma sola» (``Rete.forma``). Il giornale non lo sa: la rete viva e la sua istantanea
elencano i ruoli e fanno scattare i fatti in un altro ordine rispetto alla rete riletta dal giornale.
"""

import pytest

from sinapsidb import lingua
from sinapsidb.memoria import Memoria
from sinapsidb.motore import Rete
from test_revisione_circuiti_090 import aperta
from test_segno_e_disco import guasta
from test_velocita import _uno_alla_volta


VIE = (("uno", _uno_alla_volta), ("molti", Rete.racconta_molti))
DOMANDA = "in vendita leggi cliente, totale"


def risposte(r):
    """Le prime due righe, come le vede chi chiede con un limite, e i ruoli del tipo come li elenca il vocabolario."""
    return lingua.chiedi(r, DOMANDA, limite=2)["righe"], [t["ruoli"] for t in lingua.vocabolario(r) if t["tipo"] == "vendita"]


@pytest.mark.parametrize("perche", ["frase-non-codificabile", "disco-pieno"])
@pytest.mark.parametrize("via,scrivi", VIE, ids=[v for v, _ in VIE])
def test_un_fatto_non_scritto_in_mezzo_ai_molti_non_cambia_le_risposte_di_dopo(tmp_path, monkeypatch, via, scrivi, perche):
    c = tmp_path / "rete"
    with aperta(Rete(str(c), sync="os")) as r:
        n = r.n
        vendita, totale, cliente = n("vendita"), n("totale"), n("cliente")

        def v(frase, chi, quanto):
            return frase, vendita, [(cliente, n(chi)), (totale, n(quanto))]

        assert scrivi(r, "importazione", [v("Prima", "Ada", "10"), v("Seconda", "Bea", "20"), v("Terza", "Ada", "30")]) == 3
        prima = risposte(r)
        # Stesso tipo, un ruolo in meno, nessun neurone nuovo: non arriva nel giornale.
        if perche == "frase-non-codificabile":
            illeggibile = b"Vendita \xff".decode("utf-8", "surrogateescape")
            with pytest.raises(UnicodeEncodeError):
                scrivi(r, "importazione", [(illeggibile, vendita, [(totale, n("10"))])])
        else:
            with monkeypatch.context() as mp:
                guasta(mp, r, scrivi=0)
                with pytest.raises(OSError):
                    scrivi(r, "importazione", [("Vendita senza cliente", vendita, [(totale, n("10"))])])
        assert not r.fuori_uso and r.conteggi()["fatti"] == 3
        dopo = risposte(r)
        r.istantanea()
    giornale, oltre = Rete.com_era(str(c), 3)
    assert oltre is None and risposte(giornale) == prima  # nel giornale non è cambiato niente
    with aperta(Rete(str(c))) as da_foto:
        foto = risposte(da_foto)
    assert dopo == prima, "la rete viva risponde in un altro modo dopo una scrittura che non è avvenuta"
    assert foto == prima, "l'istantanea non dice quello che dice il giornale"


def test_una_prova_non_cambia_la_risposta_alla_domanda_di_dopo(tmp_path):
    c = tmp_path / "memoria"
    with aperta(Memoria(c, scritture=True)) as m:
        for frase, chi, quanto in (("Prima", "Ada", "10"), ("Seconda", "Bea", "20"), ("Terza", "Ada", "30")):
            assert m.racconta(frase, [{"tipo": "vendita", "legami": {"cliente": chi, "totale": quanto}}])["esito"] == "applicato"
        prima = m.chiedi(DOMANDA, limite=2)["righe"]
        eventi = m.conteggi()["eventi"]
        assert m.racconta("Solo una prova", [{"tipo": "vendita", "legami": {"totale": "10"}}], prova=True)["esito"] == "prova"
        assert m.conteggi()["eventi"] == eventi
        dopo = m.chiedi(DOMANDA, limite=2)["righe"]
    with aperta(Memoria(c)) as riaperta:
        assert riaperta.chiedi(DOMANDA, limite=2)["righe"] == prima
    assert dopo == prima, "la prova ha lasciato una traccia: la stessa domanda dà altre righe"
