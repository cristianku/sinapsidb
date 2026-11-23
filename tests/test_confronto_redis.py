"""Redis nel confronto: le risposte alle quindici domande preparate prima, controllate contro PostgreSQL.

PostgreSQL e Redis sono privati e temporanei: partono per questi test e alla fine vengono spenti e cancellati. Il
Redis di sistema non viene toccato.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

import redis_temporaneo
from sinapsidb import confronto, confronto_redis
from sinapsidb.confronto import DOMANDE, Banco, importa, misura
from sinapsidb.motore import Rete
from test_confronto import SEME, TAGLIE, _aspetta, _base, cluster, dati, pg  # noqa: F401


@pytest.fixture(scope="module")
def servizio_redis():
    try:
        contesto = redis_temporaneo.avvia({"maxmemory": "200mb"})
        servizio = contesto.__enter__()
    except redis_temporaneo.NonDisponibile as e:
        pytest.skip(f"Redis temporaneo non disponibile: {e}")
    try:
        yield f"unix://{servizio.socket}"
    finally:
        contesto.__exit__(None, None, None)


@pytest.fixture()
def r(servizio_redis: str):
    cliente = confronto_redis.collega(servizio_redis)
    cliente.flushall()  # il Redis è privato, di questi test
    yield cliente
    cliente.close()


def test_le_risposte_pronte_sono_quelle_di_postgres(pg: str, r) -> None:
    """Tutte e quindici le domande, per tutti i valori del campione: quello che Redis restituisce, riga per riga, è
    quello che restituisce PostgreSQL."""
    r.set("di-un-altro", "resta")
    with confronto.collega(pg) as conn:
        esito = confronto_redis.carica(conn, r, {"seme": SEME})
        assert esito["righe"] > 5000 and esito["chiavi"] > 1000 and esito["memoria_usata"] > esito["memoria"] > 100_000
        assert confronto_redis.occupazione(r)["chiavi"] == esito["chiavi"] == confronto_redis.conta(r)
        assert confronto_redis.caricamento(r)["generazione"] == {"seme": SEME}
        parametri = confronto.campioni(conn, 60)
        with conn.cursor() as cur:
            for i, d in enumerate(DOMANDE):
                per_sql = d.get("sql_parametri")
                con_righe = 0
                for p in parametri[d["parametri"]]:
                    di_pg = sorted(tuple(confronto._testo(x) for x in riga) for riga in cur.execute(d["sql"], per_sql(p) if per_sql else p).fetchall())
                    di_redis = sorted(confronto_redis.righe(i, confronto_redis.chiedi(r, i, p)))
                    assert di_redis == di_pg, (d["nome"], p)
                    con_righe += bool(di_pg)
                assert con_righe, d["nome"]  # il campione ha davvero esercitato la domanda
    # un valore che non c'è dà una risposta vuota, come in PostgreSQL
    assert confronto_redis.chiedi(r, 0, ("F-inesistente",)) == [] and confronto_redis.chiedi(r, 6, ("P-x", "nessuno")) == []
    # svuotare e ricaricare toccano solo le chiavi del confronto
    assert confronto_redis.svuota(r) == esito["chiavi"] + 1  # le risposte, più il segno del caricamento
    assert r.get("di-un-altro") == "resta" and confronto_redis.caricamento(r) is None
    assert confronto_redis.conta(r) == 0 and confronto_redis.occupazione(r)["chiavi"] == 0


def test_misura_con_redis(pg: str, r, tmp_path: Path) -> None:
    rete = Rete(str(tmp_path / "rete"), sync="os")
    with confronto.collega(pg) as conn:
        importa(conn, rete)
        confronto_redis.carica(conn, r)
        senza = misura(conn, rete, quante=20)
        con = misura(conn, rete, quante=20, redis=r)
    assert senza["redis"] is None and all(d["redis"] is None and d["redis_risposte_diverse"] is None for d in senza["domande"])
    assert con["risposte_diverse"] == 0 and con["redis"]["risposte_diverse"] == 0 and con["redis"]["versione"]
    # il prezzo della cache viaggia con il confronto: quanto è durata la preparazione delle risposte che ha letto
    assert con["redis"]["preparazione_s"] == confronto_redis.caricamento(r)["secondi"] > 0
    assert [d["nome"] for d in con["domande"]] == [d["nome"] for d in DOMANDE]
    for d in con["domande"]:
        assert d["redis_risposte_diverse"] == 0 and d["redis"]["p50_us"] > 0 and d["redis"]["p99_us"] >= d["redis"]["p50_us"], d["nome"]
        # il tempo dentro il server di Redis viene dai suoi contatori: c'è, ed è più piccolo di quello che vede il programma
        assert d["redis_nel_server_p50_us"] is not None and 0 <= d["redis_nel_server_p50_us"] < d["redis"]["p50_us"], d["nome"]
        assert d["redis"]["media_us"] > 0 and d["redis_nel_server_media_us"] is not None and 0 <= d["redis_nel_server_media_us"] < d["redis"]["media_us"], d["nome"]
    rete.chiudi()


def test_il_banco_carica_redis_e_si_accorge_se_i_dati_cambiano(cluster, servizio_redis: str, r, tmp_path: Path) -> None:
    dsn = _base(cluster, "con_redis")
    banco = Banco(tmp_path / "banco", dsn, TAGLIE, redis_url=servizio_redis)

    def lancia(nome: str, righe: int | None = None) -> dict:
        assert banco.avvia(nome, righe) is None
        stato = _aspetta(banco.stato)
        banco._re = {"letto": 0.0, "stato": None}
        banco._pg = {"letto": 0.0, "stato": None}
        return banco.stato()

    try:
        s = banco.stato()
        assert s["redis"]["configurato"] and s["redis"]["raggiungibile"] and s["redis"]["caricamento"] is None and not s["redis"]["allineato"]
        assert s["redis"]["via"] == "socket" and s["redis"]["lettore_c"] in (True, False)
        assert banco.avvia("redis") is None  # senza dati in PostgreSQL non c'è niente da preparare
        assert "prima vanno generati i dati" in _aspetta(banco.stato)["lavoro"]["errore"]
        s = lancia("genera", TAGLIE[0])
        assert s["lavoro"]["errore"] is None and not s["redis"]["allineato"]
        # la stima di quanta memoria serve è detta prima di provarci, e qui il tetto basta
        assert s["redis"]["memoria_basta"] is True and 0 < s["redis"]["memoria_stimata"] < 200 * 1024 * 1024
        s = lancia("redis")
        assert s["lavoro"]["errore"] is None and s["redis"]["allineato"] and s["redis"]["chiavi"] > 1000
        # la stima non è lontana dal vero: tra la metà e il doppio di quello che le risposte occupano davvero
        assert 0.5 < s["redis"]["memoria_stimata"] / s["redis"]["memoria_risposte"] < 2
        assert s["redis"]["caricamento"]["generazione"] == s["postgres"]["generazione"] and s["redis"]["memoria_tetto"] == 200 * 1024 * 1024
        lancia("importa")
        s = lancia("misura")
        assert s["lavoro"]["errore"] is None and s["confronto"]["redis"]["risposte_diverse"] == 0
        assert all(d["redis"] is not None and d["redis_comando"].startswith(("LRANGE ", "ZRANGEBYSCORE ")) for d in s["confronto"]["domande"])
        assert s["confronto"]["redis"]["memoria_risposte"] == s["redis"]["memoria_risposte"] > 100_000
        assert s["confronto"]["redis"]["via"] == "socket" and s["confronto"]["redis"]["lettore_c"] == s["redis"]["lettore_c"]
        # nuovi dati in PostgreSQL: le risposte pronte in Redis sono di quelli di prima, e il confronto non le usa più
        s = lancia("genera", TAGLIE[0])
        assert not s["redis"]["allineato"] and s["redis"]["caricamento"] is not None
        lancia("importa")
        s = lancia("misura")
        assert s["confronto"]["redis"] is None and all(d["redis"] is None for d in s["confronto"]["domande"])
        # risposte preparate quando il confronto aveva una domanda in meno: non valgono più, e lo stato dice perché
        assert lancia("redis")["redis"]["allineato"]
        segno = json.loads(r.get(confronto_redis.SEGNO))
        r.set(confronto_redis.SEGNO, json.dumps({**segno, "formato": 1}))
        banco._re = {"letto": 0.0, "stato": None}
        s = banco.stato()
        assert s["redis"]["superate"] and not s["redis"]["allineato"]
        assert lancia("redis")["redis"]["allineato"] and not banco.stato()["redis"]["superate"]
        # «svuota» toglie le risposte, e solo quelle
        r.set("di-un-altro", "resta")
        s = lancia("svuota")
        assert s["lavoro"]["errore"] is None and s["redis"]["caricamento"] is None and s["redis"]["chiavi"] == 0
        assert confronto_redis.conta(r) == 0 and r.get("di-un-altro") == "resta"
    finally:
        banco.chiudi()


def test_redis_non_configurato_o_spento_o_pieno(pg: str, tmp_path: Path, monkeypatch) -> None:
    senza = Banco(tmp_path / "senza", pg, TAGLIE)
    try:
        s = senza.stato()["redis"]
        assert not s["configurato"] and not s["raggiungibile"] and s["errore"] is None
        assert senza.avvia("redis") is None and "non è configurato" in _aspetta(senza.stato)["lavoro"]["errore"]
    finally:
        senza.chiudi()
    spento = Banco(tmp_path / "spento", pg, TAGLIE, redis_url="unix:///tmp/qui-non-c-e-nessuno.sock")
    try:
        s = spento.stato()["redis"]
        assert s["configurato"] and not s["raggiungibile"] and s["errore"]
    finally:
        spento.chiudi()
    # un Redis con troppa poca memoria, di molto: lo si sa prima, e il lavoro lo dice subito con i numeri
    with redis_temporaneo.avvia({"maxmemory": "1200kb"}) as piccolo:
        banco = Banco(tmp_path / "piccolo", pg, TAGLIE, redis_url=f"unix://{piccolo.socket}")
        try:
            s = banco.stato()["redis"]
            assert s["memoria_basta"] is False and s["memoria_stimata"] > 0
            assert banco.avvia("redis") is None
            errore = _aspetta(banco.stato)["lavoro"]["errore"]
            assert "occupano circa" in errore and "non bastano" in errore and "ha finito la memoria" not in errore
        finally:
            banco.chiudi()
    # un Redis a cui la memoria manca di poco: la stima non lo esclude, il caricamento ci prova, finisce la memoria,
    # lo dice in parole e non lascia un caricamento a metà
    monkeypatch.setattr(confronto_redis, "BYTE_PER_RECORD", 1)  # una stima troppo ottimista, che non esclude niente
    with redis_temporaneo.avvia() as piccolo:
        cliente = confronto_redis.collega(f"unix://{piccolo.socket}")
        cliente.config_set("maxmemory", confronto_redis.memoria(cliente)["memoria_usata"] + 200_000)  # il Redis è privato, di questo test
        cliente.close()
        banco = Banco(tmp_path / "quasi", pg, TAGLIE, redis_url=f"unix://{piccolo.socket}")
        try:
            assert banco.stato()["redis"]["memoria_basta"] is True
            assert banco.avvia("redis") is None
            errore = _aspetta(banco.stato)["lavoro"]["errore"]
            assert "ha finito la memoria" in errore
            cliente = confronto_redis.collega(f"unix://{piccolo.socket}")
            assert confronto_redis.conta(cliente) == 0 and confronto_redis.caricamento(cliente) is None
            cliente.close()
        finally:
            banco.chiudi()
