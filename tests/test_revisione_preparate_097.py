"""Anche a parità di numero di neuroni e di schema, un nome assente può nascere e un importo può cambiare identità."""

import pytest

from sinapsidb import lingua
from sinapsidb.memoria import Memoria
from test_revisione_circuiti_090 import aperta, circuito, vendita
from test_segno_e_disco import guasta


def test_il_nome_prima_assente_nasce_senza_cambiare_la_lunghezza_della_rete(tmp_path):
    with aperta(Memoria(tmp_path / "rete", scritture=True)) as m:
        r = m.rete
        vendita(m, "2027-01-01", "5")
        posto = r.n("nome provvisorio")
        frase = "accendi cliente = Zed in vendita leggi totale"
        preparata = lingua.prepara(r, frase)
        assert preparata.esegui() == []
        r.dimentica(posto)
        assert r.n("Zed") == posto
        m.racconta("Vendita a Zed", [{"tipo": "vendita", "legami": {"cliente": "Zed", "quando": "2027-01-01", "totale": "5"}}])
        assert preparata.esegui() == m.chiedi(frase)["righe"] == [("5",)]


def test_dopo_il_rollback_somme_e_filtri_preparati_non_usano_il_numero_dimenticato(tmp_path, monkeypatch):
    with aperta(Memoria(tmp_path / "rete", scritture=True)) as m:
        r, n = m.rete, m.rete.n
        circuito(r)
        vendita(m, "2027-01-01", "10")
        frasi = ["accendi cliente = C in vendita somma totale", "accendi cliente = C in vendita dove totale > 50 leggi totale"]
        pronte = [lingua.prepara(r, frase) for frase in frasi]
        assert [p.esegui() for p in pronte] == [[("10",)], []]
        posto = n("100")

        def nota(decisione):
            for p in pronte:
                p.esegui()  # legge il 100 provvisorio; non conta se una lettura vede la proposta o soltanto la base
            return {"ricevuta": {"evento": "evento:2", "esito": "applicato", "decisione": decisione}}

        fatti = [(n("vendita"), [(n("cliente"), n("C")), (n("quando"), n("2027-01-01")), (n("totale"), posto)])]
        with monkeypatch.context() as mp:
            guasta(mp, r, scrivi=9)
            with pytest.raises(OSError):
                r.racconta("Vendita fallita", "test", fatti, neuroni_da=posto, nota_della_scrittura=nota)
        assert not r.trova("100")
        vendita(m, "2027-01-01", "2")
        assert r.trova("2") == [posto]
        fresche = [m.chiedi(s)["righe"] for s in frasi]
        assert fresche == [[("12",)], []] and m.chiedi("circuito vendite mensili")["righe"] == [("2027-01", "12")]
        assert [p.esegui() for p in pronte] == fresche
