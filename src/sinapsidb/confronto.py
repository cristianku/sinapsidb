"""Confronto tra PostgreSQL e SinapsiDB sugli stessi dati: l'azienda di prova.

In PostgreSQL c'è un'azienda inventata di venti tabelle (vedi ``dati_prova``), generata dal portale
nella taglia che si sceglie. Da lì i dati si importano in una rete di SinapsiDB tenuta a parte, la
«rete di prova», che si può azzerare senza toccare altro. Il confronto fa le stesse domande ai due e
misura i tempi.

In PostgreSQL scrive un solo lavoro, la generazione dei dati. Tutto il resto (lo stato, l'importazione,
il confronto) si collega in sola lettura.

Come si legge il confronto. SinapsiDB risponde dentro lo stesso processo che fa la domanda;
PostgreSQL è un servizio a parte e la domanda gli arriva dal socket locale. Per questo di
PostgreSQL si riportano due tempi: quello totale visto dal programma e quello della sola
esecuzione dentro il server. Le risposte dei due vengono confrontate una per una: un tempo vale
solo se le risposte sono uguali.
"""

from __future__ import annotations

import contextlib
import fcntl
import gc
import json
import logging
import os
import shutil
import threading
import time
from collections import namedtuple
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

from sinapsidb import dati_prova, lingua, nucleo
from sinapsidb.motore import Rete

try:  # il driver di PostgreSQL è facoltativo: senza, il portale dice che il confronto non è disponibile
    import psycopg
except ImportError:  # pragma: no cover - dipende dall'installazione
    psycopg = None

log = logging.getLogger("sinapsidb.confronto")

TABELLE = tuple(dati_prova.TABELLE)
ORIGINE = "importazione da PostgreSQL"
# Le taglie che il portale lascia generare se non gli si dice altro. I dati di prova arrivano al milione di
# record: con meno il confronto con PostgreSQL dice poco. Stanno nel tetto di memoria di serie dell'unità
# (1200 MB): con un milione di record il portale misurato arriva a 800 MB. Taglie più grandi si aggiungono con
# SINAPSIDB_PROVA_TAGLIE, alzando il tetto: è una scelta sulla memoria della macchina.
TAGLIE = (100_000, 300_000, 500_000, 1_000_000)
TAGLIA_PROPOSTA = 1_000_000  # quella che la pagina seleziona finché non se ne è generata un'altra


def collega(dsn: str, scrittura: bool = False):
    """Una connessione a PostgreSQL. In sola lettura, salvo quella del lavoro che genera i dati.

    Il testo si chiede sempre in UTF-8. Un cluster creato su una macchina senza lingua configurata ha la
    codifica SQL_ASCII, e senza questa richiesta il driver restituirebbe byte al posto delle stringhe.
    Chi legge non aspetta più di tre secondi una tabella bloccata: mentre i dati vengono rigenerati lo
    stato del portale deve rispondere lo stesso.
    """
    if psycopg is None:
        raise RuntimeError("il driver psycopg non è installato")
    opzioni = "-c default_transaction_read_only=off" if scrittura else "-c default_transaction_read_only=on -c lock_timeout=3000"
    return psycopg.connect(dsn, autocommit=True, connect_timeout=5, client_encoding="utf8", options=opzioni)


def _testo(x: Any) -> str:
    if type(x) is str:
        return x
    if isinstance(x, Decimal):
        return f"{x:.2f}"
    if isinstance(x, date):
        return x.isoformat()
    return str(x)


def _punti(n: int) -> str:
    return f"{n:,}".replace(",", ".")


# --- come ogni tabella diventa fatti ------------------------------------------------
# Per ogni tabella: il tipo di fatto, i ruoli dei partecipanti, la frase con cui la riga viene raccontata e
# la lettura da PostgreSQL. Nella rete una cosa è il suo codice o il suo numero, non la chiave numerica della
# tabella: per questo le letture passano dalle giunzioni. I movimenti di magazzino usano il ruolo «giorno» e
# non «quando»: sono tanti, e così la rete non spende memoria per collegarli anche a coppie (partecipante, giorno).

Fatto = namedtuple("Fatto", "tabella tipo ruoli frase sql")

FATTI = (
    Fatto("categorie", "scheda categoria", ("categoria", "reparto"), "{0} è una categoria del reparto {1}",
          "SELECT nome, reparto FROM categorie ORDER BY id"),
    Fatto("fornitori", "scheda fornitore", ("fornitore", "nome", "città", "paese"), "{1} ({0}) è un fornitore di {2}, {3}",
          "SELECT codice, ragione_sociale, citta, paese FROM fornitori ORDER BY id"),
    Fatto("prodotti", "scheda prodotto", ("prodotto", "nome", "categoria", "fornitore", "prezzo", "costo"),
          "{1} ({0}) è un prodotto della categoria {2}, del fornitore {3}: prezzo {4}, costo {5}",
          "SELECT p.codice, p.nome, c.nome, s.codice, p.prezzo, p.costo FROM prodotti p JOIN categorie c ON c.id = p.categoria_id"
          " JOIN fornitori s ON s.id = p.fornitore_id ORDER BY p.id"),
    Fatto("magazzini", "scheda magazzino", ("magazzino", "città"), "Il {0} è a {1}", "SELECT nome, citta FROM magazzini ORDER BY id"),
    Fatto("giacenze", "giacenza", ("prodotto", "magazzino", "quantità"), "Nel {1} ci sono {2} pezzi di {0}",
          "SELECT p.codice, m.nome, g.quantita FROM giacenze g JOIN prodotti p ON p.id = g.prodotto_id"
          " JOIN magazzini m ON m.id = g.magazzino_id ORDER BY g.prodotto_id, g.magazzino_id"),
    Fatto("agenti", "scheda agente", ("agente", "nome", "zona"), "{1} ({0}) è un agente della zona {2}",
          "SELECT codice, nome, zona FROM agenti ORDER BY id"),
    Fatto("clienti", "scheda cliente", ("cliente", "nome", "città", "provincia", "settore", "agente"),
          "{1} ({0}) è un cliente di {2} ({3}), {4}, seguito dall'agente {5}",
          "SELECT c.codice, c.ragione_sociale, c.citta, c.provincia, c.settore, a.codice FROM clienti c"
          " JOIN agenti a ON a.id = c.agente_id ORDER BY c.id"),
    Fatto("dipendenti", "scheda dipendente", ("dipendente", "nome", "reparto"), "{1} ({0}) lavora nel reparto {2}",
          "SELECT matricola, nome, reparto FROM dipendenti ORDER BY id"),
    Fatto("corrieri", "scheda corriere", ("corriere", "giorni"), "{0} consegna in {1} giorni", "SELECT nome, giorni FROM corrieri ORDER BY id"),
    Fatto("ordini", "ordine ricevuto", ("ordine", "cliente", "quando", "stato"), "Ordine {0} di {1} del {2}, {3}",
          "SELECT o.numero, c.codice, o.data, o.stato FROM ordini o JOIN clienti c ON c.id = o.cliente_id ORDER BY o.id"),
    Fatto("righe_ordine", "riga d'ordine", ("ordine", "prodotto", "quantità", "prezzo"), "L'ordine {0} chiede {2} pezzi di {1} a {3}",
          "SELECT o.numero, p.codice, r.quantita, r.prezzo FROM righe_ordine r JOIN ordini o ON o.id = r.ordine_id"
          " JOIN prodotti p ON p.id = r.prodotto_id ORDER BY r.id"),
    Fatto("spedizioni", "spedizione partita", ("spedizione", "ordine", "magazzino", "corriere", "quando", "stato"),
          "Spedizione {0} dell'ordine {1} dal {2} con {3} il {4}, {5}",
          "SELECT s.numero, o.numero, m.nome, k.nome, s.data, s.stato FROM spedizioni s JOIN ordini o ON o.id = s.ordine_id"
          " JOIN magazzini m ON m.id = s.magazzino_id JOIN corrieri k ON k.id = s.corriere_id ORDER BY s.id"),
    Fatto("fatture", "fattura emessa", ("fattura", "cliente", "ordine", "quando", "totale"), "Fattura {0} a {1} per l'ordine {2} del {3}, totale {4}",
          "SELECT f.numero, c.codice, o.numero, f.data, f.totale FROM fatture f JOIN clienti c ON c.id = f.cliente_id"
          " JOIN ordini o ON o.id = f.ordine_id ORDER BY f.id"),
    Fatto("righe_fattura", "riga di fattura", ("fattura", "prodotto", "quantità", "prezzo"), "La fattura {0} contiene {2} pezzi di {1} a {3}",
          "SELECT f.numero, p.codice, r.quantita, r.prezzo FROM righe_fattura r JOIN fatture f ON f.id = r.fattura_id"
          " JOIN prodotti p ON p.id = r.prodotto_id ORDER BY r.id"),
    Fatto("pagamenti", "pagamento incassato", ("fattura", "quando", "importo", "metodo"), "Pagata la fattura {0} il {1}: {2} con {3}",
          "SELECT f.numero, p.data, p.importo, p.metodo FROM pagamenti p JOIN fatture f ON f.id = p.fattura_id ORDER BY p.id"),
    Fatto("resi", "reso ricevuto", ("reso", "fattura", "prodotto", "quantità", "motivo", "quando"),
          "Reso {0} sulla fattura {1}: {3} pezzi di {2}, {4}, il {5}",
          "SELECT r.numero, f.numero, p.codice, r.quantita, r.motivo, r.data FROM resi r JOIN fatture f ON f.id = r.fattura_id"
          " JOIN prodotti p ON p.id = r.prodotto_id ORDER BY r.id"),
    Fatto("ordini_acquisto", "ordine d'acquisto emesso", ("ordine d'acquisto", "fornitore", "magazzino", "quando", "totale"),
          "Ordine d'acquisto {0} a {1} per il {2} del {3}, totale {4}",
          "SELECT a.numero, s.codice, m.nome, a.data, a.totale FROM ordini_acquisto a JOIN fornitori s ON s.id = a.fornitore_id"
          " JOIN magazzini m ON m.id = a.magazzino_id ORDER BY a.id"),
    Fatto("righe_acquisto", "riga d'acquisto", ("ordine d'acquisto", "prodotto", "quantità", "costo"),
          "L'ordine d'acquisto {0} chiede {2} pezzi di {1} a {3}",
          "SELECT a.numero, p.codice, r.quantita, r.costo FROM righe_acquisto r JOIN ordini_acquisto a ON a.id = r.ordine_acquisto_id"
          " JOIN prodotti p ON p.id = r.prodotto_id ORDER BY r.id"),
    Fatto("movimenti", "movimento di magazzino", ("prodotto", "magazzino", "movimento", "quantità", "giorno", "documento"),
          "Nel {1} {2} di {3} pezzi di {0} il {4}, documento {5}",
          "SELECT p.codice, m.nome, v.tipo, v.quantita, v.data, v.documento FROM movimenti v JOIN prodotti p ON p.id = v.prodotto_id"
          " JOIN magazzini m ON m.id = v.magazzino_id ORDER BY v.id"),
    Fatto("ticket", "ticket di assistenza", ("ticket", "cliente", "prodotto", "dipendente", "quando", "stato", "priorità"),
          "Ticket {0} di {1} su {2}, seguito da {3}, aperto il {4}: {5}, priorità {6}",
          "SELECT t.numero, c.codice, p.codice, d.matricola, t.data, t.stato, t.priorita FROM ticket t JOIN clienti c ON c.id = t.cliente_id"
          " JOIN prodotti p ON p.id = t.prodotto_id JOIN dipendenti d ON d.id = t.dipendente_id ORDER BY t.id"),
)
assert tuple(f.tabella for f in FATTI) == TABELLE  # ogni tabella diventa fatti, nessuna esclusa


class Vocabolario:
    """I neuroni dei tipi di fatto e dei ruoli dell'azienda di prova."""

    def __init__(self, rete: Rete) -> None:
        self.rete = rete
        # Tipi e ruoli nascono per primi: i loro identificativi devono restare piccoli (vedi BIT nel motore).
        self.tipo = {f.tabella: rete.n(f.tipo) for f in FATTI}
        self.ruolo: dict[str, int] = {}
        for f in FATTI:
            for r in f.ruoli:
                if r not in self.ruolo:
                    self.ruolo[r] = rete.n(r)


# --- importazione ---------------------------------------------------------------


def importa(conn, rete: Rete, avanza: Callable[[int, str], None] | None = None) -> dict[str, Any]:
    """Legge le tabelle di PostgreSQL e racconta ogni riga alla rete come un fatto, con la sua frase.

    Le righe arrivano a blocchi da un cursore del server: non stanno mai tutte in memoria.
    ``avanza`` riceve i fatti scritti finora e la tabella in corso.
    """
    if rete.cartella:
        # Prima di preparare qualsiasi cosa: l'importazione scrive a lotti, e una rete che registra le decisioni non si
        # scrive a lotti. Se il rifiuto arrivasse dopo, nella rete sbagliata resterebbero il circuito e il vocabolario.
        rete._ammette_i_lotti()
    somma_pronta(rete)  # prima dei fatti: così ogni fattura aggiorna il suo mese mentre viene raccontata
    v = Vocabolario(rete)
    n = rete.n
    inizio = time.perf_counter()
    righe: dict[str, int] = {}
    fatti = 0

    # I nomi che tornano spesso (un cliente, una data, un prodotto) non si cercano ogni volta nella rete: l'importazione
    # si ricorda il neurone degli ultimi che ha visto. Vale finché i neuroni li fa nascere solo lei: dopo aver chiamato
    # chi segue l'avanzamento, che è codice di un altro, guarda se nella rete è nato qualcosa e nel caso dimentica
    # tutto, perché un omonimo nato lì renderebbe ambiguo un nome che lei ricorda. Ogni tanto si svuota comunque, per
    # non tenere in memoria un nome per record.
    visti: dict[str, int] = {}
    ricordati = 200_000
    nomi = rete.nomi

    def avvisa(fatti: int, tabella: str) -> None:
        quanti = len(nomi)
        avanza(fatti, tabella)
        if len(nomi) != quanti:
            visti.clear()

    def dalla_tabella(cur, tabella: str, tipo: int, ruoli: list[int], frase) -> Any:
        """Le righe di una tabella come le vuole ``racconta_molti``: la frase, il tipo, i legami. Ogni tanto dice a che punto è."""
        trova = visti.get
        for i, riga in enumerate(cur, 1):
            testi = [_testo(x) for x in riga]
            legami = []
            for ruolo, testo in zip(ruoli, testi):
                neurone = trova(testo)
                if neurone is None:
                    neurone = visti[testo] = n(testo)
                legami.append((ruolo, neurone))
            yield frase(*testi), tipo, legami
            if i % 5000 == 0:
                if len(visti) > ricordati:
                    visti.clear()
                if avanza:
                    avvisa(fatti + i, tabella)

    # La rete di prova si può sempre reimportare: gli eventi vanno nel giornale a gruppi, non uno alla volta.
    with rete.a_lotti() if rete.cartella else contextlib.nullcontext():
        for fatto in FATTI:
            with conn.transaction(), conn.cursor(name="importazione") as cur:
                cur.itersize = 5000
                cur.execute(fatto.sql)
                i = rete.racconta_molti(ORIGINE, dalla_tabella(cur, fatto.tabella, v.tipo[fatto.tabella], [v.ruolo[r] for r in fatto.ruoli], fatto.frase.format))
            righe[fatto.tabella] = i
            fatti += i
            if avanza:
                avvisa(fatti, fatto.tabella)
    return {"righe_lette": righe, "secondi": round(time.perf_counter() - inizio, 2), **conteggi(rete)}


# --- le domande del confronto ----------------------------------------------------
# Le stesse domande nelle due lingue: SQL per PostgreSQL, la lingua di SinapsiDB (vedi ``lingua``) per la rete.
# Dove c'è «?» va un valore preso dai dati. Le ultime due non hanno un neurone da cui partire.

CIRCUITO_FATTURATO = "fatturato per mese"  # il circuito che tiene pronto il fatturato di ogni mese nella rete di prova


def somma_pronta(rete: Rete) -> bool:
    """Scrive nella rete di prova il circuito che tiene pronto il fatturato di ogni mese, se non c'è già. Dice se l'ha
    scritto. Da quel momento ogni fattura raccontata aggiorna la somma del suo mese nella stessa scrittura; se le
    fatture ci sono già, la somma parte da loro."""
    if any(c["nome"] == CIRCUITO_FATTURATO for c in rete.circuiti):
        return False
    n = rete.n
    rete.proponi_circuito("Il fatturato di ogni mese è la somma dei totali delle fatture emesse in quel mese", ORIGINE, CIRCUITO_FATTURATO,
                          [{"osserva": n("fattura emessa"), "lega": n("quando"), "somma": n("totale"), "peso": 1, "per": "mese"}])
    return True


def conteggi(rete) -> dict[str, int]:
    """I conteggi della rete di prova senza i fatti che definiscono la somma pronta: così «fatti» resta quello che è
    per chi guarda, uno per ogni record importato. Vale per la rete in memoria e per quella letta dal disco."""
    c = rete.conteggi()
    schema = lingua._Schema(rete)
    per_nome = {rete.nomi[t]: t for t in schema.ruoli}
    circuiti = schema.quanti(per_nome["circuito"]) if "circuito" in per_nome else 0
    definizioni = circuiti + (schema.quanti(per_nome["termine"]) if "termine" in per_nome else 0)
    return {**c, "fatti": c["fatti"] - definizioni, "fatti_in_vigore": c["fatti_in_vigore"] - definizioni, "eventi": c["eventi"] - circuiti}


DOMANDE: tuple[dict[str, Any], ...] = (
    {"nome": "Una fattura dal numero", "cosa": "cliente, data e totale di una fattura", "parametri": "fattura",
     "lingua": "accendi fattura = ? in fattura emessa leggi cliente, quando, totale",
     "sql": "SELECT c.codice, f.data, f.totale FROM fatture f JOIN clienti c ON c.id = f.cliente_id WHERE f.numero = %s"},
    {"nome": "Una spedizione dal numero", "cosa": "ordine, corriere, data e stato di una spedizione", "parametri": "spedizione",
     "lingua": "accendi spedizione = ? in spedizione partita leggi ordine, corriere, quando, stato",
     "sql": "SELECT o.numero, k.nome, s.data, s.stato FROM spedizioni s JOIN ordini o ON o.id = s.ordine_id"
            " JOIN corrieri k ON k.id = s.corriere_id WHERE s.numero = %s"},
    {"nome": "Le fatture di un cliente", "cosa": "numero e totale di tutte le fatture di un cliente", "parametri": "cliente",
     "lingua": "accendi cliente = ? in fattura emessa leggi fattura, totale",
     "sql": "SELECT f.numero, f.totale FROM fatture f JOIN clienti c ON c.id = f.cliente_id WHERE c.codice = %s"},
    {"nome": "Le righe di una fattura", "cosa": "prodotto, quantità e prezzo di ogni riga", "parametri": "fattura",
     "lingua": "accendi fattura = ? in riga di fattura leggi prodotto, quantità, prezzo",
     "sql": "SELECT p.codice, r.quantita, r.prezzo FROM righe_fattura r JOIN fatture f ON f.id = r.fattura_id"
            " JOIN prodotti p ON p.id = r.prodotto_id WHERE f.numero = %s"},
    {"nome": "I pagamenti di una fattura", "cosa": "data, importo e metodo di ogni pagamento", "parametri": "fattura",
     "lingua": "accendi fattura = ? in pagamento incassato leggi quando, importo, metodo",
     "sql": "SELECT p.data, p.importo, p.metodo FROM pagamenti p JOIN fatture f ON f.id = p.fattura_id WHERE f.numero = %s"},
    {"nome": "I prodotti di un fornitore", "cosa": "codice e prezzo dei prodotti comprati da un fornitore", "parametri": "fornitore",
     "lingua": "accendi fornitore = ? in scheda prodotto leggi prodotto, prezzo",
     "sql": "SELECT p.codice, p.prezzo FROM prodotti p JOIN fornitori s ON s.id = p.fornitore_id WHERE s.codice = %s"},
    {"nome": "Giacenza di un prodotto in un magazzino", "cosa": "due indizi insieme: prodotto e magazzino", "parametri": "giacenza",
     "lingua": "accendi prodotto = ? e magazzino = ? in giacenza leggi quantità",
     "sql": "SELECT g.quantita FROM giacenze g JOIN prodotti p ON p.id = g.prodotto_id JOIN magazzini m ON m.id = g.magazzino_id"
            " WHERE p.codice = %s AND m.nome = %s"},
    {"nome": "I ticket aperti di un cliente", "cosa": "due indizi insieme: il cliente e lo stato «aperto»", "parametri": "cliente_con_ticket",
     "lingua": "accendi cliente = ? e stato = aperto in ticket di assistenza leggi ticket, priorità",
     "sql": "SELECT t.numero, t.priorita FROM ticket t JOIN clienti c ON c.id = t.cliente_id WHERE c.codice = %s AND t.stato = 'aperto'"},
    {"nome": "Fatturato di un cliente", "cosa": "somma dei totali delle fatture di un cliente", "parametri": "cliente",
     "lingua": "accendi cliente = ? in fattura emessa somma totale",
     "sql": "SELECT COALESCE(SUM(f.totale), 0)::text FROM fatture f JOIN clienti c ON c.id = f.cliente_id WHERE c.codice = %s"},
    {"nome": "I pagamenti di un ordine", "cosa": "due passi: dall'ordine alle sue fatture, dalle fatture ai pagamenti", "parametri": "ordine",
     "lingua": "accendi ordine = ? in fattura emessa leggi fattura poi in pagamento incassato leggi fattura, quando, importo",
     "sql": "SELECT f.numero, p.data, p.importo FROM ordini o JOIN fatture f ON f.ordine_id = o.id JOIN pagamenti p ON p.fattura_id = f.id"
            " WHERE o.numero = %s"},
    {"nome": "Chi ha comprato un prodotto", "cosa": "tre passi: dal prodotto alle righe, alle fatture, ai clienti", "parametri": "prodotto",
     "lingua": "accendi prodotto = ? in riga di fattura leggi fattura poi in fattura emessa leggi distinti cliente",
     "sql": "SELECT DISTINCT c.codice FROM righe_fattura r JOIN prodotti p ON p.id = r.prodotto_id JOIN fatture f ON f.id = r.fattura_id"
            " JOIN clienti c ON c.id = f.cliente_id WHERE p.codice = %s"},
    {"nome": "I fornitori dietro agli acquisti di un cliente", "cosa": "quattro passi: cliente, fatture, righe, prodotti, fornitori",
     "parametri": "cliente",
     "lingua": "accendi cliente = ? in fattura emessa leggi fattura poi in riga di fattura leggi prodotto"
               " poi in scheda prodotto leggi distinti fornitore",
     "sql": "SELECT DISTINCT s.codice FROM clienti c JOIN fatture f ON f.cliente_id = c.id JOIN righe_fattura r ON r.fattura_id = f.id"
            " JOIN prodotti p ON p.id = r.prodotto_id JOIN fornitori s ON s.id = p.fornitore_id WHERE c.codice = %s"},
    {"nome": "Un cliente in un giorno", "cosa": "ordini, fatture e ticket di un cliente in una data: tre tabelle insieme",
     "parametri": "cliente_giorno", "sql_parametri": lambda p: p * 3,
     "lingua": "accendi cliente = ? e quando = ? leggi tipo, ordine, fattura, ticket",
     "sql": "SELECT 'ordine ricevuto', o.numero, '', '' FROM ordini o JOIN clienti c ON c.id = o.cliente_id WHERE c.codice = %s AND o.data = %s"
            " UNION ALL SELECT 'fattura emessa', o.numero, f.numero, '' FROM fatture f JOIN clienti c ON c.id = f.cliente_id"
            " JOIN ordini o ON o.id = f.ordine_id WHERE c.codice = %s AND f.data = %s"
            " UNION ALL SELECT 'ticket di assistenza', '', '', t.numero FROM ticket t JOIN clienti c ON c.id = t.cliente_id"
            " WHERE c.codice = %s AND t.data = %s"},
    {"nome": "Fatturato mese per mese", "cosa": "somma di tutte le fatture, raggruppate per mese, calcolata al momento", "parametri": "nessuno",
     "senza_partenza": True,
     "lingua": "in fattura emessa somma totale per mese(quando)",
     "sql": "SELECT to_char(data, 'YYYY-MM'), SUM(totale) FROM fatture GROUP BY 1"},
    # La stessa somma, ma tenuta pronta: in SinapsiDB un circuito aggiornato dalla scrittura di ogni fattura, in
    # PostgreSQL una vista materializzata, in Redis la solita risposta preparata. Nessuno dei tre calcola.
    {"nome": "Fatturato mese per mese, tenuto pronto", "cosa": "la stessa somma, tenuta pronta invece che calcolata al momento",
     "parametri": "nessuno", "pronta": True,
     "lingua": f"circuito {CIRCUITO_FATTURATO}",
     "sql": f"SELECT mese, trim_scale(totale)::text FROM {dati_prova.VISTA_FATTURATO}"},
    {"nome": "Fatture sopra un importo", "cosa": "i numeri delle fatture con totale maggiore di una soglia", "parametri": "soglia",
     "senza_partenza": True,
     "lingua": "in fattura emessa dove totale > ? leggi fattura",
     "sql": "SELECT numero FROM fatture WHERE totale > %s"},
)


def domande(rete: Rete) -> list[dict[str, Any]]:
    """Le domande del confronto pronte per quella rete: ``sinapsi`` esegue la domanda preparata nella lingua
    di SinapsiDB e restituisce le righe nella stessa forma delle righe SQL, già come testo. ``numero`` è il posto
    della domanda in ``DOMANDE``.

    Una domanda che legge una somma tenuta pronta si può fare solo a una rete che la tiene: la rete letta dal disco
    non ne ha. In quel caso ``sinapsi`` è None e ``perche`` dice perché: la domanda resta fuori dal confronto,
    dichiarata, invece di farlo fallire."""
    pronte = []
    for numero, d in enumerate(DOMANDE):
        try:
            pronte.append({**d, "numero": numero, "sinapsi": lingua.prepara(rete, d["lingua"]).esegui})
        except lingua.ErroreDomanda as e:
            if not d.get("pronta"):
                raise
            pronte.append({**d, "numero": numero, "sinapsi": None, "perche": str(e)})
    return pronte


def esempi(rete: Rete) -> list[dict[str, str]]:
    """Domande d'esempio per la pagina, scritte con valori che nella rete ci sono davvero."""
    schema = lingua._Schema(rete)
    if not schema.ruoli:
        return []  # la rete è vuota: non c'è niente su cui fare domande
    nomi = rete.nomi
    tipi = {nomi[t]: t for t in schema.ruoli}
    ruoli = {nomi[r]: r for r in schema.tipi}

    def primo(tipo: str, ruolo: str, salta: int = 0) -> str | None:
        """Il nome di un neurone che ha quel ruolo in quel tipo di fatto."""
        if tipo not in tipi or ruolo not in ruoli:
            return None
        chiavi = iter(schema.linea(tipi[tipo], ruoli[ruolo]))
        for _ in range(salta):
            next(chiavi, None)
        n = next(chiavi, None)
        return None if n is None else lingua._virgolette(nomi[n])

    out = []

    def aggiungi(titolo: str, domanda: str, *valori: str | None) -> None:
        if all(v is not None for v in valori):
            out.append({"titolo": titolo, "domanda": domanda.format(*valori)})

    cliente, fattura = primo("fattura emessa", "cliente", 3), primo("fattura emessa", "fattura", 20)
    aggiungi("Le fatture di un cliente", "accendi cliente = {} in fattura emessa leggi fattura, quando, totale", cliente)
    aggiungi("Le righe di una fattura, con la frase", "accendi fattura = {} in riga di fattura leggi prodotto, quantità, prezzo, frase", fattura)
    aggiungi("Due indizi: giacenza di un prodotto in un magazzino", "accendi prodotto = {} e magazzino = {} in giacenza leggi quantità",
             primo("giacenza", "prodotto"), primo("giacenza", "magazzino"))
    if "ticket di assistenza" in tipi and "aperto" in rete.per_nome:
        aperti = schema.linea(tipi["ticket di assistenza"], ruoli["stato"]).get(rete.per_nome["aperto"][0])
        for f in lingua._fatti(aperti)[:1]:
            aggiungi("Due indizi: i ticket aperti di un cliente",
                     "accendi cliente = {} e stato = aperto in ticket di assistenza leggi ticket, prodotto, priorità",
                     lingua._virgolette(nomi[rete.leggi(f, ruoli["cliente"])]))
    aggiungi("Quanto ha fatturato un cliente", "accendi cliente = {} in fattura emessa somma totale", cliente)
    aggiungi("Quattro passi: i fornitori dietro agli acquisti di un cliente",
             "accendi cliente = {} in fattura emessa leggi fattura poi in riga di fattura leggi prodotto poi in scheda prodotto leggi distinti fornitore",
             cliente)
    if "ordine ricevuto" in tipi:
        for f in lingua._fatti(next(iter(schema.linea(tipi["ordine ricevuto"], ruoli["ordine"]).values()), None))[:1]:
            aggiungi("Un cliente in un giorno, in qualsiasi tipo di fatto", "accendi cliente = {} e quando = {} leggi tipo, frase",
                     lingua._virgolette(nomi[rete.leggi(f, ruoli["cliente"])]), lingua._virgolette(nomi[rete.leggi(f, ruoli["quando"])]))
    if "ordine ricevuto" in tipi:
        aggiungi("Quanti ordini per stato", "in ordine ricevuto conta per stato")
    if "fattura emessa" in tipi:
        aggiungi("Fatturato mese per mese", "in fattura emessa somma totale per mese(quando)")
        aggiungi("Le fatture sopra 5000", "in fattura emessa dove totale > 5000 leggi fattura, cliente, totale")
    if "cliente" in ruoli and "fornitore" in ruoli:
        aggiungi("Come si arriva da un cliente ai fornitori", "percorsi da cliente a fornitore")
    aggiungi("Che cosa c'è nella rete", "vocabolario")
    return out


def campioni(conn, quanti: int) -> dict[str, list[tuple]]:
    """Valori presi a caso dai dati, da usare come parametri: gli stessi per PostgreSQL e per SinapsiDB."""
    conn.execute("SELECT setseed(0.417)")  # la stessa scelta a ogni lancio sugli stessi dati

    def prendi(sql: str, k: int = quanti) -> list[tuple]:
        return [tuple(_testo(x) for x in riga) for riga in conn.execute(sql + " ORDER BY random() LIMIT %s", (k,)).fetchall()]

    pochi = max(5, quanti // 20)
    soglie = conn.execute("SELECT percentile_disc(ARRAY[0.9, 0.95, 0.99, 0.995, 0.999]) WITHIN GROUP (ORDER BY totale) FROM fatture").fetchone()[0]
    return {
        "fattura": prendi("SELECT numero FROM fatture"),
        "spedizione": prendi("SELECT numero FROM spedizioni"),
        "cliente": prendi("SELECT codice FROM clienti"),
        "fornitore": prendi("SELECT codice FROM fornitori"),
        "prodotto": prendi("SELECT codice FROM prodotti"),
        "ordine": prendi("SELECT numero FROM ordini"),
        "giacenza": prendi("SELECT p.codice, m.nome FROM giacenze g JOIN prodotti p ON p.id = g.prodotto_id JOIN magazzini m ON m.id = g.magazzino_id"),
        "cliente_con_ticket": prendi("SELECT c.codice FROM ticket t JOIN clienti c ON c.id = t.cliente_id"),
        "cliente_giorno": prendi("SELECT c.codice, o.data FROM ordini o JOIN clienti c ON c.id = o.cliente_id"),
        "nessuno": [()] * pochi,
        "soglia": [(f"{s:.2f}",) for s in (soglie or [])] * max(1, pochi // 5),
    }


def _percentili(tempi_ns: list[int]) -> dict[str, float]:
    # la mediana, il 99° percentile e la media: la media conta anche le prove lente, la mediana no
    t = sorted(tempi_ns)
    return {"p50_us": round(t[len(t) // 2] / 1000, 2), "p99_us": round(t[min(len(t) - 1, int(len(t) * 0.99))] / 1000, 2),
            "media_us": round(sum(t) / len(t) / 1000, 2)}


def _media(valori: list[float]) -> float | None:
    return round(sum(valori) / len(valori), 2) if valori else None


def misura(conn, rete: Rete, quante: int = 300, avanza: Callable[[str, int, int], None] | None = None, redis=None) -> dict[str, Any]:
    """Fa le stesse domande ai due, controlla che le risposte coincidano e misura i tempi.

    Con ``redis``, un collegamento a un Redis in cui sono state preparate le risposte di questi stessi dati, le
    stesse domande vanno anche a lui: la sua risposta si confronta con quella di PostgreSQL e si misura il tempo
    che vede il programma. Il tempo dentro il suo server viene dai contatori di Redis, su un campione più piccolo
    come per PostgreSQL.

    ``rete`` può essere la rete tutta in memoria oppure quella letta dal disco a blocchi (``blocchi.ComeRete``). Per
    questa si misura anche la prima volta: prima di ogni domanda si buttano via i blocchi che ha in memoria, e il
    tempo della prima risposta a ogni valore è quello di una domanda che deve andare a prendere la rete sul disco.
    """
    dal_disco = hasattr(rete, "svuota")
    # ``avanza`` riceve che cosa si sta facendo, quanti passi sono fatti e quanti sono in tutto: ogni domanda ha tre
    # passi (controllo delle risposte, tempi, tempi dentro PostgreSQL) e un quarto se c'è Redis. Chi guarda vede una
    # barra che avanza e sa quando finisce.
    per_domanda = 4 if redis is not None else 3
    from sinapsidb import confronto_redis  # qui e non in cima: quel modulo usa le domande di questo

    parametri = campioni(conn, quante)
    ora = time.perf_counter_ns
    risultati = []
    saltate = []  # le domande rimaste fuori, con il perché
    con_vista = conn.execute("SELECT to_regclass(%s)", (dati_prova.VISTA_FATTURATO,)).fetchone()[0] is not None
    da_fare = []
    for d in domande(rete):
        if d["sinapsi"] is None or (d.get("pronta") and not con_vista):
            saltate.append({"nome": d["nome"], "perche": d.get("perche") or "in PostgreSQL manca la vista del fatturato per mese: nasce quando si generano i dati"})
        else:
            da_fare.append(d)
    passi = len(da_fare) * per_domanda

    def passo(quale: int, d: dict, fase: int, che_cosa: str) -> None:
        if avanza:
            avanza(f"domanda {quale + 1} di {len(da_fare)}, «{d['nome']}»: {che_cosa}", quale * per_domanda + fase, passi)

    with conn.cursor() as cur, psycopg.ClientCursor(conn) as spiega:
        for quale, d in enumerate(da_fare):
            numero = d["numero"]
            passo(quale, d, 0, "controllo che le risposte siano uguali")
            sql, sinapsi = d["sql"], d["sinapsi"]
            per_sql = d.get("sql_parametri")
            ps = parametri[d["parametri"]]
            ps_sql = [per_sql(p) for p in ps] if per_sql else ps
            # 1. le risposte devono coincidere, una per una (l'ordine delle righe non conta)
            diverse = 0
            righe_totali = 0
            t_prima = []
            if dal_disco:
                rete.svuota()
            for p, q in zip(ps, ps_sql):
                di_pg = sorted(tuple(_testo(x) for x in riga) for riga in cur.execute(sql, q, prepare=True).fetchall())
                t0 = ora()
                di_sinapsi = sinapsi(*p)
                t_prima.append(ora() - t0)
                diverse += di_pg != sorted(di_sinapsi)
                righe_totali += len(di_pg)
            # 2. i tempi: prima PostgreSQL dal programma, poi SinapsiDB, ognuno dopo un giro di riscaldamento
            passo(quale, d, 1, "misura dei tempi")
            for p, q in zip(ps[:10], ps_sql[:10]):
                cur.execute(sql, q, prepare=True).fetchall()
                sinapsi(*p)
            t_pg, t_si = [], []
            for q in ps_sql:
                t0 = ora()
                cur.execute(sql, q, prepare=True).fetchall()
                t_pg.append(ora() - t0)
            for p in ps:
                t0 = ora()
                sinapsi(*p)
                t_si.append(ora() - t0)
            # 3. il tempo della sola esecuzione dentro il server di PostgreSQL
            passo(quale, d, 2, "tempi dentro PostgreSQL")
            nel_server = []
            for q in ps_sql[:30]:
                piano = spiega.execute("EXPLAIN (ANALYZE, TIMING OFF, FORMAT JSON) " + sql, q).fetchone()[0]
                if isinstance(piano, (str, bytes)):
                    piano = json.loads(piano)
                nel_server.append(piano[0]["Execution Time"] * 1000)
            nel_server.sort()
            risultato = {
                "nome": d["nome"], "cosa": d["cosa"], "sql": sql, "lingua": d["lingua"], "senza_partenza": bool(d.get("senza_partenza")),
                "pronta": bool(d.get("pronta")),
                # l'ha calcolata il nucleo compilato, e non il Python: si dice riga per riga
                "nucleo": bool(getattr(getattr(sinapsi, "__self__", None), "nucleo", False)),
                "prove": len(ps), "righe_medie": round(righe_totali / max(1, len(ps)), 1), "risposte_diverse": diverse,
                "postgres": _percentili(t_pg), "postgres_nel_server_p50_us": round(nel_server[len(nel_server) // 2], 2) if nel_server else None,
                "postgres_nel_server_media_us": _media(nel_server),
                "sinapsi": _percentili(t_si), "sinapsi_prima_volta": _percentili(t_prima) if dal_disco else None, "redis": None, "redis_nel_server_p50_us": None,
                "redis_nel_server_media_us": None, "redis_risposte_diverse": None, "redis_comando": None,
            }
            if redis is not None:
                # 4. Redis: la risposta pronta deve essere quella di PostgreSQL; poi il tempo, con lo stesso giro di riscaldamento
                passo(quale, d, 3, "le stesse domande a Redis")
                diverse_redis = 0
                for p, q in zip(ps, ps_sql):
                    di_pg = sorted(tuple(_testo(x) for x in riga) for riga in cur.execute(sql, q, prepare=True).fetchall())
                    diverse_redis += di_pg != sorted(confronto_redis.righe(numero, confronto_redis.chiedi(redis, numero, p)))
                for p in ps[:10]:
                    confronto_redis.chiedi(redis, numero, p)
                t_re = []
                for p in ps:
                    t0 = ora()
                    confronto_redis.chiedi(redis, numero, p)
                    t_re.append(ora() - t0)
                # 5. la sola esecuzione nel server di Redis: i suoi contatori contano microsecondi interi, quindi ogni
                #    valore del campione si chiede dieci volte tra due letture, e si prende la mediana dei valori
                nel_server_redis = []
                for p in ps[:100]:
                    volte_prima, spesi_prima = confronto_redis.nel_server(redis, numero)
                    for _ in range(10):
                        confronto_redis.chiedi(redis, numero, p)
                    volte_dopo, spesi_dopo = confronto_redis.nel_server(redis, numero)
                    if volte_dopo > volte_prima:
                        nel_server_redis.append((spesi_dopo - spesi_prima) / (volte_dopo - volte_prima))
                nel_server_redis.sort()
                risultato.update(redis=_percentili(t_re), redis_risposte_diverse=diverse_redis, redis_comando=confronto_redis.comando(numero),
                                 redis_nel_server_p50_us=round(nel_server_redis[len(nel_server_redis) // 2], 2) if nel_server_redis else None,
                                 redis_nel_server_media_us=_media(nel_server_redis))
            risultati.append(risultato)
    esito = {"quando": time.time(), "domande": risultati, "risposte_diverse": sum(r["risposte_diverse"] for r in risultati),
             "versione_postgres": conn.execute("SHOW server_version").fetchone()[0], "redis": None, "saltate": saltate,
             "nucleo": nucleo.stato(),  # se durante la misura il nucleo compilato era attivo, e se no perché
             # dove stava la rete durante la misura: tutta in memoria, oppure sul disco con in memoria solo i blocchi usati
             "rete": {"modo": "disco", **rete.r.occupazione()} if dal_disco else {"modo": "memoria"}, **conteggi(rete)}
    if redis is not None:
        esito["redis"] = {"versione": redis.info("server").get("redis_version"), **confronto_redis.occupazione(redis),
                          **confronto_redis.come_collegato(redis),
                          "risposte_diverse": sum(r["redis_risposte_diverse"] for r in risultati),
                          # quanto è costato preparare le risposte che questo confronto ha letto: è il prezzo della cache
                          "preparazione_s": (confronto_redis.caricamento(redis) or {}).get("secondi")}
    return esito


# --- il banco: stato e lavori lanciati dal portale -------------------------------


# Lo stato di quando non c'è nessun lavoro, e il nome in parole di quelli che il portale lancia da solo.
TETTO_BLOCCHI = 256 << 20  # quanta rete di prova può stare in memoria quando la rete è sul disco, in byte


def _memoria_del_processo() -> int:
    """La memoria che il processo occupa adesso, in byte (0 se non si riesce a leggerla)."""
    try:
        with open("/proc/self/status", encoding="ascii") as f:
            for riga in f:
                if riga.startswith("VmRSS:"):
                    return int(riga.split()[1]) * 1024
    except (OSError, ValueError):
        pass
    return 0


def _libera_memoria() -> None:
    """Dopo aver lasciato andare una rete grande: raccoglie quello che non serve più e chiede al sistema di riprendersi
    la memoria. Senza, una parte resterebbe al processo anche se è libera."""
    gc.collect()
    try:
        import ctypes

        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except Exception:  # un sistema senza glibc, o che non lascia caricare la libreria: la memoria torna più piano
        pass


FERMO: dict[str, Any] = {"nome": None, "in_corso": False, "avanzamento": "", "fatto": None, "da_fare": None, "errore": None,
                         "iniziato": None, "finito": None}
NOMI_LAVORI = {"apertura": "la riapertura della rete di prova"}


class Occupato(RuntimeError):
    """La rete di prova è impegnata in un lavoro."""


class Banco:
    """La rete di prova e i lavori che il portale può lanciare: genera, azzera, importa, misura, e poi redis e svuota
    per le risposte pronte in Redis, disco e memoria per scegliere dove vive la rete di prova.

    Gira un lavoro alla volta, in un thread a parte. Lo stato si legge in ogni momento.

    La rete di prova vive in uno di due modi. «memoria»: tutta in memoria, come il motore la tiene da sempre.
    «disco»: sta sul disco a blocchi (``sinapsidb.blocchi``) e in memoria ci sono solo i blocchi usati di recente,
    dentro un tetto. È la stessa rete, con gli stessi identificativi e le stesse risposte: cambia quanta memoria
    occupa e quanto costa una domanda.
    """

    LAVORI = ("genera", "azzera", "importa", "misura", "redis", "svuota", "disco", "memoria")

    def __init__(self, cartella: Path, dsn: str, taglie: tuple[int, ...] = TAGLIE, redis_url: str | None = None,
                 tetto_blocchi: int = TETTO_BLOCCHI) -> None:
        self.cartella = Path(cartella)
        self.tetto_blocchi = int(tetto_blocchi)
        self._parziale = None  # la rete di prova letta dal disco a blocchi, quando il modo è «disco»
        # I blocchi sono stati confrontati con il giornale che c'è, byte per byte, da quando questo banco è partito o li
        # ha costruiti. Finché non lo sono non rispondono: un giornale ripristinato può essere lungo uguale e diverso.
        self._verificati = False
        self.dsn = dsn
        self.taglie = tuple(taglie)
        self.redis_url = redis_url or None  # None: Redis non fa parte del confronto
        self._re: dict[str, Any] = {"letto": 0.0, "stato": None}
        self._filo: threading.Thread | None = None  # il lavoro in corso, o l'ultimo
        self._errore_rete: str | None = None  # perché la rete di prova non si è aperta, se non si è aperta
        self._prima_di_aprire: dict[str, Any] = dict(FERMO)  # lo stato del lavoro prima di una riapertura
        self._lock = threading.Lock()  # protegge lo stato del lavoro
        self._lock_rete = threading.Lock()  # chi apre, legge o cambia la rete di prova lo tiene: un lavoro per tutta la sua durata
        self._rete: Rete | None = None
        self._lavoro: dict[str, Any] = {"nome": None, "in_corso": False, "avanzamento": "", "fatto": None, "da_fare": None,
                                        "errore": None, "iniziato": None, "finito": None}
        self._pg: dict[str, Any] = {"letto": 0.0, "stato": None}
        try:  # il modo resta scritto accanto alla rete: dopo un riavvio la rete sul disco resta sul disco
            self._modo = "disco" if self._file("modo").read_text().strip() == "disco" and self._file("blocchi/testa.json").exists() else "memoria"
        except OSError:
            self._modo = "memoria"
        if self._modo == "disco":
            # I blocchi valgono per il giornale da cui sono stati costruiti. Se il giornale non è più quello (è stato
            # ripristinato da una copia, o azzerato a mano) risponderebbero con una rete che non c'è più: si torna alla
            # rete in memoria, riletta dal giornale, e il modo «disco» si richiede di nuovo. Qui si guarda solo quello
            # che costa poco, la lunghezza; il contenuto lo confronta la riapertura, prima che i blocchi rispondano.
            from sinapsidb import blocchi

            identita, giornale = blocchi.giornale_dei_blocchi(self._file("blocchi")), self._file("rete") / "giornale"
            try:
                lungo = giornale.stat().st_size
            except OSError:
                lungo = -1
            if not identita or identita["byte"] != lungo:
                log.warning("i blocchi della rete di prova non provano di venire dal giornale che c'è: si torna alla rete in memoria")
                self._modo = "memoria"
        # Un banco solo per cartella. Nel modo «memoria» il giornale lo tiene la rete; nel modo «disco» non lo tiene
        # nessuno, e un secondo portale potrebbe azzerare o cambiare la rete sotto il primo. Il banco che non riesce a
        # prendere la cartella per sé risponde alle domande, ma non lancia lavori.
        self._blocco: int | None = None
        try:
            self.cartella.mkdir(parents=True, exist_ok=True)
            fd = os.open(self.cartella / "blocco", os.O_RDWR | os.O_CREAT, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self._blocco = fd
            except OSError:
                os.close(fd)
        except OSError:
            pass

    # stato

    def _file(self, nome: str) -> Path:
        return self.cartella / nome

    def _leggi_json(self, nome: str):
        try:
            return json.loads(self._file(nome).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _scrivi_json(self, nome: str, dati) -> None:
        self.cartella.mkdir(parents=True, exist_ok=True)
        self._file(nome).write_text(json.dumps(dati, ensure_ascii=False), encoding="utf-8")

    def rete(self) -> Rete:
        """La rete di prova, aperta alla prima richiesta. Va chiamata tenendo ``_lock_rete``."""
        self._scarta_se_fuori_uso()
        if self._rete is None:
            # un banco che non ha la cartella per sé (c'è già un altro portale) apre la rete solo per leggerla
            self._rete = Rete(str(self._file("rete")), sync="os", sola_lettura=self._blocco is None)
            self._errore_rete = None
        return self._rete

    def _scarta_se_fuori_uso(self) -> None:
        """Se un lavoro ha lasciato la rete di prova fuori uso (una scrittura non è arrivata sul disco), quello che ha
        in memoria può non essere quello che c'è nel giornale: la si chiude, e chi viene dopo la riapre dal giornale.
        Non si risponde mai con record che il giornale non ha. Va chiamata tenendo ``_lock_rete``."""
        if self._rete is not None and self._rete.fuori_uso:
            log.warning("la rete di prova è fuori uso e viene riaperta dal giornale: %s", self._rete.fuori_uso)
            try:
                self._rete.chiudi()
            except Exception as e:  # una rete ferma può non riuscire a chiudere pulita: si riapre comunque dal giornale
                log.warning("la rete di prova ferma non si è chiusa pulita: %s", e)
            finally:
                self._rete = None

    def _lettore(self):
        """La rete di prova letta dal disco a blocchi. Si apre in un attimo, ma solo dopo che i blocchi sono stati
        confrontati con il giornale (vedi ``_blocchi_del_giornale``). Va chiamata tenendo ``_lock_rete``."""
        if self._parziale is None:
            from sinapsidb.blocchi import ComeRete, ReteParziale

            if not self._verificati and not self._blocchi_del_giornale():
                raise Occupato("i blocchi della rete di prova non vengono dal giornale che c'è: la rete si riapre dal giornale")

            self._parziale = ComeRete(ReteParziale(self._file("blocchi"), tetto=self.tetto_blocchi))
        return self._parziale

    def _per_domande(self):
        """La rete a cui fare le domande, secondo il modo. Va chiamata tenendo ``_lock_rete``."""
        return self._lettore() if self._modo == "disco" else self.rete()

    def _blocchi_del_giornale(self) -> bool:
        """Confronta i blocchi con il giornale che c'è adesso, byte per byte (``blocchi.sono_del_giornale``). Se non ne
        vengono si torna alla rete in memoria, riletta dal giornale: il giornale è la verità, i blocchi si rifanno
        chiedendo di nuovo il modo «disco». Legge tutto il giornale. Va chiamata tenendo ``_lock_rete``."""
        from sinapsidb import blocchi

        if blocchi.sono_del_giornale(self._file("blocchi"), self._file("rete") / "giornale"):
            self._verificati = True
            return True
        log.warning("i blocchi della rete di prova non vengono dal giornale che c'è: si torna alla rete in memoria")
        if self._parziale is not None:
            self._parziale.chiudi()
            self._parziale = None
        self._verificati = False
        self._modo = "memoria"
        return False

    def _metti_modo(self, modo: str) -> None:
        if modo != "disco":
            self._verificati = False  # se si torna sul disco i blocchi si confrontano di nuovo con il giornale
        self._modo = modo
        self.cartella.mkdir(parents=True, exist_ok=True)
        self._file("modo").write_text(modo + "\n", encoding="ascii")

    def _dove(self) -> dict[str, Any]:
        """Dove sta la rete di prova e quanta memoria occupa: quella del portale intero e, quando la rete è sul disco,
        quella dei blocchi che in questo momento sono in memoria."""
        dove: dict[str, Any] = {"modo": self._modo, "portale": _memoria_del_processo(), "tetto": self.tetto_blocchi,
                                "costruzione": self._leggi_json("disco.json")}
        parziale = self._parziale
        if self._modo == "disco" and parziale is not None:
            dove.update(parziale.r.occupazione())
        else:
            dove["su_disco"] = self._peso_sul_disco(self._file("rete"))
        return dove

    @staticmethod
    def _peso_sul_disco(cartella: Path) -> int:
        """Quanti byte occupano i file di una cartella. Chi guarda lo stato lo fa anche mentre un lavoro li sta
        scrivendo: un file può sparire tra l'elenco e la misura (l'istantanea che prende il posto della vecchia, la rete
        che passa sul disco). Quello che non c'è più non pesa, e lo stato non cade per questo."""
        peso = 0
        try:
            for f in cartella.glob("*"):
                try:
                    if f.is_file():
                        peso += f.stat().st_size
                except OSError:
                    pass
        except OSError:
            pass
        return peso

    def riapri(self) -> bool:
        """Riapre la rete di prova in sottofondo, se non è aperta e non c'è un lavoro in corso. Dice se l'ha fatto.

        Rileggere dal disco una rete di dieci milioni di fatti dura un minuto: chi chiede lo stato, o fa una domanda,
        non deve restare ad aspettarla. La riapertura è un lavoro come gli altri, e si vede nello stato. Se la rete
        non si apre, non si riprova a ogni richiesta: resta scritto perché, e la si può azzerare.
        """
        with self._lock:
            # Sul disco non c'è niente da rileggere, ma prima che i blocchi rispondano si confrontano con il giornale:
            # leggerlo tutto dura qualche secondo con dieci milioni di record, e anche questo si fa in sottofondo.
            disco = self._modo == "disco"
            pronta = self._verificati if disco else self._rete is not None
            if pronta or self._errore_rete is not None or self._lavoro["in_corso"]:
                return False
            self._prima_di_aprire = dict(self._lavoro)
            self._parte("apertura", None, "controllo dei blocchi con il giornale" if disco else "lettura della rete di prova dal disco")
            return True

    def _aspetta_apertura(self, secondi: float) -> None:
        """Se la rete di prova si sta riaprendo da poco, le lascia quel tempo per finire: una rete piccola si riapre in
        un attimo, e così chi chiede subito dopo l'avvio trova lo stato completo invece di «sto riaprendo»."""
        with self._lock:
            lavoro, filo = self._lavoro, self._filo
            manca = (lavoro["iniziato"] or 0) + secondi - time.time() if lavoro["in_corso"] and lavoro["nome"] == "apertura" else 0
        if filo is not None and manca > 0:
            filo.join(manca)

    def _postgres(self) -> dict[str, Any]:
        """Che cosa c'è in PostgreSQL: tabelle, righe, con che taglia e che seme sono state generate."""
        if time.monotonic() - self._pg["letto"] < 30 and self._pg["stato"] is not None:
            return self._pg["stato"]
        stato: dict[str, Any] = {"raggiungibile": False, "disponibile": False, "errore": None, "versione": None, "puo_scrivere": False,
                                 "tabelle": [], "righe": {}, "totale": 0, "generazione": None}
        try:
            with collega(self.dsn) as conn:
                stato["raggiungibile"] = True
                stato["versione"] = conn.execute("SHOW server_version").fetchone()[0]
                stato["puo_scrivere"] = bool(conn.execute("SELECT has_schema_privilege(current_user, 'public', 'CREATE')").fetchone()[0])
                esistono = {r[0] for r in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")}
                if not set(TABELLE) <= esistono:
                    stato["errore"] = "l'azienda di prova non è ancora stata generata"
                else:
                    stato["righe"] = {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in TABELLE}
                    stato["tabelle"] = [{"nome": t, "etichetta": dati_prova.ETICHETTE[t], "righe": stato["righe"][t]} for t in TABELLE]
                    stato["totale"] = sum(stato["righe"].values())
                    stato["disponibile"] = True
                    if dati_prova.GENERAZIONE in esistono:
                        g = conn.execute(f"SELECT righe_richieste, seme, secondi, extract(epoch FROM generato) FROM {dati_prova.GENERAZIONE}"
                                         " ORDER BY generato DESC LIMIT 1").fetchone()
                        if g:
                            stato["generazione"] = {"righe_richieste": g[0], "seme": g[1], "secondi": round(float(g[2]), 1), "quando": float(g[3])}
        except Exception as e:  # driver assente, servizio fermo, permessi, tabella bloccata
            stato["errore"] = f"{type(e).__name__}: {e}".strip()[:300]
        self._pg = {"letto": time.monotonic(), "stato": stato}
        return stato

    @staticmethod
    def _memoria_per_redis(postgres: dict[str, Any], redis: dict[str, Any]) -> tuple[int, bool]:
        """Quanta memoria serviranno all'incirca le risposte pronte dei dati che ci sono in PostgreSQL, e se a Redis ne
        resta abbastanza una volta tolte quelle di prima. È una stima: si dice «non basta» solo quando manca di molto."""
        from sinapsidb import confronto_redis

        serve = confronto_redis.stima_memoria(postgres["totale"])
        libera = redis["memoria_tetto"] - (redis["memoria_usata"] - redis["memoria_risposte"])
        return serve, not redis["memoria_tetto"] or serve * 0.8 <= libera

    def _stato_redis(self, postgres: dict[str, Any]) -> dict[str, Any]:
        """Che cosa c'è in Redis per il confronto, e se sono le risposte dei dati che ci sono adesso in PostgreSQL."""
        if time.monotonic() - self._re["letto"] < 15 and self._re["stato"] is not None:
            return self._re["stato"]
        from sinapsidb import confronto_redis

        stato: dict[str, Any] = {"configurato": self.redis_url is not None, "raggiungibile": False, "errore": None, "versione": None,
                                 "caricamento": None, "allineato": False, "chiavi": 0, "memoria_risposte": 0, "memoria_usata": 0, "memoria_tetto": 0,
                                 "via": None, "lettore_c": None, "memoria_stimata": None, "memoria_basta": None, "superate": False}
        if self.redis_url is not None:
            try:
                r = confronto_redis.collega(self.redis_url)
                try:
                    stato["versione"] = r.info("server").get("redis_version")
                    stato["raggiungibile"] = True
                    stato.update(confronto_redis.come_collegato(r))
                    stato["caricamento"] = confronto_redis.caricamento(r)
                    stato.update(confronto_redis.occupazione(r))
                finally:
                    r.close()
                di = (stato["caricamento"] or {}).get("generazione")
                # le risposte servono se sono dei dati di adesso e delle domande di adesso
                stato["superate"] = bool(stato["caricamento"]) and stato["caricamento"].get("formato") != confronto_redis.FORMATO
                stato["allineato"] = bool(di) and di == postgres.get("generazione") and not stato["superate"]
                if postgres.get("disponibile"):
                    stato["memoria_stimata"], stato["memoria_basta"] = self._memoria_per_redis(postgres, stato)
            except Exception as e:  # cliente assente, servizio fermo, memoria finita
                stato["errore"] = f"{type(e).__name__}: {e}".strip()[:300]
        self._re = {"letto": time.monotonic(), "stato": stato}
        return stato

    def stato(self) -> dict[str, Any]:
        # Durante un lavoro la rete cambia: i conteggi si leggono a lavoro finito. Se un lavoro parte
        # proprio mentre si guarda, la rete è sua: si rilegge lo stato del lavoro invece di aspettarlo.
        self.riapri()
        self._aspetta_apertura(1.5)
        while True:
            with self._lock:
                lavoro = dict(self._lavoro)
            sinapsi = None
            if lavoro["in_corso"]:
                break
            if self._errore_rete is not None:
                sinapsi = {"errore": self._errore_rete}
                break
            if self._lock_rete.acquire(timeout=0.5):
                try:
                    sinapsi = conteggi(self._per_domande())
                except Exception as e:
                    sinapsi = {"errore": f"{type(e).__name__}: {e}"[:300]}
                finally:
                    self._lock_rete.release()
                break
        # Mentre i dati vengono rigenerati le tabelle sono bloccate: si mostra l'ultimo stato letto, senza interrogarle.
        if lavoro["in_corso"] and lavoro["nome"] == "genera" and self._pg["stato"] is not None:
            postgres = self._pg["stato"]
        else:
            postgres = self._postgres()
        # Mentre Redis viene caricato lo si lascia lavorare: si mostra l'ultimo stato letto.
        if lavoro["in_corso"] and lavoro["nome"] == "redis" and self._re["stato"] is not None:
            di_redis = self._re["stato"]
        else:
            di_redis = self._stato_redis(postgres)
        return {"postgres": postgres, "redis": di_redis, "sinapsi": sinapsi, "rete": self._dove(), "lavoro": lavoro, "taglie": list(self.taglie),
                "nucleo": nucleo.stato(),
                "taglia_proposta": TAGLIA_PROPOSTA if TAGLIA_PROPOSTA in self.taglie else max(self.taglie),
                "importazione": self._leggi_json("importazione.json"), "confronto": self._leggi_json("confronto.json")}

    # lavori

    def avvia(self, nome: str, righe: int | None = None) -> str | None:
        """Lancia un lavoro. Restituisce None se è partito, altrimenti il motivo per cui non parte."""
        if nome not in self.LAVORI:
            return "lavoro sconosciuto"
        if nome == "genera" and righe not in self.taglie:
            return f"taglia non ammessa: si può scegliere tra {', '.join(_punti(t) for t in self.taglie)} record"
        if self._blocco is None:
            return "questa cartella la sta già usando un altro portale: da qui si può solo leggere, i lavori si lanciano da quello"
        self._aspetta_apertura(2)  # una riapertura breve non deve far rifiutare un lavoro chiesto subito dopo l'avvio
        with self._lock:
            if self._lavoro["in_corso"]:
                return f"c'è già un lavoro in corso: {NOMI_LAVORI.get(self._lavoro['nome'], self._lavoro['nome'])}"
            self._parte(nome, righe, "avvio")
        return None

    def _parte(self, nome: str, righe: int | None, avanzamento: str) -> None:
        """Segna il lavoro come in corso e lo fa partire nel suo thread. Va chiamata tenendo ``_lock``."""
        self._lavoro = {"nome": nome, "in_corso": True, "avanzamento": avanzamento, "fatto": None, "da_fare": None,
                        "errore": None, "iniziato": time.time(), "finito": None}
        self._filo = threading.Thread(target=self._esegui, args=(nome, righe), name=f"confronto-{nome}", daemon=True)
        self._filo.start()

    def _avanza(self, testo: str, fatto: int | None = None, da_fare: int | None = None) -> None:
        with self._lock:
            self._lavoro.update(avanzamento=testo, fatto=fatto, da_fare=da_fare)

    def _esegui(self, nome: str, righe: int | None) -> None:
        errore = None
        try:
            with self._lock_rete:
                if nome == "genera":
                    self._genera(righe)
                else:
                    getattr(self, f"_{nome}")()
        except Exception as e:
            log.exception("lavoro %s fallito", nome)
            errore = f"{type(e).__name__}: {e}".strip()[:400]
            if nome == "apertura":
                self._errore_rete = errore
        with self._lock:
            if nome == "apertura" and errore is None and time.time() - self._lavoro["iniziato"] < 2:
                self._lavoro = self._prima_di_aprire  # una riapertura di un attimo non è una notizia: lo stato torna com'era
            else:
                self._lavoro.update(in_corso=False, errore=errore, finito=time.time(), avanzamento="finito" if errore is None else "fallito")

    def _apertura(self) -> None:
        if self._modo == "disco" and self._blocchi_del_giornale():
            return  # i blocchi vengono da questo giornale: rispondono loro. Altrimenti si riapre la rete in memoria
        self._avanza("lettura della rete di prova dal disco")
        rete = self.rete()
        if rete.istantanea_scartata:
            # L'istantanea trovata era di una versione di prima e non provava di contenere solo neuroni scritti nel
            # giornale: il motore non l'ha usata e ha riletto tutto il giornale. Se ne salva subito una nuova, così la
            # rilettura lunga si paga una volta sola e non a ogni riavvio.
            self._avanza("salvataggio di una nuova istantanea")
            rete.istantanea()
            rete.istantanea_scartata = False

    def _genera(self, righe: int) -> None:
        """Rigenera l'azienda di prova in PostgreSQL. È l'unico lavoro che ci scrive."""

        def progresso(scritte: dict[str, int], fase: str) -> None:
            fatte = sum(scritte.values())
            self._avanza("scrittura dei record" if fase == "scrittura" else fase, fatte, righe)

        self._postgres()  # l'ultimo stato letto resta a disposizione di chi guarda mentre le tabelle sono bloccate
        try:
            with collega(self.dsn, scrittura=True) as conn:
                dati_prova.carica(conn, righe, avanza=progresso)
        except psycopg.errors.InsufficientPrivilege as e:
            raise RuntimeError("il portale non ha il permesso di creare le tabelle in questa base di dati: il suo ruolo ne deve "
                               f"essere il proprietario, e lo decide chi amministra ({str(e).strip()[:120]})") from e
        finally:
            self._pg = {"letto": 0.0, "stato": None}
            self._re = {"letto": 0.0, "stato": None}  # le risposte pronte in Redis, se c'erano, sono dei dati di prima
        # I dati sono cambiati: la rete di prova e l'ultimo confronto parlavano di quelli di prima.
        self._avanza("azzeramento della rete di prova")
        self._azzera()

    def _azzera(self) -> None:
        if self._rete is not None:
            self._rete.chiudi()
            self._rete = None
        if self._parziale is not None:
            self._parziale.chiudi()
            self._parziale = None
        for cartella in ("rete", "blocchi", "blocchi.nuovi"):
            shutil.rmtree(self._file(cartella), ignore_errors=True)
        for nome in ("importazione.json", "confronto.json", "disco.json", "modo"):
            self._file(nome).unlink(missing_ok=True)
        self._modo = "memoria"  # una rete nuova nasce in memoria: sul disco ci va quando lo si chiede
        self._verificati = False
        self._errore_rete = None  # una rete che non si apriva si rimedia così: buttandola via
        _libera_memoria()
        self.rete()

    def _importa(self) -> None:
        if self._modo == "disco":
            raise RuntimeError("la rete di prova non è vuota: va azzerata prima di importare")
        rete = self.rete()
        if conteggi(rete)["fatti"]:
            raise RuntimeError("la rete di prova non è vuota: va azzerata prima di importare")
        with collega(self.dsn) as conn:
            da_fare = sum(conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in TABELLE)
            esito = importa(conn, rete, lambda fatti, tabella: self._avanza(dati_prova.ETICHETTE[tabella], fatti, da_fare))
        self._avanza("salvataggio dell'istantanea")
        rete.istantanea()
        esito["quando"] = time.time()
        self._scrivi_json("importazione.json", esito)

    def _vista(self) -> None:
        """La vista del fatturato per mese in PostgreSQL nasce con la generazione dei dati. Se i dati sono stati generati
        prima che esistesse, la si crea adesso. È l'unica altra scrittura che il portale fa in PostgreSQL oltre a
        «genera», e non tocca i dati: aggiunge una risposta tenuta pronta, quella che serve al confronto."""
        with collega(self.dsn) as conn:
            if conn.execute("SELECT to_regclass(%s)", (dati_prova.VISTA_FATTURATO,)).fetchone()[0] is not None:
                return
        with collega(self.dsn, scrittura=True) as conn:
            conn.execute(dati_prova.SQL_VISTA_FATTURATO)

    def _misura(self) -> None:
        rete = self._per_domande()
        if not conteggi(rete)["fatti"]:
            raise RuntimeError("la rete di prova è vuota: prima va fatta l'importazione")
        # I risultati di prima spariscono appena il confronto parte: chi guarda non deve chiedersi se quelli che vede
        # sono i vecchi o i nuovi. Se il confronto non riesce, non resta niente da mostrare come se fosse buono.
        self._file("confronto.json").unlink(missing_ok=True)
        self._avanza("preparazione")
        if self._modo == "memoria":
            somma_pronta(rete)  # una rete importata prima che la somma pronta esistesse la riceve adesso, dai fatti che ha
        try:
            self._vista()
        except psycopg.Error as e:  # senza la vista quella domanda resta fuori dal confronto, e il confronto lo dice
            log.warning("la vista del fatturato per mese non si è potuta creare: %s", str(e).strip()[:200])
        self._pg = {"letto": 0.0, "stato": None}
        self._re = {"letto": 0.0, "stato": None}
        # Redis entra nel confronto solo se ha le risposte dei dati che ci sono adesso in PostgreSQL
        r = None
        if self._stato_redis(self._postgres())["allineato"]:
            from sinapsidb import confronto_redis

            r = confronto_redis.collega(self.redis_url)
        try:
            with collega(self.dsn) as conn:
                self._scrivi_json("confronto.json", misura(conn, rete, avanza=self._avanza, redis=r))
        finally:
            if r is not None:
                r.close()

    def _redis(self) -> None:
        """Prepara in Redis le risposte alle domande del confronto, leggendo PostgreSQL in sola lettura. Tocca solo le chiavi
        del confronto."""
        from sinapsidb import confronto_redis

        if self.redis_url is None:
            raise RuntimeError("Redis non è configurato per il confronto: lo decide chi installa, con SINAPSIDB_PROVA_REDIS")
        self._pg = {"letto": 0.0, "stato": None}
        postgres = self._postgres()
        if not postgres["disponibile"]:
            raise RuntimeError("in PostgreSQL non c'è l'azienda di prova: prima vanno generati i dati")
        da_fare = confronto_redis.stima_righe(postgres["righe"])
        self._vista()  # una delle risposte pronte si legge dalla vista del fatturato per mese
        r = confronto_redis.collega(self.redis_url)
        try:
            adesso = confronto_redis.occupazione(r)
            serve, basta = self._memoria_per_redis(postgres, adesso)
            if not basta:  # inutile caricare per minuti e poi fallire: lo si dice subito, con i numeri
                raise RuntimeError(f"per questi dati le risposte pronte occupano circa {_punti(round(serve / 2**20))} MB; a Redis ne sono concessi "
                                   f"{_punti(round(adesso['memoria_tetto'] / 2**20))}, e non bastano: il suo tetto lo alza chi amministra")
            try:
                with collega(self.dsn) as conn:
                    confronto_redis.carica(conn, r, postgres["generazione"], lambda nome, righe: self._avanza(nome, righe, da_fare))
            except Exception as e:
                if confronto_redis.memoria_finita(e):
                    confronto_redis.svuota(r)  # un caricamento a metà non serve a niente: non si lascia lì a occupare memoria
                    raise RuntimeError("Redis ha finito la memoria che gli è concessa prima di aver preparato tutte le risposte: per questa "
                                       "taglia il suo tetto lo alza chi amministra") from e
                raise
        finally:
            r.close()
            self._re = {"letto": 0.0, "stato": None}

    def _disco(self) -> None:
        """Porta la rete di prova sul disco: costruisce i blocchi dal giornale, senza tenere la rete in memoria, e da lì
        in poi in memoria restano solo i blocchi usati di recente. La rete in memoria viene lasciata andare."""
        from sinapsidb import blocchi

        if self._modo == "disco":
            raise RuntimeError("la rete di prova è già sul disco")
        giornale = self._file("rete") / "giornale"
        fatti = None
        if self._rete is not None:
            fatti = conteggi(self._rete)["fatti"]
        if fatti == 0 or not giornale.is_file() or not giornale.stat().st_size:
            raise RuntimeError("la rete di prova è vuota: prima va fatta l'importazione")
        if self._rete is not None:
            self._rete.chiudi()  # il giornale è scritto fino in fondo, e la memoria si libera prima di costruire
            self._rete = None
            _libera_memoria()
        lungo = giornale.stat().st_size
        # Blocchi costruiti prima si riusano solo se vengono proprio da questo giornale: lungo uguale non basta, un
        # giornale ripristinato da una copia può esserlo e dire altro. Si confronta il contenuto (legge il giornale).
        self._avanza("controllo dei blocchi con il giornale")
        if not blocchi.sono_del_giornale(self._file("blocchi"), giornale):
            libero = shutil.disk_usage(self.cartella).free
            if libero < lungo * 1.5:  # i blocchi occupano più o meno quanto il giornale, più i file provvisori delle linee
                raise RuntimeError(f"sul disco non c'è posto per portarci la rete: servono circa {_punti(round(lungo * 1.5 / 2**20))} MB "
                                   f"e ce ne sono {_punti(round(libero / 2**20))} liberi")
            for cartella in ("blocchi", "blocchi.nuovi"):
                shutil.rmtree(self._file(cartella), ignore_errors=True)
            numeri = blocchi.costruisci(giornale, self._file("blocchi.nuovi"), lambda fase, n: self._avanza(fase, n, fatti))
            os.replace(self._file("blocchi.nuovi"), self._file("blocchi"))
            self._scrivi_json("disco.json", {**numeri, "giornale_byte": numeri["giornale"]["byte"], "quando": time.time()})
        self._metti_modo("disco")
        self._verificati = True  # appena costruiti da questo giornale, o appena confrontati con lui
        self._lettore()

    def _memoria(self) -> None:
        """Riporta la rete di prova tutta in memoria, rileggendola dal giornale. I blocchi restano sul disco: tornarci è
        immediato finché la rete non cambia."""
        if self._modo != "disco":
            raise RuntimeError("la rete di prova è già tutta in memoria")
        self._avanza("lettura della rete di prova dal disco")
        # Prima si apre la rete, poi si lascia il disco: se non si apre (il giornale è guasto, o lo tiene un altro
        # programma) il modo resta «disco» e i blocchi restano lì a rispondere.
        rete = Rete(str(self._file("rete")), sync="os")
        self._rete, self._errore_rete = rete, None
        if self._parziale is not None:
            self._parziale.chiudi()
            self._parziale = None
        self._metti_modo("memoria")
        if rete.istantanea_scartata:
            # L'istantanea trovata non si poteva usare (una di prima, senza sigillo): se ne salva una nuova adesso,
            # altrimenti la rilettura di tutto il giornale si ripaga a ogni riavvio.
            self._avanza("salvataggio di una nuova istantanea")
            rete.istantanea()
            rete.istantanea_scartata = False

    def _svuota(self) -> None:
        """Toglie da Redis le risposte pronte del confronto, e solo quelle."""
        from sinapsidb import confronto_redis

        if self.redis_url is None:
            raise RuntimeError("Redis non è configurato per il confronto: lo decide chi installa, con SINAPSIDB_PROVA_REDIS")
        r = confronto_redis.collega(self.redis_url)
        try:
            confronto_redis.svuota(r)
        finally:
            r.close()
            self._re = {"letto": 0.0, "stato": None}

    # domande libere sulla rete di prova

    def _con_la_rete(self, fa: Callable[[Rete], Any]) -> Any:
        """Esegue una lettura sulla rete di prova. Durante un lavoro la rete è sua: chi chiede lo viene a sapere subito."""
        if self._lock_rete.acquire(timeout=1.5):  # una rete rimasta fuori uso non risponde: si riapre dal giornale
            try:
                self._scarta_se_fuori_uso()
            finally:
                self._lock_rete.release()
        self.riapri()
        self._aspetta_apertura(1.5)
        if not self._lock_rete.acquire(timeout=1.5):
            raise Occupato("c'è un lavoro in corso sulla rete di prova: la domanda si può fare quando finisce")
        try:
            if self._modo == "disco":
                if not self._verificati:  # non lo si fa qui: con molti dati chi chiede aspetterebbe qualche secondo
                    raise Occupato("i blocchi della rete di prova si stanno confrontando con il giornale: la domanda si può fare tra poco")
                return fa(self._lettore())
            # Si guarda adesso, con la stessa presa con cui si risponde: tra il controllo di prima e questo un lavoro
            # può essere fallito lasciando la rete fuori uso, e allora non risponde lei.
            self._scarta_se_fuori_uso()
            if self._rete is None:  # non la si apre qui: con molti dati chi chiede resterebbe ad aspettare un minuto
                raise Occupato("la rete di prova non è aperta: " + (self._errore_rete or "si sta riaprendo"))
            return fa(self._rete)
        finally:
            self._lock_rete.release()

    def chiedi(self, testo: str) -> dict[str, Any]:
        """Risponde a una frase della lingua di SinapsiDB sulla rete di prova. Legge soltanto."""
        if len(testo) > 2000:
            raise lingua.ErroreDomanda("la domanda è troppo lunga")
        return self._con_la_rete(lambda rete: lingua.chiedi(rete, testo, limite=200))

    def aiuto(self) -> dict[str, Any]:
        """Che cosa c'è nella rete di prova e qualche domanda d'esempio con valori veri."""
        return self._con_la_rete(lambda rete: {"vocabolario": lingua.vocabolario(rete), "esempi": esempi(rete)})

    def chiudi(self) -> None:
        with self._lock_rete:
            try:
                if self._rete is not None:
                    self._rete.chiudi()
            finally:
                self._rete = None
                if self._parziale is not None:
                    self._parziale.chiudi()
                    self._parziale = None
                if self._blocco is not None:  # la cartella torna libera
                    os.close(self._blocco)
                    self._blocco = None
