"""Due casi di aggiornamento scritti dalla 0.9.0, senza costruire a mano né record né istantanea.

    git archive 24a5d89 src | tar -x -C "$tmp"
    PYTHONPATH="$tmp/src" python tests/dati/genera_circuiti_090.py "$tmp/storico-24a5d89-circuiti"

La destinazione deve essere nuova. I test copiano i dati già prodotti; non eseguono questo generatore.
"""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sinapsidb import __version__, memoria, motore
from sinapsidb.memoria import Memoria


def mensile(r):
    n = r.n
    r.proponi_circuito("Somma per mese", "test", "mensile", [
        {"osserva": n("vendita"), "lega": n("quando"), "somma": n("totale"), "peso": 1, "per": "mese"}])


def salva(destinazione):
    if __version__ != "0.9.0":
        raise RuntimeError("usare i sorgenti di 24a5d89")
    destinazione.mkdir()
    m = Memoria(destinazione / "istantanea", scritture=True)
    try:
        r, n = m.rete, m.rete.n
        mensile(r)
        r.proponi_regola("Osserva il documento", "test", "presenza documento", 2, "vieta", [
            {"nome": "presente", "modo": "presenza", "osserva": n("vendita"), "lega": n("documento"), "peso": 1}])
        legami = [(n("quando"), n("2027-04-09")), (n("totale"), n("20"))]
        r.racconta("Promemoria", "test", [(n("promemoria"), legami)])
        # Solo il mese nasce nella prova; il giornale fino a qui è integro e non lo contiene.
        r.racconta("Solo una prova", "test", [(n("vendita"), legami)], prova=True)
        assert r.trova("2027-04") and not motore.Rete.com_era(str(m.cartella), 3)[0].trova("2027-04")
        r.istantanea()
    finally:
        m.chiudi()

    m = Memoria(destinazione / "rettifica", scritture=True)
    try:
        mensile(m.rete)
        for data, chiave, ritiri in (("2026-13-07", "prima", []),
                                     ("2026-12-07", "rettifica", [{"tipo": "vendita", "indizi": {"quando": "2026-13-07"}}])):
            x = m.racconta("Vendita " + data, [{"tipo": "vendita", "legami": {"quando": data, "totale": "10"}}],
                           sostituisce=ritiri, chiave=chiave)
            assert x["esito"] == "applicato" and m.verifica(x["evento"])["esito"] == "coerente"
    finally:
        m.chiudi()


if __name__ == "__main__":
    orologio = SimpleNamespace(time=lambda: 1_790_000_000.0)
    with patch.object(memoria, "time", orologio), patch.object(motore, "time", orologio):
        salva(Path(sys.argv[1]))
