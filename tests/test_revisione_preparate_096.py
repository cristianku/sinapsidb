"""Una domanda ordinaria, come la lettura di un circuito, è preparata per un'identità, non per una coppia ID/nome."""

import pytest

from sinapsidb import lingua
from sinapsidb.memoria import Memoria
from test_revisione_circuiti_090 import aperta, circuito


@pytest.mark.parametrize("frase", ["accendi cliente = Ada in vendita leggi totale", "circuito ricavi di Ada"])
def test_una_nuova_ada_con_stesso_nome_e_numero_non_e_la_ada_dimenticata(tmp_path, frase):
    with aperta(Memoria(tmp_path / "rete", scritture=True)) as m:
        r, n = m.rete, m.rete.n
        circuito(r, "ricavi", per=None, lega="cliente")
        r.racconta("Vendita di Zoe", "test", [(n("vendita"), [(n("cliente"), n("Zoe")), (n("totale"), n("1"))])])
        segno = len(r.nomi)
        ada = n("Ada")
        p = lingua.prepara(r, frase)
        r.dimentica(segno)
        assert not r.trova("Ada")
        altra = r.nuovo("Ada")  # esplicitamente nuova, nessun evento conosceva quella di prima
        assert altra == ada
        r.racconta("Vendita alla nuova Ada", "test", [(n("vendita"), [(n("cliente"), altra), (n("totale"), n("70"))])])
        with pytest.raises(lingua.ErroreDomanda):
            p.esegui()
        assert m.chiedi(frase)["righe"] in ([("70",)], [("Ada", "70")])


@pytest.mark.parametrize("indizio", ["nome", "ID", "stabile"], ids=["nome-con-omonima", "id-con-nome-letterale", "senza-omonimi"])
def test_ricompilare_il_piano_non_risolve_di_nuovo_un_valore_gia_legato(tmp_path, indizio):
    with aperta(Memoria(tmp_path / "rete", scritture=True)) as m:
        r, n = m.rete, m.rete.n
        t, cl, tot, a = (n(s) for s in ("vendita", "cliente", "totale", "Ada"))
        r.racconta("Prima Ada", "test", [(t, [(cl, a), (tot, n("5"))])])
        letterale = f"neurone:{a}" if indizio == "ID" else "Ada"
        q = lingua.prepara(r, f"accendi cliente = {letterale} in vendita leggi totale")
        assert q.esegui() == [("5",)]
        altra = r.nuovo("Bea" if indizio == "stabile" else letterale)
        # Il ruolo in più forza la ricompilazione; il destinatario di prima esiste ancora ed è confermato.
        r.racconta("Un'altra vendita", "test", [(t, [(cl, altra), (tot, n("90")), (n("documento"), n("F2"))])])
        assert r.nomi[a] == "Ada" and a != altra
        assert q.esegui() == [("5",)]
