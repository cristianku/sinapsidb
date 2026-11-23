"""Baseline del concorrente, solo sul PostgreSQL privato. Nessuna misura dei tempi."""

import os
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from pg_temporaneo import NonDisponibile, avvia

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from prove.fido.tracciato import collaudo, registra
from prove.fido.registro import postgresql
from prove.fido.trasporto import apri, ferma
from prove.fido.verificatore import Giudice

pytest.importorskip("psycopg")
from prove.fido.postgres.base import Database, PARAMETRI, collega


@pytest.fixture()
def privato():
    try:
        with avvia(opzioni=PARAMETRI) as dsn:
            yield dsn
    except NonDisponibile as exc:
        pytest.skip(str(exc))


def test_stesso_collaudo_su_postgres_con_ripresa_del_wal(privato):
    with tempfile.TemporaryDirectory(prefix="fpg-") as d:
        socket = str(Path(d) / "s")
        server, filo = apri(socket, Database(privato))

        def riavvia():
            nonlocal server, filo
            ferma(server, filo)
            with collega(privato) as db:
                data = Path(db.execute("SHOW data_directory").fetchone()["data_directory"])
                assert data.parent == Path(db.info.host) and data.parent.name.startswith("sinapsidb-pg-")
                assert db.execute("SHOW synchronous_commit").fetchone()["synchronous_commit"] == "on"
            # Solo il processo privato creato da pg_temporaneo. Stop immediato,
            # non un checkpoint di spegnimento: il nuovo postmaster rilegge il WAL.
            pid = (data / "postmaster.pid").read_text().splitlines()[0]
            ctl = Path(os.readlink(f"/proc/{pid}/exe")).with_name("pg_ctl")
            subprocess.run([shutil.which("runuser"), "-u", "postgres", "--", str(ctl), "restart",
                            "-D", str(data), "-w", "-m", "immediate", "-l", str(data.parent / "ripresa.log")],
                           check=True, capture_output=True, timeout=40)
            server, filo = apri(socket, Database(privato))

        try:
            piano = list(collaudo())
            osservate = list(registra(piano, socket, dopo_ripresa=riavvia))
            assert Giudice(osservate, piano).verifica(postgresql(privato)) == {
                "esito": "coerente", "tentativi": len(piano), "decisioni": 27, "conferme_perse": 2}
        finally:
            ferma(server, filo)


def test_pg_chiave_concorrente_finestra_senza_scritture_e_outbox(privato):
    db = Database(privato, con_cache=True)
    q = {"op": "accorda", "chiave": "unica", "atto": "F", "cliente": "A", "importo": "800.000001"}

    def tenta(i):
        with db.sessione() as s:
            return s.esegui({**q, "id_tentativo": i})

    with ThreadPoolExecutor(max_workers=6) as pool:
        risposte = list(pool.map(tenta, range(6)))
    assert [r["id_tentativo"] for r in risposte] == list(range(6))
    assert {r["evento"] for r in risposte} == {2}
    assert sum(not r["ripetuta"] for r in risposte) == 1
    with db.sessione() as s:
        assert s.esegui({**q, "importo": "200"})["esito"] == "conflitto"
        p = s.esegui({"op": "pagamento", "chiave": "P", "cliente": "A", "causale": "P", "importo": "400",
                      "vale_da": "2080-01-01", "vale_fino_a": "2080-01-02"})
        assert p["esito"] == "applicato"
        da = s.una("SELECT da FROM fatti WHERE nato=%s", p["evento"])["da"]
        for t, saldo in [(da-1, "800.000001"), (da, "1200.000001"), (da+86400, "800.000001"), (da, "1200.000001")]:
            with s.db.transaction():
                x = s.proiezione("A", t)
                assert x["valore"] == saldo and x["revisione"] == p["evento"]
                assert (x["da"] is None or x["da"] <= t) and (x["fine"] is None or t < x["fine"])
        assert s.revisione("A") == {"revisione": p["evento"], "formula": 0, "aggiornato_a": p["evento"]}
        assert s.una("SELECT count(*) AS n FROM coda_cache")["n"] == 2
        # Proiezione e coda non cambiano in caso di rifiuto; la ricevuta però è durevole.
        no = s.esegui({"op": "ordine", "chiave": "N", "cliente": "A", "numero": "N", "importo": "9000"})
        assert no["esito"] == "violazione_regola"
        assert s.esegui({"op": "stato", "chiave": "N"})["evento"] == no["evento"]
        assert s.revisione("A") == {"revisione": p["evento"], "formula": 0, "aggiornato_a": no["evento"]}
        assert s.una("SELECT count(*) AS n FROM coda_cache")["n"] == 2


def test_un_errore_di_lettura_e_un_errore_al_commit_non_lasciano_fatti_o_ricevute(privato):
    db = Database(privato, con_cache=True)
    with db.sessione() as s:
        q = {"op": "accorda", "chiave": "F", "atto": "F", "cliente": "C", "importo": "800"}
        prima = s.esegui({"op": "stato"})

        def invariato():
            assert s.esegui({"op": "stato"}) == prima
            assert s.esegui({"op": "stato", "chiave": "F"})["presente"] is False
            assert s.una("SELECT count(*) AS n FROM coda_cache")["n"] == 0

        # Una lettura necessaria della regola non è disponibile: non è un sì o un no.
        s.db.execute("ALTER FUNCTION saldo_al(text,float8,bigint,bigint[]) RENAME TO saldo_spento")
        assert s.esegui(q)["tipo"] == "guasto"
        s.db.execute("ALTER FUNCTION saldo_spento(text,float8,bigint,bigint[]) RENAME TO saldo_al")
        invariato()
        # Questa volta scrivi() finisce; il guasto avviene precisamente al COMMIT,
        # dopo l'inserimento di fatti, ricevuta e outbox. Non deve uscire l'ACK.
        s.db.execute("""CREATE FUNCTION interrompi() RETURNS trigger LANGUAGE plpgsql AS
                        $$ BEGIN RAISE EXCEPTION 'errore di collaudo'; END $$""")
        s.db.execute("""CREATE CONSTRAINT TRIGGER interrompi AFTER INSERT ON eventi DEFERRABLE INITIALLY DEFERRED
                        FOR EACH ROW EXECUTE FUNCTION interrompi()""")
        assert s.esegui(q)["tipo"] == "guasto"
        invariato()
        s.db.execute("DROP TRIGGER interrompi ON eventi")
        assert s.esegui(q)["evento"] == prima["ultimo_evento"] + 1
