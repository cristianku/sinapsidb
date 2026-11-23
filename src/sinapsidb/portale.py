"""Portale di SinapsiDB: dati, decisioni e confronto.

L'indirizzo ``/`` porta alla pagina delle decisioni (``/decisioni``), l'esempio del fido dei clienti
(vedi ``sinapsidb.fido``). La pagina dei dati (``/dati``) mostra la rete di neuroni e
sinapsi memorizzata, il diario degli eventi e il percorso di una domanda; finché il motore non
è collegato mostra un esempio dichiarato. La pagina del confronto (``/confronto``) genera in
PostgreSQL un'azienda di prova della taglia scelta, la importa in una rete di prova e fa le stesse
domande ai due, misurando i tempi (vedi ``sinapsidb.confronto`` e ``sinapsidb.dati_prova``); se è
collegato un Redis, gli prepara le risposte e fa le stesse domande anche a lui
(``sinapsidb.confronto_redis``). Dalla
stessa pagina si può fare una domanda alla rete di prova, scritta nella lingua di SinapsiDB (vedi
``sinapsidb.lingua``).

Il file SQLite delle metriche (vedi ``sinapsidb.metriche``) e la sonda servono a un server MCP di
SinapsiDB, che non fa parte di questa uscita: la sonda è spenta di serie. Se la si accende, ogni
``SINAPSIDB_SONDA_INTERVALLO`` secondi interroga il server MCP con una prova sintetica
(``server/discover`` più una ``search``) e scrive l'esito nelle metriche, che ``/api/riepilogo`` riassume.

Configurazione da variabili d'ambiente:

- ``SINAPSIDB_PORTALE_BIND`` indirizzo di ascolto, predefinito ``127.0.0.1``
- ``SINAPSIDB_PORTALE_PORT`` porta, predefinita ``8419``
- ``SINAPSIDB_PORTALE_ALLOWED_CLIENTS`` IP sorgente ammessi, predefiniti ``127.0.0.1,::1``
- ``SINAPSIDB_PORTALE_ALLOWED_HOSTS`` nomi ammessi nell'header Host, senza porta, predefiniti
  ``127.0.0.1,localhost,::1``; ``*`` li ammette tutti
- ``SINAPSIDB_METRICHE_DB`` file SQLite delle metriche (obbligatoria); accanto a lui stanno, se non si
  dice altro, le cartelle della rete di prova e della memoria delle decisioni
- ``SINAPSIDB_SONDA_URL`` URL del server MCP da provare, predefinito ``http://127.0.0.1:8417/mcp``
- ``SINAPSIDB_SONDA_INTERVALLO`` secondi tra due prove, predefiniti 0: sonda spenta
- ``SINAPSIDB_PORTALE_LOG_LEVEL`` predefinito ``info``
- ``SINAPSIDB_PROVA_PG`` come collegarsi al PostgreSQL dei dati di prova, predefinito
  ``dbname=sinapsidb_prova`` (socket locale, utente del servizio, nessuna password)
- ``SINAPSIDB_PROVA_DIR`` cartella della rete di prova, predefinita ``prova`` accanto alle metriche
- ``SINAPSIDB_PROVA_TAGLIE`` le taglie dell'azienda di prova che il portale lascia generare, in righe
  totali, predefinite ``100000,300000,500000,1000000``. La rete di prova sta nella memoria del portale:
  circa 430 MB a riposo e 800 di picco per milione di record. Taglie più grandi vanno insieme a un
  tetto di memoria più alto nell'unità

Una variabile presente ma vuota vale come assente. Le prove della sonda portano un gettone
casuale, scritto accanto al file delle metriche: così il server MCP le distingue dal traffico
dei client senza fidarsi di una semplice intestazione.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import sqlite3
import time
import urllib.error
import urllib.request
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

import anyio
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Route
from starlette.types import ASGIApp

from sinapsidb import __version__
from sinapsidb.confronto import TAGLIE, Banco, Occupato
from sinapsidb.fido import Fido
from sinapsidb.lingua import ErroreDomanda
from sinapsidb.metriche import FINESTRE, INTESTAZIONE_SONDA, apri, gettone_sonda, riepilogo
from sinapsidb.rete import FiltroClient, FiltroHost

log = logging.getLogger("sinapsidb.portale")

VERSIONE_MODERNA = "2026-07-28"
RITARDO_PRIMA_PROVA = 3.0  # secondi
INTESTAZIONI_SICUREZZA = {
    "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
}
STATICI = {
    "/portale.css": ("portale.css", "text/css; charset=utf-8"),
    "/icona.svg": ("icona.svg", "image/svg+xml"),
    "/tema.js": ("tema.js", "text/javascript; charset=utf-8"),
    "/dati": ("dati.html", "text/html; charset=utf-8"),
    "/dati.js": ("dati.js", "text/javascript; charset=utf-8"),
    "/dati.css": ("dati.css", "text/css; charset=utf-8"),
    "/esempio-rete.json": ("esempio-rete.json", "application/json"),
    "/confronto": ("confronto.html", "text/html; charset=utf-8"),
    "/confronto.js": ("confronto.js", "text/javascript; charset=utf-8"),
    "/confronto.css": ("confronto.css", "text/css; charset=utf-8"),
    "/decisioni": ("decisioni.html", "text/html; charset=utf-8"),
    "/decisioni.js": ("decisioni.js", "text/javascript; charset=utf-8"),
    "/decisioni.css": ("decisioni.css", "text/css; charset=utf-8"),
}


@dataclass
class ConfigPortale:
    bind: str = "127.0.0.1"
    port: int = 8419
    allowed_clients: list[str] = field(default_factory=lambda: ["127.0.0.1", "::1"])
    allowed_hosts: list[str] = field(default_factory=lambda: ["127.0.0.1", "localhost", "::1"])
    metriche_db: Path = Path("metriche.db")
    sonda_url: str = "http://127.0.0.1:8417/mcp"
    sonda_intervallo: float = 0.0  # spenta: il server MCP non fa parte di questa uscita
    log_level: str = "info"
    prova_pg: str = "dbname=sinapsidb_prova"
    prova_dir: Path | None = None  # None: la cartella «prova» accanto al file delle metriche
    prova_taglie: tuple[int, ...] = TAGLIE
    decisioni_dir: Path | None = None  # None: la cartella «decisioni» accanto al file delle metriche
    prova_redis: str | None = None  # None: Redis non fa parte del confronto
    prova_tetto_blocchi_mb: int = 256  # quanta rete di prova sta in memoria quando la rete è sul disco

    @classmethod
    def da_ambiente(cls) -> ConfigPortale:
        c = cls()
        db = os.environ.get("SINAPSIDB_METRICHE_DB")
        if not db:
            raise SystemExit("SINAPSIDB_METRICHE_DB non impostata: il portale non sa dove leggere le metriche")


        def lista(nome: str, predefinita: list[str]) -> list[str]:
            voci = [x.strip() for x in (os.environ.get(nome) or "").split(",") if x.strip()]
            return voci or predefinita

        # Una variabile presente ma vuota vale come assente: un BIND vuoto farebbe ascoltare
        # su tutte le interfacce un portale che non ha autenticazione propria.
        return cls(
            bind=os.environ.get("SINAPSIDB_PORTALE_BIND") or c.bind,
            port=int(os.environ.get("SINAPSIDB_PORTALE_PORT") or c.port),
            allowed_clients=lista("SINAPSIDB_PORTALE_ALLOWED_CLIENTS", c.allowed_clients),
            allowed_hosts=lista("SINAPSIDB_PORTALE_ALLOWED_HOSTS", c.allowed_hosts),
            metriche_db=Path(db),
            sonda_url=os.environ.get("SINAPSIDB_SONDA_URL") or c.sonda_url,
            sonda_intervallo=float(os.environ.get("SINAPSIDB_SONDA_INTERVALLO") or c.sonda_intervallo),
            log_level=(os.environ.get("SINAPSIDB_PORTALE_LOG_LEVEL") or c.log_level).lower(),
            prova_pg=os.environ.get("SINAPSIDB_PROVA_PG") or c.prova_pg,
            prova_dir=Path(os.environ["SINAPSIDB_PROVA_DIR"]) if os.environ.get("SINAPSIDB_PROVA_DIR") else None,
            prova_taglie=tuple(int(x) for x in lista("SINAPSIDB_PROVA_TAGLIE", [str(t) for t in c.prova_taglie])),
            decisioni_dir=Path(os.environ["SINAPSIDB_DECISIONI_DIR"]) if os.environ.get("SINAPSIDB_DECISIONI_DIR") else None,
            prova_redis=os.environ.get("SINAPSIDB_PROVA_REDIS") or None,
            prova_tetto_blocchi_mb=int(os.environ.get("SINAPSIDB_PROVA_TETTO_BLOCCHI_MB") or c.prova_tetto_blocchi_mb),
        )


# --- sonda sintetica ----------------------------------------------------------


def _post_mcp(
    url: str, metodo: str, params: dict, nome: str | None = None, gettone: str = "", timeout: float = 10
) -> Any:
    meta = {
        "io.modelcontextprotocol/protocolVersion": VERSIONE_MODERNA,
        "io.modelcontextprotocol/clientInfo": {"name": "sinapsidb-sonda", "version": __version__},
        "io.modelcontextprotocol/clientCapabilities": {},
    }
    corpo = {"jsonrpc": "2.0", "id": 1, "method": metodo, "params": {**params, "_meta": meta}}
    intestazioni = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": VERSIONE_MODERNA,
        "Mcp-Method": metodo,
    }
    if gettone:
        intestazioni[INTESTAZIONE_SONDA] = gettone
    if nome:
        intestazioni["Mcp-Name"] = nome
    req = urllib.request.Request(url, data=json.dumps(corpo).encode(), headers=intestazioni, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        testo = r.read().decode("utf-8")
    if testo.lstrip().startswith("{"):
        return json.loads(testo)
    righe = [x[5:].strip() for x in testo.splitlines() if x.startswith("data:") and x[5:].strip()]
    return json.loads(righe[-1]) if righe else {}


def prova(url: str, gettone: str = "") -> tuple[bool, float, str]:
    """Una prova completa: discover a 2026-07-28 e una search. Restituisce (ok, durata_ms, dettaglio).

    Non solleva mai: qualsiasi errore diventa una prova fallita con il suo dettaglio, così il
    giro della sonda non si interrompe e il portale mostra il guasto.
    """
    inizio = time.perf_counter()

    def fallita(dettaglio: str) -> tuple[bool, float, str]:
        return False, (time.perf_counter() - inizio) * 1000, dettaglio[:200]

    try:
        r = _post_mcp(url, "server/discover", {}, gettone=gettone)
        risultato = r.get("result") if isinstance(r, dict) else None
        versioni = risultato.get("supportedVersions") if isinstance(risultato, dict) else None
        if not isinstance(versioni, list) or VERSIONE_MODERNA not in versioni:
            return fallita(f"discover inatteso: {json.dumps(r)[:160]}")
        r = _post_mcp(
            url, "tools/call", {"name": "search", "arguments": {"query": "sinapsidb"}}, nome="search", gettone=gettone
        )
        risultato = r.get("result") if isinstance(r, dict) else None
        if not isinstance(risultato, dict) or risultato.get("isError"):
            return fallita(f"search fallita: {json.dumps(r)[:160]}")
    except urllib.error.HTTPError as e:
        return fallita(f"HTTP {e.code}")
    except Exception as e:  # connessione rifiutata, risposta troncata, JSON non valido, URL sbagliato
        return fallita(f"{type(e).__name__}: {getattr(e, 'reason', e)}")
    return True, (time.perf_counter() - inizio) * 1000, ""


def registra_prova(percorso: Path, ok: bool, durata_ms: float, dettaglio: str, ts: float | None = None) -> None:
    conn = apri(percorso)
    try:
        with conn:
            conn.execute(
                "INSERT INTO sonde (ts, ok, durata_ms, dettaglio) VALUES (?,?,?,?)",
                (time.time() if ts is None else ts, int(ok), round(durata_ms, 3), dettaglio),
            )
    finally:
        conn.close()


async def _ciclo_sonda(config: ConfigPortale) -> None:
    # All'avvio della macchina il portale può partire un attimo prima del server MCP:
    # senza questa attesa ogni riavvio lascerebbe una prova fallita che non dice niente.
    await anyio.sleep(min(RITARDO_PRIMA_PROVA, config.sonda_intervallo))
    while True:
        # Un errore qui non deve fermare il giro: il portale continuerebbe a mostrare
        # l'ultima prova buona senza farne più nessuna.
        try:
            try:
                gettone = await anyio.to_thread.run_sync(gettone_sonda, config.metriche_db)
            except OSError:
                gettone = ""  # senza gettone le prove contano come traffico dei client
                log.warning("gettone della sonda non disponibile in %s", config.metriche_db.parent)
            ok, durata, dettaglio = await anyio.to_thread.run_sync(prova, config.sonda_url, gettone)
            if not ok:
                log.warning("sonda fallita in %.0f ms: %s", durata, dettaglio)
            await anyio.to_thread.run_sync(registra_prova, config.metriche_db, ok, durata, dettaglio)
        except Exception:
            log.exception("giro della sonda fallito")
        await anyio.sleep(config.sonda_intervallo)


# --- app ----------------------------------------------------------------------


def crea_app(config: ConfigPortale) -> ASGIApp:
    statici = {
        percorso: (resources.files("sinapsidb.statici").joinpath(nome).read_bytes(), tipo)
        for percorso, (nome, tipo) in STATICI.items()
    }

    def statico(request: Request) -> Response:
        contenuto, tipo = statici[request.url.path]
        return Response(contenuto, media_type=tipo, headers={**INTESTAZIONI_SICUREZZA, "Cache-Control": "no-cache"})

    def api_riepilogo(request: Request) -> JSONResponse:
        finestra = request.query_params.get("finestra", "24h")
        if finestra not in FINESTRE:
            return JSONResponse({"error": f"finestra non valida, usare una di {list(FINESTRE)}"}, status_code=400)
        try:
            conn = apri(config.metriche_db)
            try:
                dati = riepilogo(conn, finestra)
            finally:
                conn.close()
        except sqlite3.Error as e:
            log.error("lettura delle metriche fallita: %r", e)
            return JSONResponse(
                {"error": "le metriche non sono leggibili in questo momento"},
                status_code=503,
                headers={**INTESTAZIONI_SICUREZZA, "Cache-Control": "no-store"},
            )
        dati["sonda"]["intervallo_s"] = config.sonda_intervallo
        dati["versione_portale"] = __version__
        return JSONResponse(dati, headers={**INTESTAZIONI_SICUREZZA, "Cache-Control": "no-store"})

    def api_rete(_: Request) -> JSONResponse:
        """La rete memorizzata, per la pagina dei dati.

        Il motore non è ancora collegato a questa pagina, quindi la rete è vuota e ``motore`` è
        falso: la pagina lo dice e mostra un esempio dichiarato come tale. Quando lo sarà, qui
        arriveranno i suoi neuroni, le sue sinapsi e i suoi eventi.
        """
        return JSONResponse(
            {"motore": False, "neuroni": {}, "sinapsi": [], "eventi": []},
            headers={**INTESTAZIONI_SICUREZZA, "Cache-Control": "no-store"},
        )

    banco = Banco(config.prova_dir or config.metriche_db.parent / "prova", config.prova_pg, config.prova_taglie, config.prova_redis,
                  tetto_blocchi=max(8, config.prova_tetto_blocchi_mb) << 20)
    senza_cache = {**INTESTAZIONI_SICUREZZA, "Cache-Control": "no-store"}

    def api_confronto(_: Request) -> JSONResponse:
        return JSONResponse(banco.stato(), headers=senza_cache)

    def nostra(request: Request) -> bool:
        """Vero se la richiesta viene dalla nostra pagina: porta la sua intestazione e, se dichiara un'origine, è la nostra."""
        origine = request.headers.get("origin")
        return request.headers.get("x-sinapsidb-azione") == "1" and not (origine and origine.split("://")[-1] != request.headers.get("host"))

    def api_aiuto(_: Request) -> JSONResponse:
        """Che cosa c'è nella rete di prova (tipi di fatto e ruoli) e qualche domanda d'esempio."""
        try:
            return JSONResponse(banco.aiuto(), headers=senza_cache)
        except Occupato as e:
            return JSONResponse({"error": str(e)}, status_code=409, headers=senza_cache)

    async def api_chiedi(request: Request) -> JSONResponse:
        """Una domanda alla rete di prova, scritta nella lingua di SinapsiDB. Legge soltanto."""
        if not nostra(request):
            return JSONResponse({"error": "richiesta non ammessa"}, status_code=403, headers=senza_cache)
        try:
            if int(request.headers.get("content-length") or 0) > 8192:
                raise ValueError("troppo lunga")
            domanda = (await request.json())["domanda"]
            if not isinstance(domanda, str):
                raise ValueError("non è un testo")
            domanda.encode()  # un carattere a metà (un surrogato da solo) non si può nemmeno ripetere nella risposta
        except (ValueError, KeyError, TypeError, RecursionError):
            return JSONResponse({"error": "serve un oggetto JSON con il campo «domanda»"}, status_code=400, headers=senza_cache)
        try:
            return JSONResponse(await anyio.to_thread.run_sync(banco.chiedi, domanda), headers=senza_cache)
        except ErroreDomanda as e:
            return JSONResponse({"error": str(e)}, status_code=400, headers=senza_cache)
        except Occupato as e:
            return JSONResponse({"error": str(e)}, status_code=409, headers=senza_cache)

    def api_lavoro(request: Request) -> JSONResponse:
        """Lancia un lavoro del confronto: genera, azzera, importa, misura, redis, svuota, disco o memoria.

        «genera» riscrive l'azienda di prova in PostgreSQL, nella taglia chiesta con ``?righe=``, e azzera
        la rete di prova. Gli altri in PostgreSQL leggono soltanto: toccano la rete di prova oppure, «redis»
        e «svuota», le sole chiavi del confronto in Redis. «disco» e «memoria» scelgono dove vive la rete di
        prova: sul disco con in memoria solo quello che serve, oppure tutta in memoria.

        Il portale non ha autenticazione propria, quindi una pagina di un altro sito non deve poter
        far partire un lavoro dal browser di chi è collegato: si accettano solo richieste che portano
        l'intestazione scritta dalla nostra pagina e che, se dichiarano un'origine, è la nostra.
        """
        if not nostra(request):
            return JSONResponse({"error": "richiesta non ammessa"}, status_code=403, headers=senza_cache)
        if request.path_params["lavoro"] not in Banco.LAVORI:
            return JSONResponse({"error": "lavoro sconosciuto"}, status_code=404, headers=senza_cache)
        righe = None
        if request.path_params["lavoro"] == "genera":
            try:
                righe = int(request.query_params.get("righe", ""))
            except ValueError:
                righe = -1
            if righe not in banco.taglie:
                return JSONResponse({"error": f"taglia non ammessa: scegliere tra {list(banco.taglie)}"}, status_code=400, headers=senza_cache)
        motivo = banco.avvia(request.path_params["lavoro"], righe)
        if motivo:
            return JSONResponse({"error": motivo}, status_code=409, headers=senza_cache)
        return JSONResponse({"avviato": request.path_params["lavoro"]}, status_code=202, headers=senza_cache)

    # La pagina delle decisioni lavora su una memoria di dimostrazione tutta sua: non è la memoria vera, che il
    # portale non tocca, e non è la rete di prova del confronto. «Azzera» butta via solo quella.
    fido = Fido(config.decisioni_dir or config.metriche_db.parent / "decisioni")
    SCRITTURE_FIDO = ("accorda", "ordine", "pagamento", "storno", "esempio", "azzera")
    LETTURE_FIDO = ("riesamina", "candidate", "verifica")

    def api_decisioni(_: Request) -> JSONResponse:
        """Il fido di ogni cliente adesso e le ultime decisioni della memoria di dimostrazione."""
        quadro = fido.esegui({"op": "quadro"})
        if not quadro.get("ok"):  # la memoria non si apre, o si è appena fermata: lo si dice, non si manda un quadro a metà
            return JSONResponse({"error": quadro.get("errore"), "tipo": quadro.get("tipo")}, status_code=503, headers=senza_cache)
        return JSONResponse({k: v for k, v in quadro.items() if k not in ("id", "ok")}, headers=senza_cache)

    async def api_decisione(request: Request) -> JSONResponse:
        """Un'operazione sulla memoria di dimostrazione: accordare un fido, chiedere un ordine, registrare un pagamento
        o stornarlo, lanciare l'esempio, azzerare; oppure riesaminare una decisione, cercare le decisioni che una
        rettifica tocca, verificare una ricevuta. Le ultime tre non scrivono niente.

        Come per i lavori del confronto, si accettano solo richieste che vengono dalla nostra pagina.
        """
        if not nostra(request):
            return JSONResponse({"error": "richiesta non ammessa"}, status_code=403, headers=senza_cache)
        op = request.path_params["op"]
        if op not in SCRITTURE_FIDO + LETTURE_FIDO:
            return JSONResponse({"error": "operazione sconosciuta"}, status_code=404, headers=senza_cache)
        try:
            if int(request.headers.get("content-length") or 0) > 8192:
                raise ValueError("troppo lunga")
            corpo = await request.json() if int(request.headers.get("content-length") or 0) else {}
            if not isinstance(corpo, dict):
                raise ValueError("non è un oggetto")
            richiesta = {"op": op}
            for campo in ("cliente", "importo", "causale", "vale_da", "vale_fino_a"):  # i testi che la pagina può mandare
                if corpo.get(campo) not in (None, ""):
                    if not isinstance(corpo[campo], (str, int, float)) or isinstance(corpo[campo], bool) or len(str(corpo[campo])) > 80:
                        raise ValueError(campo)
                    richiesta[campo] = str(corpo[campo]).strip()
            def numero(x: object) -> int:  # il numero di un evento: un intero scritto come intero, non «vero» né 1.9 né 1e999
                if type(x) is not int or not 0 <= x < 1 << 62:
                    raise ValueError("numero")
                return x

            for campo in ("evento", "rettifica"):
                if campo in corpo:
                    richiesta[campo] = numero(corpo[campo])
            if "rettifiche" in corpo:
                if not isinstance(corpo["rettifiche"], list) or len(corpo["rettifiche"]) > 50:
                    raise ValueError("rettifiche")
                richiesta["rettifiche"] = [numero(x) for x in corpo["rettifiche"]]
        except (ValueError, TypeError, OverflowError, RecursionError):
            return JSONResponse({"error": "la richiesta non è scritta bene: serve un oggetto JSON con i campi dell'operazione"},
                                status_code=400, headers=senza_cache)
        if op == "candidate":
            richiesta["riesamina"] = True
        risposta = await anyio.to_thread.run_sync(fido.esegui, richiesta)
        if not risposta.pop("ok"):
            return JSONResponse({"error": risposta["errore"], "tipo": risposta.get("tipo")}, status_code=400, headers=senza_cache)
        risposta.pop("id", None)
        return JSONResponse(risposta, headers=senza_cache)

    def healthz(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "version": __version__})

    @contextlib.asynccontextmanager
    async def vita(_: Starlette) -> AsyncIterator[None]:
        apri(config.metriche_db).close()
        try:
            gettone_sonda(config.metriche_db)  # subito, così c'è già quando un server MCP lo cerca
        except OSError:
            log.warning("gettone della sonda non creato in %s", config.metriche_db.parent)
        # La rete di prova si riapre subito e in sottofondo: con dieci milioni di fatti dura un minuto, e nel
        # frattempo il portale risponde e lo stato del confronto dice che cosa sta facendo.
        banco.riapri()
        async with anyio.create_task_group() as tg:
            if config.sonda_intervallo > 0:
                tg.start_soon(_ciclo_sonda, config)
            yield
            tg.cancel_scope.cancel()
        banco.chiudi()
        fido.chiudi()

    def inizio(_: Request) -> Response:
        return RedirectResponse("/decisioni", status_code=307, headers=INTESTAZIONI_SICUREZZA)

    rotte = [Route(p, statico, methods=["GET"]) for p in STATICI]
    rotte += [
        Route("/", inizio, methods=["GET"]),
        Route("/api/riepilogo", api_riepilogo, methods=["GET"]),
        Route("/api/rete", api_rete, methods=["GET"]),
        Route("/api/confronto", api_confronto, methods=["GET"]),
        Route("/api/confronto/aiuto", api_aiuto, methods=["GET"]),
        Route("/api/confronto/chiedi", api_chiedi, methods=["POST"]),
        Route("/api/confronto/{lavoro}", api_lavoro, methods=["POST"]),
        Route("/api/decisioni", api_decisioni, methods=["GET"]),
        Route("/api/decisioni/{op}", api_decisione, methods=["POST"]),
        Route("/healthz", healthz, methods=["GET"]),
    ]
    app: ASGIApp = FiltroHost(Starlette(routes=rotte, lifespan=vita), config.allowed_hosts)
    return FiltroClient(app, config.allowed_clients)


def main() -> None:
    import uvicorn

    config = ConfigPortale.da_ambiente()
    logging.basicConfig(level=config.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log.info(
        "sinapsidb-portale %s su %s:%d; client ammessi %s; host ammessi %s; metriche %s; sonda %s ogni %ss",
        __version__,
        config.bind,
        config.port,
        ",".join(config.allowed_clients),
        ",".join(config.allowed_hosts),
        config.metriche_db,
        config.sonda_url,
        config.sonda_intervallo,
    )
    uvicorn.run(
        crea_app(config),
        host=config.bind,
        port=config.port,
        proxy_headers=False,
        server_header=False,
        log_level=config.log_level,
    )


if __name__ == "__main__":
    main()
