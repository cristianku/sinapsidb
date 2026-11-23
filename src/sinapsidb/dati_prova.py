"""L'azienda di prova: dati inventati per il confronto con PostgreSQL.

Un'azienda che vende all'ingrosso strumenti musicali e ricambi, con tutto il suo giro: clienti seguiti
da agenti, prodotti comprati da fornitori e tenuti in più magazzini, ordini che diventano spedizioni,
fatture, pagamenti e qualche reso, ordini d'acquisto, movimenti di magazzino, ticket di assistenza.
Venti tabelle.

Si sceglie quante righe si vogliono in tutto (centinaia di migliaia, un milione) e il generatore
dimensiona l'azienda di conseguenza. I dati sono a caso ma coerenti: una fattura è la somma delle sue
righe, un pagamento non supera la fattura, un reso riguarda un prodotto davvero fatturato. A ogni
generazione il seme cambia, quindi cambiano anche i dati; lo stesso seme con la stessa taglia dà gli
stessi dati.

``genera`` produce le righe a blocchi, senza tenerle tutte in memoria. ``carica`` crea le tabelle in
PostgreSQL, le riempie e mette chiavi, riferimenti e indici, come in un database ben tenuto.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable, Iterator
from datetime import date, timedelta
from decimal import Decimal

# --- le tabelle -----------------------------------------------------------------
# Per ogni tabella: etichetta per il portale, colonne con il tipo, colonne uniche, riferimenti, altri indici.

TABELLE_DEF: dict[str, dict] = {
    "categorie": {"etichetta": "Categorie", "colonne": "id integer, nome text, reparto text", "uniche": ["nome"]},
    "fornitori": {"etichetta": "Fornitori", "colonne": "id integer, codice text, ragione_sociale text, citta text, paese text", "uniche": ["codice"]},
    "prodotti": {"etichetta": "Prodotti", "uniche": ["codice"],
                 "colonne": "id integer, codice text, nome text, categoria_id integer, fornitore_id integer, prezzo numeric(10,2), costo numeric(10,2)",
                 "riferimenti": {"categoria_id": "categorie", "fornitore_id": "fornitori"}},
    "magazzini": {"etichetta": "Magazzini", "colonne": "id integer, nome text, citta text", "uniche": ["nome"]},
    "giacenze": {"etichetta": "Giacenze", "colonne": "prodotto_id integer, magazzino_id integer, quantita integer",
                 "chiave": "prodotto_id, magazzino_id", "riferimenti": {"prodotto_id": "prodotti", "magazzino_id": "magazzini"}},
    "agenti": {"etichetta": "Agenti", "colonne": "id integer, codice text, nome text, zona text", "uniche": ["codice"]},
    "clienti": {"etichetta": "Clienti", "uniche": ["codice"],
                "colonne": "id integer, codice text, ragione_sociale text, citta text, provincia text, settore text, agente_id integer",
                "riferimenti": {"agente_id": "agenti"}},
    "dipendenti": {"etichetta": "Dipendenti", "colonne": "id integer, matricola text, nome text, reparto text", "uniche": ["matricola"]},
    "corrieri": {"etichetta": "Corrieri", "colonne": "id integer, nome text, giorni integer", "uniche": ["nome"]},
    "ordini": {"etichetta": "Ordini dei clienti", "colonne": "id integer, numero text, data date, cliente_id integer, stato text",
               "uniche": ["numero"], "riferimenti": {"cliente_id": "clienti"}, "indici": ["cliente_id, data", "data"]},
    "righe_ordine": {"etichetta": "Righe d'ordine", "colonne": "id integer, ordine_id integer, prodotto_id integer, quantita integer, prezzo numeric(10,2)",
                     "riferimenti": {"ordine_id": "ordini", "prodotto_id": "prodotti"}},
    "spedizioni": {"etichetta": "Spedizioni", "uniche": ["numero"],
                   "colonne": "id integer, numero text, data date, ordine_id integer, magazzino_id integer, corriere_id integer, stato text",
                   "riferimenti": {"ordine_id": "ordini", "magazzino_id": "magazzini", "corriere_id": "corrieri"}},
    "fatture": {"etichetta": "Fatture", "colonne": "id integer, numero text, data date, cliente_id integer, ordine_id integer, totale numeric(12,2)",
                "uniche": ["numero"], "riferimenti": {"cliente_id": "clienti", "ordine_id": "ordini"}, "indici": ["cliente_id, data", "data", "totale"]},
    "righe_fattura": {"etichetta": "Righe di fattura", "colonne": "id integer, fattura_id integer, prodotto_id integer, quantita integer, prezzo numeric(10,2)",
                      "riferimenti": {"fattura_id": "fatture", "prodotto_id": "prodotti"}},
    "pagamenti": {"etichetta": "Pagamenti", "colonne": "id integer, fattura_id integer, data date, importo numeric(12,2), metodo text",
                  "riferimenti": {"fattura_id": "fatture"}},
    "resi": {"etichetta": "Resi", "colonne": "id integer, numero text, data date, fattura_id integer, prodotto_id integer, quantita integer, motivo text",
             "uniche": ["numero"], "riferimenti": {"fattura_id": "fatture", "prodotto_id": "prodotti"}},
    "ordini_acquisto": {"etichetta": "Ordini d'acquisto", "uniche": ["numero"],
                        "colonne": "id integer, numero text, data date, fornitore_id integer, magazzino_id integer, totale numeric(12,2)",
                        "riferimenti": {"fornitore_id": "fornitori", "magazzino_id": "magazzini"}},
    "righe_acquisto": {"etichetta": "Righe d'acquisto",
                       "colonne": "id integer, ordine_acquisto_id integer, prodotto_id integer, quantita integer, costo numeric(10,2)",
                       "riferimenti": {"ordine_acquisto_id": "ordini_acquisto", "prodotto_id": "prodotti"}},
    "movimenti": {"etichetta": "Movimenti di magazzino",
                  "colonne": "id integer, data date, prodotto_id integer, magazzino_id integer, tipo text, quantita integer, documento text",
                  "riferimenti": {"prodotto_id": "prodotti", "magazzino_id": "magazzini"}},
    "ticket": {"etichetta": "Ticket di assistenza", "uniche": ["numero"],
               "colonne": "id integer, numero text, data date, cliente_id integer, prodotto_id integer, dipendente_id integer, stato text, priorita text",
               "riferimenti": {"cliente_id": "clienti", "prodotto_id": "prodotti", "dipendente_id": "dipendenti"}, "indici": ["cliente_id, data"]},
}


def _colonne(tabella: str) -> tuple[str, ...]:
    return tuple(c.split()[0] for c in TABELLE_DEF[tabella]["colonne"].split(", "))


TABELLE: dict[str, tuple[str, ...]] = {t: _colonne(t) for t in TABELLE_DEF}
ETICHETTE: dict[str, str] = {t: d["etichetta"] for t, d in TABELLE_DEF.items()}
# Dove si ricorda con quale taglia e quale seme sono nati i dati che ci sono adesso.
GENERAZIONE = "prova_generazione"
# Il fatturato di ogni mese tenuto pronto, come lo tiene un database a tabelle: una vista materializzata. Nasce con i
# dati. Serve alla domanda del confronto in cui tutti e tre i sistemi leggono una risposta già fatta.
VISTA_FATTURATO = "fatturato_per_mese"
SQL_VISTA_FATTURATO = (f"CREATE MATERIALIZED VIEW IF NOT EXISTS {VISTA_FATTURATO} AS "
                       "SELECT to_char(data, 'YYYY-MM') AS mese, SUM(totale) AS totale FROM fatture GROUP BY 1")


def _schema() -> str:
    righe = [f"DROP TABLE IF EXISTS {', '.join(reversed(TABELLE))}, {GENERAZIONE} CASCADE;"]
    for tabella, d in TABELLE_DEF.items():
        colonne = ", ".join(f"{c} NOT NULL" for c in d["colonne"].split(", "))
        righe.append(f"CREATE TABLE {tabella} ({colonne});")
    righe.append(f"CREATE TABLE {GENERAZIONE} (righe_richieste integer NOT NULL, righe integer NOT NULL, seme bigint NOT NULL,"
                 " secondi real NOT NULL, generato timestamptz NOT NULL DEFAULT now());")
    return "\n".join(righe)


def _vincoli() -> str:
    """Chiavi, riferimenti e indici, messi dopo il caricamento come si fa quando si caricano molti dati."""
    righe = []
    for tabella, d in TABELLE_DEF.items():
        righe.append(f"ALTER TABLE {tabella} ADD PRIMARY KEY ({d.get('chiave', 'id')});")
        righe += [f"ALTER TABLE {tabella} ADD UNIQUE ({c});" for c in d.get("uniche", ())]
    for tabella, d in TABELLE_DEF.items():
        for colonna, verso in d.get("riferimenti", {}).items():
            righe.append(f"ALTER TABLE {tabella} ADD FOREIGN KEY ({colonna}) REFERENCES {verso};")
            if d.get("chiave", "").split(", ")[0] != colonna:  # la chiave primaria copre già la sua prima colonna
                righe.append(f"CREATE INDEX ON {tabella} ({colonna});")
        righe += [f"CREATE INDEX ON {tabella} ({c});" for c in d.get("indici", ())]
    righe.append("ANALYZE;")
    return "\n".join(righe)


SCHEMA = _schema()
VINCOLI = _vincoli()

# --- il materiale per inventare -----------------------------------------------------

COGNOMI = ["Rossi", "Bianchi", "Ferrari", "Russo", "Colombo", "Ricci", "Marino", "Greco", "Bruno", "Gallo", "Conti", "Costa",
           "Giordano", "Mancini", "Rizzo", "Lombardi", "Moretti", "Barbieri", "Fontana", "Santoro", "Mariani", "Rinaldi",
           "Caruso", "Ferrara", "Galli", "Martini", "Leone", "Longo", "Gentile", "Martinelli", "Vitale", "Zanetti",
           "Pellegrini", "Farina", "Bellini", "Basile", "Riva", "Donati", "Orlando", "Sala", "Villa", "Cattaneo"]
NOMI = ["Luca", "Marco", "Giulia", "Sara", "Paolo", "Anna", "Davide", "Elena", "Matteo", "Chiara", "Andrea", "Marta",
        "Stefano", "Laura", "Simone", "Silvia", "Fabio", "Irene", "Giorgio", "Paola", "Nicola", "Elisa", "Pietro", "Alice"]
FORME = ["Srl", "Spa", "Snc", "Sas", "& Figli Srl", "Group Srl", "& C. Snc"]
ATTIVITA = ["Strumenti", "Elettronica", "Liuteria", "Audio", "Musica", "Service", "Impianti", "Studio", "Forniture", "Ricambi",
            "Sound", "Note", "Accordi", "Palco"]
RAMI = ["Componenti", "Parts", "Industrie", "Trading", "Elettronica"]
CITTA = [("Verona", "VR"), ("Milano", "MI"), ("Torino", "TO"), ("Bologna", "BO"), ("Padova", "PD"), ("Brescia", "BS"), ("Vicenza", "VI"),
         ("Trento", "TN"), ("Bergamo", "BG"), ("Modena", "MO"), ("Parma", "PR"), ("Firenze", "FI"), ("Roma", "RM"), ("Napoli", "NA"),
         ("Bari", "BA"), ("Genova", "GE"), ("Venezia", "VE"), ("Treviso", "TV"), ("Udine", "UD"), ("Lugano", "TI"), ("Como", "CO"),
         ("Ancona", "AN"), ("Perugia", "PG"), ("Pescara", "PE"), ("Palermo", "PA"), ("Cagliari", "CA")]
SETTORI = ["negozio di strumenti", "liutaio", "studio di registrazione", "scuola di musica", "service audio", "distributore", "riparatore",
           "sala prove", "teatro"]
# categoria: (reparto, prezzo minimo, prezzo massimo)
CATEGORIE = {
    "Pickup": ("Chitarre", 39, 289), "Corde": ("Chitarre", 5, 29), "Ponti": ("Chitarre", 25, 190), "Meccaniche": ("Chitarre", 19, 149),
    "Battipenna": ("Chitarre", 12, 59), "Tastiere": ("Chitarre", 29, 240), "Potenziometri": ("Elettronica", 4, 19),
    "Cavi": ("Elettronica", 9, 79), "Amplificatori": ("Amplificazione", 190, 1890), "Casse": ("Amplificazione", 120, 1400),
    "Pedali": ("Effetti", 59, 349), "Microfoni": ("Audio", 49, 690),
}
MAGAZZINI = [("Magazzino Verona", "Verona"), ("Magazzino Milano", "Milano"), ("Magazzino Bologna", "Bologna"),
             ("Magazzino Roma", "Roma"), ("Deposito Lugano", "Lugano")]
CORRIERI = [("Corriere Alpi", 1), ("Espresso Adige", 2), ("Freccia Sud", 3), ("Posta Veloce", 2), ("Trasporti Lario", 4), ("Elvetica Cargo", 3)]
PAESI = [("Italia", 60), ("Germania", 12), ("Svizzera", 6), ("Giappone", 8), ("Stati Uniti", 8), ("Cina", 6)]
CITTA_ESTERE = {"Germania": ["Monaco", "Berlino", "Stoccarda"], "Svizzera": ["Zurigo", "Lugano", "Basilea"], "Giappone": ["Osaka", "Nagoya"],
                "Stati Uniti": ["Nashville", "Chicago"], "Cina": ["Shenzhen", "Canton"]}
ZONE = ["Nord-Est", "Nord-Ovest", "Centro", "Sud", "Isole", "Svizzera"]
REPARTI = ["Vendite", "Assistenza", "Magazzino", "Acquisti", "Amministrazione", "Assistenza"]
METODI = ["bonifico", "bonifico", "bonifico", "ricevuta bancaria", "ricevuta bancaria", "carta", "contanti"]
MOTIVI = ["difettoso", "articolo errato", "non conforme", "danneggiato nel trasporto", "ripensamento"]
PRIORITA = ["bassa", "media", "media", "alta"]

PRIMO_GIORNO = date(2025, 1, 1)
GIORNI = 690
ULTIMO_GIORNO = PRIMO_GIORNO + timedelta(days=GIORNI - 1)  # il 21 novembre 2026: dopo non è ancora successo niente
_GIORNO = [PRIMO_GIORNO + timedelta(days=g) for g in range(GIORNI)]
# In media un ordine porta con sé circa 15 righe tra tutte le tabelle: serve a dimensionare l'azienda.
RIGHE_PER_ORDINE = 15.0
BLOCCO = 1000  # ordini per blocco: quante righe stanno in memoria insieme
MASSIMO = 20_000_000  # oltre non si genera: è un errore di chi chiama, non una taglia


def dimensioni(righe: int) -> dict[str, int]:
    """Quanto è grande l'azienda che dà all'incirca quel numero di righe in tutto."""
    if not 0 < righe <= MASSIMO:
        raise ValueError(f"righe deve stare tra 1 e {MASSIMO}")
    ordini = max(40, round(righe / RIGHE_PER_ORDINE))
    clienti = max(20, ordini // 8)
    prodotti = max(30, ordini // 16)
    return {"ordini": ordini, "clienti": clienti, "prodotti": prodotti, "fornitori": max(5, prodotti // 25),
            "agenti": max(3, clienti // 60), "dipendenti": 12 + ordini // 1500}


def genera(righe: int = 300_000, seme: int = 417) -> Iterator[tuple[str, list[tuple]]]:
    """Le righe di tutte le tabelle, a blocchi ``(tabella, righe)``, nell'ordine in cui vanno caricate."""
    rng = random.Random(seme)
    caso, intero, scelta = rng.random, rng.randint, rng.choice
    d = dimensioni(righe)
    n_ordini, n_clienti, n_prodotti = d["ordini"], d["clienti"], d["prodotti"]
    centesimo = Decimal("0.01")

    # --- anagrafiche -------------------------------------------------------------
    categorie = [(i, nome, reparto) for i, (nome, (reparto, _, _)) in enumerate(CATEGORIE.items(), 1)]
    yield "categorie", categorie

    paesi = [p for p, peso in PAESI for _ in range(peso)]
    fornitori = []
    for i in range(1, d["fornitori"] + 1):
        paese = scelta(paesi)
        citta = scelta(CITTA)[0] if paese == "Italia" else scelta(CITTA_ESTERE[paese])
        fornitori.append((i, f"S{i:05d}", f"{scelta(COGNOMI)} {scelta(RAMI)} {scelta(FORME)}", citta, paese))
    yield "fornitori", fornitori
    n_fornitori = len(fornitori)
    del fornitori

    prodotti = []  # (id, codice, nome, categoria_id, fornitore_id, prezzo, costo)
    del_fornitore: dict[int, list[int]] = {}
    prezzo_di: list = [None]
    costo_di: list = [None]
    for i in range(1, n_prodotti + 1):
        categoria = categorie[i % len(categorie)]
        _, minimo, massimo = CATEGORIE[categoria[1]]
        prezzo = Decimal(intero(minimo * 100, massimo * 100)) / 100
        costo = (prezzo * intero(45, 70) / 100).quantize(centesimo)
        fornitore = intero(1, n_fornitori)
        del_fornitore.setdefault(fornitore, []).append(i)
        prezzo_di.append(prezzo)
        costo_di.append(costo)
        prodotti.append((i, f"P{i:05d}", f"{categoria[1]} modello {intero(100, 999)}-{i}", categoria[0], fornitore, prezzo, costo))
    yield "prodotti", prodotti
    del prodotti

    magazzini = [(i, nome, citta) for i, (nome, citta) in enumerate(MAGAZZINI, 1)]
    yield "magazzini", magazzini
    giacenze = []
    for p in range(1, n_prodotti + 1):
        for m in rng.sample(magazzini, intero(2, len(magazzini))):
            giacenze.append((p, m[0], intero(0, 400)))
    yield "giacenze", giacenze
    del giacenze

    agenti = [(i, f"A{i:03d}", f"{scelta(NOMI)} {scelta(COGNOMI)}", scelta(ZONE)) for i in range(1, d["agenti"] + 1)]
    yield "agenti", agenti
    clienti = []
    for i in range(1, n_clienti + 1):
        citta, provincia = scelta(CITTA)
        clienti.append((i, f"C{i:05d}", f"{scelta(COGNOMI)} {scelta(ATTIVITA)} {scelta(FORME)}", citta, provincia, scelta(SETTORI), intero(1, len(agenti))))
    yield "clienti", clienti
    del clienti
    dipendenti = [(i, f"D{i:04d}", f"{scelta(NOMI)} {scelta(COGNOMI)}", REPARTI[i % len(REPARTI)]) for i in range(1, d["dipendenti"] + 1)]
    yield "dipendenti", dipendenti
    assistenza = [x[0] for x in dipendenti if x[3] == "Assistenza"]
    yield "corrieri", [(i, nome, giorni) for i, (nome, giorni) in enumerate(CORRIERI, 1)]

    # --- il giro dei documenti ------------------------------------------------------
    ordine_delle_tabelle = ("ordini", "righe_ordine", "spedizioni", "fatture", "righe_fattura", "pagamenti", "resi",
                            "ordini_acquisto", "righe_acquisto", "movimenti", "ticket")
    blocco: dict[str, list[tuple]] = {t: [] for t in ordine_delle_tabelle}
    prossimo = dict.fromkeys(ordine_delle_tabelle, 0)

    def nuovo(tabella: str) -> int:
        prossimo[tabella] += 1
        return prossimo[tabella]

    for i in range(1, n_ordini + 1):
        g = (i - 1) * GIORNI // n_ordini  # i numeri d'ordine crescono con il tempo, come in un'azienda vera
        giorno = _GIORNO[g]
        cliente = 1 + int(n_clienti * caso() ** 2)  # pochi clienti comprano molto, molti comprano poco
        scelti: set[int] = set()
        for _ in range(intero(1, 6)):
            scelti.add(1 + int(n_prodotti * caso() ** 1.6))  # anche i prodotti: pochi vanno molto
        righe_ordine = [(p, intero(1, 12)) for p in sorted(scelti)]
        x = caso()
        g_spedizione = g + intero(1, 4)
        if x < 0.05:
            stato = "annullato"
        elif g_spedizione >= GIORNI or x < 0.08:
            stato = "in lavorazione"
        else:
            stato = "evaso"
        id_ordine = nuovo("ordini")
        blocco["ordini"].append((id_ordine, f"O{giorno.year}/{id_ordine:07d}", giorno, cliente, stato))
        for p, q in righe_ordine:
            blocco["righe_ordine"].append((nuovo("righe_ordine"), id_ordine, p, q, prezzo_di[p]))

        if stato == "evaso":
            data = _GIORNO[g_spedizione]
            magazzino = intero(1, len(MAGAZZINI))
            id_spedizione = nuovo("spedizioni")
            ddt = f"DDT{data.year}/{id_spedizione:07d}"
            y = caso()
            blocco["spedizioni"].append((id_spedizione, ddt, data, id_ordine, magazzino, intero(1, len(CORRIERI)),
                                         "consegnata" if y < 0.9 else "in viaggio" if y < 0.98 else "respinta"))
            id_fattura = nuovo("fatture")
            totale = Decimal(0)
            for p, q in righe_ordine:
                blocco["righe_fattura"].append((nuovo("righe_fattura"), id_fattura, p, q, prezzo_di[p]))
                blocco["movimenti"].append((nuovo("movimenti"), data, p, magazzino, "scarico", q, ddt))
                totale += prezzo_di[p] * q
            blocco["fatture"].append((id_fattura, f"F{data.year}/{id_fattura:07d}", data, cliente, id_ordine, totale))
            # pagamenti: quasi tutte pagate, alcune in due rate, qualcuna a metà, qualcuna per niente
            z = caso()
            meta = (totale / 2).quantize(centesimo)
            rate = [totale] if z < 0.70 else [meta, totale - meta] if z < 0.85 else [meta] if z < 0.90 else []
            g_pagamento = g_spedizione
            for importo in rate:
                g_pagamento += intero(0, 45)
                if g_pagamento < GIORNI and importo > 0:  # quello che cadrebbe nel futuro non è ancora successo
                    blocco["pagamenti"].append((nuovo("pagamenti"), id_fattura, _GIORNO[g_pagamento], importo, scelta(METODI)))
            if caso() < 0.04 and g_spedizione + 5 < GIORNI:
                p, q = scelta(righe_ordine)
                id_reso = nuovo("resi")
                data_reso = _GIORNO[min(GIORNI - 1, g_spedizione + intero(5, 40))]
                blocco["resi"].append((id_reso, f"R{data_reso.year}/{id_reso:06d}", data_reso, id_fattura, p, intero(1, q), scelta(MOTIVI)))
            if caso() < 0.06 and g_spedizione + 3 < GIORNI:
                id_ticket = nuovo("ticket")
                data_ticket = _GIORNO[min(GIORNI - 1, g_spedizione + intero(3, 90))]
                w = caso()
                blocco["ticket"].append((id_ticket, f"T{data_ticket.year}/{id_ticket:06d}", data_ticket, cliente, scelta(righe_ordine)[0],
                                         scelta(assistenza), "aperto" if w < 0.25 else "in lavorazione" if w < 0.40 else "chiuso",
                                         scelta(PRIORITA)))

        if i % 20 == 0:  # ogni venti ordini dei clienti, un ordine a un fornitore per riempire un magazzino
            fornitore = intero(1, n_fornitori)
            suoi = del_fornitore.get(fornitore)
            if suoi:
                id_acquisto = nuovo("ordini_acquisto")
                numero = f"OA{giorno.year}/{id_acquisto:06d}"
                magazzino = intero(1, len(MAGAZZINI))
                totale = Decimal(0)
                for p in rng.sample(suoi, min(len(suoi), intero(4, 12))):
                    q = intero(20, 200)
                    blocco["righe_acquisto"].append((nuovo("righe_acquisto"), id_acquisto, p, q, costo_di[p]))
                    blocco["movimenti"].append((nuovo("movimenti"), giorno, p, magazzino, "carico", q, numero))
                    totale += costo_di[p] * q
                blocco["ordini_acquisto"].append((id_acquisto, numero, giorno, fornitore, magazzino, totale))

        if i % BLOCCO == 0 or i == n_ordini:
            for tabella in ordine_delle_tabelle:
                if blocco[tabella]:
                    yield tabella, blocco[tabella]
                    blocco[tabella] = []


def tutte(righe: int = 300_000, seme: int = 417) -> dict[str, list[tuple]]:
    """Tutte le righe in memoria, tabella per tabella: comodo nei test, da non usare con le taglie grandi."""
    dati: dict[str, list[tuple]] = {t: [] for t in TABELLE}
    for tabella, blocco in genera(righe, seme):
        dati[tabella].extend(blocco)
    return dati


def seme_nuovo() -> int:
    return random.SystemRandom().randrange(1, 1_000_000_000)


def carica(conn, righe: int = 300_000, seme: int | None = None, avanza: Callable[[dict[str, int], str], None] | None = None,
           lettore: str | None = None) -> dict:
    """Crea le tabelle, le riempie e mette chiavi, riferimenti e indici. Sostituisce i dati che c'erano.

    ``conn`` è una connessione psycopg di chi possiede la base di dati. Tutto avviene in una sola
    transazione: se qualcosa va storto restano i dati di prima. ``avanza`` riceve a ogni blocco le righe
    scritte finora per tabella e che cosa si sta facendo. ``lettore`` è un ruolo a cui dare la sola lettura;
    se non esiste viene ignorato.
    """
    from psycopg import sql

    seme = seme_nuovo() if seme is None else seme
    dimensioni(righe)  # rifiuta subito una taglia senza senso, prima di toccare le tabelle
    inizio = time.perf_counter()
    scritte = dict.fromkeys(TABELLE, 0)
    with conn.transaction():
        conn.execute(SCHEMA)
        with conn.cursor() as cur:
            for tabella, blocco in genera(righe, seme):
                with cur.copy(f"COPY {tabella} ({', '.join(TABELLE[tabella])}) FROM STDIN") as copia:
                    for riga in blocco:
                        copia.write_row(riga)
                scritte[tabella] += len(blocco)
                if avanza:
                    avanza(scritte, "scrittura")
        if avanza:
            avanza(scritte, "chiavi e indici")
        conn.execute(VINCOLI)
        conn.execute(SQL_VISTA_FATTURATO)  # la vista di prima è andata via con le tabelle
        secondi = round(time.perf_counter() - inizio, 2)
        conn.execute(sql.SQL("INSERT INTO {} (righe_richieste, righe, seme, secondi) VALUES (%s, %s, %s, %s)").format(sql.Identifier(GENERAZIONE)),
                     (righe, sum(scritte.values()), seme, secondi))
        if lettore and conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (lettore,)).fetchone():
            conn.execute(sql.SQL("GRANT SELECT ON {} TO {}").format(
                sql.SQL(", ").join(sql.Identifier(t) for t in (*TABELLE, GENERAZIONE, VISTA_FATTURATO)), sql.Identifier(lettore)))
    return {"righe": scritte, "totale": sum(scritte.values()), "righe_richieste": righe, "seme": seme, "secondi": secondi}


def main() -> None:
    """Genera l'azienda di prova in PostgreSQL dalla riga di comando. Di norma lo fa il portale, dal
    pulsante della pagina del confronto: questo comando serve per le prove e per chi amministra.

        sinapsidb-dati-prova --dsn dbname=sinapsidb_prova --righe 300000
    """
    import argparse

    import psycopg

    parser = argparse.ArgumentParser(description="Genera in PostgreSQL l'azienda di prova di SinapsiDB.")
    parser.add_argument("--dsn", default="dbname=sinapsidb_prova")
    parser.add_argument("--righe", type=int, default=300_000, help="quante righe in tutto, all'incirca")
    parser.add_argument("--seme", type=int, help="per rifare gli stessi dati; se manca è a caso")
    parser.add_argument("--lettore", help="ruolo a cui dare la sola lettura delle tabelle")
    argomenti = parser.parse_args()
    # UTF-8 anche qui, per scrivere il testo nello stesso modo in cui il confronto lo rilegge (vedi confronto.collega).
    with psycopg.connect(argomenti.dsn, autocommit=True, client_encoding="utf8") as conn:
        esito = carica(conn, argomenti.righe, argomenti.seme, lettore=argomenti.lettore)
    for tabella, n in esito["righe"].items():
        print(f"{ETICHETTE[tabella]:<24} {n:>9} righe")
    print(f"{'totale':<24} {esito['totale']:>9} righe in {esito['secondi']:.1f} secondi, seme {esito['seme']}")


if __name__ == "__main__":
    main()
