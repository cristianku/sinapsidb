"""Dalla revisione: l'ordine del giornale e l'istante di scrittura valgono anche quando si cambia percorso."""

from types import SimpleNamespace

import pytest

from sinapsidb import lingua, memoria, motore
from sinapsidb.memoria import Memoria
from sinapsidb.motore import Rete, Vietato
from test_calcolo_dichiarato import fido
from test_revisione_circuiti_090 import aperta
from test_revisione_lotti_0100 import nota


@pytest.mark.parametrize("modo", ["frase", "os"])
def test_uscire_dai_lotti_non_scambia_due_destinatari(tmp_path, modo):
    c = tmp_path / "rete"
    with aperta(Rete(str(c), sync="os")) as r:
        nota(r, "Prima")
        r.sync = "lotti"
        assert nota(r, "Ada") == 1
        try:
            r.sync = modo
        except (ValueError, RuntimeError):  # accettabile esigere che il chiamante scarichi prima
            r.scarica()
            r.sync = modo
        assert nota(r, "Bea") == 2
        r.istantanea()
    q = "in nota leggi frase, cosa"
    giornale, oltre = Rete.com_era(str(c), 3)
    assert oltre is None
    with aperta(Rete(str(c))) as foto:
        attese = [("Prima", "Prima"), ("Ada", "Ada"), ("Bea", "Bea")]
        assert lingua.prepara(foto, q).esegui() == lingua.prepara(giornale, q).esegui() == attese


def test_un_segno_ripristinato_dopo_la_scelta_dei_lotti_protegge_anche_lo_scarico(tmp_path):
    c = tmp_path / "memoria"
    with aperta(Memoria(c, scritture=True)) as m:
        a = m.racconta("Prima", [{"tipo": "nota", "legami": {"cosa": "A"}}], chiave="A")
    # È davvero il segno di questo prefisso. Nessun altro scrittore apre il giornale.
    segno, copia = c / "formato", tmp_path / "copia-del-segno"
    segno.rename(copia)
    try:
        with aperta(Rete(str(c), sync="lotti")) as r:
            nota(r, "Seconda")
            copia.rename(segno)  # prima della pubblicazione del gruppo viene ripristinato il suo contratto
            r.scarica()
    except (ValueError, RuntimeError):
        pass
    finally:
        if copia.exists():
            copia.rename(segno)
    with aperta(Memoria(c)) as m:
        assert m.ricevuta_di("A") == a
        # Lecito fermarsi o conservare una decisione vera: mai un evento senza ricevuta dopo la frontiera.
        for e in range(m.conteggi()["eventi"]):
            assert m.verifica(e)["esito"] == "coerente"


@pytest.mark.parametrize("scade_durante_la_lettura", [False, True], ids=["orologio-fermo", "scadenza-durante-segno"])
def test_il_tempo_passato_leggendo_il_segno_precede_la_scrittura(tmp_path, monkeypatch, scade_durante_la_lettura):
    confine = motore.istante("2027-02-01")
    ora = [confine - 60]
    clock = SimpleNamespace(time=lambda: ora[0])
    monkeypatch.setattr(motore, "time", clock)
    monkeypatch.setattr(memoria, "time", clock)
    risultati = {}
    for via in ("memoria", "singolo", "molti"):
        c = tmp_path / via
        ora[0] = confine - 60
        with aperta(Memoria(c, scritture=True)) as m:
            r, n = m.rete, m.rete.n
            fido(m)
            r.racconta("Credito", "test", [(n("accredito"), [(n("cliente"), n("C")), (n("importo"), n("100")),
                                                              (n("vale fino a"), n("2027-02-01"))])])
            legami = [(n("cliente"), n("C")), (n("importo"), n("60"))]
            ordine = n("ordine")
            ora[0] = confine - 1
            leggi = r.segno

            def leggi_segno(cartella):
                segno = leggi(cartella)
                if scade_durante_la_lettura:
                    ora[0] = confine  # I/O terminato: l'ordine non è ancora stato scritto
                return segno

            with monkeypatch.context() as mp:
                mp.setattr(r, "segno", leggi_segno)
                try:
                    if via == "molti":
                        r.racconta_molti("test", [("Ordine", ordine, legami)])
                    elif via == "singolo":
                        r.racconta("Ordine", "test", [(ordine, legami)])
                    else:
                        m.racconta("Ordine", [{"tipo": "ordine", "legami": {"cliente": "C", "importo": "60"}}], chi="test")
                except Vietato:
                    pass  # si confronta la decisione registrata, non la convenzione di ritorno dell'API
            record = list(Rete._record(str(c)))[-1]
            assert m.verifica(3)["esito"] == "coerente"
            risultati[via] = (record[7]["ricevuta"]["esito"], record[8] if len(record) > 8 else record[2])
    atteso = ("violazione_regola", confine) if scade_durante_la_lettura else ("applicato", confine - 1)
    assert risultati["memoria"] == risultati["singolo"] == atteso
    assert risultati["molti"] == atteso
