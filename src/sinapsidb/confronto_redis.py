"""Redis nel confronto: le risposte alle domande del confronto, già pronte.

Redis non è un database a cui si fanno domande: è un archivio di chiavi e valori molto veloce. Per metterlo nel
confronto gli si prepara, per ognuna delle domande, la risposta di ogni valore possibile: una chiave per
ogni fattura, per ogni cliente, per ogni coppia cliente e giorno, e così via. È il modo in cui lo si usa accanto a un
database: come una copia delle risposte, da tenere allineata.

Le risposte si preparano leggendo PostgreSQL, con le stesse letture delle domande ma senza il valore cercato: così
quello che Redis restituisce si può confrontare riga per riga con quello che restituisce PostgreSQL. Ogni risposta è
una lista di righe; l'ultima domanda, le fatture sopra un importo, usa invece un insieme ordinato per totale, che è la
struttura di Redis fatta per quello.

Tutte le chiavi hanno lo stesso prefisso: caricare e svuotare toccano solo quelle, perché il Redis può servire anche
ad altro.
"""

from __future__ import annotations

import json
import time
from decimal import Decimal
from typing import Any, Callable

from sinapsidb.confronto import DOMANDE, _testo

try:  # il cliente di Redis è facoltativo: senza, il portale dice che Redis non è disponibile
    import redis
except ImportError:  # pragma: no cover - dipende dall'installazione
    redis = None

PREFISSO = "sinapsidb:prova:"
SEGNO = PREFISSO + "caricamento"  # che cosa c'è dentro: da quali dati di PostgreSQL viene, quante chiavi, quando
SEPARATORE = "\x1f"  # tra i valori di una chiave fatta di due valori (prodotto e magazzino, cliente e giorno)
BLOCCO = 2000  # comandi mandati insieme durante il caricamento
FORMATO = 2  # cambia quando cambiano le domande o la forma delle risposte: quelle caricate con un altro formato vanno rifatte
# Quanta memoria occupano in Redis le risposte pronte, per ogni record dell'azienda di prova: misurato con Redis 8.0 a
# centomila record (92,6 byte) e a un milione (91,7). Serve a dire prima se il tetto di Redis basta.
BYTE_PER_RECORD = 95

# Per ogni domanda, in ordine: i valori che fanno la chiave, e la lettura da PostgreSQL che dà, per tutte le chiavi
# insieme, prima i valori della chiave e poi le colonne della risposta, uguali a quelle della domanda.
# L'ultima non ha una chiave per valore: è l'insieme ordinato, con il numero della fattura e il suo totale.
ORDINATO = None
PRONTE: tuple[tuple[tuple[str, ...] | None, str], ...] = (
    (("fattura",), "SELECT f.numero, c.codice, f.data, f.totale FROM fatture f JOIN clienti c ON c.id = f.cliente_id"),
    (("spedizione",), "SELECT s.numero, o.numero, k.nome, s.data, s.stato FROM spedizioni s JOIN ordini o ON o.id = s.ordine_id"
        " JOIN corrieri k ON k.id = s.corriere_id"),
    (("cliente",), "SELECT c.codice, f.numero, f.totale FROM fatture f JOIN clienti c ON c.id = f.cliente_id"),
    (("fattura",), "SELECT f.numero, p.codice, r.quantita, r.prezzo FROM righe_fattura r JOIN fatture f ON f.id = r.fattura_id"
        " JOIN prodotti p ON p.id = r.prodotto_id"),
    (("fattura",), "SELECT f.numero, p.data, p.importo, p.metodo FROM pagamenti p JOIN fatture f ON f.id = p.fattura_id"),
    (("fornitore",), "SELECT s.codice, p.codice, p.prezzo FROM prodotti p JOIN fornitori s ON s.id = p.fornitore_id"),
    (("prodotto", "magazzino"), "SELECT p.codice, m.nome, g.quantita FROM giacenze g JOIN prodotti p ON p.id = g.prodotto_id JOIN magazzini m ON m.id = g.magazzino_id"),
    (("cliente",), "SELECT c.codice, t.numero, t.priorita FROM ticket t JOIN clienti c ON c.id = t.cliente_id WHERE t.stato = 'aperto'"),
    (("cliente",), "SELECT c.codice, COALESCE(SUM(f.totale), 0)::text FROM clienti c LEFT JOIN fatture f ON f.cliente_id = c.id GROUP BY c.codice"),
    (("ordine",), "SELECT o.numero, f.numero, p.data, p.importo FROM ordini o JOIN fatture f ON f.ordine_id = o.id JOIN pagamenti p ON p.fattura_id = f.id"),
    (("prodotto",), "SELECT DISTINCT p.codice, c.codice FROM righe_fattura r JOIN prodotti p ON p.id = r.prodotto_id JOIN fatture f ON f.id = r.fattura_id"
        " JOIN clienti c ON c.id = f.cliente_id"),
    (("cliente",), "SELECT DISTINCT c.codice, s.codice FROM clienti c JOIN fatture f ON f.cliente_id = c.id JOIN righe_fattura r ON r.fattura_id = f.id"
        " JOIN prodotti p ON p.id = r.prodotto_id JOIN fornitori s ON s.id = p.fornitore_id"),
    (("cliente", "giorno"), "SELECT c.codice, o.data, 'ordine ricevuto', o.numero, '', '' FROM ordini o JOIN clienti c ON c.id = o.cliente_id"
        " UNION ALL SELECT c.codice, f.data, 'fattura emessa', o.numero, f.numero, '' FROM fatture f JOIN clienti c ON c.id = f.cliente_id"
        " JOIN ordini o ON o.id = f.ordine_id"
        " UNION ALL SELECT c.codice, t.data, 'ticket di assistenza', '', '', t.numero FROM ticket t JOIN clienti c ON c.id = t.cliente_id"),
    ((), "SELECT to_char(data, 'YYYY-MM'), SUM(totale) FROM fatture GROUP BY 1"),
    ((), "SELECT mese, trim_scale(totale)::text FROM fatturato_per_mese"),
    (ORDINATO, "SELECT numero, totale FROM fatture"),
)
assert len(PRONTE) == len(DOMANDE)  # ogni domanda del confronto ha la sua risposta pronta: chi ne aggiunge una aggiunge anche questa


class NonDisponibile(RuntimeError):
    """Redis non è configurato, non risponde, oppure manca il suo cliente."""


class MemoriaFinita(RuntimeError):
    """Redis ha finito la memoria che gli è concessa prima di aver preparato tutte le risposte."""


def collega(url: str):
    """Si collega a Redis: ``redis://127.0.0.1:6379/0`` oppure ``unix:///percorso/del/socket``."""
    if redis is None:
        raise NonDisponibile("il cliente Python di Redis non è installato")
    return redis.Redis.from_url(url, decode_responses=True, socket_connect_timeout=3, socket_timeout=120)


def come_collegato(r) -> dict[str, Any]:
    """Come il programma parla con Redis: dal socket locale o da una porta di rete, e se il cliente ha il lettore in C
    (``hiredis``). Tutte e due le cose pesano sui tempi che vede il programma, e il confronto le dichiara."""
    from redis.utils import HIREDIS_AVAILABLE

    return {"via": "socket" if "path" in r.connection_pool.connection_kwargs else "tcp", "lettore_c": bool(HIREDIS_AVAILABLE)}


def chiave(i: int, valori: tuple) -> str:
    return f"{PREFISSO}{i}:{SEPARATORE.join(valori)}"


def centesimi(importo: str) -> int:
    return int((Decimal(importo) * 100).to_integral_value())


def svuota(r) -> int:
    """Toglie da Redis tutte le chiavi del confronto, e solo quelle. Restituisce quante erano."""
    tolte, gruppo = 0, []
    for k in r.scan_iter(match=PREFISSO + "*", count=5000):
        gruppo.append(k)
        if len(gruppo) >= 5000:
            tolte += r.unlink(*gruppo)
            gruppo = []
    if gruppo:
        tolte += r.unlink(*gruppo)
    return tolte


def carica(conn, r, generazione: dict | None = None, avanza: Callable[[str, int], None] | None = None) -> dict[str, Any]:
    """Prepara in Redis le risposte alle domande del confronto, leggendo PostgreSQL. Prima toglie quelle di prima.

    ``conn`` è un collegamento in sola lettura a PostgreSQL. ``generazione`` dice da quali dati vengono le risposte:
    resta scritto in Redis, così si vede se sono ancora quelle dei dati che ci sono in PostgreSQL.
    """
    inizio = time.perf_counter()
    svuota(r)
    prima = _memoria_libera_da_code(r)
    righe = 0
    sfrattate = _sfrattate(r)
    for i, (nomi, sql) in enumerate(PRONTE):
        nome = DOMANDE[i]["nome"]
        tubo = r.pipeline(transaction=False)
        in_attesa = 0
        with conn.transaction(), conn.cursor(name="risposte_pronte") as cur:
            cur.itersize = 5000
            cur.execute(sql)
            for riga in cur:
                testi = [_testo(x) for x in riga]
                if nomi is ORDINATO:  # il numero della fattura, con il totale in centesimi come posto nell'ordine
                    tubo.zadd(chiave(i, ()), {testi[0]: centesimi(testi[1])})
                else:
                    tubo.rpush(chiave(i, tuple(testi[:len(nomi)])), json.dumps(testi[len(nomi):], ensure_ascii=False, separators=(",", ":")))
                righe += 1
                in_attesa += 1
                if in_attesa >= BLOCCO:
                    tubo.execute()
                    in_attesa = 0
                    if avanza:
                        avanza(nome, righe)
        if in_attesa:
            tubo.execute()
        if avanza:
            avanza(nome, righe)
    if _sfrattate(r) != sfrattate:
        # Un Redis che, finita la memoria, butta via chiavi vecchie invece di rifiutare le nuove: le risposte non ci sono tutte
        raise MemoriaFinita("Redis ha buttato via delle chiavi per fare posto (maxmemory-policy diversa da noeviction)")
    # Quante chiavi e quanta memoria in più: si contano una volta sola, qui, e restano scritte nel segno del caricamento
    stato = {"formato": FORMATO, "generazione": generazione, "righe": righe, "chiavi": conta(r), "memoria": max(0, memoria(r)["memoria_usata"] - prima),
             "secondi": round(time.perf_counter() - inizio, 2), "quando": time.time()}
    r.set(SEGNO, json.dumps(stato))
    return {**stato, **memoria(r)}


def memoria_finita(errore: Exception) -> bool:
    """Vero se un caricamento è fallito perché Redis ha finito la memoria che gli è concessa."""
    return isinstance(errore, MemoriaFinita) or (redis is not None and isinstance(errore, redis.exceptions.OutOfMemoryError)) \
        or "OOM" in str(errore) or "maxmemory" in str(errore)


def _sfrattate(r) -> int:
    return int(r.info("stats").get("evicted_keys", 0))


def _memoria_libera_da_code(r) -> int:
    """La memoria usata da Redis dopo che ha finito di liberare quella delle chiavi appena tolte (lo fa in un secondo
    momento): è il punto da cui si misura quanta ne occupano le risposte pronte."""
    for _ in range(100):
        if not int(r.info("memory").get("lazyfree_pending_objects", 0)):
            break
        time.sleep(0.1)
    return memoria(r)["memoria_usata"]


def stima_righe(righe: dict[str, int]) -> int:
    """Quante righe finiscono in Redis, al massimo, dati i conteggi delle tabelle di PostgreSQL: serve a mostrare a
    che punto è il caricamento senza contare prima tutte le risposte."""
    n = righe.get
    return (3 * n("fatture", 0) + n("spedizioni", 0) + 3 * n("righe_fattura", 0) + 2 * n("pagamenti", 0) + n("prodotti", 0) + n("giacenze", 0)
            + n("ticket", 0) + n("clienti", 0) + n("ordini", 0) + n("fatture", 0) + n("ticket", 0))


def stima_memoria(record: int) -> int:
    """Quanti byte occuperanno in Redis, all'incirca, le risposte pronte di un'azienda di prova di ``record`` record."""
    return record * BYTE_PER_RECORD


def comando(i: int) -> str:
    """Il comando di Redis che dà la risposta alla domanda ``i``, scritto con i nomi al posto dei valori."""
    nomi = PRONTE[i][0]
    if nomi is ORDINATO:
        return f"ZRANGEBYSCORE {chiave(i, ())} (<soglia in centesimi> +inf"
    return f"LRANGE {chiave(i, tuple(f'<{n}>' for n in nomi))} 0 -1".replace(SEPARATORE, "\\x1f")


def memoria(r) -> dict[str, int]:
    """Quanta memoria usa Redis in tutto, e il suo tetto (zero: nessun tetto)."""
    m = r.info("memory")
    return {"memoria_usata": int(m.get("used_memory", 0)), "memoria_tetto": int(m.get("maxmemory", 0))}


def conta(r) -> int:
    """Quante chiavi di risposte pronte ci sono, contandole una per una: con molti dati è una passata lunga."""
    return sum(1 for k in r.scan_iter(match=PREFISSO + "*", count=10000) if k != SEGNO)


def occupazione(r) -> dict[str, Any]:
    """Quante chiavi di risposte pronte ci sono, quanta memoria occupano e quanta ne usa Redis in tutto, con il suo
    tetto. Chiavi e memoria delle risposte sono quelle contate alla fine del caricamento: non si ricontano."""
    c = caricamento(r) or {}
    return {"chiavi": int(c.get("chiavi", 0)), "memoria_risposte": int(c.get("memoria", 0)), **memoria(r)}


def caricamento(r) -> dict[str, Any] | None:
    """Che cosa c'è caricato in Redis per il confronto, oppure None."""
    testo = r.get(SEGNO)
    try:
        return json.loads(testo) if testo else None
    except ValueError:
        return None


def chiedi(r, i: int, parametri: tuple) -> list:
    """La risposta pronta alla domanda ``i`` per quei valori, così come Redis la restituisce: un comando solo."""
    nomi = PRONTE[i][0]
    if nomi is ORDINATO:
        return r.zrangebyscore(chiave(i, ()), f"({centesimi(parametri[0])}", "+inf")
    return r.lrange(chiave(i, tuple(parametri[:len(nomi)])), 0, -1)


def righe(i: int, risposta: list) -> list[tuple]:
    """La risposta di Redis come righe, nella stessa forma di quelle di PostgreSQL, per confrontarle."""
    if PRONTE[i][0] is ORDINATO:
        return [(numero,) for numero in risposta]
    return [tuple(json.loads(x)) for x in risposta]


def nel_server(r, i: int) -> tuple[int, int]:
    """Quante volte Redis ha eseguito finora il comando della domanda ``i`` e quanti microsecondi ci ha speso dentro il
    server: sono i suoi contatori. La differenza tra due letture dà il tempo delle chiamate fatte in mezzo."""
    v = r.info("commandstats").get("cmdstat_zrangebyscore" if PRONTE[i][0] is ORDINATO else "cmdstat_lrange")
    return (int(v["calls"]), int(v["usec"])) if v else (0, 0)
