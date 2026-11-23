"""Revisione della candidata 0.10.4: una prova con un ruolo in più lascia il segno nella forma del tipo.

``Rete._fatto`` annota in ``forma`` che i fatti di un tipo non hanno più tutti gli stessi ruoli; ``_annulla`` toglie il
fatto provato ma non rimette la forma com'era. Dopo una prova (o un no) la rete in memoria crede che le vendite abbiano
forme diverse: il nucleo si fa da parte per sempre e il Python passa i fatti in un altro ordine. Chi riapre dal giornale,
dove la prova non c'è, ritrova la forma di prima: stessa rete, stessa domanda, righe in un altro ordine.
"""

import pytest

from sinapsidb import lingua, nucleo
from sinapsidb.motore import Rete
from test_nucleo import libreria
from test_revisione_circuiti_090 import aperta

DOMANDA = "in vendita conta per cliente"


@pytest.fixture
def modo(libreria, monkeypatch):
    def scegli(compilato):
        monkeypatch.setenv("SINAPSIDB_NUCLEO", str(libreria) if compilato else "0")
        nucleo.ricarica()
        assert nucleo.attivo() is compilato
    yield scegli
    nucleo.ricarica()


def chiedi(r):
    p = lingua.prepara(r, DOMANDA)
    return p.esegui(), p.nucleo


@pytest.mark.parametrize("compilato", [False, True], ids=["python", "nucleo-c"])
def test_una_prova_con_un_ruolo_in_piu_non_cambia_le_risposte_di_dopo(tmp_path, modo, compilato):
    modo(compilato)
    with aperta(Rete(str(tmp_path / "rete"))) as r:
        n = r.n
        vendita = lambda chi, quanto, *altri: [(n("vendita"), [(n("cliente"), n(chi)), (n("totale"), n(quanto)), *altri])]
        for chi, quanto in (("A", "1"), ("B", "2"), ("A", "3")):
            r.racconta("Vendita", "test", vendita(chi, quanto))
        r.racconta("Storno della prima", "test", ritira=[0])
        prima = chiedi(r)
        assert prima == ([("A", "1"), ("B", "1")], compilato)
        segno = len(r.nomi)
        esito = r.racconta("E con una nota?", "test", vendita("A", "1", (n("nota"), n("urgente"))), prova=True, neuroni_da=segno)
        assert esito["esito"] == "prova" and len(r.nomi) == segno and len(r.f_tipo) == 3  # niente fatti, niente neuroni
        dopo = chiedi(r)
    with aperta(Rete(str(tmp_path / "rete"))) as riaperta:
        dal_giornale = chiedi(riaperta)
    assert dal_giornale == prima  # nel giornale la prova non c'è
    assert dopo == dal_giornale  # e nemmeno nella memoria che l'ha fatta: stesse righe, stesso ordine, stesso percorso
