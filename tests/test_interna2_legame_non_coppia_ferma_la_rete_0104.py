"""Secondo giro, sulla candidata 0.10.4: un legame che non è una coppia ferma la rete invece di essere rifiutato.

Contratto §9: «Un fatto che non può entrare intero è rifiutato prima di toccare la rete: ... un legame che non è una
coppia di neuroni che ci sono». AGENTS: «``racconta`` rifiuta un fatto che non entra intero prima di toccare la rete;
``racconta_molti`` in quel caso lo passa a ``racconta``».

Due buchi nei controlli nuovi:

- ``_racconta_molti`` decide che i legami sono coppie contando i numeri (``len(piatti) != 2 * len(legami)``): un legame
  di un numero accanto a uno di tre, o uno vuoto accanto a uno di quattro, fanno il conto giusto. Il fatto prende la
  strada corta, ``_fatto`` si ferma dopo aver toccato le colonne e la rete resta fuori uso con la memoria guasta.
  ``racconta``, per lo stesso ingresso, rifiuta pulito con un ValueError.
- ``racconta`` controlla le coppie scorrendole, e poi le riscorre in ``_fatto`` e in ``_sul_disco``. Una coppia che si
  può scorrere una volta sola (``map(r.n, ("cosa", "Bea"))``) passa il controllo e arriva vuota a ``_fatto``: la rete
  si ferma a metà di un fatto, in tutti e due i percorsi.

In tutti i casi il giornale resta intatto: ma un ingresso che andava solo rifiutato lascia la rete inservibile fino
alla riapertura, e un'importazione si ferma lì.
"""

import pytest

from sinapsidb import lingua
from sinapsidb.motore import Rete
from test_revisione_circuiti_090 import aperta
from test_velocita import _uno_alla_volta

VIE = (("uno", _uno_alla_volta), ("molti", Rete.racconta_molti))
COLONNE = ("f_tipo", "f_inizio", "f_n", "f_nato", "f_morto", "s_ruolo", "s_a")
DOMANDA = "in nota leggi frase, cosa"


def storto(r, guasto):
    tipo, ruolo, bea = (r.n(s) for s in ("nota", "cosa", "Bea"))
    if guasto == "uno-e-tre":  # due legami, quattro numeri: ma nessuno dei due è una coppia
        return tipo, [(ruolo,), (ruolo, bea, bea)]
    if guasto == "zero-e-quattro":
        return tipo, [(), (ruolo, bea, ruolo, bea)]
    return tipo, [map(r.n, ("cosa", "Bea"))]  # una coppia che si scorre una volta sola


def stato(r):
    return (r.conteggi(), {k: len(getattr(r, k)) for k in COLONNE}, list(r.nomi), r._confermati,
            {k: list(v.items()) for k, v in r.vivi.items()}, dict(r.forma))


@pytest.mark.parametrize("guasto", ["uno-e-tre", "zero-e-quattro", "coppia-che-si-consuma"])
@pytest.mark.parametrize("via,scrivi", VIE, ids=[v for v, _ in VIE])
def test_un_legame_che_non_e_una_coppia_e_rifiutato_senza_fermare_la_rete(tmp_path, via, scrivi, guasto):
    c = tmp_path / "rete"
    with aperta(Rete(str(c))) as r:
        tipo, ruolo, ada, cleo = (r.n(s) for s in ("nota", "cosa", "Ada", "Cleo"))
        r.racconta("Prima", "test", [(tipo, [(ruolo, ada)])])
        fatto = storto(r, guasto)
        prima, giornale = stato(r), (c / "giornale").read_bytes()
        try:
            scrivi(r, "test", [("Non entra", *fatto)])
            rifiutato = False
        except (TypeError, ValueError):
            rifiutato = True
        # Un ingresso sbagliato si rifiuta: non lascia la rete fuori uso fino alla riapertura.
        assert r.fuori_uso is None and not r.memoria_guasta, f"la rete si è fermata: {r.fuori_uso}"
        if rifiutato:
            assert stato(r) == prima and (c / "giornale").read_bytes() == giornale
        else:  # lecito solo per la coppia che si consuma, se viene letta una volta sola e scritta com'è
            assert guasto == "coppia-che-si-consuma"
        assert scrivi(r, "test", [("Dopo", tipo, [(ruolo, cleo)])]) == 1  # e la rete serve ancora
        in_memoria = lingua.chiedi(r, DOMANDA)["righe"]
    with aperta(Rete(str(c), sola_lettura=True)) as riletta:
        assert lingua.chiedi(riletta, DOMANDA)["righe"] == in_memoria
