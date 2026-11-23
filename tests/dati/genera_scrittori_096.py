"""Storie del sesto giro, scritte con i binari del periodo. I test ne leggono soltanto copie.

Eseguire con PYTHONPATH=<git archive della revisione>/src, all'ora fissata qui sotto:
  - `senza-tratto <cartella-nuova>` con 10c5ab7: il writer usa l'API Rete con callback per la ricevuta;
  - `inizio <cartella-nuova>` con a7b4eab, `intervallo <stessa-cartella>` con a28cd35,
    `ripresa <stessa-cartella>` con a7b4eab: tre sessioni successive, mai due writer insieme.
Nessun record e nessun segno vengono costruiti o ritoccati dal generatore.
"""

import shutil
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from genera_calcoli_non_dichiarati import fido
from sinapsidb import __version__, memoria, motore
from sinapsidb.memoria import Memoria
from sinapsidb.motore import Rete


def senza_tratto(cartella):
    shutil.copytree(Path(__file__).parent / "storico-8cad802-incrementale", cartella)
    prima = (cartella / "formato").read_bytes()
    r = Rete(str(cartella))  # la ricevuta la fornisce chi chiama: niente decisioni automatiche
    try:
        e, n = len(r.e_frase), r.n
        cliente, b, c = n("cliente"), n("B"), n("C")

        def nota(d):
            return {"ricevuta": {"esito": "applicato", "evento": f"evento:{e}", "decisione": d}}

        assert r.racconta("Accredito per B e C", "test", [(n("accredito"), [
            (cliente, b), (cliente, b), (cliente, c), (n("importo"), n("10"))])], nota_della_scrittura=nota) == e == 3
    finally:
        r.chiudi()
    assert (cartella / "formato").read_bytes() == prima
    # La 0.9.5 mette una frontiera anche solo aprendo Memoria: si verifica su una copia separata.
    with TemporaryDirectory(dir=cartella.parent) as tmp:
        m = Memoria(shutil.copytree(cartella, Path(tmp) / "rete"))
        try:
            v = m.verifica(e)
            assert v["esito"] == "coerente" and v["calcolo"] == 4
        finally:
            m.chiudi()


def mista(cartella, fase):
    if fase == "inizio":
        cartella.mkdir()
    m = Memoria(cartella, scritture=True)
    try:
        if fase != "intervallo":
            e = 0 if fase == "inizio" else 6
            x = m.racconta(fase, [{"tipo": "nota", "legami": {"cosa": fase}}])
            assert x["evento"] == f"evento:{e}" and x["decisione"]["calcolo"] == 4
        else:
            r, n = m.rete, m.rete.n
            r.racconta("Anagrafica", "test", [(n("anagrafica"), [(n("cliente"), n("B"))])])
            fido(r)
            x = m.racconta("Ordine di B", [{"tipo": "ordine", "legami": {"cliente": "B", "importo": "60"}}], chiave="ordine-B")
            a = r.racconta("Accredito retroattivo per A e B", "test", [(n("accredito"), [
                (n("cliente"), n("A")), (n("cliente"), n("B")), (n("importo"), n("70")), (n("vale da"), n("2000-01-01"))])])
            assert a == 5 and x["evento"] == "evento:4" and m.verifica(4)["esito"] == "coerente"
            y = m.riesamina(4, [5])["ipotetico"]
            assert y["esito"] == "violazione_regola" and [v["valore"] for v in y["letture"]] == ["0", "-60"]
    finally:
        m.chiudi()


if __name__ == "__main__":
    fase, cartella = sys.argv[1], Path(sys.argv[2])
    if __version__ != {"senza-tratto": "0.9.5", "intervallo": "0.9.3", "inizio": "0.9.6", "ripresa": "0.9.6"}[fase]:
        raise RuntimeError("fase da eseguire con i sorgenti della revisione corrispondente")
    orologio = SimpleNamespace(time=lambda: 1_790_000_100.0)
    with patch.object(memoria, "time", orologio), patch.object(motore, "time", orologio):
        if fase == "senza-tratto":
            senza_tratto(cartella)
        else:
            mista(cartella, fase)
