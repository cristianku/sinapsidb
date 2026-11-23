"""Terzo giro interno sulla 0.10.4: un numero di un'altra classe resta in memoria com'è, e l'istantanea non si salva più.

``racconta`` ammette come numero di un neurone qualunque ``int``, anche di una sottoclasse (``isinstance(x, int)``): chi
tiene i tipi e i ruoli in un'enumerazione (``class Ruolo(IntEnum)``) scrive, e la scrittura è confermata. Nel giornale
va il numero. In memoria no: ``Rete._fatto`` riceve l'oggetto di chi ha chiamato e lo mette così com'è tra le chiavi
delle linee (``vivi``) e nella forma del tipo. Da lì ``Rete.istantanea`` solleva ``ValueError: unmarshallable object``,
ogni volta, finché la rete non viene riaperta dal giornale: la rete viva non è più quella che si rilegge.

L'ammissione riscritta in questo giro dice che i legami «si prendono una volta sola e si tengono come coppie: quello
che viene controllato qui è quello che entra nella rete e nel giornale». Per la frase, l'origine e la nota è così
(``testo_semplice``, ``_nota_scritta``: in memoria resta quello che si rilegge dal giornale, non l'oggetto di chi ha
chiamato); per i numeri dei fatti no. C'era già nella candidata di prima: non è una regressione.
"""

import enum

import pytest

from sinapsidb.motore import Rete
from test_revisione_circuiti_090 import aperta
from test_velocita import _uno_alla_volta

VIE = (("uno", _uno_alla_volta), ("molti", Rete.racconta_molti))


def fatti(r):
    return [r.in_parole(f) for f in range(len(r.f_tipo))]


@pytest.mark.parametrize("dove", ["tipo", "ruolo", "neurone"])
@pytest.mark.parametrize("via,scrivi", VIE, ids=[v for v, _ in VIE])
def test_dopo_una_scrittura_confermata_l_istantanea_si_salva_e_si_rilegge(tmp_path, via, scrivi, dove):
    c = tmp_path / "rete"
    with aperta(Rete(str(c))) as r:
        nota, appunto, ruolo, ada, bea = (r.n(s) for s in ("nota", "appunto", "cosa", "Ada", "Bea"))
        Numero = enum.IntEnum("Numero", {"APPUNTO": appunto, "RUOLO": ruolo, "BEA": bea})
        assert r.racconta("Prima", "test", [(nota, [(ruolo, ada)])]) == 0
        # il primo fatto di un tipo nuovo: la sua forma e le sue linee nascono con lui
        fatto = {"tipo": (Numero.APPUNTO, [(ruolo, bea)]), "ruolo": (appunto, [(Numero.RUOLO, bea)]), "neurone": (appunto, [(ruolo, Numero.BEA)])}[dove]
        assert scrivi(r, "test", [("Con un numero di un'altra classe", *fatto)]) == 1  # ammesso, scritto e confermato
        assert r.fuori_uso is None
        in_memoria = fatti(r)
        assert in_memoria == ["nota (cosa: Ada)", "appunto (cosa: Bea)"]
        r.istantanea()  # la rete in servizio deve poter salvare quello che ha confermato
    with aperta(Rete(str(c), sola_lettura=True)) as riletta:
        assert not riletta.istantanea_scartata
        assert fatti(riletta) == in_memoria
