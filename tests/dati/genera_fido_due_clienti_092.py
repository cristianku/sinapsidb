"""Storia di aggiornamento: lo stesso ruolo verso due clienti in un circuito ordinario, senza mesi.

    git archive 760859b src | tar -x -C "$tmp"
    PYTHONPATH="$tmp/src" python tests/dati/genera_fido_due_clienti_092.py "$tmp/storico-760859b-due-clienti"

La destinazione deve essere nuova. Nei test si legge una copia del giornale autentico.
"""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sinapsidb import __version__, memoria, motore
from sinapsidb.memoria import Memoria


def ordine_dopo_definizione(m):
    r, n = m.rete, m.rete.n
    # Rete ammette due legami con lo stesso ruolo. Il fatto PRECEDE la definizione:
    # nella 0.9.2 sia il primo calcolo sia la verifica dal giornale attribuivano 70 a B.
    r.racconta("Credito condiviso", "test", [
        (n("accredito"), [(n("cliente"), n("A")), (n("cliente"), n("B")), (n("importo"), n("70"))])])
    r.proponi_circuito("Saldo", "test", "fido", [
        {"osserva": n(t), "lega": n("cliente"), "somma": n("importo"), "peso": peso}
        for t, peso in (("accredito", 1), ("ordine", -1))])
    r.proponi_regola("Fido non negativo", "test", "limite", 1, "vieta", [
        {"nome": "negativo", "circuito": n("fido"), "limite": 0, "peso": 1}])
    return m.racconta("Ordine di B", [{"tipo": "ordine", "legami": {"cliente": "B", "importo": "60"}}], chiave="ordine-B")


if __name__ == "__main__":
    if __version__ != "0.9.2":
        raise RuntimeError("usare i sorgenti di 760859b")
    cartella = Path(sys.argv[1])
    cartella.mkdir()
    orologio = SimpleNamespace(time=lambda: 1_790_000_000.0)
    with patch.object(memoria, "time", orologio), patch.object(motore, "time", orologio):
        db = Memoria(cartella, scritture=True)
        try:
            x = ordine_dopo_definizione(db)
            assert x["esito"] == "applicato" and [v["valore"] for v in x["decisione"]["regole"][0]["letture"]] == ["70", "10"]
            assert db.verifica(x["evento"])["esito"] == "coerente"
        finally:
            db.chiudi()
