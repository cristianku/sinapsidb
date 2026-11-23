"""Test del confronto con PostgreSQL: l'azienda di prova, l'importazione nella rete, le misure e l'API del portale.

I test che parlano con PostgreSQL usano un servizio privato e temporaneo (vedi ``pg_temporaneo``).
Dove non si può avviare vengono saltati; gli altri girano comunque. Come sulla macchina dove gira
SinapsiDB, il portale si collega con un ruolo che non è amministratore: possiede solo la sua base di dati.
"""

from __future__ import annotations

import hashlib
import threading
import time
from decimal import Decimal
from pathlib import Path

import pytest
from starlette.testclient import TestClient

import pg_temporaneo
from sinapsidb import confronto, dati_prova, motore, portale
from sinapsidb import lingua as lingua_di_sinapsidb
from sinapsidb.confronto import Banco, Occupato, domande, importa, misura
from sinapsidb.motore import Rete
from sinapsidb.portale import ConfigPortale

RIGHE = 6000  # circa 400 ordini: basta perché tutte le tabelle abbiano righe e tutte le domande una risposta
SEME = 417
TAGLIE = (4000, 8000)  # le taglie che il portale lascia generare nei test: piccole, per fare in fretta
BASE = "http://127.0.0.1:8419"
AZIONE = {"x-sinapsidb-azione": "1"}
LOCALE = ("127.0.0.1", 5000)
VUOTA = {"neuroni": 0, "fatti": 0, "fatti_in_vigore": 0, "sinapsi": 0, "eventi": 0}


@pytest.fixture(scope="module")
def dati() -> dict[str, list[tuple]]:
    return dati_prova.tutte(RIGHE, SEME)


@pytest.fixture(scope="module")
def cluster():
    """DSN da amministratore di un PostgreSQL temporaneo, con il ruolo ``sinapsidb`` che non è amministratore."""
    try:
        contesto = pg_temporaneo.avvia()
        dsn = contesto.__enter__()
    except pg_temporaneo.NonDisponibile as e:
        pytest.skip(f"PostgreSQL temporaneo non disponibile: {e}")
    try:
        import psycopg

        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute("CREATE ROLE sinapsidb LOGIN")
        yield dsn
    finally:
        contesto.__exit__(None, None, None)


def _base(amministratore: str, nome: str, proprietario: str | None = "sinapsidb") -> str:
    """Crea una base di dati e restituisce il DSN con cui ci si collega il portale (ruolo ``sinapsidb``)."""
    import psycopg

    with psycopg.connect(amministratore, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{nome}"' + (f' OWNER "{proprietario}"' if proprietario else ""))
    return amministratore.replace("user=postgres", "user=sinapsidb").replace("dbname=prova", f"dbname={nome}")


@pytest.fixture(scope="module")
def pg(cluster, dati):
    """DSN del portale su una base di dati con l'azienda di prova già caricata (gli stessi dati di ``dati``)."""
    dsn = _base(cluster, "azienda")
    with confronto.collega(dsn, scrittura=True) as conn:
        esito = dati_prova.carica(conn, RIGHE, SEME)
    assert esito["righe"] == {t: len(r) for t, r in dati.items()} and esito["seme"] == SEME
    return dsn


def _aspetta(stato, secondi: float = 90.0) -> dict:
    """Aspetta la fine del lavoro in corso e restituisce l'ultimo stato."""
    fine = time.monotonic() + secondi
    while time.monotonic() < fine:
        s = stato()
        if not s["lavoro"]["in_corso"]:
            return s
        time.sleep(0.05)
    raise AssertionError("il lavoro non è finito in tempo")


# --- l'azienda di prova --------------------------------------------------------------


def test_generatore_coerente(dati) -> None:
    assert dati_prova.tutte(RIGHE, SEME) == dati  # stesso seme, stessi dati
    assert dati_prova.tutte(RIGHE, SEME + 1) != dati
    assert list(dati) == list(dati_prova.TABELLE) and len(dati) == 20
    for tabella, colonne in dati_prova.TABELLE.items():
        assert dati[tabella], f"{tabella} è vuota"
        assert all(len(r) == len(colonne) for r in dati[tabella]), tabella
    assert 0.9 < sum(len(r) for r in dati.values()) / RIGHE < 1.1

    # chiavi, colonne uniche e riferimenti: quello che PostgreSQL controllerà al caricamento
    for tabella, d in dati_prova.TABELLE_DEF.items():
        colonne = dati_prova.TABELLE[tabella]
        chiave = [colonne.index(c) for c in d.get("chiave", "id").split(", ")]
        assert len({tuple(r[i] for i in chiave) for r in dati[tabella]}) == len(dati[tabella]), tabella
        for unica in d.get("uniche", ()):
            assert len({r[colonne.index(unica)] for r in dati[tabella]}) == len(dati[tabella]), (tabella, unica)
        for colonna, verso in d.get("riferimenti", {}).items():
            esistono = {r[0] for r in dati[verso]}
            assert all(r[colonne.index(colonna)] in esistono for r in dati[tabella]), (tabella, colonna)

    # i conti tornano
    ordini = {o[0]: o for o in dati["ordini"]}
    fatture = {f[0]: f for f in dati["fatture"]}
    righe_ordine: dict[int, list] = {}
    for _, ordine, prodotto, quantita, prezzo in dati["righe_ordine"]:
        righe_ordine.setdefault(ordine, []).append((prodotto, quantita, prezzo))
    righe_fattura: dict[int, list] = {}
    for _, fattura, prodotto, quantita, prezzo in dati["righe_fattura"]:
        righe_fattura.setdefault(fattura, []).append((prodotto, quantita, prezzo))
    for f in fatture.values():
        assert sum(p * q for _, q, p in righe_fattura[f[0]]) == f[5]  # il totale è la somma delle righe
        assert righe_fattura[f[0]] == righe_ordine[f[4]]  # si fattura quello che è stato ordinato
        assert ordini[f[4]][4] == "evaso" and ordini[f[4]][3] == f[3] and f[2] > ordini[f[4]][2]
    pagato: dict[int, Decimal] = {}
    for _, fattura, data, importo, _ in dati["pagamenti"]:
        assert importo > 0 and data >= fatture[fattura][2]
        pagato[fattura] = pagato.get(fattura, Decimal(0)) + importo
    assert all(p <= fatture[f][5] for f, p in pagato.items())  # non si paga più della fattura
    assert 0 < len(pagato) < len(fatture)  # qualche fattura non è ancora pagata
    for _, _, data, fattura, prodotto, quantita, _ in dati["resi"]:
        assert any(p == prodotto and quantita <= q for p, q, _ in righe_fattura[fattura]) and data > fatture[fattura][2]
    assert len(dati["spedizioni"]) == len(fatture) == sum(o[4] == "evaso" for o in ordini.values())
    assert {o[4] for o in ordini.values()} == {"evaso", "in lavorazione", "annullato"}
    acquisti: dict[int, Decimal] = {}
    for _, acquisto, _, quantita, costo in dati["righe_acquisto"]:
        acquisti[acquisto] = acquisti.get(acquisto, Decimal(0)) + costo * quantita
    assert all(acquisti[a[0]] == a[5] for a in dati["ordini_acquisto"])
    assert sum(m[4] == "scarico" for m in dati["movimenti"]) == len(dati["righe_fattura"])
    assert sum(m[4] == "carico" for m in dati["movimenti"]) == len(dati["righe_acquisto"])
    costo_di = {p[0]: p for p in dati["prodotti"]}
    assert all(0 < p[6] < p[5] for p in costo_di.values())  # si vende a più di quanto si compra
    # niente accade nel futuro, e i numeri dei documenti crescono con il tempo
    for tabella in ("ordini", "spedizioni", "fatture", "pagamenti", "resi", "ordini_acquisto", "movimenti", "ticket"):
        i = dati_prova.TABELLE[tabella].index("data")
        assert all(dati_prova.PRIMO_GIORNO <= r[i] <= dati_prova.ULTIMO_GIORNO for r in dati[tabella]), tabella
    assert [o[2] for o in dati["ordini"]] == sorted(o[2] for o in dati["ordini"])


def test_generatore_taglie() -> None:
    conta = dict.fromkeys(dati_prova.TABELLE, 0)
    piu_grande = 0
    for tabella, blocco in dati_prova.genera(100_000, 7):
        conta[tabella] += len(blocco)
        piu_grande = max(piu_grande, len(blocco))
    assert 0.95 < sum(conta.values()) / 100_000 < 1.05  # la taglia chiesta è quella che arriva, a pochi per cento
    assert piu_grande < 20_000  # le righe arrivano a blocchi: non stanno mai tutte in memoria
    assert all(conta.values())
    for sbagliata in (0, -5, dati_prova.MASSIMO + 1):
        with pytest.raises(ValueError):
            dati_prova.dimensioni(sbagliata)
    assert dati_prova.seme_nuovo() != dati_prova.seme_nuovo()


def test_carica_in_postgres(pg, dati) -> None:
    with confronto.collega(pg) as conn:
        for tabella in dati_prova.TABELLE:
            assert conn.execute(f"SELECT count(*) FROM {tabella}").fetchone()[0] == len(dati[tabella]), tabella
        assert conn.execute(f"SELECT righe_richieste, righe, seme FROM {dati_prova.GENERAZIONE}").fetchall() == [
            (RIGHE, sum(len(r) for r in dati.values()), SEME)]
        vincoli = dict(conn.execute("SELECT contype, count(*) FROM pg_constraint WHERE connamespace = 'public'::regnamespace GROUP BY 1").fetchall())
        definizioni = dati_prova.TABELLE_DEF.values()
        assert vincoli["p"] == 20  # una chiave primaria per tabella
        assert vincoli["f"] == sum(len(d.get("riferimenti", {})) for d in definizioni) > 20
        assert vincoli["u"] == sum(len(d.get("uniche", ())) for d in definizioni) > 10
        assert conn.execute("SELECT count(*) FROM pg_indexes WHERE schemaname = 'public'").fetchone()[0] > 60
        # il totale di una fattura presa a caso, ricalcolato da PostgreSQL sulle sue righe
        assert conn.execute("SELECT bool_and(f.totale = r.somma) FROM fatture f JOIN (SELECT fattura_id, sum(quantita * prezzo) AS somma"
                            " FROM righe_fattura GROUP BY 1) r ON r.fattura_id = f.id").fetchone()[0] is True


# --- importazione e misure ------------------------------------------------------


def test_importa_e_risponde_come_i_dati(pg, dati, tmp_path: Path) -> None:
    rete = Rete(str(tmp_path / "rete"), sync="os")
    passi: list[tuple[int, str]] = []
    with confronto.collega(pg) as conn:
        esito = importa(conn, rete, lambda fatti, tabella: passi.append((fatti, tabella)))
    righe = sum(len(r) for r in dati.values())
    assert esito["fatti"] == esito["eventi"] == righe  # un fatto per ogni riga, di ogni tabella
    assert esito["righe_lette"] == {t: len(r) for t, r in dati.items()}
    assert passi[-1] == (righe, "ticket") and [p[0] for p in passi] == sorted(p[0] for p in passi)

    # Le risposte della rete confrontate con i dati generati, senza passare da PostgreSQL.
    d = {x["nome"]: x["sinapsi"] for x in domande(rete)}
    cliente = {c[0]: c[1] for c in dati["clienti"]}
    prodotto = {p[0]: p[1] for p in dati["prodotti"]}
    fornitore = {s[0]: s[1] for s in dati["fornitori"]}
    fornitore_del_prodotto = {p[0]: p[4] for p in dati["prodotti"]}
    magazzino = {m[0]: m[1] for m in dati["magazzini"]}
    corriere = {k[0]: k[1] for k in dati["corrieri"]}
    ordine = {o[0]: o[1] for o in dati["ordini"]}
    fatture = {f[0]: f for f in dati["fatture"]}

    id_fattura, numero, giorno, id_cliente, id_ordine, totale = dati["fatture"][7]
    assert d["Una fattura dal numero"](numero) == [(cliente[id_cliente], giorno.isoformat(), f"{totale:.2f}")]
    assert d["Una fattura dal numero"]("F1999/0000000") == []
    s = dati["spedizioni"][3]
    assert d["Una spedizione dal numero"](s[1]) == [(ordine[s[3]], corriere[s[5]], s[2].isoformat(), s[6])]
    sue = sorted((f[1], f"{f[5]:.2f}") for f in dati["fatture"] if f[3] == id_cliente)
    assert sorted(d["Le fatture di un cliente"](cliente[id_cliente])) == sue
    assert d["Fatturato di un cliente"](cliente[id_cliente]) == [(f"{sum(Decimal(t) for _, t in sue):.2f}",)]
    assert d["Fatturato di un cliente"]("C99999") == [("0",)]  # la somma di niente è zero
    assert sorted(d["Le righe di una fattura"](numero)) == sorted(
        (prodotto[r[2]], str(r[3]), f"{r[4]:.2f}") for r in dati["righe_fattura"] if r[1] == id_fattura)

    pagata = dati["pagamenti"][0][1]
    pagamenti = sorted((p[2].isoformat(), f"{p[3]:.2f}", p[4]) for p in dati["pagamenti"] if p[1] == pagata)
    assert sorted(d["I pagamenti di una fattura"](fatture[pagata][1])) == pagamenti
    assert sorted(d["I pagamenti di un ordine"](ordine[fatture[pagata][4]])) == sorted((fatture[pagata][1], g, i) for g, i, _ in pagamenti)
    annullato = next(o for o in dati["ordini"] if o[4] == "annullato")
    assert d["I pagamenti di un ordine"](annullato[1]) == []

    un_fornitore = dati["prodotti"][0][4]
    assert sorted(d["I prodotti di un fornitore"](fornitore[un_fornitore])) == sorted(
        (p[1], f"{p[5]:.2f}") for p in dati["prodotti"] if p[4] == un_fornitore)
    p, m, quantita = dati["giacenze"][5]
    assert d["Giacenza di un prodotto in un magazzino"](prodotto[p], magazzino[m]) == [(str(quantita),)]
    assert d["Giacenza di un prodotto in un magazzino"](prodotto[p], "Magazzino Che Non Esiste") == []

    aperto = next(t for t in dati["ticket"] if t[6] == "aperto")
    assert sorted(d["I ticket aperti di un cliente"](cliente[aperto[3]])) == sorted(
        (t[1], t[7]) for t in dati["ticket"] if t[3] == aperto[3] and t[6] == "aperto")

    un_prodotto = dati["righe_fattura"][0][2]
    assert {c for (c,) in d["Chi ha comprato un prodotto"](prodotto[un_prodotto])} == {
        cliente[fatture[r[1]][3]] for r in dati["righe_fattura"] if r[2] == un_prodotto}
    dietro = {fornitore[fornitore_del_prodotto[r[2]]] for r in dati["righe_fattura"] if fatture[r[1]][3] == id_cliente}
    assert {x for (x,) in d["I fornitori dietro agli acquisti di un cliente"](cliente[id_cliente])} == dietro and dietro

    # un cliente in un giorno: ordini, fatture e ticket insieme. Si prova su ogni ordine, così capitano anche i giorni con più cose.
    piu_di_uno = 0
    for o in dati["ordini"]:
        attesi = sorted([("ordine ricevuto", x[1], "", "") for x in dati["ordini"] if x[3] == o[3] and x[2] == o[2]]
                        + [("fattura emessa", ordine[f[4]], f[1], "") for f in dati["fatture"] if f[3] == o[3] and f[2] == o[2]]
                        + [("ticket di assistenza", "", "", t[1]) for t in dati["ticket"] if t[3] == o[3] and t[2] == o[2]])
        assert sorted(d["Un cliente in un giorno"](cliente[o[3]], o[2].isoformat())) == attesi
        piu_di_uno += len(attesi) > 1
    assert piu_di_uno > 0

    mesi: dict[str, Decimal] = {}
    for f in dati["fatture"]:
        mesi[f[2].isoformat()[:7]] = mesi.get(f[2].isoformat()[:7], Decimal(0)) + f[5]
    assert sorted(d["Fatturato mese per mese"]()) == [(mese, f"{somma:.2f}") for mese, somma in sorted(mesi.items())]
    sopra = sorted((f[1],) for f in dati["fatture"] if f[5] > 3000)
    assert sorted(d["Fatture sopra un importo"]("3000.00")) == sopra and 0 < len(sopra) < len(dati["fatture"])

    # Riaperta dal disco la rete è la stessa.
    conteggi = rete.conteggi()
    rete.chiudi()
    riaperta = Rete(str(tmp_path / "rete"), sync="os")
    assert riaperta.conteggi() == conteggi
    assert sorted({x["nome"]: x["sinapsi"] for x in domande(riaperta)}["Le fatture di un cliente"](cliente[id_cliente])) == sue
    riaperta.chiudi()


def test_l_importazione_rapida_scrive_lo_stesso_giornale_di_un_racconto_alla_volta(pg, tmp_path: Path, monkeypatch) -> None:
    """L'importazione era lenta. Ora passa da ``racconta_molti``, scrive il giornale a gruppi e si
    ricorda i nomi che tornano: nessuna delle tre cose deve cambiare un byte di quello che la rete scrive."""
    from types import SimpleNamespace

    from sinapsidb import motore
    from sinapsidb.confronto import FATTI, ORIGINE, Vocabolario, _testo, somma_pronta

    monkeypatch.setattr(motore, "time", SimpleNamespace(time=lambda: 1_790_000_000.5))
    rapida, lenta = Rete(str(tmp_path / "rapida"), sync="os"), Rete(str(tmp_path / "lenta"), sync="os")
    with confronto.collega(pg) as conn:
        importa(conn, rapida)
        # com'era prima: un racconto per riga, ogni nome cercato nella rete, un evento alla volta nel giornale
        somma_pronta(lenta)
        v = Vocabolario(lenta)
        for fatto in FATTI:
            tipo, ruoli = v.tipo[fatto.tabella], [v.ruolo[r] for r in fatto.ruoli]
            with conn.transaction(), conn.cursor(name="importazione") as cur:
                cur.execute(fatto.sql)
                for riga in cur:
                    testi = [_testo(x) for x in riga]
                    lenta.racconta(fatto.frase.format(*testi), ORIGINE, [(tipo, list(zip(ruoli, [lenta.n(t) for t in testi])))])
    for rete in (rapida, lenta):
        rete.chiudi()
    assert (tmp_path / "rapida" / "giornale").read_bytes() == (tmp_path / "lenta" / "giornale").read_bytes()
    assert rapida.conteggi() == lenta.conteggi() and rapida.valori == lenta.valori and rapida.vivi == lenta.vivi and rapida.nomi == lenta.nomi


def test_misura_risposte_uguali(pg, tmp_path: Path) -> None:
    import psycopg

    rete = Rete(str(tmp_path / "rete"), sync="os")
    passi: list[str] = []
    with confronto.collega(pg) as conn:
        importa(conn, rete)
        esito = misura(conn, rete, quante=40, avanza=lambda *passo: passi.append(passo))
        # la connessione del confronto è in sola lettura: in PostgreSQL scrive solo il lavoro che genera i dati
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            conn.execute("DELETE FROM giacenze")
    rete.chiudi()
    assert esito["risposte_diverse"] == 0
    assert len(esito["domande"]) == 16 == len(confronto.DOMANDE) and esito["saltate"] == []
    # chi guarda sa a che punto è: tre passi per domanda, contati dal primo all'ultimo, e ogni passo dice di che domanda è
    assert [(fatto, da_fare) for _, fatto, da_fare in passi] == [(i, 48) for i in range(48)]
    for i, d in enumerate(esito["domande"]):
        assert all(f"domanda {i + 1} di 16, «{d['nome']}»" in testo for testo, _, _ in passi[3 * i:3 * i + 3])
    assert passi[0][0].endswith("controllo che le risposte siano uguali") and passi[1][0].endswith("misura dei tempi")
    for d in esito["domande"]:
        assert d["prove"] > 0 and d["risposte_diverse"] == 0, d["nome"]
        assert d["postgres"]["p50_us"] > 0 and d["sinapsi"]["p50_us"] > 0 and d["postgres_nel_server_p50_us"] > 0
        assert d["postgres"]["p99_us"] >= d["postgres"]["p50_us"] and d["sinapsi"]["p99_us"] >= d["sinapsi"]["p50_us"]
        # il tempo medio c'è per tutti, e una media sta tra il più rapido e il più lento
        assert d["sinapsi"]["media_us"] > 0 and d["postgres"]["media_us"] > 0 and d["postgres_nel_server_media_us"] > 0, d["nome"]
        assert d["redis_nel_server_media_us"] is None
        assert d["sql"].startswith("SELECT") and d["lingua"].startswith(("accendi ", "in ", "circuito "))
        assert d["righe_medie"] > 0, d["nome"]  # nessuna domanda misurata solo su risposte vuote
    assert [d["senza_partenza"] for d in esito["domande"]] == [False] * 13 + [True, False, True]
    # il fatturato per mese c'è due volte: calcolato al momento, e tenuto pronto da un circuito e da una vista
    calcolato, pronto = esito["domande"][13], esito["domande"][14]
    assert (calcolato["nome"], pronto["nome"]) == ("Fatturato mese per mese", "Fatturato mese per mese, tenuto pronto")
    assert pronto["pronta"] and not calcolato["pronta"] and pronto["righe_medie"] == calcolato["righe_medie"] > 5
    assert pronto["lingua"] == "circuito fatturato per mese" and "fatturato_per_mese" in pronto["sql"]
    # i conteggi parlano dei record: i due fatti che definiscono il circuito non si contano
    assert esito["fatti"] == rete.conteggi()["fatti"] - 2 == confronto.conteggi(rete)["fatti"] and esito["versione_postgres"]


# --- il banco e l'API del portale -----------------------------------------------


def test_un_lavoro_alla_volta(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    banco = Banco(tmp_path / "prova", "host=/nonesiste port=1 dbname=x", TAGLIE)
    partito, via = threading.Event(), threading.Event()

    def lento(self) -> None:
        partito.set()
        assert via.wait(10)

    monkeypatch.setattr(Banco, "_importa", lento)
    assert banco.avvia("boh") == "lavoro sconosciuto"
    assert "taglia non ammessa" in banco.avvia("genera") and "taglia non ammessa" in banco.avvia("genera", 123)
    assert banco.avvia("importa") is None
    assert partito.wait(5)
    assert "già un lavoro" in banco.avvia("misura") and "già un lavoro" in banco.avvia("genera", TAGLIE[0])
    s = banco.stato()
    assert s["lavoro"]["in_corso"] and s["lavoro"]["nome"] == "importa" and s["sinapsi"] is None
    with pytest.raises(Occupato):  # durante un lavoro la rete è sua: una domanda non resta ad aspettare
        banco.chiedi("vocabolario")
    with pytest.raises(Occupato):
        banco.aiuto()
    via.set()
    s = _aspetta(banco.stato)
    assert s["lavoro"]["errore"] is None and s["lavoro"]["finito"] >= s["lavoro"]["iniziato"]
    assert s["sinapsi"] == VUOTA and s["taglie"] == list(TAGLIE) and s["taglia_proposta"] == max(TAGLIE)
    banco.chiudi()


def test_senza_postgres_il_portale_lo_dice(tmp_path: Path) -> None:
    config = ConfigPortale(metriche_db=tmp_path / "m.db", sonda_intervallo=0, prova_pg=f"host={tmp_path}/nessuno port=1 dbname=x", prova_taglie=TAGLIE)
    with TestClient(portale.crea_app(config), base_url=BASE, client=LOCALE) as c:
        stato = lambda: c.get("/api/confronto").json()  # noqa: E731
        s = stato()
        assert s["postgres"]["raggiungibile"] is False and s["postgres"]["disponibile"] is False and s["postgres"]["errore"]
        assert s["sinapsi"] == VUOTA and s["importazione"] is None and s["confronto"] is None
        assert (tmp_path / "prova" / "rete").is_dir()  # la rete di prova sta accanto alle metriche
        # ogni lavoro parte ma fallisce, e lo stato dice perché: la rete resta vuota
        for lavoro, domanda in (("genera", f"?righe={TAGLIE[0]}"), ("importa", ""), ("misura", "")):
            assert c.post(f"/api/confronto/{lavoro}{domanda}", headers=AZIONE).status_code == 202
            s = _aspetta(stato)
            assert s["lavoro"]["nome"] == lavoro and s["lavoro"]["errore"] and s["sinapsi"] == VUOTA, lavoro
        assert "vuota" in s["lavoro"]["errore"]


def test_portale_dall_inizio_alla_fine(cluster, tmp_path: Path) -> None:
    dsn = _base(cluster, "portale")
    config = ConfigPortale(metriche_db=tmp_path / "m.db", sonda_intervallo=0, prova_pg=dsn, prova_dir=tmp_path / "banco", prova_taglie=TAGLIE)
    with TestClient(portale.crea_app(config), base_url=BASE, client=LOCALE) as c:
        stato = lambda: c.get("/api/confronto").json()  # noqa: E731

        r = c.get("/api/confronto")
        assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
        s = r.json()
        assert s["postgres"]["raggiungibile"] and s["postgres"]["puo_scrivere"] and not s["postgres"]["disponibile"]
        assert "non è ancora stata generata" in s["postgres"]["errore"] and s["taglie"] == list(TAGLIE) and s["sinapsi"] == VUOTA
        assert c.get("/api/confronto/aiuto").json() == {"vocabolario": [], "esempi": []}  # rete vuota: niente da proporre

        # solo la nostra pagina può lanciare un lavoro, e solo nelle taglie ammesse
        assert c.post(f"/api/confronto/genera?righe={TAGLIE[0]}").status_code == 403
        assert c.post(f"/api/confronto/genera?righe={TAGLIE[0]}", headers={**AZIONE, "origin": "https://altro.example"}).status_code == 403
        assert c.post("/api/confronto/importa", headers={**AZIONE, "origin": "null"}).status_code == 403
        for sbagliata in ("", "?righe=", "?righe=abc", "?righe=5000000", "?righe=-1", f"?righe={TAGLIE[0]}.5"):
            assert c.post(f"/api/confronto/genera{sbagliata}", headers=AZIONE).status_code == 400, sbagliata
        assert c.get("/api/confronto/genera").status_code == 405
        assert c.post("/api/confronto/boh", headers=AZIONE).status_code == 404
        assert stato()["lavoro"]["nome"] is None

        # 1. genera: il portale dice quanti record ha messo in PostgreSQL, tabella per tabella
        assert c.post(f"/api/confronto/genera?righe={TAGLIE[0]}", headers={**AZIONE, "origin": BASE}).status_code == 202
        s = _aspetta(stato)
        assert s["lavoro"]["errore"] is None, s["lavoro"]
        pg = s["postgres"]
        assert pg["disponibile"] and [t["nome"] for t in pg["tabelle"]] == list(dati_prova.TABELLE)
        assert all(t["righe"] > 0 and t["etichetta"] for t in pg["tabelle"])
        assert pg["totale"] == sum(t["righe"] for t in pg["tabelle"]) == sum(pg["righe"].values())
        assert 0.9 < pg["totale"] / TAGLIE[0] < 1.1
        assert pg["generazione"]["righe_richieste"] == TAGLIE[0] and pg["generazione"]["seme"] > 0 and pg["generazione"]["secondi"] >= 0
        primo_seme, primo_totale = pg["generazione"]["seme"], pg["totale"]
        assert s["sinapsi"] == VUOTA and s["confronto"] is None

        # 2. importa: un fatto per ogni record
        assert c.post("/api/confronto/importa", headers=AZIONE).status_code == 202
        s = _aspetta(stato)
        assert s["lavoro"]["errore"] is None, s["lavoro"]
        assert s["sinapsi"]["fatti"] == primo_totale == s["importazione"]["fatti"] and s["importazione"]["secondi"] >= 0
        # una domanda libera alla rete di prova, nella lingua di SinapsiDB: gli esempi proposti dalla pagina funzionano tutti
        aiuto = c.get("/api/confronto/aiuto").json()
        # venti tipi di fatto per i record, più i due che definiscono la somma tenuta pronta: anche lei è scritta nella rete
        assert len(aiuto["vocabolario"]) == 22 and sum(v["fatti"] for v in aiuto["vocabolario"]) == primo_totale + 2
        assert {v["tipo"] for v in aiuto["vocabolario"]} >= {"circuito", "termine", "fattura emessa"}
        assert len(aiuto["esempi"]) >= 11
        for esempio in aiuto["esempi"]:
            r = c.post("/api/confronto/chiedi", json={"domanda": esempio["domanda"]}, headers=AZIONE)
            assert r.status_code == 200 and r.headers["cache-control"] == "no-store", (esempio, r.text)
            risposta = r.json()
            assert risposta["colonne"] and risposta["piano"] and len(risposta["righe"]) <= 200, esempio
            assert risposta["quante"] > 0 or "> 5000" in esempio["domanda"], esempio
        r = c.post("/api/confronto/chiedi", json={"domanda": "in fattura emessa conta"}, headers=AZIONE).json()
        assert r["righe"] == [[str(pg["righe"]["fatture"])]] and r["esecuzione_us"] > 0
        grande = c.post("/api/confronto/chiedi", json={"domanda": "in riga d'ordine leggi ordine, prodotto"}, headers=AZIONE).json()
        assert grande["quante"] == 200 and grande["troncata"]  # la pagina riceve al massimo duecento righe
        assert c.post("/api/confronto/chiedi", json={"domanda": "in fattura emessa conta"}).status_code == 403
        assert c.post("/api/confronto/chiedi", json={"domanda": "in fattura emessa conta"}, headers={**AZIONE, "origin": "https://altro.example"}).status_code == 403
        r = c.post("/api/confronto/chiedi", json={"domanda": "in fatura emessa conta"}, headers=AZIONE)
        assert r.status_code == 400 and "«fattura emessa»" in r.json()["error"]
        for corpo in (b"non e' json", b'{"altro": 1}', b'{"domanda": 5}', b"[]", b'{"domanda": "' + b"x" * 9000 + b'"}'):
            r = c.post("/api/confronto/chiedi", content=corpo, headers={**AZIONE, "content-type": "application/json"})
            assert r.status_code == 400, corpo[:20]
        assert stato()["sinapsi"]["fatti"] == primo_totale  # le domande leggono soltanto

        # una seconda importazione sopra la prima non parte: la rete va prima azzerata
        assert c.post("/api/confronto/importa", headers=AZIONE).status_code == 202
        s = _aspetta(stato)
        assert "azzerata" in s["lavoro"]["errore"] and s["sinapsi"]["fatti"] == primo_totale

        # 3. confronta
        assert c.post("/api/confronto/misura", headers=AZIONE).status_code == 202
        s = _aspetta(stato, 180)
        assert s["lavoro"]["errore"] is None, s["lavoro"]
        assert s["confronto"]["risposte_diverse"] == 0 and len(s["confronto"]["domande"]) == 16 and s["confronto"]["fatti"] == primo_totale

    # Il portale riparte: la rete di prova e l'ultimo confronto ci sono ancora.
    with TestClient(portale.crea_app(config), base_url=BASE, client=LOCALE) as c:
        stato = lambda: c.get("/api/confronto").json()  # noqa: E731
        s = stato()
        assert s["sinapsi"]["fatti"] == primo_totale and len(s["confronto"]["domande"]) == 16 and s["lavoro"]["nome"] is None

        # Rigenerare con un'altra taglia dà altri dati, e svuota la rete di prova e il confronto che parlavano dei vecchi.
        assert c.post(f"/api/confronto/genera?righe={TAGLIE[1]}", headers=AZIONE).status_code == 202
        s = _aspetta(stato)
        assert s["lavoro"]["errore"] is None, s["lavoro"]
        assert s["postgres"]["generazione"]["righe_richieste"] == TAGLIE[1] and s["postgres"]["generazione"]["seme"] != primo_seme
        assert 0.9 < s["postgres"]["totale"] / TAGLIE[1] < 1.1
        assert s["sinapsi"] == VUOTA and s["importazione"] is None and s["confronto"] is None

        assert c.post("/api/confronto/importa", headers=AZIONE).status_code == 202
        s = _aspetta(stato)
        assert s["sinapsi"]["fatti"] == s["postgres"]["totale"]
        assert c.post("/api/confronto/azzera", headers=AZIONE).status_code == 202
        s = _aspetta(stato)
        assert s["lavoro"]["errore"] is None and s["sinapsi"] == VUOTA
        assert 0.9 < s["postgres"]["totale"] / TAGLIE[1] < 1.1  # azzerare la rete di prova non tocca PostgreSQL


def test_genera_dove_ci_sono_tabelle_di_un_altro(cluster, tmp_path: Path) -> None:
    """Come sulla macchina dove gira SinapsiDB: le prime tabelle le aveva create l'amministratore di PostgreSQL."""
    import psycopg

    dsn = _base(cluster, "vecchia")
    with psycopg.connect(cluster.replace("dbname=prova", "dbname=vecchia"), autocommit=True) as conn:
        conn.execute("CREATE TABLE clienti (id integer PRIMARY KEY, codice text); CREATE TABLE fatture (id integer PRIMARY KEY,"
                     " cliente_id integer REFERENCES clienti); INSERT INTO clienti VALUES (1, 'C00001'); GRANT SELECT ON clienti, fatture TO sinapsidb")
    banco = Banco(tmp_path / "prova", dsn, TAGLIE)
    s = banco.stato()
    assert s["postgres"]["raggiungibile"] and s["postgres"]["puo_scrivere"] and not s["postgres"]["disponibile"]
    assert banco.avvia("genera", TAGLIE[0]) is None
    s = _aspetta(banco.stato)
    assert s["lavoro"]["errore"] is None, s["lavoro"]
    assert s["postgres"]["disponibile"] and 0.9 < s["postgres"]["totale"] / TAGLIE[0] < 1.1
    with psycopg.connect(cluster.replace("dbname=prova", "dbname=vecchia"), autocommit=True) as conn:
        assert {r[0] for r in conn.execute("SELECT DISTINCT tableowner FROM pg_tables WHERE schemaname = 'public'")} == {"sinapsidb"}
    banco.chiudi()


def test_senza_permesso_di_scrivere_il_portale_lo_dice(cluster, tmp_path: Path) -> None:
    dsn = _base(cluster, "sola_lettura", proprietario=None)  # la base di dati è dell'amministratore: il portale può solo leggere
    banco = Banco(tmp_path / "prova", dsn, TAGLIE)
    s = banco.stato()
    assert s["postgres"]["raggiungibile"] and s["postgres"]["puo_scrivere"] is False and not s["postgres"]["disponibile"]
    assert banco.avvia("genera", TAGLIE[0]) is None
    s = _aspetta(banco.stato)
    assert "permesso" in s["lavoro"]["errore"] and "proprietario" in s["lavoro"]["errore"]
    assert not s["postgres"]["disponibile"] and s["sinapsi"] == VUOTA
    banco.chiudi()


def test_lo_stato_non_resta_appeso_a_una_tabella_bloccata(pg, tmp_path: Path) -> None:
    """Mentre qualcuno tiene bloccata una tabella (come fa la generazione) lo stato deve rispondere lo stesso, e in fretta."""
    banco = Banco(tmp_path / "prova", pg, TAGLIE)
    assert banco.stato()["postgres"]["disponibile"]
    banco._pg = {"letto": 0.0, "stato": None}  # la prossima lettura torna a interrogare PostgreSQL
    with confronto.collega(pg, scrittura=True) as blocca, blocca.transaction():
        blocca.execute("LOCK TABLE fatture IN ACCESS EXCLUSIVE MODE")
        inizio = time.monotonic()
        s = banco.stato()
        assert time.monotonic() - inizio < 8
        assert s["postgres"]["raggiungibile"] and not s["postgres"]["disponibile"] and "lock" in s["postgres"]["errore"].lower()
    banco._pg = {"letto": 0.0, "stato": None}
    assert banco.stato()["postgres"]["disponibile"]
    banco.chiudi()


def test_postgres_senza_lingua_configurata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    """Un cluster creato con la lingua «C» ha la codifica SQL_ASCII, come su una macchina appena installata.

    Lì il driver restituisce il testo come byte se non gli si chiede UTF-8: la 0.3.0 rispondeva 500 su
    /api/confronto. Tutto il giro deve funzionare anche così: generazione dal comando, stato, importazione, confronto.
    """
    import psycopg

    try:
        contesto = pg_temporaneo.avvia(locale="C")
        dsn = contesto.__enter__()
    except pg_temporaneo.NonDisponibile as e:
        pytest.skip(f"PostgreSQL temporaneo non disponibile: {e}")
    try:
        with psycopg.connect(dsn, autocommit=True) as conn:
            assert conn.execute("SHOW server_encoding").fetchone()[0] == b"SQL_ASCII"  # la condizione che si vuole provare
        monkeypatch.setattr("sys.argv", ["sinapsidb-dati-prova", "--dsn", dsn, "--righe", str(RIGHE), "--seme", "5"])
        dati_prova.main()
        uscita = capsys.readouterr().out
        assert "Ticket di assistenza" in uscita and "seme 5" in uscita
        config = ConfigPortale(metriche_db=tmp_path / "m.db", sonda_intervallo=0, prova_pg=dsn, prova_taglie=TAGLIE)
        with TestClient(portale.crea_app(config), base_url=BASE, client=LOCALE) as c:
            stato = lambda: c.get("/api/confronto").json()  # noqa: E731
            r = c.get("/api/confronto")
            assert r.status_code == 200, r.text
            s = r.json()
            assert s["postgres"]["disponibile"] and isinstance(s["postgres"]["versione"], str) and s["postgres"]["generazione"]["seme"] == 5
            assert c.post("/api/confronto/importa", headers=AZIONE).status_code == 202
            s = _aspetta(stato)
            assert s["lavoro"]["errore"] is None, s["lavoro"]
            assert c.post("/api/confronto/misura", headers=AZIONE).status_code == 202
            s = _aspetta(stato, 180)
            assert s["lavoro"]["errore"] is None, s["lavoro"]
            assert s["confronto"]["risposte_diverse"] == 0 and isinstance(s["confronto"]["versione_postgres"], str)
    finally:
        contesto.__exit__(None, None, None)


def test_taglie_dall_ambiente(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SINAPSIDB_METRICHE_DB", str(tmp_path / "m.db"))
    monkeypatch.delenv("SINAPSIDB_PROVA_TAGLIE", raising=False)
    assert ConfigPortale.da_ambiente().prova_taglie == confronto.TAGLIE == (100_000, 300_000, 500_000, 1_000_000)
    assert confronto.TAGLIA_PROPOSTA == 1_000_000  # i dati di prova sono nell'ordine del milione di record
    monkeypatch.setenv("SINAPSIDB_PROVA_TAGLIE", "50000, 200000")
    assert ConfigPortale.da_ambiente().prova_taglie == (50_000, 200_000)
    monkeypatch.setenv("SINAPSIDB_PROVA_TAGLIE", "")
    assert ConfigPortale.da_ambiente().prova_taglie == confronto.TAGLIE


def test_pagina_del_confronto(tmp_path: Path) -> None:
    config = ConfigPortale(metriche_db=tmp_path / "m.db", sonda_intervallo=0, prova_pg=f"host={tmp_path}/nessuno port=1 dbname=x")
    with TestClient(portale.crea_app(config), base_url=BASE, client=LOCALE) as c:
        for percorso, tipo in (("/confronto", "text/html"), ("/confronto.js", "text/javascript"), ("/confronto.css", "text/css")):
            r = c.get(percorso)
            assert r.status_code == 200 and r.headers["content-type"].startswith(tipo) and r.content, percorso
            assert "default-src 'self'" in r.headers["content-security-policy"]
        pagina = c.get("/confronto").content
        assert b"/confronto.js" in pagina and b"<script>" not in pagina and b"style=" not in pagina
        for pezzo in (b'id="genera"', b'id="taglia"', b'id="importa"', b'id="azzera"', b'id="misura"', b'id="stato-pg"', b'id="domanda"', b'id="chiedi"',
                      b'id="tabella-rapporti"', b'id="grafico-domande"'):
            assert pezzo in pagina, pezzo
        codice = c.get("/confronto.js").content
        assert b"innerHTML" not in codice and b"x-sinapsidb-azione" in codice and b"/api/confronto/chiedi" in codice
        # le tre pagine si raggiungono l'una dall'altra, e l'inizio porta alle decisioni
        for percorso in ("/decisioni", "/dati", "/confronto"):
            corpo = c.get(percorso).content
            assert all(f'href="{p}"'.encode() in corpo for p in ("/decisioni", "/dati", "/confronto")), percorso
        inizio = c.get("/", follow_redirects=False)
        assert inizio.status_code == 307 and inizio.headers["location"] == "/decisioni"


def test_la_rete_si_riapre_in_sottofondo(pg: str, tmp_path: Path, monkeypatch) -> None:
    """Rileggere dal disco una rete grande dura un minuto: lo stato non resta ad aspettarla, dice che si sta riaprendo.
    Segnalato da chi installa: dopo un riavvio con dieci milioni di fatti lo stato non rispondeva per un minuto."""
    primo = Banco(tmp_path / "banco", pg, TAGLIE)
    try:
        s = primo.stato()  # una rete piccola si apre in un attimo: nessun lavoro da mostrare
        assert s["lavoro"]["nome"] is None and s["sinapsi"] == VUOTA
        assert primo.avvia("importa") is None and _aspetta(primo.stato)["lavoro"]["errore"] is None
        fatti = primo.stato()["sinapsi"]["fatti"]
        assert fatti > 5000
    finally:
        primo.chiudi()

    vera, via = confronto.Rete, threading.Event()

    def lenta(*argomenti, **opzioni):
        assert via.wait(60)
        return vera(*argomenti, **opzioni)

    monkeypatch.setattr(confronto, "Rete", lenta)
    banco = Banco(tmp_path / "banco", pg, TAGLIE)
    try:
        inizio = time.monotonic()
        s = banco.stato()
        assert time.monotonic() - inizio < 3.5  # non ha aspettato la riapertura
        assert s["lavoro"]["nome"] == "apertura" and s["lavoro"]["in_corso"] and s["sinapsi"] is None
        assert s["postgres"]["disponibile"]  # il resto dello stato c'è
        with pytest.raises(Occupato):  # una domanda non resta ad aspettare
            banco.chiedi("in fattura emessa conta")
        assert "riapertura della rete di prova" in banco.avvia("misura")  # e un lavoro dice perché non parte
        time.sleep(2.1)  # una riapertura lunga resta scritta nello stato, con quanto è durata
        via.set()
        s = _aspetta(banco.stato)
        assert s["lavoro"]["nome"] == "apertura" and s["lavoro"]["errore"] is None and s["sinapsi"]["fatti"] == fatti
        assert int(banco.chiedi("in fattura emessa conta")["righe"][0][0]) > 0
    finally:
        banco.chiudi()


def test_una_rete_che_non_si_apre_si_dice_e_si_azzera(pg: str, tmp_path: Path, monkeypatch) -> None:
    vera, aperture = confronto.Rete, []

    def rotta(*argomenti, **opzioni):
        aperture.append(1)
        if len(aperture) <= 1:
            raise OSError("giornale illeggibile")
        return vera(*argomenti, **opzioni)

    monkeypatch.setattr(confronto, "Rete", rotta)
    banco = Banco(tmp_path / "banco", pg, TAGLIE)
    try:
        s = banco.stato()
        assert "giornale illeggibile" in s["sinapsi"]["errore"] and not s["lavoro"]["in_corso"] and "giornale illeggibile" in s["lavoro"]["errore"]
        assert "giornale illeggibile" in banco.stato()["sinapsi"]["errore"] and len(aperture) == 1  # non riprova a ogni richiesta
        with pytest.raises(Occupato, match="giornale illeggibile"):
            banco.chiedi("in fattura emessa conta")
        assert banco.avvia("azzera") is None  # azzerarla la rimedia
        s = _aspetta(banco.stato)
        assert s["lavoro"]["errore"] is None and s["sinapsi"] == VUOTA
    finally:
        banco.chiudi()


def test_la_rete_di_prova_sul_disco_da_le_stesse_risposte(pg: str, tmp_path: Path) -> None:
    """La rete di prova portata sul disco, con in memoria solo i blocchi che servono: stessi conteggi, stesse risposte
    di PostgreSQL in tutte e quindici le domande, e il tetto di memoria rispettato."""
    tetto = 48 << 10  # quarantotto kilobyte, tre o quattro blocchi: quasi ogni domanda deve buttare fuori qualcosa
    banco = Banco(tmp_path / "banco", pg, TAGLIE, tetto_blocchi=tetto)

    def lancia(nome: str) -> dict:
        assert banco.avvia(nome) is None
        return _aspetta(banco.stato)

    try:
        s = banco.stato()
        assert s["rete"]["modo"] == "memoria" and s["rete"]["portale"] > 0
        assert banco.avvia("disco") is None  # una rete vuota non ha niente da portare sul disco
        assert "è vuota" in _aspetta(banco.stato)["lavoro"]["errore"]
        assert lancia("importa")["lavoro"]["errore"] is None
        in_memoria = lancia("misura")
        assert in_memoria["confronto"]["rete"] == {"modo": "memoria"} and in_memoria["confronto"]["risposte_diverse"] == 0
        assert all(d["sinapsi_prima_volta"] is None for d in in_memoria["confronto"]["domande"])
        conteggi, una = in_memoria["sinapsi"], banco.chiedi("in fattura emessa somma totale per mese(quando)")

        s = lancia("disco")
        assert s["lavoro"]["errore"] is None and s["rete"]["modo"] == "disco" and s["sinapsi"] == conteggi
        # sul disco vanno tutti i fatti della rete, compresi i due che definiscono la somma pronta
        assert s["rete"]["costruzione"]["fatti"] == conteggi["fatti"] + 2 and s["rete"]["su_disco"] > 100_000 and s["rete"]["tetto"] == tetto
        assert banco._rete is None  # la rete in memoria è stata lasciata andare
        assert "già sul disco" in lancia("disco")["lavoro"]["errore"]
        # le domande libere: stessa risposta, stesso piano
        dal_disco = banco.chiedi("in fattura emessa somma totale per mese(quando)")
        assert sorted(dal_disco["righe"]) == sorted(una["righe"]) and dal_disco["piano"] == una["piano"]
        assert banco.aiuto()["esempi"] and banco.aiuto()["vocabolario"]
        # il confronto: le quindici domande, controllate contro PostgreSQL, con la prima volta misurata a parte
        s = lancia("misura")
        c = s["confronto"]
        assert s["lavoro"]["errore"] is None and c["risposte_diverse"] == 0 and c["fatti"] == conteggi["fatti"]
        assert c["rete"]["modo"] == "disco" and c["rete"]["espulsi"] > 0 and c["rete"]["in_memoria"] <= tetto
        assert all(d["sinapsi_prima_volta"]["p50_us"] > 0 and d["sinapsi"]["p50_us"] > 0 for d in c["domande"])
        # la somma tenuta pronta c'è solo nella rete in memoria: sul disco quella domanda resta fuori, e il confronto lo dice
        assert len(c["domande"]) == 15 and [x["nome"] for x in c["saltate"]] == ["Fatturato mese per mese, tenuto pronto"]
        assert "solo nella rete tutta in memoria" in c["saltate"][0]["perche"] and len(in_memoria["confronto"]["domande"]) == 16
        with pytest.raises(lingua_di_sinapsidb.ErroreDomanda, match="non tiene somme pronte"):
            banco.chiedi("circuito fatturato per mese")
        assert s["rete"]["in_memoria"] <= tetto and s["rete"]["caricati"] > s["rete"]["espulsi"] > 0
        # importare sopra una rete che sta sul disco non si può: va azzerata
        assert "va azzerata" in lancia("importa")["lavoro"]["errore"]
    finally:
        banco.chiudi()

    # il portale riparte: la rete resta sul disco, e non c'è niente da riaprire
    banco = Banco(tmp_path / "banco", pg, TAGLIE, tetto_blocchi=tetto)
    try:
        s = banco.stato()
        assert s["rete"]["modo"] == "disco" and s["sinapsi"] == conteggi and s["lavoro"]["nome"] is None and banco._rete is None
        # di nuovo tutta in memoria: stessi conteggi; e tornare sul disco non ricostruisce i blocchi
        s = lancia("memoria")
        assert s["lavoro"]["errore"] is None and s["rete"]["modo"] == "memoria" and s["sinapsi"] == conteggi and banco._rete is not None
        assert "già tutta in memoria" in lancia("memoria")["lavoro"]["errore"]
        costruiti = (tmp_path / "banco" / "blocchi" / "testa.json").stat().st_mtime_ns
        s = lancia("disco")
        assert s["lavoro"]["errore"] is None and s["rete"]["modo"] == "disco"
        assert (tmp_path / "banco" / "blocchi" / "testa.json").stat().st_mtime_ns == costruiti
        # azzerare butta via anche i blocchi, e la rete nuova nasce in memoria
        s = lancia("azzera")
        assert s["rete"]["modo"] == "memoria" and s["sinapsi"] == VUOTA and not (tmp_path / "banco" / "blocchi").exists()
    finally:
        banco.chiudi()


def test_la_somma_pronta_arriva_anche_a_chi_ha_importato_e_generato_prima(cluster, tmp_path: Path, monkeypatch) -> None:
    """Una rete di prova importata prima che la somma pronta esistesse, e dati di PostgreSQL generati prima che esistesse
    la vista: il confronto se ne accorge, scrive il circuito partendo dalle fatture che ci sono e crea la vista."""
    import psycopg

    dsn = _base(cluster, "di_prima")
    banco = Banco(tmp_path / "banco", dsn, TAGLIE)

    def lancia(nome: str, righe: int | None = None) -> dict:
        assert banco.avvia(nome, righe) is None
        return _aspetta(banco.stato)

    try:
        assert lancia("genera", TAGLIE[0])["lavoro"]["errore"] is None
        with monkeypatch.context() as senza:  # come importava la versione di prima: nessun circuito
            senza.setattr(confronto, "somma_pronta", lambda rete: False)
            s = lancia("importa")
        assert s["lavoro"]["errore"] is None and not banco._rete.circuiti
        record = s["sinapsi"]["fatti"]
        assert record == s["postgres"]["totale"]
        with psycopg.connect(cluster.replace("dbname=prova", "dbname=di_prima"), autocommit=True) as conn:  # come generava la versione di prima
            conn.execute("DROP MATERIALIZED VIEW fatturato_per_mese")
        s = lancia("misura")
        c = s["confronto"]
        assert s["lavoro"]["errore"] is None and c["risposte_diverse"] == 0 and c["saltate"] == [] and len(c["domande"]) == 16
        assert [k["nome"] for k in banco._rete.circuiti] == ["fatturato per mese"] and banco._rete.verifica_circuiti() == []
        # la rete ha due fatti in più, quelli della definizione, ma per chi guarda i fatti restano i record
        assert s["sinapsi"]["fatti"] == record == c["fatti"] and banco._rete.conteggi()["fatti"] == record + 2
        with psycopg.connect(cluster.replace("dbname=prova", "dbname=di_prima"), autocommit=True) as conn:
            assert conn.execute("SELECT count(*) FROM fatturato_per_mese").fetchone()[0] > 5
        # una nuova generazione porta via la vista con le tabelle e la rifà sui dati nuovi
        assert lancia("genera", TAGLIE[0])["lavoro"]["errore"] is None
        with psycopg.connect(cluster.replace("dbname=prova", "dbname=di_prima"), autocommit=True) as conn:
            assert conn.execute("SELECT (SELECT sum(totale) FROM fatturato_per_mese) = (SELECT sum(totale) FROM fatture)").fetchone()[0]
    finally:
        banco.chiudi()


def test_i_risultati_di_prima_spariscono_appena_il_confronto_parte_e_si_vede_a_che_punto_e(pg: str, tmp_path: Path, monkeypatch) -> None:
    """Chi lancia il confronto deve vedere a che punto è, e i risultati vecchi devono sparire appena parte: altrimenti
    non si capisce se quelli che vede sono nuovi o vecchi."""
    banco = Banco(tmp_path / "banco", pg, TAGLIE)
    try:
        assert banco.avvia("importa") is None and _aspetta(banco.stato)["lavoro"]["errore"] is None
        assert banco.avvia("misura") is None
        primo = _aspetta(banco.stato)
        assert primo["lavoro"]["errore"] is None and len(primo["confronto"]["domande"]) == 16
        # il secondo confronto: mentre gira, quello di prima non c'è più e il lavoro dice quanti passi ha fatto e quanti ne mancano
        vera, a_meta, via = confronto.misura, threading.Event(), threading.Event()

        def con_una_pausa(conn, rete, **opzioni):
            avanza = opzioni["avanza"]

            def fermati_a_meta(testo, fatto, da_fare):
                avanza(testo, fatto, da_fare)
                if fatto == da_fare // 2:
                    a_meta.set()
                    assert via.wait(30)

            return vera(conn, rete, **{**opzioni, "avanza": fermati_a_meta})

        monkeypatch.setattr(confronto, "misura", con_una_pausa)
        assert banco.avvia("misura") is None
        assert a_meta.wait(60)
        s = banco.stato()
        assert s["confronto"] is None and s["lavoro"]["in_corso"] and s["lavoro"]["nome"] == "misura"
        assert s["lavoro"]["da_fare"] == 48 and s["lavoro"]["fatto"] == 24 and "domanda 9 di 16" in s["lavoro"]["avanzamento"]
        via.set()
        s = _aspetta(banco.stato)
        assert s["lavoro"]["errore"] is None and len(s["confronto"]["domande"]) == 16 and s["confronto"]["quando"] > primo["confronto"]["quando"]
        # un confronto che non riesce non lascia in vista quello di prima come se fosse buono
        monkeypatch.setattr(confronto, "misura", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("PostgreSQL se n'è andato")))
        assert banco.avvia("misura") is None
        s = _aspetta(banco.stato)
        assert "PostgreSQL se n'è andato" in s["lavoro"]["errore"] and s["confronto"] is None
    finally:
        banco.chiudi()


def test_un_istantanea_di_prima_viene_riletta_dal_giornale_e_rifatta_una_volta_sola(pg: str, tmp_path: Path) -> None:
    """Le istantanee scritte prima della 0.9.3 non dicono se contengono solo neuroni scritti nel giornale: il motore non
    si fida e rilegge il giornale. Il portale ne salva subito una nuova, così la rilettura lunga non si ripete."""
    import marshal

    banco = Banco(tmp_path / "banco", pg, TAGLIE)
    try:
        assert banco.avvia("importa") is None and _aspetta(banco.stato)["lavoro"]["errore"] is None
        conteggi = banco.stato()["sinapsi"]
    finally:
        banco.chiudi()
    istantanea = tmp_path / "banco" / "rete" / "istantanea"
    stato = marshal.loads(istantanea.read_bytes())
    assert stato.pop("confermati") == len(stato["nomi"])  # com'era un'istantanea di prima: senza la prova
    istantanea.write_bytes(marshal.dumps(stato))
    banco = Banco(tmp_path / "banco", pg, TAGLIE)
    try:
        s = _aspetta(banco.stato)
        assert s["sinapsi"] == conteggi and s["lavoro"]["errore"] is None
        assert marshal.loads(istantanea.read_bytes())["confermati"] == len(banco._rete.nomi) and not banco._rete.istantanea_scartata
        assert int(banco.chiedi("in fattura emessa conta")["righe"][0][0]) > 0
    finally:
        banco.chiudi()
    banco = Banco(tmp_path / "banco", pg, TAGLIE)  # la nuova è buona: si riapre da lì
    try:
        assert banco.stato()["sinapsi"] == conteggi and not banco._rete.istantanea_scartata
    finally:
        banco.chiudi()


def test_l_origine_e_il_tipo_degli_eventi_sono_un_testo_solo_anche_rileggendo(pg: str, tmp_path: Path) -> None:
    """Segnalato da chi installa: dopo aver riletto dal giornale dieci milioni di eventi il portale occupava 6,3 GB
    invece di 4,4, e l'istantanea 2,3 GB. Per ogni evento il motore teneva una copia dell'origine e del tipo, che sono
    sempre gli stessi due testi. Devono essere un oggetto solo: scrivendo, rileggendo il giornale, e leggendo
    un'istantanea salvata da una versione che li teneva separati."""
    import marshal

    cartella = tmp_path / "rete"
    rete = Rete(str(cartella), sync="os")
    with confronto.collega(pg) as conn:
        importa(conn, rete)
    eventi = len(rete.e_frase)

    def condivisi(r: Rete) -> bool:
        return len({id(x) for x in r.e_origine}) == len(set(r.e_origine)) and len({id(x) for x in r.e_tipo}) == len(set(r.e_tipo))

    assert eventi > 5000 and condivisi(rete)
    rete.chiudi()
    dal_giornale = Rete(str(cartella), sync="os")
    assert len(dal_giornale.e_frase) == eventi and condivisi(dal_giornale)
    dal_giornale.istantanea()
    compatta = (cartella / "istantanea").stat().st_size
    dal_giornale.chiudi()
    # un'istantanea come la scriveva una versione di prima dopo una rilettura: una copia per evento
    stato = marshal.loads((cartella / "istantanea").read_bytes())
    for colonna in ("e_origine", "e_tipo"):
        stato[colonna] = [str(x.encode(), "utf8") for x in stato[colonna]]
    corpo = marshal.dumps(stato)  # con il suo sigillo, come la salva il motore: senza non verrebbe usata
    (cartella / "istantanea").write_bytes(corpo + motore.SIGILLO + hashlib.sha256(corpo).hexdigest().encode())
    assert (cartella / "istantanea").stat().st_size > compatta + 10 * eventi
    riletta = Rete(str(cartella), sync="os")
    assert not riletta.istantanea_scartata and len(riletta.e_frase) == eventi and condivisi(riletta)
    riletta.istantanea()  # e quella che salva adesso è di nuovo compatta
    assert abs((cartella / "istantanea").stat().st_size - compatta) < 100  # qualche byte di differenza nel modo di scrivere un testo condiviso
    riletta.chiudi()


def test_lo_stato_non_cade_se_un_file_sparisce_mentre_lo_si_misura(tmp_path: Path, monkeypatch) -> None:
    """Chi guarda lo stato lo fa anche mentre un lavoro scrive: tra l'elenco dei file e la loro misura l'istantanea può
    essere sostituita, o la rete passare sul disco. Il file che non c'è più non pesa, e lo stato risponde lo stesso."""
    cartella = tmp_path / "rete"
    cartella.mkdir()
    (cartella / "giornale").write_bytes(b"x" * 10)
    (cartella / "istantanea").write_bytes(b"y" * 7)
    assert Banco._peso_sul_disco(cartella) == 17 and Banco._peso_sul_disco(tmp_path / "non-c-e") == 0
    vera = Path.stat

    def sparisce(self, *a, **k):  # l'istantanea sparisce dopo essere stata elencata
        if self.name == "istantanea" and self.parent == cartella:
            raise FileNotFoundError(2, "No such file or directory", str(self))
        return vera(self, *a, **k)

    monkeypatch.setattr(Path, "stat", sparisce)
    assert Banco._peso_sul_disco(cartella) == 10
