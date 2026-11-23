"""Dopo un allineamento fallito a evento già scritto, Memoria.racconta risponde ancora leggendo la memoria guasta.

``ricevuta_di``, ``valore``, ``chiedi`` e una scrittura vera esigono la riapertura. ``Memoria.racconta`` no, quando
risponde senza arrivare a scrivere: la stessa chiave ripetuta, e una prova."""

from types import SimpleNamespace

import pytest

from sinapsidb import memoria, motore
from sinapsidb.memoria import Memoria
from sinapsidb.motore import Rete
from test_revisione_circuiti_090 import aperta
from test_segno_e_disco import fido, movimento

TERMINE = [{"tipo": "termine", "legami": {"termine di": "fido", "osserva": "accredito", "lega": "cliente",
                                          "somma": "importo", "peso": "-1"}}]
ORDINE = [{"tipo": "ordine", "legami": {"cliente": "A", "importo": "60"}}]


@pytest.fixture
def guasta_dopo_il_commit(tmp_path, monkeypatch):
    """Una memoria con il fido di A a 100, in cui la definizione che lo compensa (e lo porta a 0) è arrivata nel
    giornale ma il ricalcolo delle somme, subito dopo, è fallito. È il guasto di ``test_revisione_dopo_commit_0103.py``, uno solo."""
    c = tmp_path / "rete"
    clock = SimpleNamespace(time=lambda: 1000.5)
    monkeypatch.setattr(memoria, "time", clock)
    monkeypatch.setattr(motore, "time", clock)
    m = Memoria(c, scritture=True)
    fido(m)
    assert movimento(m, "accredito", "A", "100", "credito")["esito"] == "applicato"
    prima = m.conteggi()["eventi"]
    somme = m.rete._somme

    def leggi(*args, **kwargs):
        if len(list(Rete._record(str(c)))) > prima:  # l'ammissione riesce: fallisce solo il ricalcolo dopo il commit
            raise OSError("lettura della somma fallita dopo il commit")
        return somme(*args, **kwargs)

    with monkeypatch.context() as mp:
        mp.setattr(m.rete, "_somme", leggi)
        with pytest.raises((OSError, RuntimeError)):
            m.racconta("Compensa il credito", TERMINE, chiave="compensa")
    assert len(list(Rete._record(str(c)))) == prima + 1, "l'evento è nel giornale"
    assert m.rete.fuori_uso and m.rete.memoria_guasta
    yield m, c
    m.chiudi()


def test_la_stessa_chiave_ripetuta_non_riceve_una_ricevuta_diversa_da_quella_del_giornale(guasta_dopo_il_commit):
    m, c = guasta_dopo_il_commit
    with aperta(Memoria(c)) as lettore:
        durevole = lettore.ricevuta_di("compensa")
    assert durevole["esito"] == "applicato" and durevole["scritture_applicate"] == 1
    # Chi ha chiesto ha visto un errore, non una conferma: ripete la stessa richiesta con la stessa chiave.
    try:
        ripetuta = m.racconta("Compensa il credito", TERMINE, chiave="compensa")
    except RuntimeError:
        return  # lecito: va riaperta prima di sapere se la richiesta è entrata
    ripetuta.pop("ripetuta", None)
    assert ripetuta == durevole, "il giornale ha già la ricevuta di questa chiave: è «applicato», con un evento"


def test_una_prova_non_si_decide_sul_saldo_che_il_giornale_non_ha_piu(guasta_dopo_il_commit):
    m, c = guasta_dopo_il_commit
    with aperta(Memoria(c)) as lettore:
        assert lettore.valore("fido", "A")["valore"] == "0"
        dal_giornale = lettore.racconta("ordine di A", ORDINE, prova=True)
    assert [r["regola"] for r in dal_giornale["regole_scattate"]] == ["limite"]
    with pytest.raises(RuntimeError):
        m.valore("fido", "A")  # la lettura si rifiuta: la memoria va riaperta
    try:
        calda = m.racconta("ordine di A", ORDINE, prova=True)
    except RuntimeError:
        return  # lecito: va riaperta prima di rispondere
    assert calda["regole_scattate"] == dal_giornale["regole_scattate"], "la prova ha deciso con il saldo di prima del commit"
