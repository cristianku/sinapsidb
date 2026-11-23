"""Terzo giro di revisione interna della candidata 0.10.4, lente «giornale»: il massimo di un record, scrivendo a lotti.

Il record di un evento è lungo al massimo ``MASSIMO_RECORD`` (256 MB): «oltre non si scrive, e non si crede a chi lo
dichiara». Chi rilegge ci conta: ``_rileggi_il_giornale`` davanti a una lunghezza più grande si ferma e la giudica un
guasto, e i camminatori (``_record``, ``prefisso``, ``com_era``, ``eventi_nel_giornale``) non la leggono nemmeno.

``_scrivi_record`` però il controllo lo fa dopo aver messo in fila i record scritti a lotti: quando ``sync`` è
«lotti» esce prima di arrivarci. Lo stesso evento che negli altri modi è rifiutato («questo evento va diviso») a
lotti viene confermato, numerato, e ``scarica`` lo mette nel giornale. Da lì in poi quel giornale la candidata non lo
apre più: «GiornaleGuasto ... è intero ... va ripristinato da una copia». Non c'è una copia da cui ripristinare un
giornale che il motore stesso ha appena scritto: l'evento confermato, e tutti quelli confermati dopo di lui, si
perdono, e con loro l'accesso a quelli di prima finché qualcuno non taglia il file a mano.

La revisione di prima non aveva il massimo: scriveva quel record e lo rileggeva. È una regressione, sul
percorso dei lotti, che è quello dell'importazione.

La prova è in scala, come per gli altri limiti: le tre costanti divise per 65.536 (pezzo e finestra 1024 byte, record
al massimo 4096) e un evento di cinquemila byte.
"""

import pytest

from sinapsidb import motore
from sinapsidb.motore import GiornaleGuasto, Rete
from test_revisione_circuiti_090 import aperta

IN_SCALA = {"PEZZO_DI_GIORNALE": 1024, "FINESTRA_DI_CODA": 1024, "MASSIMO_RECORD": 4096}  # 64 MB, 64 MB e 256 MB, diviso 65.536
LUNGA = "Una frase troppo lunga " + "x" * 5000


@pytest.mark.parametrize("dopo", [False, True], ids=["ultimo-evento", "seguito-da-un-altro"])
@pytest.mark.parametrize("via", ["uno", "molti", "aperta-a-lotti"])
def test_un_evento_oltre_il_massimo_non_si_scrive_nemmeno_a_lotti(tmp_path, monkeypatch, via, dopo):
    c = tmp_path / "rete"
    for nome, valore in IN_SCALA.items():
        monkeypatch.setattr(motore, nome, valore)
    with aperta(Rete(str(c), sync="os")) as r:
        tipo, ruolo, ada = r.n("nota"), r.n("cosa"), r.n("Ada")
        assert r.racconta("Prima", "test", [(tipo, [(ruolo, ada)])]) == 0
        with pytest.raises(ValueError, match="al massimo"):  # fuori dai lotti il limite c'è, e la rete resta in uso
            r.racconta(LUNGA, "test", [(tipo, [(ruolo, ada)])])
        assert r.conteggi()["eventi"] == 1 and not r.fuori_uso
    rifiutato = False
    with aperta(Rete(str(c), sync="lotti" if via == "aperta-a-lotti" else "os")) as r:
        tipo, ruolo, ada = r.n("nota"), r.n("cosa"), r.n("Ada")
        try:
            if via == "aperta-a-lotti":
                r.racconta(LUNGA, "test", [(tipo, [(ruolo, ada)])])
            else:
                with r.a_lotti():
                    if via == "uno":
                        r.racconta(LUNGA, "test", [(tipo, [(ruolo, ada)])])
                    else:
                        r.racconta_molti("test", [(LUNGA, tipo, [(ruolo, ada)])])
        except ValueError as limite:
            rifiutato = "al massimo" in str(limite)
        if dopo:
            r.racconta("Dopo", "test", [(tipo, [(ruolo, ada)])])
        confermati, ferma = r.conteggi()["eventi"], r.fuori_uso
    assert not ferma
    # Quello che la rete ha confermato è quello che si rilegge: dal giornale, da chi apre e da chi ci cammina sopra.
    try:
        with aperta(Rete(str(c), sola_lettura=True)) as riaperta:
            riletti = riaperta.conteggi()["eventi"]
    except GiornaleGuasto as guasto:
        pytest.fail(f"a lotti l'evento oltre il massimo {'è stato rifiutato' if rifiutato else 'è stato confermato'}: la rete aveva "
                    f"{confermati} eventi confermati, e il giornale che ha scritto lei non si apre più: {guasto}")
    assert riletti == confermati == sum(1 for _ in Rete._record(str(c)))
    assert rifiutato and confermati == (2 if dopo else 1)  # e l'evento troppo lungo non c'è: va diviso, come negli altri modi
