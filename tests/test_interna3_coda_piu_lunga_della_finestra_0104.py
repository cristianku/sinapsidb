"""Terzo giro di revisione interna della candidata 0.10.4, lente «giornale»: una coda a metà più lunga della finestra.

Il record di un evento può essere lungo fino a ``MASSIMO_RECORD`` (256 MB: è un limite dichiarato, e fin lì
``_scrivi_record`` lo scrive). ``Rete.giudica_la_coda`` però guarda al massimo ``FINESTRA_DI_CODA`` byte (64 MB) e,
se quello che resta dopo l'ultimo record buono è più lungo, decide che «è troppo lungo per essere una scrittura sola»
e solleva ``GiornaleGuasto``. Una scrittura sola può essere lunga quattro finestre. Un record legittimo tra 64 e
256 MB, rimasto a metà dopo più di 64 MB (un arresto brusco, o semplicemente chi scrive che non ha ancora finito
mentre un altro apre per leggere), è una coda scritta a metà come tutte le altre: il README promette che «una coda
scritta a metà da un arresto brusco si taglia come prima», e il contratto (paragrafo 9) che una coda a metà è «un
record incompleto in fondo». La candidata invece non apre più il giornale, né per leggere né per scrivere, e dice di
ripristinarlo da una copia: tutti gli eventi confermati prima restano chiusi dentro.

La revisione di prima lo apriva: leggeva fino in fondo e giudicava il resto senza un tetto. È una regressione.

La prova è in scala, come chiede la lente: le tre costanti divise per 65.536 (pezzo e finestra 1024 byte, record al
massimo 4096), un record legittimo di tremila byte e la sua scrittura fermata a metà. Con le costanti di serie lo
stesso file si apre: lo si controlla prima, è la prova che il giornale è buono.
"""

import pytest

from sinapsidb import motore
from sinapsidb.motore import GiornaleGuasto, Rete
from test_revisione_circuiti_090 import aperta

IN_SCALA = {"PEZZO_DI_GIORNALE": 1024, "FINESTRA_DI_CODA": 1024, "MASSIMO_RECORD": 4096}  # 64 MB, 64 MB e 256 MB, diviso 65.536


def un_evento_corto_e_uno_lungo(c):
    """Il giornale intero, e dove finisce il primo record."""
    with aperta(Rete(str(c), sync="os")) as r:
        tipo, ruolo, ada = r.n("nota"), r.n("cosa"), r.n("Ada")
        r.racconta("Corta", "test", [(tipo, [(ruolo, ada)])])
        fine_del_primo = (c / "giornale").stat().st_size
        r.racconta("Lunga " + "x" * 3000, "test", [(tipo, [(ruolo, ada)])])  # sta nel massimo: è un record legittimo
    return (c / "giornale").read_bytes(), fine_del_primo


@pytest.mark.parametrize("scritture", [True, False], ids=["chi-scrive", "chi-legge"])
@pytest.mark.parametrize("arrivati", [1025, 2000], ids=["un-byte-oltre-la-finestra", "due-finestre"])
def test_una_coda_a_meta_piu_lunga_della_finestra_resta_una_coda(tmp_path, monkeypatch, scritture, arrivati):
    c = tmp_path / "rete"
    intero, fine_del_primo = un_evento_corto_e_uno_lungo(c)
    assert len(intero) - fine_del_primo - 8 <= IN_SCALA["MASSIMO_RECORD"]  # il secondo record sta nel massimo, in scala
    a_meta = intero[:fine_del_primo + arrivati]  # la scrittura del secondo record si è fermata qui
    (c / "giornale").write_bytes(a_meta)
    with aperta(Rete(str(c), sola_lettura=True)) as di_serie:  # con le costanti di serie è una coda, e si apre
        assert di_serie.conteggi()["eventi"] == 1
    for nome, valore in IN_SCALA.items():
        monkeypatch.setattr(motore, nome, valore)
    try:
        r = Rete(str(c), sola_lettura=not scritture)
    except GiornaleGuasto as guasto:
        assert (c / "giornale").read_bytes() == a_meta
        pytest.fail(f"un record legittimo scritto a metà ({arrivati} byte arrivati, finestra di {motore.FINESTRA_DI_CODA}, "
                    f"massimo di {motore.MASSIMO_RECORD}) non è stato preso per una coda: {guasto}")
    with aperta(r):
        assert r.conteggi()["eventi"] == 1 and not r.fuori_uso
        # chi apre per scrivere taglia la coda, chi legge non tocca niente
        assert (c / "giornale").read_bytes() == (intero[:fine_del_primo] if scritture else a_meta)
        if scritture:
            assert r.racconta("Dopo", "test", [(r.n("nota"), [(r.n("cosa"), r.n("Ada"))])]) == 1
    with aperta(Rete(str(c), sola_lettura=True)) as dopo:
        assert dopo.conteggi()["eventi"] == (2 if scritture else 1)
