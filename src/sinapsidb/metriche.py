"""Metriche di SinapsiDB: registrazione delle richieste e riepiloghi per il portale.

Il server MCP scrive, il portale legge, attraverso lo stesso file SQLite in modalità WAL.
Si registrano tempi, metodi, nomi degli strumenti ed esiti. Gli argomenti delle chiamate
(per esempio il testo di una ricerca) non vengono mai salvati.

Tabelle:

- ``richieste``: una riga per richiesta HTTP ricevuta dal server MCP.
- ``processo``: campioni periodici di memoria e CPU del processo del server MCP.
- ``sonde``: esito delle prove sintetiche che il portale fa al server MCP.

Accanto al file delle metriche sta ``<file>.sonda``: un gettone casuale creato dal portale.
Le prove del portale lo mandano nell'intestazione ``x-sinapsidb-sonda`` e solo così il server
MCP le tiene fuori dalle statistiche dei client. Un client che manda l'intestazione senza
conoscere il gettone resta un client.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import queue
import secrets
import sqlite3
import threading
import time
from array import array
from collections import deque
from pathlib import Path
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

log = logging.getLogger("sinapsidb.metriche")

SCHEMA = """
CREATE TABLE IF NOT EXISTS richieste (
    ts REAL NOT NULL,
    durata_ms REAL NOT NULL,
    percorso TEXT NOT NULL,
    http_metodo TEXT NOT NULL,
    stato_http INTEGER NOT NULL,
    metodo TEXT NOT NULL DEFAULT '',
    strumento TEXT NOT NULL DEFAULT '',
    versione TEXT NOT NULL DEFAULT '',
    esito TEXT NOT NULL,
    codice_errore INTEGER,
    client TEXT NOT NULL DEFAULT '',
    origine TEXT NOT NULL DEFAULT 'client',
    byte_risposta INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS richieste_ts ON richieste(ts);
CREATE TABLE IF NOT EXISTS processo (
    ts REAL NOT NULL,
    rss_kb INTEGER NOT NULL,
    cpu_pct REAL NOT NULL,
    thread INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS processo_ts ON processo(ts);
CREATE TABLE IF NOT EXISTS sonde (
    ts REAL NOT NULL,
    ok INTEGER NOT NULL,
    durata_ms REAL NOT NULL,
    dettaglio TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS sonde_ts ON sonde(ts);
"""

ESITI = ("ok", "errore_strumento", "errore_rpc", "errore_http", "rifiutata")
INTESTAZIONE_SONDA = "x-sinapsidb-sonda"
MAX_CORPO = 1 << 20  # byte di richiesta e risposta tenuti per capire metodo ed esito
MAX_PERCORSO = 200  # caratteri del percorso HTTP conservati
PRIMO_CAMPIONE = 1.0  # secondi tra l'avvio e il primo campione del processo
# Tetti alle voci distinte di un riepilogo: i nomi li sceglie il client, quindi senza tetto
# una raffica di nomi inventati farebbe crescere la risposta senza limite.
MAX_OPERAZIONI_DISTINTE = 200
MAX_OPERAZIONI_MOSTRATE = 50
MAX_VERSIONI = 50
MAX_IP_RIFIUTATI = 200
ALTRE = "(altre)"


def apri(percorso: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(str(percorso), timeout=5, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(SCHEMA)
    return conn


# --- gettone della sonda ------------------------------------------------------


def file_gettone(db: Path | str) -> Path:
    db = Path(db)
    return db.with_name(db.name + ".sonda")


def gettone_sonda(db: Path | str) -> str:
    """Gettone delle prove del portale: riusa quello che c'è, altrimenti ne crea uno (file 0600)."""
    percorso = file_gettone(db)
    try:
        esistente = percorso.read_text(encoding="ascii").strip()
        if esistente:
            return esistente
    except (OSError, UnicodeDecodeError):
        pass
    nuovo = secrets.token_urlsafe(24)
    percorso.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(percorso, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as f:
        f.write(nuovo + "\n")
    return nuovo


class RiconosciSonda:
    """Dice se un valore dell'intestazione della sonda è il gettone del portale.

    Rilegge il file al più una volta al secondo, e solo quando il valore non coincide con
    quello in memoria: serve dopo un riavvio del portale che abbia cambiato il gettone.
    """

    def __init__(self, db: Path | str) -> None:
        self.percorso = file_gettone(db)
        self._gettone = b""
        self._letto = float("-inf")

    def __call__(self, valore: str) -> bool:
        if not valore:
            return False
        ricevuto = valore.encode("latin-1", "replace")
        if self._gettone and hmac.compare_digest(ricevuto, self._gettone):
            return True
        ora = time.monotonic()
        if ora - self._letto < 1.0:
            return False
        self._letto = ora
        try:
            self._gettone = self.percorso.read_bytes().strip()
        except OSError:
            self._gettone = b""
        return bool(self._gettone) and hmac.compare_digest(ricevuto, self._gettone)


# --- scrittura ----------------------------------------------------------------


class Registro:
    """Scrive le metriche su SQLite da un thread dedicato, senza bloccare le richieste.

    Fa anche il campionamento del processo e la pulizia delle righe più vecchie
    di ``giorni``.
    """

    def __init__(
        self,
        percorso: Path | str,
        giorni: int = 30,
        intervallo_processo: float = 15.0,
        campiona_processo: bool = True,
    ) -> None:
        self.percorso = Path(percorso)
        self.percorso.parent.mkdir(parents=True, exist_ok=True)
        self.giorni = giorni
        self.intervallo_processo = intervallo_processo
        self.e_sonda = RiconosciSonda(self.percorso)
        self._coda: queue.Queue[tuple[str, tuple]] = queue.Queue(maxsize=100_000)
        self._fine = threading.Event()
        apri(self.percorso).close()  # crea lo schema subito, così il portale lo trova
        self._scrittore = threading.Thread(target=self._scrivi, name="metriche-scrittore", daemon=True)
        self._scrittore.start()
        self._campionatore: threading.Thread | None = None
        if campiona_processo:
            self._campionatore = threading.Thread(target=self._campiona, name="metriche-processo", daemon=True)
            self._campionatore.start()

    def _metti(self, sql: str, valori: tuple) -> None:
        try:
            self._coda.put_nowait((sql, valori))
        except queue.Full:
            log.warning("coda delle metriche piena: riga scartata")

    def richiesta(self, **r: Any) -> None:
        self._metti(
            "INSERT INTO richieste (ts, durata_ms, percorso, http_metodo, stato_http, metodo, strumento, versione,"
            " esito, codice_errore, client, origine, byte_risposta) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                r["ts"], r["durata_ms"], r["percorso"], r["http_metodo"], r["stato_http"], r.get("metodo", ""),
                r.get("strumento", ""), r.get("versione", ""), r["esito"], r.get("codice_errore"),
                r.get("client", ""), r.get("origine", "client"), r.get("byte_risposta", 0),
            ),
        )

    def processo(self, ts: float, rss_kb: int, cpu_pct: float, thread: int) -> None:
        self._metti("INSERT INTO processo (ts, rss_kb, cpu_pct, thread) VALUES (?,?,?,?)", (ts, rss_kb, cpu_pct, thread))

    def scrittore_vivo(self) -> bool:
        return self._scrittore.is_alive()

    def svuota(self, attesa: float = 5.0) -> None:
        """Aspetta che la coda sia scritta (per i test e allo spegnimento)."""
        limite = time.monotonic() + attesa
        while not self._coda.empty() and time.monotonic() < limite:
            time.sleep(0.01)
        time.sleep(0.05)

    def chiudi(self) -> None:
        self.svuota()
        self._fine.set()

    def _scrivi(self) -> None:
        conn = apri(self.percorso)
        ultima_pulizia = 0.0
        while not self._fine.is_set():
            try:
                primo = self._coda.get(timeout=0.5)
            except queue.Empty:
                primo = None
            lotto = [primo] if primo else []
            while len(lotto) < 1000:
                try:
                    lotto.append(self._coda.get_nowait())
                except queue.Empty:
                    break
            # Qualsiasi errore resta dentro il giro: il thread non deve morire, altrimenti
            # le metriche si fermano in silenzio fino al riavvio del servizio.
            try:
                if lotto:
                    scartate = 0
                    primo_errore: Exception | None = None
                    with conn:
                        for sql, valori in lotto:
                            try:
                                conn.execute(sql, valori)
                            except Exception as e:  # una riga sbagliata non butta via le altre
                                scartate += 1
                                primo_errore = primo_errore or e
                    if scartate:
                        log.error("metriche: %d righe scartate su %d: %r", scartate, len(lotto), primo_errore)
                if time.time() - ultima_pulizia > 3600:
                    ultima_pulizia = time.time()
                    soglia = ultima_pulizia - self.giorni * 86400
                    with conn:
                        for tabella in ("richieste", "processo", "sonde"):
                            conn.execute(f"DELETE FROM {tabella} WHERE ts < ?", (soglia,))
            except Exception:
                log.exception("scrittura delle metriche fallita")
        conn.close()

    def _campiona(self) -> None:
        tick = os.sysconf("SC_CLK_TCK")
        precedente: tuple[float, float] | None = None
        attesa = 0.0
        while not self._fine.wait(attesa):
            try:
                campione = _leggi_proc()
            except (OSError, ValueError, IndexError):
                log.warning("campioni del processo non disponibili: /proc non leggibile")
                return
            if campione is None:
                return
            rss_kb, cpu_tick, thread = campione
            ora = time.monotonic()
            cpu_s = cpu_tick / tick
            if precedente is None:
                # La CPU è una differenza: il primo campione si scrive dopo un secondo, poi a regime.
                attesa = min(PRIMO_CAMPIONE, self.intervallo_processo)
            else:
                trascorso = ora - precedente[0]
                pct = 100.0 * (cpu_s - precedente[1]) / trascorso if trascorso > 0 else 0.0
                self.processo(time.time(), rss_kb, round(pct, 2), thread)
                attesa = self.intervallo_processo
            precedente = (ora, cpu_s)


def _leggi_proc() -> tuple[int, int, int] | None:
    stat = Path("/proc/self/stat")
    if not stat.exists():
        return None
    campi = stat.read_text().rsplit(")", 1)[1].split()
    # campi[0] è lo stato (campo 3 di proc(5)): utime e stime sono i campi 14 e 15, i thread il 20.
    utime, stime, thread = int(campi[11]), int(campi[12]), int(campi[17])
    rss_kb = 0
    for riga in Path("/proc/self/status").read_text().splitlines():
        if riga.startswith("VmRSS:"):
            rss_kb = int(riga.split()[1])
            break
    return rss_kb, utime + stime, thread


# --- middleware ---------------------------------------------------------------


def _intestazione(scope: Scope, nome: str) -> str:
    nome_b = nome.encode()
    for k, v in scope.get("headers", []):
        if k.lower() == nome_b:
            return v.decode("latin-1")
    return ""


def _json(corpo: bytes, tipo: str) -> Any:
    if not corpo:
        return None
    try:
        if "text/event-stream" in tipo:
            dati = [r[5:].strip() for r in corpo.decode("utf-8", "replace").splitlines() if r.startswith("data:")]
            for d in reversed(dati):
                if d:
                    return json.loads(d)
            return None
        return json.loads(corpo)
    except (ValueError, UnicodeDecodeError, RecursionError):
        return None


def _testo(valore: Any, limite: int) -> str:
    """Stringa sicura da scrivere: solo se il valore è una stringa, senza caratteri non codificabili.

    Un oggetto o una lista al posto di un nome non vengono convertiti in testo: conterrebbero
    pezzi degli argomenti, che non si registrano mai.
    """
    if not isinstance(valore, str):
        return ""
    return valore.encode("utf-8", "replace").decode("utf-8")[:limite]


def classifica(stato_http: int, risposta: Any) -> tuple[str, int | None]:
    """Esito di una richiesta a partire dallo stato HTTP e dal corpo JSON-RPC."""
    if stato_http in (403, 421):
        return "rifiutata", None
    if stato_http >= 400:
        codice = None
        if isinstance(risposta, dict) and isinstance(risposta.get("error"), dict):
            codice = risposta["error"].get("code")
        return "errore_http", codice
    if isinstance(risposta, dict):
        if isinstance(risposta.get("error"), dict):
            return "errore_rpc", risposta["error"].get("code")
        risultato = risposta.get("result")
        if isinstance(risultato, dict) and risultato.get("isError") is True:
            return "errore_strumento", None
    return "ok", None


class MetricheASGI:
    """Middleware ASGI che misura ogni richiesta HTTP e la passa al registro."""

    def __init__(self, app: ASGIApp, registro: Registro) -> None:
        self.app = app
        self.registro = registro

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        inizio = time.perf_counter()
        ts = time.time()
        corpo_rich = bytearray()
        corpo_risp = bytearray()
        stato = {"http": 0, "tipo": "", "byte": 0, "registrata": False}

        async def ricevi() -> Message:
            msg = await receive()
            if msg["type"] == "http.request" and len(corpo_rich) < MAX_CORPO:
                corpo_rich.extend(msg.get("body", b"")[: MAX_CORPO - len(corpo_rich)])
            return msg

        def registra() -> None:
            if stato["registrata"]:
                return
            stato["registrata"] = True
            self._registra(scope, ts, inizio, bytes(corpo_rich), bytes(corpo_risp), stato)

        async def invia(msg: Message) -> None:
            if msg["type"] == "http.response.start":
                stato["http"] = msg["status"]
                for k, v in msg.get("headers", []):
                    if k.lower() == b"content-type":
                        stato["tipo"] = v.decode("latin-1")
            elif msg["type"] == "http.response.body":
                pezzo = msg.get("body", b"")
                stato["byte"] += len(pezzo)
                if len(corpo_risp) < MAX_CORPO:
                    corpo_risp.extend(pezzo[: MAX_CORPO - len(corpo_risp)])
            await send(msg)
            if msg["type"] == "http.response.body" and not msg.get("more_body", False):
                registra()

        try:
            await self.app(scope, ricevi, invia)
        finally:
            if not stato["http"]:
                stato["http"] = 500
            registra()

    def _registra(self, scope: Scope, ts: float, inizio: float, rich: bytes, risp: bytes, stato: dict) -> None:
        try:
            durata_ms = (time.perf_counter() - inizio) * 1000
            corpo = _json(rich, "application/json")
            corpo = corpo if isinstance(corpo, dict) else {}
            params = corpo.get("params") if isinstance(corpo.get("params"), dict) else {}
            metodo = _testo(_intestazione(scope, "mcp-method") or corpo.get("method"), 100)
            strumento = ""
            if metodo == "tools/call":
                strumento = _testo(_intestazione(scope, "mcp-name") or params.get("name"), 100)
            versione = _testo(_intestazione(scope, "mcp-protocol-version") or params.get("protocolVersion"), 20)
            esito, codice = classifica(stato["http"], _json(risp, stato["tipo"]))
            client = scope.get("client")
            self.registro.richiesta(
                ts=ts,
                durata_ms=round(durata_ms, 3),
                percorso=_testo(scope.get("path", ""), MAX_PERCORSO),
                http_metodo=_testo(scope.get("method", ""), 20),
                stato_http=stato["http"],
                metodo=metodo,
                strumento=strumento,
                versione=versione,
                esito=esito,
                codice_errore=codice if isinstance(codice, int) and not isinstance(codice, bool) else None,
                client=_testo(client[0], 100) if client else "",
                origine="sonda" if self.registro.e_sonda(_intestazione(scope, INTESTAZIONE_SONDA)) else "client",
                byte_risposta=stato["byte"],
            )
        except Exception:  # le metriche non devono mai rompere una richiesta
            log.exception("registrazione della richiesta fallita")


# --- lettura e riepiloghi -----------------------------------------------------

FINESTRE = {  # nome: (durata in secondi, ampiezza di un intervallo del grafico)
    "1h": (3600, 60),
    "24h": (86400, 1800),
    "7d": (7 * 86400, 3 * 3600),
    "30d": (30 * 86400, 12 * 3600),
}


def _percentile_ordinato(v: list[float], p: float) -> float | None:
    if not v:
        return None
    if len(v) == 1:
        return v[0]
    k = (len(v) - 1) * p / 100
    basso = int(k)
    alto = min(basso + 1, len(v) - 1)
    return v[basso] + (v[alto] - v[basso]) * (k - basso)


def percentile(valori: Any, p: float) -> float | None:
    """Percentile con interpolazione lineare (come numpy 'linear'); None se non ci sono valori."""
    return _percentile_ordinato(sorted(valori), p)


def _arrotonda(x: float | None, cifre: int = 1) -> float | None:
    return None if x is None else round(x, cifre)


def _statistiche(durate: Any, errori: int) -> dict[str, Any]:
    v = sorted(durate)
    n = len(v)
    return {
        "richieste": n,
        "errori": errori,
        "tasso_errori": round(errori / n, 4) if n else None,
        "p50_ms": _arrotonda(_percentile_ordinato(v, 50)),
        "p95_ms": _arrotonda(_percentile_ordinato(v, 95)),
        "p99_ms": _arrotonda(_percentile_ordinato(v, 99)),
        "max_ms": _arrotonda(v[-1] if v else None),
    }


def _dizionari(cursore: sqlite3.Cursor) -> list[dict[str, Any]]:
    nomi = [c[0] for c in cursore.description]
    return [dict(zip(nomi, riga)) for riga in cursore]


def _conta(mappa: dict[str, int], chiave: str, tetto: int) -> None:
    if chiave not in mappa and len(mappa) >= tetto:
        chiave = ALTRE
    mappa[chiave] = mappa.get(chiave, 0) + 1


def riepilogo(conn: sqlite3.Connection, finestra: str = "24h", ora: float | None = None) -> dict[str, Any]:
    """Tutto quello che mostra il portale, per la finestra di tempo scelta.

    Le righe si leggono una alla volta e delle durate si tengono solo i numeri, in array
    compatti: una finestra con milioni di richieste costa decine di megabyte, non gigabyte.
    Le richieste MCP sono le POST su /mcp non rifiutate: una GET che riceve 405 resta
    visibile negli esiti e nelle ultime richieste, ma non conta tra le operazioni.
    """
    if finestra not in FINESTRE:
        raise ValueError(f"finestra sconosciuta: {finestra}")
    durata, passo = FINESTRE[finestra]
    ora = time.time() if ora is None else ora
    fine = (int(ora // passo) + 1) * passo  # gli intervalli sono allineati al passo
    inizio = fine - durata
    n_int = durata // passo
    conn.row_factory = None

    # richieste dei client
    durate_tot = array("d")
    errori_tot = 0
    durate_int = [array("d") for _ in range(n_int)]
    errori_int = [0] * n_int
    per_op: dict[str, list[Any]] = {}
    versioni: dict[str, int] = {}
    esiti = dict.fromkeys(ESITI, 0)
    per_ip: dict[str, int] = {}
    n_rifiutate = 0
    cursore = conn.execute(
        "SELECT ts, durata_ms, percorso, http_metodo, metodo, strumento, versione, esito, client"
        " FROM richieste WHERE ts >= ? AND origine = 'client'",
        (inizio,),
    )
    for ts, durata_ms, percorso, http_metodo, metodo, strumento, versione, esito, client in cursore:
        if esito in esiti:
            esiti[esito] += 1
        if esito == "rifiutata":
            n_rifiutate += 1
            _conta(per_ip, client, MAX_IP_RIFIUTATI)
            continue
        if http_metodo != "POST" or percorso.rstrip("/") != "/mcp":
            continue
        errore = esito != "ok"
        durate_tot.append(durata_ms)
        errori_tot += errore
        i = int((ts - inizio) // passo)
        if 0 <= i < n_int:
            durate_int[i].append(durata_ms)
            errori_int[i] += errore
        chiave = f"tools/call {strumento}" if metodo == "tools/call" else (metodo or "(senza metodo)")
        if chiave not in per_op and len(per_op) >= MAX_OPERAZIONI_DISTINTE:
            chiave = ALTRE
        voce = per_op.get(chiave)
        if voce is None:
            voce = per_op[chiave] = [array("d"), 0]
        voce[0].append(durata_ms)
        voce[1] += errore
        _conta(versioni, versione or "(nessuna)", MAX_VERSIONI)

    intervalli: list[dict[str, Any]] = []
    for i in range(n_int):
        v = sorted(durate_int[i])
        intervalli.append(
            {
                "t": inizio + i * passo,
                "richieste": len(v),
                "errori": errori_int[i],
                "p50_ms": _arrotonda(_percentile_ordinato(v, 50)),
                "p95_ms": _arrotonda(_percentile_ordinato(v, 95)),
            }
        )
    operazioni = sorted(
        ({"operazione": k, **_statistiche(d, e)} for k, (d, e) in per_op.items()),
        key=lambda x: -x["richieste"],
    )

    ultime = _dizionari(
        conn.execute(
            "SELECT ts, durata_ms, percorso, http_metodo, stato_http, metodo, strumento, versione, esito,"
            " codice_errore, client FROM richieste WHERE origine = 'client' ORDER BY ts DESC LIMIT 50"
        )
    )

    # processo del server MCP
    rss_max = [0] * n_int
    cpu_somma = [0.0] * n_int
    cpu_n = [0] * n_int
    ultimo_proc: dict[str, Any] | None = None
    for ts, rss_kb, cpu_pct, thread in conn.execute(
        "SELECT ts, rss_kb, cpu_pct, thread FROM processo WHERE ts >= ? ORDER BY ts", (inizio,)
    ):
        ultimo_proc = {"ts": ts, "rss_kb": rss_kb, "cpu_pct": cpu_pct, "thread": thread}
        i = int((ts - inizio) // passo)
        if 0 <= i < n_int:
            rss_max[i] = max(rss_max[i], rss_kb)
            cpu_somma[i] += cpu_pct
            cpu_n[i] += 1
    proc_int = [
        {
            "t": inizio + i * passo,
            "rss_mb": round(rss_max[i] / 1024, 1) if cpu_n[i] else None,
            "cpu_pct": round(cpu_somma[i] / cpu_n[i], 2) if cpu_n[i] else None,
        }
        for i in range(n_int)
    ]

    # sonda del portale
    prove_int = [0] * n_int
    fallite_int = [0] * n_int
    ok_int = [array("d") for _ in range(n_int)]
    prove = 0
    fallite = 0
    ultime_fallite: deque[dict[str, Any]] = deque(maxlen=20)
    for ts, ok, durata_ms, dettaglio in conn.execute(
        "SELECT ts, ok, durata_ms, dettaglio FROM sonde WHERE ts >= ? ORDER BY ts", (inizio,)
    ):
        prove += 1
        if not ok:
            fallite += 1
            ultime_fallite.append({"ts": ts, "ok": ok, "durata_ms": durata_ms, "dettaglio": dettaglio})
        i = int((ts - inizio) // passo)
        if 0 <= i < n_int:
            prove_int[i] += 1
            if ok:
                ok_int[i].append(durata_ms)
            else:
                fallite_int[i] += 1
    sonde_int = [
        {
            "t": inizio + i * passo,
            "prove": prove_int[i],
            "fallite": fallite_int[i],
            "p50_ms": _arrotonda(percentile(ok_int[i], 50)),
        }
        for i in range(n_int)
    ]
    ultima_sonda = _dizionari(conn.execute("SELECT ts, ok, durata_ms, dettaglio FROM sonde ORDER BY ts DESC LIMIT 1"))

    return {
        "finestra": finestra,
        "inizio": inizio,
        "fine": fine,
        "passo": passo,
        "generato": ora,
        "totale": _statistiche(durate_tot, errori_tot),
        "rifiutate": n_rifiutate,
        "rifiutate_per_ip": [{"client": k, "richieste": v} for k, v in sorted(per_ip.items(), key=lambda x: -x[1])],
        "esiti": esiti,
        "versioni": [{"versione": k, "richieste": v} for k, v in sorted(versioni.items(), key=lambda x: -x[1])],
        "intervalli": intervalli,
        "operazioni": operazioni[:MAX_OPERAZIONI_MOSTRATE],
        "operazioni_distinte": len(operazioni),
        "ultime": ultime,
        "processo": {"intervalli": proc_int, "ultimo": ultimo_proc},
        "sonda": {
            "intervalli": sonde_int,
            "prove": prove,
            "fallite": fallite,
            "disponibilita": round((prove - fallite) / prove, 4) if prove else None,
            "ultima": ultima_sonda[0] if ultima_sonda else None,
            "ultime_fallite": list(ultime_fallite),
        },
    }
