"""Fixture di provenienza per la revisione 0.9.5. Usare i sorgenti dei veri scrittori:

    git archive <revisione> src | tar -x -C "$tmp"
    PYTHONPATH="$tmp/src" python tests/dati/genera_calcoli_non_dichiarati.py <cartella-nuova>

760859b e a28cd35 scrivono indipendentemente la stessa storia e devono produrre due file
identici (giornale e formato), pur dando due risposte diverse al riesame. Di quei byte
conserviamo una sola copia. 8cad802 scrive l'altro giornale, con un accredito incrementale.
"""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sinapsidb import __version__, memoria, motore
from sinapsidb.memoria import Memoria


def fido(r):
    n = r.n
    r.proponi_circuito("Saldo", "test", "fido", [
        {"osserva": n(t), "lega": n("cliente"), "somma": n("importo"), "peso": k}
        for t, k in (("accredito", 1), ("ordine", -1))])
    r.proponi_regola("Fido", "test", "limite", 1, "vieta", [
        {"nome": "negativo", "circuito": n("fido"), "limite": 0, "peso": 1}])


def non_distinguibili(m):
    r, n = m.rete, m.rete.n
    r.racconta("Anagrafica", "test", [(n("anagrafica"), [(n("cliente"), n("B"))])])
    fido(r)
    d = m.racconta("Ordine di B", [{"tipo": "ordine", "legami": {"cliente": "B", "importo": "60"}}], chiave="ordine-B")
    a = r.racconta("Accredito retroattivo per A e B", "test", [(n("accredito"), [
        (n("cliente"), n("A")), (n("cliente"), n("B")), (n("importo"), n("70")), (n("vale da"), n("2000-01-01"))])])
    assert d["evento"] == "evento:3" and a == 4 and d["esito"] == "violazione_regola"
    assert m.verifica(3)["esito"] == m.verifica(4)["esito"] == "coerente"
    x = m.riesamina(3, [4])["ipotetico"]
    atteso = ("applicato", ["70", "10"]) if __version__ == "0.9.2" else ("violazione_regola", ["0", "-60"])
    assert (x["esito"], [y["valore"] for y in x["letture"]]) == atteso


def incrementale_094(m):
    r, n = m.rete, m.rete.n
    fido(r)
    # Quando arriva il fatto, la 0.9.4 somma due occorrenze per A. Questa precisa ricevuta si verificava allora.
    e = r.racconta("Accredito di 70", "test", [(n("accredito"), [
        (n("cliente"), n("A")), (n("cliente"), n("A")), (n("importo"), n("70"))])])
    letture = r.e_nota[e]["ricevuta"]["decisione"]["regole"][0]["letture"]
    assert e == 2 and [y["valore"] for y in letture] == ["0", "140"] and m.verifica(e)["esito"] == "coerente"


if __name__ == "__main__":
    if __version__ not in ("0.9.2", "0.9.3", "0.9.4"):
        raise RuntimeError("usare i sorgenti di 760859b, a28cd35 o 8cad802")
    cartella = Path(sys.argv[1])
    cartella.mkdir()
    orologio = SimpleNamespace(time=lambda: 1_790_000_000.0)
    with patch.object(memoria, "time", orologio), patch.object(motore, "time", orologio):
        m = Memoria(cartella, scritture=True)
        try:
            (incrementale_094 if __version__ == "0.9.4" else non_distinguibili)(m)
        finally:
            m.chiudi()
