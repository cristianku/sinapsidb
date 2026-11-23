"""Terzo giro interno sulla 0.10.4: un legame che dichiara due pezzi e ne dà tre (o uno) ferma la rete, dai molti.

Il secondo giro ha fatto guardare a ``racconta_molti`` la lunghezza di ogni legame (``set(map(len, legami)) != {2}``) e ha
tolto il conto che c'era prima (``len(piatti) != 2 * len(legami)``: quanti numeri ci sono davvero, contro due per legame).
Ma la lunghezza che un legame dichiara non è quello che dà quando lo si scorre. Un legame che dichiara due e dà tre
pezzi, o uno solo, adesso passa per caso semplice: ``Rete._fatto`` lo spacchetta dopo aver già toccato le colonne, si
ferma lì, e la rete resta fuori uso con la memoria guasta. ``racconta`` lo stesso fatto lo rifiuta prima di toccare la
rete, perché i legami li prende una volta e li tiene come coppie. E la candidata di prima dai molti lo
rifiutava e basta: il conto dei numeri non tornava, e il fatto andava a ``racconta``. È una regressione.

Le regole (AGENTS.md): «Quello che può fallire a metà si controlla prima»; «I legami di un fatto si prendono una volta e
si controllano come coppie nei due percorsi; lo stesso ingresso sbagliato dà lo stesso errore da ``racconta`` e da
``racconta_molti``, e non ferma la rete».
"""

import pytest

from sinapsidb.motore import Rete
from test_revisione_circuiti_090 import aperta
from test_velocita import _uno_alla_volta

VIE = (("uno", _uno_alla_volta), ("molti", Rete.racconta_molti))
COLONNE = ("f_tipo", "f_inizio", "f_n", "f_nato", "f_morto", "s_ruolo", "s_a")


class Dichiara:
    """Un legame che dichiara una lunghezza e, scorso, dà un altro numero di pezzi."""

    def __init__(self, pezzi, lunghezza):
        self.pezzi, self.lunghezza = tuple(pezzi), lunghezza

    def __len__(self):
        return self.lunghezza

    def __iter__(self):
        return iter(self.pezzi)


def stato(r):
    return r.conteggi(), {k: len(getattr(r, k)) for k in COLONNE}, list(r.nomi)


def fatti(r):
    return [r.in_parole(f) for f in range(len(r.f_tipo))]


@pytest.mark.parametrize("pezzi", [3, 1], ids=["dichiara-due-ne-da-tre", "dichiara-due-ne-da-uno"])
@pytest.mark.parametrize("via,scrivi", VIE, ids=[v for v, _ in VIE])
def test_un_legame_che_dichiara_due_pezzi_e_ne_da_altri_si_rifiuta_e_la_rete_resta_in_servizio(tmp_path, via, scrivi, pezzi):
    c = tmp_path / "rete"
    with aperta(Rete(str(c))) as r:
        tipo, ruolo, ada, bea, dora = (r.n(s) for s in ("nota", "cosa", "Ada", "Bea", "Dora"))
        assert r.racconta("Prima", "test", [(tipo, [(ruolo, ada)])]) == 0
        storto = Dichiara((ruolo, ada, bea)[:pezzi], 2)
        prima, giornale = stato(r), (c / "giornale").read_bytes()
        with pytest.raises(ValueError):
            scrivi(r, "test", [("Storto", tipo, [storto])])
        assert (c / "giornale").read_bytes() == giornale  # non è stato scritto niente
        # è un ingresso da rifiutare, non un guasto: la rete resta in servizio, com'era
        assert r.fuori_uso is None and not r.memoria_guasta
        assert stato(r) == prima
        assert r.racconta("Dopo", "test", [(tipo, [(ruolo, dora)])]) == 1  # e la scrittura dopo è confermata
        in_memoria = fatti(r)
    with aperta(Rete(str(c), sola_lettura=True)) as riletta:
        assert fatti(riletta) == in_memoria == ["nota (cosa: Ada)", "nota (cosa: Dora)"]
