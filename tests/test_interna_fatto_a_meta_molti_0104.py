"""Sulla candidata 0.10.4: un fatto che non entra nelle colonne del motore non resta a metà nella rete.

``Rete._fatto`` tocca le colonne prima di sapere se il fatto ci sta. Se solleva a metà, il fatto non è ancora tra
quelli che ``racconta`` e ``racconta_molti`` sanno annullare: resta nella rete, che non si ferma e continua a
scrivere con numeri di fatto che il giornale non ha.
"""

import pytest

from sinapsidb import lingua
from sinapsidb.motore import Rete
from test_revisione_circuiti_090 import aperta
from test_velocita import _uno_alla_volta


VIE = (("uno", _uno_alla_volta), ("molti", Rete.racconta_molti))
COLONNE = ("f_tipo", "f_inizio", "f_n", "f_nato", "f_morto", "s_ruolo", "s_a")
DOMANDA = "in nota leggi frase, cosa"


def che_non_entra(r, guasto):
    """Un fatto che il motore non sa tenere: lo deve rifiutare intero, in mezzo ai molti come da solo."""
    tipo, ruolo, bea = (r.n(s) for s in ("nota", "cosa", "Bea"))
    if guasto == "128-legami":  # la colonna che conta le sinapsi di un fatto arriva a 127
        return tipo, [(ruolo, bea)] * 128
    if guasto == "partecipante-mancante":  # None dove va il numero di un neurone
        return tipo, [(ruolo, bea), (ruolo, None)]
    if guasto == "numero-fuori-colonna":  # un numero che non sta in una colonna di interi a 32 bit
        return tipo, [(ruolo, bea), (ruolo, 2 ** 31)]
    return tipo, [(ruolo, bea), (ruolo,)]  # un legame che non è una coppia


def stato(r):
    """Quanti fatti e quante sinapsi, e quanto è lunga ogni colonna: devono restare allineate tra loro."""
    return r.conteggi(), {k: len(getattr(r, k)) for k in COLONNE}


@pytest.mark.parametrize("guasto", ["128-legami", "partecipante-mancante", "numero-fuori-colonna", "legame-non-coppia"])
@pytest.mark.parametrize("via,scrivi", VIE, ids=[v for v, _ in VIE])
def test_un_fatto_che_non_entra_nelle_colonne_non_lascia_niente_nella_rete(tmp_path, via, scrivi, guasto):
    c = tmp_path / "rete"
    with aperta(Rete(str(c))) as r:
        tipo, ruolo, ada = (r.n(s) for s in ("nota", "cosa", "Ada"))
        r.racconta("Prima", "test", [(tipo, [(ruolo, ada)])])
        storto = che_non_entra(r, guasto)
        prima, giornale = stato(r), (c / "giornale").read_bytes()
        with pytest.raises((OverflowError, TypeError, ValueError)):
            scrivi(r, "test", [("Non entra", *storto)])
        assert (c / "giornale").read_bytes() == giornale
        # Lecito fermarsi fino alla riapertura. Non lecito restare in servizio con un pezzo di fatto nelle colonne.
        assert r.fuori_uso or stato(r) == prima


@pytest.mark.parametrize("via,scrivi", VIE, ids=[v for v, _ in VIE])
def test_dopo_un_fatto_rimasto_a_meta_il_giornale_che_si_scrive_si_riapre_ancora(tmp_path, via, scrivi):
    c = tmp_path / "rete"
    with aperta(Rete(str(c))) as r:
        tipo, ruolo, ada, cleo = (r.n(s) for s in ("nota", "cosa", "Ada", "Cleo"))
        r.racconta("Prima", "test", [(tipo, [(ruolo, ada)])])
        with pytest.raises((TypeError, ValueError)):  # il rifiuto pulito di oggi è un ValueError, prima di toccare la rete
            scrivi(r, "test", [("Non entra", *che_non_entra(r, "partecipante-mancante"))])
        if r.fuori_uso:
            return  # si è fermata: chi la tiene aperta la riapre dal giornale
        assert scrivi(r, "test", [("Seconda", tipo, [(ruolo, cleo)])]) == 1
        seconda, = r.scatta(tipo, [(ruolo, cleo)])  # il numero del fatto, come lo dà la rete a chi lo chiede
        r.racconta("Ritiro la seconda", "test", ritira=[seconda])
        in_memoria = lingua.chiedi(r, DOMANDA)["righe"]
    try:
        riletta = Rete(str(c))
    except Exception as e:
        pytest.fail(f"il giornale scritto da questa rete non si riapre più: {type(e).__name__}: {e}")
    with aperta(riletta):
        assert lingua.chiedi(riletta, DOMANDA)["righe"] == in_memoria == [("Prima", "Ada")]
