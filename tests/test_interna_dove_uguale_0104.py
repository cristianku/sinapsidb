"""«dove ruolo = nome» tiene i fatti che hanno quel neurone in quel ruolo: non quelli che il ruolo non ce l'hanno.

Se il nome non indica nessun neurone la lingua lo tiene come -1, e ``Rete.leggi`` risponde -1 per un fatto che non ha
il ruolo: -1 == -1, e il fatto passa la condizione. Per i confronti numerici la stessa cosa è già stata chiusa («un ruolo
che manca non è un numero»); per l'uguaglianza no. Così la risposta dipende dal fatto che nella rete esista, da qualche
parte, un neurone con quel nome, e la stessa richiesta scritta con due indizi (``Rete.scatta``) dà un'altra risposta.
"""

import pytest

from sinapsidb import lingua
from sinapsidb.memoria import Memoria
from test_revisione_circuiti_090 import aperta, evento

CON_DOVE = "accendi cliente = C in ordine dove stato = sospeso leggi importo"
CON_DUE_INDIZI = "accendi cliente = C e stato = sospeso in ordine leggi importo"


def ordini(m):
    m.racconta("Ordine con stato", [{"tipo": "ordine", "legami": {"cliente": "C", "importo": "5", "stato": "aperto"}}])
    return evento(m.racconta("Ordine senza stato", [{"tipo": "ordine", "legami": {"cliente": "C", "importo": "7"}}]))


@pytest.mark.parametrize("frase", [CON_DOVE, "in ordine dove cliente = C e stato = sospeso leggi importo"])
def test_nessun_ordine_e_sospeso_e_la_condizione_non_ne_trova(tmp_path, frase):
    with aperta(Memoria(tmp_path / "rete", scritture=True)) as m:
        ordini(m)
        assert not m.rete.trova("sospeso")
        assert m.chiedi(CON_DUE_INDIZI)["righe"] == []  # il percorso di riferimento: i fatti che ricevono tutti gli indizi
        assert m.chiedi(frase)["righe"] == []


def test_con_il_segnaposto_la_risposta_e_la_stessa_che_con_i_due_indizi(tmp_path):
    with aperta(Memoria(tmp_path / "rete", scritture=True)) as m:
        ordini(m)
        preparata = lingua.prepara(m.rete, "accendi cliente = C in ordine dove stato = ? leggi importo")
        assert preparata.esegui("aperto") == [("5",)]
        assert preparata.esegui("sospeso") == lingua.prepara(m.rete, "accendi cliente = C e stato = ? in ordine leggi importo").esegui("sospeso") == []


def test_la_risposta_non_dipende_da_un_neurone_che_nasce_altrove(tmp_path):
    with aperta(Memoria(tmp_path / "rete", scritture=True)) as m:
        ordini(m)
        prima = m.chiedi(CON_DOVE)["righe"]
        # Un fatto che con gli ordini non c'entra fa nascere un neurone che si chiama «sospeso»: gli ordini sono gli stessi.
        m.racconta("Un servizio sospeso", [{"tipo": "servizio", "legami": {"nome": "posta", "condizione": "sospeso"}}])
        assert m.chiedi(CON_DOVE)["righe"] == []
        assert prima == []  # gli stessi ordini, la stessa domanda: la stessa risposta


def test_nel_passato_un_ordine_senza_stato_non_risulta_sospeso(tmp_path):
    with aperta(Memoria(tmp_path / "rete", scritture=True)) as m:
        ultimo = ordini(m)
        m.racconta("Ordine sospeso", [{"tipo": "ordine", "legami": {"cliente": "C", "importo": "9", "stato": "sospeso"}}])
        assert m.chiedi(CON_DOVE)["righe"] == [("9",)]
        # A quell'evento il giornale ha due ordini di C: uno aperto e uno senza stato. Nessuno dei due era sospeso.
        assert m.chiedi(f"{CON_DOVE} come risultava all'evento {ultimo}")["righe"] == []
