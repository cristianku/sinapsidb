"""La richiesta del caso 0.9.2 eseguita dalla 0.9.3, più un fatto senza legami ammesso allora.

    git archive a28cd35 src | tar -x -C "$tmp"
    PYTHONPATH="$tmp/src" python tests/dati/genera_ricevute_093.py "$tmp/storico-a28cd35-ricevute"

La destinazione deve essere nuova. I test usano copie dei file prodotti, non questo programma.
"""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from genera_fido_due_clienti_092 import ordine_dopo_definizione
from sinapsidb import __version__, memoria, motore
from sinapsidb.memoria import Memoria


if __name__ == "__main__":
    if __version__ != "0.9.3":
        raise RuntimeError("usare i sorgenti di a28cd35")
    cartella = Path(sys.argv[1])
    cartella.mkdir()
    orologio = SimpleNamespace(time=lambda: 1_790_000_000.0)
    with patch.object(memoria, "time", orologio), patch.object(motore, "time", orologio):
        db = Memoria(cartella, scritture=True)
        try:
            x = ordine_dopo_definizione(db)
            assert x["esito"] == "violazione_regola" and [y["valore"] for y in x["decisione"]["regole"][0]["letture"]] == ["0", "-60"]
            assert db.verifica(x["evento"])["esito"] == "coerente"
            r = db.rete
            vuoto = r.racconta("Promemoria senza partecipanti", "test", [(r.n("promemoria"), [])])
            assert vuoto == 4 and db.verifica(vuoto)["esito"] == "coerente"
            r.racconta("Rettifica di un altro tipo", "test", [(r.n("nota"), [(r.n("numero"), r.n("N2"))])])
        finally:
            db.chiudi()
