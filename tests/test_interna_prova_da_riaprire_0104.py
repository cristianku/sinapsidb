"""Controprova sulla candidata 0.10.4: una memoria che va riaperta non dice più il saldo, ma ci decide ancora una prova.

È il caso di ``test_revisione_dopo_commit_0103.py`` chiesto con ``prova=True``. Il nuovo termine è già
nel giornale e porta il fido di A a 0; il ricalcolo in memoria è fallito, e l'istanza rimasta aperta tiene ancora 100.
Regola: dopo un errore a metà non si continua a decidere su uno stato che non è quello del giornale; ``Memoria._viva``
va chiamata «prima di rispondere a qualunque cosa che legga la rete in memoria».
"""

from types import SimpleNamespace

from sinapsidb import memoria, motore
from sinapsidb.memoria import Memoria
from sinapsidb.motore import Rete
from test_revisione_circuiti_090 import aperta
from test_segno_e_disco import fido, movimento

ORDINE = [{"tipo": "ordine", "legami": {"cliente": "A", "importo": "60"}}]


def letto(prova):
    """Che cosa ha deciso una prova: le regole che scatterebbero e i valori che ha letto."""
    return ([x["regola"] for x in prova["regole_scattate"]],
            [y["valore"] for x in prova["decisione"]["regole"] for y in x["letture"]])


def test_una_prova_non_si_decide_sul_saldo_che_il_giornale_non_ha_piu(tmp_path, monkeypatch):
    c = tmp_path / "rete"
    clock = SimpleNamespace(time=lambda: 1000.5)
    monkeypatch.setattr(memoria, "time", clock)
    monkeypatch.setattr(motore, "time", clock)
    with aperta(Memoria(c, scritture=True)) as m:
        fido(m)
        assert movimento(m, "accredito", "A", "100", "credito")["esito"] == "applicato"
        termine = [{"tipo": "termine", "legami": {"termine di": "fido", "osserva": "accredito", "lega": "cliente",
                                                  "somma": "importo", "peso": "-1"}}]
        prima = m.conteggi()["eventi"]
        somme = m.rete._somme

        def leggi(*args, **kwargs):
            # L'ammissione riesce. Fallisce solo il ricalcolo che trova già il nuovo record sul disco.
            if len(list(Rete._record(str(c)))) > prima:
                raise OSError("lettura della somma fallita dopo il commit")
            return somme(*args, **kwargs)

        with monkeypatch.context() as mp:
            mp.setattr(m.rete, "_somme", leggi)
            try:
                m.racconta("Compensa il credito", termine, chiave="compensa")
            except (OSError, RuntimeError):
                pass  # lecito esigere la riapertura
        with aperta(Memoria(c)) as lettore:  # il giornale: il fido di A è 0, un ordine da 60 non passerebbe
            assert lettore.ricevuta_di("compensa")["esito"] == "applicato" and lettore.valore("fido", "A")["valore"] == "0"
            dal_giornale = letto(lettore.racconta("Ordine di A", ORDINE, prova=True))
            assert dal_giornale == (["limite"], ["0", "-60"])
        try:
            calda = m.racconta("Ordine di A", ORDINE, prova=True)
        except RuntimeError:
            assert m.rete.fuori_uso  # esige prima una riapertura: va bene
        else:  # se risponde, risponde come il giornale
            assert letto(calda) == dal_giornale
