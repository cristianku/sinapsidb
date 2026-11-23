"""Una lettura senza clausola lascia nella rete linee che prima non c'erano.

``Memoria.chiedi`` mette da parte con ``Rete._ritira`` i fatti che adesso non valgono e poi li rimette con
``Rete._annulla``. ``_ritira`` fa nascere la linea dei ritirati (``morti``) se non c'è; ``_annulla`` toglie le linee
nate vuote solo se qualcuno le ha segnate in ``_linee_nate``, e fuori da una scrittura nessuno le segna. Restano, vuote.
Da lì in poi le linee dei ritirati della rete viva stanno in un ordine diverso da quello di chi riapre dal giornale, e
un'istantanea salvata dopo la lettura se le porta dietro.

Non ho trovato una domanda che cambi righe per questo: è la rete in memoria a non essere quella di prima, né quella
che si rilegge dal giornale."""

from sinapsidb.memoria import Memoria
from sinapsidb.motore import Rete
from test_revisione_circuiti_090 import aperta


def linee(r):
    return {nome: [(k, list(d.items())) for k, d in getattr(r, nome).items()] for nome in ("vivi", "morti", "coppie")}


def storia(m):
    assert m.racconta("Nota che varrà", [{"tipo": "nota", "legami": {"di": "A", "cosa": "x", "vale da": "2099-01-01"}}])["esito"] == "applicato"
    assert m.racconta("Appunto", [{"tipo": "appunto", "legami": {"di": "A", "cosa": "y"}}])["esito"] == "applicato"


def test_una_lettura_senza_clausola_lascia_la_rete_come_era(tmp_path):
    with aperta(Memoria(tmp_path / "rete", scritture=True)) as m:
        storia(m)
        prima = linee(m.rete)
        assert m.chiedi("in appunto conta per di")["righe"] == [("A", "1")]
        assert linee(m.rete) == prima, "una lettura non lascia tracce: le linee sono quelle di prima"


def test_dopo_una_lettura_le_linee_dei_ritirati_sono_quelle_di_chi_riapre(tmp_path):
    c = tmp_path / "rete"
    with aperta(Memoria(c, scritture=True)) as m:
        storia(m)
        m.chiedi("in appunto conta per di")  # la nota del 2099 viene messa da parte e rimessa
        for tipo in ("appunto", "nota"):  # due scritture vere: i due fatti vengono ritirati, in quest'ordine
            assert m.racconta(f"Via {tipo}", [], sostituisce=[{"tipo": tipo, "indizi": {"di": "A"}}])["esito"] == "applicato"
        viva = [(k, list(d.items())) for k, d in m.rete.morti.items() if d]
    with aperta(Rete(str(c), sola_lettura=True)) as riaperta:
        assert [(k, list(d.items())) for k, d in riaperta.morti.items() if d] == viva
