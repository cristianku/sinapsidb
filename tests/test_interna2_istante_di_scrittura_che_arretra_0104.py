"""Secondo giro, sulla candidata 0.10.4: un istante di scrittura che arretra resta in memoria e non nel giornale.

``Rete.racconta`` adesso controlla gli istanti prima di toccare la rete (devono essere numeri finiti), ma accetta un
``scritto`` che viene prima dell'ultimo evento scritto. Il motore dice che l'istante di scrittura «non torna mai
indietro» (``e_scritto``), e il giornale lo dà per scontato: quando ``scritto`` è uguale a ``quando`` il record non lo
porta, e chi rilegge mette ``max(evento di prima, quando)``. Così la rete viva tiene l'istante passato da chi chiama,
e chi riapre dal giornale ne trova un altro.

Non è solo un numero: un fatto che non dichiara un periodo vale dall'istante di scrittura, quindi la somma di un
circuito letta a un istante cambia tra prima e dopo la riapertura. Chiamare ``racconta(quando=t, scritto=t)`` è il
modo che usano i test del repository (test_velocita, test_revisione_lotti_0100, test_revisione_nucleo_0100).

Regola violata: quello che la rete ha in memoria è quello che si rilegge dal giornale (eventi e somme dei circuiti);
«quello che può fallire a metà si controlla prima»: un istante che il giornale non sa riportare va rifiutato, o
scritto nel record.
"""

from sinapsidb.motore import Rete
from test_revisione_circuiti_090 import aperta


def test_l_istante_di_scrittura_in_memoria_e_quello_che_si_rilegge_dal_giornale(tmp_path):
    c = tmp_path / "rete"
    with aperta(Rete(str(c))) as r:
        n = r.n
        r.proponi_circuito("Ricavi", "test", "ricavi", [{"osserva": n("vendita"), "lega": n("cliente"), "somma": n("totale"), "peso": 1}])
        ricavi, ada, t0 = n("ricavi"), n("Ada"), r.e_scritto[-1]
        r.racconta("Dieci", "test", [(n("vendita"), [(n("cliente"), ada), (n("totale"), n("10"))])], quando=t0 + 100, scritto=t0 + 100)
        try:  # arrivata e scritta «prima» dell'evento precedente: o si rifiuta, o resta com'è anche nel giornale
            r.racconta("Venti", "test", [(n("vendita"), [(n("cliente"), ada), (n("totale"), n("20"))])], quando=t0 + 50, scritto=t0 + 50)
        except ValueError:
            pass
        assert not r.fuori_uso
        scritti = [x - t0 for x in r.e_scritto]
        a_meta_strada = r.valore(ricavi, ada, al=t0 + 75)
    with aperta(Rete(str(c), sola_lettura=True)) as riletta:
        assert ([x - t0 for x in riletta.e_scritto], riletta.valore(ricavi, ada, al=t0 + 75)) == (scritti, a_meta_strada)
