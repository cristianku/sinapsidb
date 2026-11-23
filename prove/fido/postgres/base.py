"""Baseline PostgreSQL della prova; usata ANCHE dall'adattatore con cache.

Nessun import dal giudice o da SinapsiDB. Una connessione persistente per
richiedente, ed una transazione PostgreSQL per operazione sul socket.
"""

from __future__ import annotations

import re
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import psycopg
from psycopg import errors
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

PARAMETRI = {"fsync": "on", "synchronous_commit": "on", "full_page_writes": "on",
             "autovacuum": "on", "shared_buffers": "256MB", "work_mem": "4MB", "max_connections": "32"}
OPERAZIONI = {
    "accorda": ("cliente", "importo", "atto"), "pagamento": ("cliente", "importo", "causale"),
    "ordine": ("cliente", "importo", "numero"), "sposta": ("cliente", "importo", "causale"),
    "storno": ("causale",),
}


class Incompleto(ValueError):
    pass


def collega(dsn):
    # Il cluster privato ha un socket e una base espliciti. Mai i default di libpq.
    parametri = conninfo_to_dict(dsn)
    host = parametri.get("host", "")
    if not host.startswith("/") or "," in host or parametri.get("hostaddr") or parametri.get("dbname") != "prova":
        raise ValueError("la prova richiede il DSN esplicito del PostgreSQL privato: socket assoluto, dbname=prova")
    return psycopg.connect(dsn, autocommit=True, row_factory=dict_row,
                           options="-c search_path=prova_fido,pg_catalog -c timezone=UTC -c client_encoding=UTF8")


class Database:
    """`con_cache=True` attiva l'outbox. Non cambia né la regola né chi conferma."""

    def __init__(self, dsn, *, con_cache=False):
        self.dsn = dsn
        with collega(dsn) as db, db.transaction():
            for k in ("fsync", "synchronous_commit", "full_page_writes"):
                if db.execute("SELECT current_setting(%s) AS v", (k,)).fetchone()["v"] != "on":
                    raise ValueError(f"la prova richiede {k}=on")
            db.execute("SELECT pg_advisory_xact_lock(4912,0)")
            if not db.execute("SELECT to_regnamespace('prova_fido') IS NOT NULL AS esiste").fetchone()["esiste"]:
                db.execute(Path(__file__).with_name("schema.sql").read_text(), prepare=False)
                db.execute("UPDATE testa SET coda_cache=%s", (con_cache,))
            elif db.execute("SELECT coda_cache FROM testa").fetchone()["coda_cache"] != con_cache:
                raise ValueError("database già inizializzato per l'altro schieramento; usarne uno distinto")

    @contextmanager
    def sessione(self):
        with collega(self.dsn) as db:
            yield Sessione(db)

    def chiudi(self):
        """Le connessioni sono di proprietà delle singole conversazioni."""


def _numero(x):
    if type(x) is int:
        return x
    if isinstance(x, str) and re.fullmatch(r"(?:evento:)?[0-9]+", x):
        return int(x.removeprefix("evento:"))
    raise ValueError(f"identificativo di evento non valido: {x!r}")


def _richiesta(r):
    op = r["op"]
    q = {"op": op}
    for k in ("chiave", *OPERAZIONI[op]):
        v = r[k]
        if not isinstance(v, str) or not v.strip():
            raise ValueError(f"{k} deve essere un testo non vuoto")
        q[k] = v
    if "importo" in q and not re.fullmatch(r"-?0*[0-9]{1,30}(?:\.[0-9]{1,6})?", q["importo"]):
        raise ValueError("importo fuori dal dominio del carico")
    if op in ("accorda", "pagamento", "sposta"):
        for k in ("vale_da", "vale_fino_a"):
            if k in r:
                datetime.fromisoformat(r[k])
                q[k] = r[k]
    return q


class Sessione:
    """API per connessione. `proiezione` e `revisione` servono al lato Redis."""

    def __init__(self, db):
        self.db = db

    def una(self, sql, *args):
        r = self.db.execute(sql, args).fetchone()
        if r is None:
            raise ValueError("evento assente")
        return r

    def proiezione(self, cliente, t):
        """Saldo, revisione, formula e [da,fine) dallo stesso snapshot. NULL = nessun limite."""
        return self.una("SELECT proiezione(%s,%s) AS x", cliente, t)["x"]

    def revisione(self, cliente):
        """Barriera di freschezza di una lettura Redis: solo metadati, senza calcolare il saldo."""
        return self.una("""SELECT coalesce(c.revisione,1) AS revisione, 0 AS formula, g.ultimo AS aggiornato_a
                           FROM testa g LEFT JOIN clienti c ON c.cliente=%s""", cliente)

    def fido(self, q, t):
        p = self.proiezione(q["cliente"], t)
        return {k: p[k] for k in ("valore", "aggiornato_a", "valido_al")}

    def _evento(self, e):
        return self.una("SELECT evento,arrivo,scritto,esito,letture,ritira FROM eventi WHERE evento=%s", e)

    def _esame(self, e, scelte):
        return self.una("""SELECT valuta(evento-1,%s::bigint[],arrivo,scritto,nuovo,ritira) AS x
                           FROM eventi WHERE evento=%s""", scelte, e)["x"]

    def verifica(self, e):
        x = self._evento(e)
        atteso = self._esame(e, [])
        differenze = [k for k in ("esito", "letture") if atteso[k] != x[k]]
        return {"evento": f"evento:{e}", "tipo": "scrittura" if x["esito"] == "applicato" else "rifiuto",
                "esito": "guasto" if differenze else "coerente", "differenze": differenze, "forma": ["sql-1"]}

    def riesamina(self, e, scelte=None, fino_a=None):
        x = self._evento(e)
        if scelte is not None and fino_a is not None:
            raise ValueError("indicare rettifiche oppure fino_a")
        if fino_a is not None:
            if not e <= fino_a <= self.una("SELECT ultimo FROM testa")["ultimo"]:
                raise ValueError("frontiera inesistente o anteriore alla decisione")
            scelte = [r["evento"] for r in self.db.execute("""SELECT evento FROM eventi WHERE evento>%s AND evento<=%s
                                                              AND esito='applicato' ORDER BY evento""", (e, fino_a))]
        scelte = sorted({_numero(i) for i in scelte or []})
        if scelte and (scelte[0] <= e or self.una("""SELECT count(*) AS n FROM eventi
                           WHERE evento=ANY(%s) AND esito='applicato'""", scelte)["n"] != len(scelte)):
            raise ValueError("una rettifica deve essere una scrittura successiva")
        allora = {k: x[k] for k in ("esito", "letture")}
        if allora != self._esame(e, []):
            raise ValueError(f"la ricevuta dell'evento {e} non coincide con la sua base")
        manca = self.db.execute("""SELECT evento,ritira FROM eventi WHERE evento=ANY(%s) AND ritira>=%s
                                   AND NOT ritira=ANY(%s) ORDER BY evento LIMIT 1""", (scelte, e, scelte)).fetchone()
        if manca:
            raise Incompleto(f"l'evento {manca['evento']} ritira il fatto dell'evento escluso {manca['ritira']}")
        fuori = x["ritira"] is not None and self.una(
            "SELECT coalesce(morto=ANY(%s),false) AS ritirato FROM fatti WHERE nato=%s", scelte, x["ritira"])["ritirato"]
        dopo = {"esito": "non_applicabile", "letture": []} if fuori else self._esame(e, scelte)
        lette = {(v["quando"], v["su"], v["con_proposta"]) for v in allora["letture"]}
        return {"evento": f"evento:{e}", "base": f"evento:{e-1}", "istante": x["arrivo"], "scritto": x["scritto"],
                "modo": "per decisione", "forma": "sql-1", "allora": allora, "ipotetico": dopo,
                "cambia": allora["esito"] != dopo["esito"], "eventi_usati": [f"evento:{i}" for i in scelte],
                "eventi_esclusi": [], "regole": [["fido non superato", 1]] if lette or dopo["letture"] else [],
                "letture_in_piu": [v for v in dopo["letture"] if (v["quando"], v["su"], v["con_proposta"]) not in lette]}

    def candidate(self, e, riesamina):
        if self._evento(e)["esito"] != "applicato":
            raise ValueError("un rifiuto non è una rettifica")
        # Tutti e due gli ambiti: vecchio e nuovo cliente, anche se è cambiata solo la validità.
        toccate = """SELECT cliente,da,fine FROM fatti WHERE nato=%s OR morto=%s"""
        righe = self.db.execute(f"""SELECT DISTINCT d.evento FROM ({toccate}) f JOIN dipendenze d
                                    ON d.cliente=f.cliente AND d.al>=f.da AND d.al<f.fine
                                    WHERE d.evento<%s ORDER BY d.evento""", (e, e, e)).fetchall()
        esito = {"rettifica": f"evento:{e}", "candidate": [f"evento:{r['evento']}" for r in righe], "quante": len(righe),
                 "entita_toccate": self.una(f"SELECT count(DISTINCT cliente) AS n FROM ({toccate}) f", e, e)["n"],
                 "copertura": {"completa": True, "eventi_senza_letture": 0}}
        if riesamina:
            mancanti, cambiate, guaste = [], [], []
            for r in righe:
                n = r["evento"]
                try:
                    v = self.riesamina(n, [e])
                except Incompleto as ex:
                    mancanti.append({"evento": f"evento:{n}", "perche": str(ex)})
                except ValueError as ex:
                    guaste.append({"evento": f"evento:{n}", "perche": str(ex)})
                else:
                    if v["cambia"]:
                        cambiate.append({"evento": v["evento"], "allora": v["allora"]["esito"], "ipotetico": v["ipotetico"]["esito"]})
            esito.update(riesaminate=len(righe)-len(mancanti)-len(guaste), esiti_cambiati=cambiate,
                         scenari_incompleti=mancanti, non_riesaminabili=guaste)
        return esito

    def stato(self, q):
        if "chiave" in q:
            r = self.db.execute("SELECT ricevuta(e) AS x FROM eventi e WHERE chiave=%s", (q["chiave"],)).fetchone()
            return {"chiave": q["chiave"], "presente": r is not None, **(r["x"] if r else {})}
        return self.una("""SELECT ultimo AS ultimo_evento, ultimo+1 AS eventi, (SELECT count(*) FROM fatti) AS fatti,
                                  jsonb_build_object('formato',formato,'dal',dal) AS segno,
                                  CASE WHEN EXISTS (SELECT 1 FROM clienti c WHERE
                                      c.saldo<>(SELECT saldo FROM fotografia(c.cliente,c.da)))
                                      THEN 'guasto' ELSE 'coerente' END AS circuiti FROM testa""")

    def esegui(self, q):
        arrivo = time.time()
        id_r = {"id": q.get("id"), **({"id_tentativo": q["id_tentativo"]} if "id_tentativo" in q else {})} if isinstance(q, dict) else {"id": None}
        try:
            op = q["op"]
            for tentativo in range(4):
                try:
                    with self.db.transaction():
                        if op in OPERAZIONI:
                            v = self.una("SELECT scrivi(%s,%s) AS x", Jsonb(_richiesta(q)), arrivo)["x"]
                        elif op == "fido":
                            v = self.fido(q, arrivo)
                        else:
                            self.db.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
                            if op == "stato":
                                v = self.stato(q)
                            elif op == "riesamina":
                                v = self.riesamina(_numero(q["evento"]), q.get("rettifiche"),
                                                   _numero(q["fino_a"]) if "fino_a" in q else None)
                            elif op == "verifica":
                                v = self.verifica(_numero(q["evento"]))
                            elif op == "candidate":
                                v = self.candidate(_numero(q["rettifica"]), q.get("riesamina", False))
                            else:
                                raise ValueError("operazione non riconosciuta")
                    # L'ACK esce solo DOPO il COMMIT della transazione.
                    return {**id_r, "ok": True, **v}
                except (errors.DeadlockDetected, errors.SerializationFailure):
                    if tentativo == 3:
                        raise
        except Incompleto as ex:
            return {**id_r, "ok": False, "tipo": "scenario_incompleto", "errore": str(ex)}
        except (ValueError, KeyError, TypeError, psycopg.DataError) as ex:
            return {**id_r, "ok": False, "tipo": "richiesta", "errore": str(ex)}
        except psycopg.Error as ex:
            # Una perdita della connessione attorno al COMMIT non prova un
            # rollback. Il richiedente deve risolverla con stato(chiave).
            incerto = not ex.sqlstate or ex.sqlstate.startswith(("08", "57P"))
            return {**id_r, "ok": False, "tipo": "indeterminato" if incerto else "guasto",
                    "errore": f"errore PostgreSQL ({ex.sqlstate or 'connessione'})"}
