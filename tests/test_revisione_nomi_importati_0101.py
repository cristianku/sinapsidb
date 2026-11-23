"""Un nome già letto dal cursore torna dopo un callback: confrontare col vecchio percorso che risolveva ogni riga."""

from types import SimpleNamespace

import pytest

from sinapsidb import confronto, motore
from sinapsidb.motore import Ambiguo, Rete
from test_confronto import cluster, dati, pg
from test_revisione_circuiti_090 import aperta


def singoli(conn, r, avanza):
    """Importazione di riferimento: lo stesso PostgreSQL e la stessa notifica tra le due tabelle."""
    confronto.somma_pronta(r)
    v = confronto.Vocabolario(r)
    scritti = 0
    for f in confronto.FATTI:
        with conn.transaction(), conn.cursor(name="uno_alla_volta") as cur:
            cur.execute(f.sql)
            for riga in cur:
                testi = [confronto._testo(x) for x in riga]
                legami = [(v.ruolo[ruolo], r.n(testo)) for ruolo, testo in zip(f.ruoli, testi)]
                r.racconta(f.frase.format(*testi), confronto.ORIGINE, [(v.tipo[f.tabella], legami)])
                scritti += 1
        avanza(scritti, f.tabella)


@pytest.mark.parametrize("omonima", [False, True], ids=["solo-progressi", "omonima-fra-due-tabelle"])
def test_la_cache_di_importazione_non_sceglie_quale_categoria_quando_il_nome_diventa_ambiguo(tmp_path, pg, monkeypatch, omonima):
    monkeypatch.setattr(motore, "time", SimpleNamespace(time=lambda: 1_790_000_000.5))
    for via, importa in (("uno", singoli), ("molti", confronto.importa)):
        c = tmp_path / via
        with confronto.collega(pg) as conn, aperta(Rete(str(c), sync="os")) as r:
            # Una categoria di cui sappiamo che arriverà almeno un prodotto.
            nome = conn.execute("SELECT c.nome FROM categorie c JOIN prodotti p ON p.categoria_id = c.id ORDER BY p.id LIMIT 1").fetchone()[0]
            prima = None

            def avanza(fatti, tabella):
                nonlocal prima
                if tabella == "categorie" and omonima:
                    prima, = r.trova(nome)
                    r.nuovo(nome)

            if omonima:
                with pytest.raises((Ambiguo, ValueError)):
                    importa(conn, r, avanza)
                assert prima is not None and len(r.trova(nome)) == 2
            else:
                importa(conn, r, avanza)
    assert (tmp_path / "molti/giornale").read_bytes() == (tmp_path / "uno/giornale").read_bytes()
