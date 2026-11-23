"""Una preparazione ripetuta che fallisce a metà non deve lasciare alla domanda il numero di un neurone senza la presa.

La correzione della 0.10.4 invalida il piano quando ``Domanda._prepara`` fallisce (``_valida = (-1, [])``). Ma prima di
fallire ``_prepara`` ha già messo in ``_legati`` i nomi che ha trovato, con la loro presa, e ``_provvisori``, l'elenco
che ``esegui`` controlla con ``Rete.tiene``, si rifà solo in fondo a una preparazione riuscita. Così un neurone trovato
in una preparazione fallita resta legato alla domanda senza che nessuno guardi se è ancora lui: se viene dimenticato e il
suo numero tocca a un altro nome, alla chiamata dopo la domanda si prepara di nuovo con quel numero e risponde con i
fatti dell'altro. Solo la volta successiva dice «non c'è più».

Il contratto (paragrafo 9): se il neurone per cui la domanda era preparata è stato dimenticato, chi esegue lo vede e
dice che la domanda va preparata di nuovo, anche se nel frattempo è nato un altro con lo stesso numero.
"""

import pytest

from sinapsidb import lingua
from sinapsidb.motore import Rete
from test_revisione_circuiti_090 import aperta

DOMANDA = "accendi cliente = Ada e canale = web in vendita leggi totale"


def test_dopo_una_preparazione_fallita_la_domanda_non_risponde_per_chi_ha_preso_il_numero_di_un_dimenticato(tmp_path):
    with aperta(Rete(str(tmp_path / "rete"))) as r:
        n = r.n
        vendita = lambda chi, totale, canale: [(n("vendita"), [(n("cliente"), chi), (n("totale"), n(totale)), (n("canale"), canale)])]
        r.racconta("Vendita a Zoe", "test", vendita(n("Zoe"), "1", n("negozio")))
        p = lingua.prepara(r, DOMANDA)
        assert p.esegui() == []  # né Ada né «web» ci sono ancora
        segno = len(r.nomi)
        ada = n("Ada")  # nominata per una scrittura che poi non avviene: non è nel giornale
        r.nuovo("web"), r.nuovo("web")  # e due canali con lo stesso nome
        with pytest.raises(lingua.ErroreDomanda, match="indica 2 cose"):
            p.esegui()  # la domanda si prepara di nuovo, trova Ada, e si ferma su «web»
        r.dimentica(segno)  # la scrittura non è avvenuta: quei nomi non restano
        bea = n("Bea")
        assert bea == ada  # il numero di Ada tocca a Bea
        r.racconta("Vendita a Bea", "test", vendita(bea, "90", n("web")))
        assert not r.trova("Ada")
        assert lingua.prepara(r, DOMANDA).esegui() == []  # una domanda fresca: Ada non ha vendite
        try:
            righe = p.esegui()
        except lingua.ErroreDomanda:
            return  # «non c'è più: la domanda va preparata di nuovo» va bene
        assert righe == [], "la vendita di Bea è stata data come vendita di Ada"
