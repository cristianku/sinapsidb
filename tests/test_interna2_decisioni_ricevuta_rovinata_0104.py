"""Secondo giro interno sulla candidata 0.10.4: una ricevuta rovinata fa ancora cadere l'elenco delle decisioni.

La correzione del primo giro (``tests/test_interna_ricevuta_guasta_0104.py``) ha protetto ``verifica`` e la ricerca
delle candidate: una decisione senza le regole consultate, o con un testo al posto della decisione, è un «guasto»
segnalato e un evento «senza letture». ``Memoria.decisioni`` chiama ancora ``_letture`` senza protezione: con le
stesse due manomissioni solleva KeyError e AttributeError, e con lei ``quadro`` del fido, cioè la pagina «Decisioni»:
per una ricevuta rovinata non si vede più nessuna decisione, e l'errore dice «alla richiesta manca 'regole'».

Contratto, paragrafo 8: «ogni differenza è un guasto segnalato». AGENTS.md, revisione interna: il portale non cade
per una memoria che ha un guasto.
"""

import pytest

from sinapsidb.fido import Fido
from sinapsidb.memoria import Memoria
from test_decisioni import _record, _riscrivi
from test_revisione_circuiti_090 import aperta
from test_segno_e_disco import fido, movimento

MANOMISSIONI = [
    lambda ricevuta: ricevuta["decisione"].pop("regole"),  # il no non dice più che cosa ha letto
    lambda ricevuta: ricevuta.__setitem__("decisione", "x"),  # al posto della decisione c'è un testo
]
NOMI = ["decisione-senza-regole", "decisione-testo"]


@pytest.mark.parametrize("cambia", MANOMISSIONI, ids=NOMI)
def test_una_ricevuta_rovinata_non_fa_cadere_l_elenco_delle_decisioni(tmp_path, cambia):
    c = tmp_path / "rete"
    with aperta(Memoria(c, scritture=True)) as m:
        fido(m)  # eventi 0 e 1: il circuito e la regola
        assert movimento(m, "accredito", "Rossi", "100", "a1")["esito"] == "applicato"  # evento 2
        assert movimento(m, "ordine", "Rossi", "150", "o1")["esito"] == "violazione_regola"  # evento 3
        assert movimento(m, "ordine", "Rossi", "60", "o2")["esito"] == "applicato"  # evento 4
    record = _record(c)
    cambia(record[3][7]["ricevuta"])
    _riscrivi(c, record)
    with aperta(Memoria(c)) as m:
        assert m.verifica(3)["esito"] == "guasto"  # la verifica lo sa dire
        assert m.candidate(4)["copertura"]["completa"] is False  # la ricerca delle candidate anche
        righe = m.decisioni()  # KeyError 'regole' / AttributeError: 'str' object has no attribute 'get'
        assert {2, 4} <= {r["evento"] for r in righe}  # le altre decisioni si vedono ancora


@pytest.mark.parametrize("cambia", MANOMISSIONI, ids=NOMI)
def test_il_quadro_del_fido_non_cade_per_una_ricevuta_rovinata(tmp_path, cambia):
    c = tmp_path / "rete"
    f = Fido(c)
    try:
        assert f.esegui({"op": "accorda", "cliente": "Rossi", "importo": "800", "chiave": "a1"})["esito"] == "applicato"
        no = f.esegui({"op": "ordine", "cliente": "Rossi", "importo": "1000", "chiave": "o1"})
        assert no["esito"] == "violazione_regola"
    finally:
        f.chiudi()
    record = _record(c)
    cambia(record[no["evento"]][7]["ricevuta"])
    _riscrivi(c, record)
    f = Fido(c)
    try:
        assert f.esegui({"op": "verifica", "evento": no["evento"]})["esito"] == "guasto"
        quadro = f.esegui({"op": "quadro"})
        assert quadro["ok"], quadro  # {'ok': False, 'tipo': 'richiesta', 'errore': "alla richiesta manca 'regole'"}
        assert quadro["clienti"] == [{"nome": "Rossi", "id": quadro["clienti"][0]["id"], "fido": "800"}]
    finally:
        f.chiudi()
