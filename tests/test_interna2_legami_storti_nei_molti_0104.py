"""Secondo giro interno sulla 0.10.4: legami storti che «tornano nel conto» fermano la rete, se arrivano dai molti.

``racconta_molti`` passa a ``racconta`` un fatto i cui legami non sono coppie guardando un conto solo: quanti numeri
ci sono in tutto, contro due per legame. Due legami storti in senso opposto (uno di tre pezzi e uno di uno, oppure uno
di quattro e uno vuoto) fanno tornare il conto: il fatto prende la strada rapida, ``Rete._fatto`` si ferma dopo aver
già toccato le colonne, e la rete resta fuori uso con la memoria guasta. ``racconta`` lo stesso fatto lo rifiuta
prima di toccare la rete («ogni legame di un fatto è una coppia»), e la rete continua a servire.

La regola (AGENTS.md, «Quello che può fallire a metà si controlla prima»): ``racconta`` rifiuta un fatto che non entra
intero prima di toccare la rete; ``racconta_molti`` in quel caso lo passa a ``racconta``. E ``racconta_molti`` promette
di fare «esattamente quello che farebbe racconta chiamata una volta per fatto».
"""

import pytest

from sinapsidb.motore import Rete
from test_revisione_circuiti_090 import aperta
from test_velocita import _uno_alla_volta

VIE = (("uno", _uno_alla_volta), ("molti", Rete.racconta_molti))
COLONNE = ("f_tipo", "f_inizio", "f_n", "f_nato", "f_morto", "s_ruolo", "s_a")


def storti(r, come):
    ruolo, ada, bea, cleo = (r.n(s) for s in ("cosa", "Ada", "Bea", "Cleo"))
    if come == "tre-e-uno":
        return [(ruolo, ada, bea), (ruolo,)]
    return [(ruolo, ada, bea, cleo), ()]  # quattro-e-zero


def stato(r):
    return r.conteggi(), {k: len(getattr(r, k)) for k in COLONNE}, list(r.nomi)


def fatti(r):
    return [r.in_parole(f) for f in range(len(r.f_tipo))]


@pytest.mark.parametrize("come", ["tre-e-uno", "quattro-e-zero"])
@pytest.mark.parametrize("via,scrivi", VIE, ids=[v for v, _ in VIE])
def test_legami_che_non_sono_coppie_si_rifiutano_e_basta_anche_se_il_conto_torna(tmp_path, via, scrivi, come):
    c = tmp_path / "rete"
    with aperta(Rete(str(c))) as r:
        tipo, ruolo, ada, dora = (r.n(s) for s in ("nota", "cosa", "Ada", "Dora"))
        legami = storti(r, come)
        assert r.racconta("Prima", "test", [(tipo, [(ruolo, ada)])]) == 0
        prima, giornale = stato(r), (c / "giornale").read_bytes()
        with pytest.raises(ValueError):
            scrivi(r, "test", [("Storto", tipo, legami)])
        assert (c / "giornale").read_bytes() == giornale  # non è stato scritto niente
        # è un ingresso da rifiutare, non un guasto: la rete resta in servizio, com'era
        assert r.fuori_uso is None and not r.memoria_guasta
        assert stato(r) == prima
        assert r.racconta("Dopo", "test", [(tipo, [(ruolo, dora)])]) == 1  # e la scrittura dopo è confermata
        in_memoria = fatti(r)
    with aperta(Rete(str(c), sola_lettura=True)) as riletta:
        assert fatti(riletta) == in_memoria == ["nota (cosa: Ada)", "nota (cosa: Dora)"]
