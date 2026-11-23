"""Secondo giro interno sulla 0.10.4: i controlli fatti prima di toccare la rete, tenuti fermi da una prova.

La candidata controlla un fatto prima di toccare la rete: i legami sono al massimo 127, il tipo e ogni legame sono il
numero di un neurone che c'è, gli istanti sono numeri finiti, una rete chiusa non scrive. Le prove che c'erano
accettavano anche che la rete si fermasse («lecito fermarsi»), e non provavano mai un numero che sta in una colonna
ma non è un neurone (-1, uno oltre l'ultimo): tolti uno alla volta, questi controlli non facevano diventare rossa
nessuna prova. Senza, un fatto legato a un neurone che non c'è viene scritto e confermato, un evento porta un istante
che non è un numero, una rete chiusa conferma scritture che il giornale non ha, e un fatto troppo largo ferma la rete
invece di essere rifiutato.

La regola (AGENTS.md, «Quello che può fallire a metà si controlla prima»): un ingresso che non sta in piedi si
rifiuta e basta; la rete resta com'era e continua a servire, per le due strade.
"""

import math

import pytest

from sinapsidb import motore
from sinapsidb.motore import Rete
from test_revisione_circuiti_090 import aperta
from test_velocita import _uno_alla_volta

VIE = (("uno", _uno_alla_volta), ("molti", Rete.racconta_molti))
COLONNE = ("f_tipo", "f_inizio", "f_n", "f_nato", "f_morto", "s_ruolo", "s_a")


def stato(r):
    return r.conteggi(), {k: len(getattr(r, k)) for k in COLONNE}, list(r.nomi)


def fatti(r):
    return [r.in_parole(f) for f in range(len(r.f_tipo))]


def che_non_sta_in_piedi(r, guasto):
    """Un fatto da rifiutare: (tipo, legami). I neuroni che nomina nascono prima, come fa chi chiama."""
    tipo, ruolo, bea = (r.n(s) for s in ("nota", "cosa", "Bea"))
    quanti = len(r.nomi)
    return {
        "128-legami": (tipo, [(ruolo, bea)] * 128),
        "legame-di-un-pezzo": (tipo, [(ruolo, bea), (ruolo,)]),
        "neurone--1": (tipo, [(ruolo, -1)]),  # in una lista -1 è l'ultimo: si leggerebbe come un altro neurone
        "neurone-oltre-l-ultimo": (tipo, [(ruolo, quanti)]),
        "neurone-un-milione": (tipo, [(ruolo, 10 ** 6)]),
        "ruolo--1": (tipo, [(-1, bea)]),
        "ruolo-oltre-l-ultimo": (tipo, [(quanti, bea)]),
        "tipo--1": (-1, [(ruolo, bea)]),
        "tipo-oltre-l-ultimo": (quanti, [(ruolo, bea)]),
    }[guasto]


@pytest.mark.parametrize("guasto", ["128-legami", "legame-di-un-pezzo", "neurone--1", "neurone-oltre-l-ultimo", "neurone-un-milione",
                                    "ruolo--1", "ruolo-oltre-l-ultimo", "tipo--1", "tipo-oltre-l-ultimo"])
@pytest.mark.parametrize("via,scrivi", VIE, ids=[v for v, _ in VIE])
def test_un_fatto_che_non_sta_in_piedi_si_rifiuta_e_la_rete_resta_in_servizio(tmp_path, via, scrivi, guasto):
    c = tmp_path / "rete"
    with aperta(Rete(str(c))) as r:
        tipo, ruolo, ada, dora = (r.n(s) for s in ("nota", "cosa", "Ada", "Dora"))
        assert r.racconta("Prima", "test", [(tipo, [(ruolo, ada)])]) == 0
        storto = che_non_sta_in_piedi(r, guasto)
        prima, giornale = stato(r), (c / "giornale").read_bytes()
        with pytest.raises(ValueError):  # un rifiuto, non un trabocco delle colonne e non una scrittura
            scrivi(r, "test", [("Storto", *storto)])
        assert (c / "giornale").read_bytes() == giornale
        assert r.fuori_uso is None and not r.memoria_guasta  # non è un guasto: non c'è niente da riaprire
        assert stato(r) == prima
        assert r.racconta("Dopo", "test", [(tipo, [(ruolo, dora)])]) == 1
        in_memoria = fatti(r)
    with aperta(Rete(str(c), sola_lettura=True)) as riletta:
        assert fatti(riletta) == in_memoria == ["nota (cosa: Ada)", "nota (cosa: Dora)"]


@pytest.mark.parametrize("istanti", [dict(quando=math.nan), dict(quando=math.inf), dict(quando=-math.inf),
                                     dict(quando=2.0, scritto=math.inf), dict(quando=2.0, scritto=math.nan)],
                         ids=["arrivo-nan", "arrivo-inf", "arrivo-meno-inf", "scrittura-inf", "scrittura-nan"])
def test_un_istante_che_non_e_un_numero_finito_non_entra_nel_giornale(tmp_path, istanti):
    c = tmp_path / "rete"
    with aperta(Rete(str(c))) as r:
        tipo, ruolo, ada = (r.n(s) for s in ("nota", "cosa", "Ada"))
        r.racconta("Prima", "test", [(tipo, [(ruolo, ada)])], quando=1.0, scritto=1.0)
        prima, giornale = stato(r), (c / "giornale").read_bytes()
        with pytest.raises(ValueError, match="finito"):
            r.racconta("Seconda", "test", [(tipo, [(ruolo, ada)])], **istanti)
        assert (c / "giornale").read_bytes() == giornale
        assert r.fuori_uso is None and stato(r) == prima
        assert all(math.isfinite(x) for x in (*r.e_quando, *r.e_scritto))


def test_i_molti_non_scrivono_un_istante_che_l_orologio_non_sa_dare(tmp_path, monkeypatch):
    c = tmp_path / "rete"
    with aperta(Rete(str(c))) as r:
        tipo, ruolo, ada = (r.n(s) for s in ("nota", "cosa", "Ada"))
        r.racconta("Prima", "test", [(tipo, [(ruolo, ada)])])
        prima, giornale = stato(r), (c / "giornale").read_bytes()
        monkeypatch.setattr(motore.time, "time", lambda: math.inf)
        with pytest.raises(ValueError, match="finito"):
            r.racconta_molti("test", [("Seconda", tipo, [(ruolo, ada)])])
        monkeypatch.undo()
        assert (c / "giornale").read_bytes() == giornale
        assert r.fuori_uso is None and stato(r) == prima
    with aperta(Rete(str(c))) as riaperta:  # e il giornale si riapre: dentro non c'è un record che non si rilegge
        assert fatti(riaperta) == ["nota (cosa: Ada)"]


@pytest.mark.parametrize("via,scrivi", VIE, ids=[v for v, _ in VIE])
def test_una_rete_chiusa_non_conferma_niente_nemmeno_dai_molti(tmp_path, via, scrivi):
    c = tmp_path / "rete"
    r = Rete(str(c))
    tipo, ruolo, ada, bea = (r.n(s) for s in ("nota", "cosa", "Ada", "Bea"))
    r.racconta("Prima", "test", [(tipo, [(ruolo, ada)])])
    r.chiudi()
    prima, giornale = stato(r), (c / "giornale").read_bytes()
    with pytest.raises(RuntimeError, match="chiusa"):
        scrivi(r, "test", [("Seconda", tipo, [(ruolo, bea)])])
    assert stato(r) == prima  # non ha confermato una scrittura che il giornale non ha
    assert (c / "giornale").read_bytes() == giornale
    with aperta(Rete(str(c), sola_lettura=True)) as riletta:
        assert fatti(riletta) == fatti(r) == ["nota (cosa: Ada)"]
