"""Le ricevute delle decisioni e la loro verifica: il passo B di docs/contratto-decisioni.md.

Ogni decisione, sì o no, lascia nel giornale su che cosa ha deciso: la base, l'istante, le regole consultate, anche
quelle che non sono scattate, e le letture che hanno fatto. ``verifica`` rifà la decisione dal solo giornale e la
confronta con la ricevuta.
"""

from __future__ import annotations

import json
import shutil
import struct
import zlib
from pathlib import Path

import pytest

from sinapsidb.memoria import Memoria
from sinapsidb.motore import Rete


def riga(prodotto: str, ordine: str = "417") -> dict:
    return {"tipo": "riga d'ordine", "soggetto": "ordine", "legami": {"ordine": ordine, "prodotto": prodotto, "quantità": 1}}


def stato(valore: str, ordine: str = "417") -> dict:
    return {"tipo": "stato dell'ordine", "legami": {"ordine": ordine, "stato": valore}}


def togli(prodotto: str, ordine: str = "417") -> dict:
    return {"tipo": "riga d'ordine", "indizi": {"ordine": ordine, "prodotto": prodotto}}


@pytest.fixture()
def m(tmp_path: Path) -> Memoria:
    """Una memoria con due regole che guardano le righe d'ordine: una vieta un ordine confermato vuoto, l'altra vieta
    di avere righe in un ordine chiuso. Su un ordine confermato con righe sono consultate tutte e due e non scattano."""
    memoria = Memoria(tmp_path / "rete", scritture=True)
    n = memoria.rete.n
    confermato = {"nome": "confermato", "modo": "presenza", "osserva": n("stato dell'ordine"), "lega": n("ordine"), "peso": 1,
                  "con_ruolo": n("stato"), "con_valore": n("confermato")}
    chiuso = {**confermato, "nome": "chiuso", "con_valore": n("chiuso")}
    memoria.rete.proponi_regola("Un ordine confermato non può restare vuoto", "Nora", "ordine non vuoto", 2, "vieta", [
        confermato, {"nome": "senza righe", "modo": "assenza", "osserva": n("riga d'ordine"), "lega": n("ordine"), "peso": 1}])
    memoria.rete.proponi_regola("Un ordine chiuso non ha righe", "Nora", "ordine chiuso senza righe", 2, "vieta", [
        chiuso, {"nome": "con righe", "modo": "presenza", "osserva": n("riga d'ordine"), "lega": n("ordine"), "peso": 1}])
    assert memoria.racconta("L'ordine 417 contiene un pickup", [riga("pickup")])["esito"] == "applicato"
    assert memoria.racconta("L'ordine 417 è confermato", [stato("confermato")])["esito"] == "applicato"
    yield memoria
    memoria.chiudi()


def _numero(evento: str) -> int:
    return int(evento.removeprefix("evento:"))


def _record(cartella: Path) -> list[list]:
    """I record del giornale, in ordine."""
    dati, pos, record = (cartella / "giornale").read_bytes(), 0, []
    while pos + 8 <= len(dati):
        lunghezza, crc = struct.unpack_from("<II", dati, pos)
        corpo = dati[pos + 8:pos + 8 + lunghezza]
        assert zlib.crc32(corpo) == crc
        record.append(json.loads(corpo))
        pos += 8 + lunghezza
    return record


def _riscrivi(cartella: Path, record: list[list]) -> None:
    """Riscrive il giornale con questi record: serve a manomettere una copia di prova."""
    with open(cartella / "giornale", "wb") as f:
        for r in record:
            corpo = json.dumps(r, ensure_ascii=False, separators=(",", ":")).encode()
            f.write(struct.pack("<II", len(corpo), zlib.crc32(corpo)) + corpo)


def _manomessa(tmp_path: Path, evento: int, nome: str, cambia, con_istantanea: bool = True) -> dict:
    """Che cosa dice la verifica su una copia del giornale in cui la ricevuta di quell'evento è stata cambiata.
    Con ``cambia`` uguale a None toglie tutta la nota. L'istantanea, se c'è, resta quella di prima del cambio."""
    copia = tmp_path / nome
    shutil.copytree(tmp_path / "rete", copia)
    if not con_istantanea and (copia / "istantanea").exists():
        (copia / "istantanea").unlink()
    record = _record(copia)
    if cambia is None:
        record[evento] = record[evento][:7]
    else:
        cambia(record[evento][7]["ricevuta"])
    _riscrivi(copia, record)
    memoria = Memoria(copia)
    try:
        return memoria.verifica(evento)
    finally:
        memoria.chiudi()


def test_un_si_lascia_scritto_su_che_cosa_ha_deciso(m: Memoria, tmp_path: Path) -> None:
    r = m.racconta("L'ordine 417 contiene anche un ponte", [riga("ponte")])
    assert r["esito"] == "applicato"
    d, evento = r["decisione"], _numero(r["evento"])
    # la base è l'ultimo evento già nel giornale prima della decisione, non l'evento della decisione
    assert d["base"] == evento - 1 and d["istante"] == m.rete.e_quando[evento]
    # sono dichiarate tutte e due le regole consultate, anche se nessuna è scattata
    assert [(x["regola"], x["verdetto"], x["su"]) for x in d["regole"]] == [
        ("ordine non vuoto", "passa", "417"), ("ordine chiuso senza righe", "passa", "417")]
    # ogni lettura dice che cosa, di chi, su quale base, per quale momento, se con la proposta, e il valore
    letture = {(x["regola"], y["segnale"]): y for x in d["regole"] for y in x["letture"]}
    assert {k: v["valore"] for k, v in letture.items()} == {
        ("ordine non vuoto", "confermato"): 1, ("ordine non vuoto", "senza righe"): 0,
        ("ordine chiuso senza righe", "chiuso"): 0, ("ordine chiuso senza righe", "con righe"): 1}
    for y in letture.values():
        assert [y["di"]] == m.rete.trova("417") and y["base"] == evento - 1 and y["valido_al"] == d["istante"] and y["con_proposta"] is True
        assert y["cosa"] in ("stato dell'ordine", "riga d'ordine") and y["modo"] in ("presenza", "assenza")
    # la ricevuta sta nello stesso record dei fatti, anche senza chiave
    record = _record(tmp_path / "rete")[evento]
    assert record[3] == "scrittura" and record[5] and record[7]["ricevuta"]["decisione"] == d
    assert m.verifica(r["evento"]) == {"evento": r["evento"], "tipo": "scrittura", "esito": "coerente", "differenze": [],
                                       "regole_consultate": 2, "letture": 4, "forma": ["2"], "calcolo": 4, "calcolo_da": "ricevuta"}


def test_un_no_si_verifica_riapplicando_la_richiesta(m: Memoria, tmp_path: Path) -> None:
    """La regola legge lo stato con la proposta applicata: «senza righe» è vero solo dopo aver tolto l'ultima riga.
    Una verifica che rifacesse la lettura sulla sola base troverebbe la riga e denuncerebbe un guasto che non c'è."""
    prima = m.conteggi()
    n = m.rete.n
    la_riga = list(m.rete.scatta(n("riga d'ordine"), [(n("ordine"), n("417"))]))
    assert len(la_riga) == 1
    r = m.racconta("Togli il pickup dall'ordine 417", sostituisce=[togli("pickup")])
    assert r["esito"] == "violazione_regola" and r["regole"][0]["regola"] == "ordine non vuoto"
    d, evento = r["decisione"], _numero(r["evento"])
    scattata = next(x for x in d["regole"] if x["verdetto"] == "vieta")
    assert {y["segnale"]: y["valore"] for y in scattata["letture"]} == {"confermato": 1, "senza righe": 1}
    # un no conserva la richiesta e la ricevuta, non la modifica negata
    assert d["proposta"]["ritira"] == la_riga and d["proposta"]["fatti"] == []
    dopo = m.conteggi()
    assert {**dopo, "eventi": prima["eventi"]} == prima and dopo["eventi"] == prima["eventi"] + 1
    record = _record(tmp_path / "rete")[evento]
    assert record[3] == "rifiuto" and record[5] == [] and record[6] == [] and record[7]["ricevuta"]["decisione"] == d
    # sulla sola base la riga c'è ancora: la lettura darebbe 0, non 1
    base, _ = Rete.com_era(str(tmp_path / "rete"), evento)
    assert list(base.scatta(n("riga d'ordine"), [(n("ordine"), n("417"))])) == la_riga
    assert m.verifica(evento)["esito"] == "coerente"
    # un sì e un no con la chiave: la verifica vale allo stesso modo, e anche dopo un riavvio
    si = m.racconta("L'ordine 417 contiene anche un cavo", [riga("cavo")], chiave="cavo")
    no = m.racconta("Chiudi l'ordine 417", [stato("chiuso")], chiave="chiudi")
    assert si["esito"] == "applicato" and no["esito"] == "violazione_regola" and no["regole"][0]["regola"] == "ordine chiuso senza righe"
    m.chiudi()
    riaperta = Memoria(tmp_path / "rete", scritture=True)
    assert [riaperta.verifica(x["evento"])["esito"] for x in (r, si, no)] == ["coerente"] * 3
    assert riaperta.racconta("Chiudi l'ordine 417", [stato("chiuso")], chiave="chiudi") == {**no, "ripetuta": True}
    riaperta.chiudi()


def test_la_verifica_trova_anche_quello_che_nella_ricevuta_manca(m: Memoria, tmp_path: Path) -> None:
    """Su una copia di prova si toglie dalla ricevuta di un sì una delle due regole consultate: l'esito è lo stesso,
    ma la ricevuta non dice più tutto quello che è stato guardato, e la verifica lo deve segnalare."""
    r = m.racconta("L'ordine 417 contiene anche un ponte", [riga("ponte")])
    evento = _numero(r["evento"])
    m.chiudi()
    ordine = m.rete.trova("417")[0]

    def manomessa(nome: str, cambia) -> dict:
        return _manomessa(tmp_path, evento, nome, lambda ricevuta: cambia(ricevuta["decisione"]))

    assert manomessa("intatta", lambda d: None)["esito"] == "coerente"
    senza = manomessa("senza-una-regola", lambda d: d["regole"].pop(1))
    assert senza["esito"] == "guasto" and senza["tipo"] == "scrittura"
    assert senza["differenze"] == [
        f"la regola «ordine chiuso senza righe» (versione 1) su «417» (neurone:{ordine}) andava consultata e nella ricevuta non c'è"]

    def altro_valore(d: dict) -> None:
        d["regole"][0]["letture"][1]["valore"] = 1

    assert manomessa("valore-diverso", altro_valore)["differenze"] == [
        f"la regola «ordine non vuoto» (versione 0) su «417» (neurone:{ordine}): nella ricevuta sono diversi da quelli rifatti: letture"]

    def altro_peso(d: dict) -> None:  # anche i campi che spiegano, come i segnali con i pesi, devono coincidere
        d["regole"][0]["segnali"][0][1] = 5.0

    assert "segnali" in manomessa("peso-diverso", altro_peso)["differenze"][0]

    def altra_base(d: dict) -> None:
        d["base"] -= 1

    assert manomessa("base-diversa", altra_base)["esito"] == "guasto"

    def due_volte(d: dict) -> None:
        d["regole"].append(d["regole"][0])

    assert "compare" not in manomessa("due-volte", due_volte)["differenze"][0] and manomessa("due-volte-b", due_volte)["esito"] == "guasto"


def test_due_entita_con_lo_stesso_nome_restano_due(m: Memoria, tmp_path: Path) -> None:
    """Un controesempio della revisione: una scrittura tocca due ordini diversi che si chiamano tutti e due «417». La stessa
    regola è consultata su tutti e due. Se dalla ricevuta sparisce la consultazione del primo, il nome uguale non
    deve nasconderlo."""
    altro = m.racconta("C'è un altro ordine 417, di un altro cliente, con un cavo", [riga("cavo")], nuovi=["417"])
    primo, secondo = m.rete.trova("417")
    assert altro["esito"] == "applicato" and altro["neuroni_nuovi"][0] == {"id": f"neurone:{secondo}", "nome": "417"}
    r = m.racconta("Un ponte in ciascuno dei due ordini 417", [riga("ponte", f"neurone:{primo}"), riga("ponte", f"neurone:{secondo}")])
    assert r["esito"] == "applicato"
    consultate = [(x["regola"], x["su"], x["su_id"]) for x in r["decisione"]["regole"]]
    assert consultate == [("ordine non vuoto", "417", primo), ("ordine non vuoto", "417", secondo),
                          ("ordine chiuso senza righe", "417", primo), ("ordine chiuso senza righe", "417", secondo)]
    evento = _numero(r["evento"])
    assert m.verifica(evento) == {"evento": r["evento"], "tipo": "scrittura", "esito": "coerente", "differenze": [],
                                  "regole_consultate": 4, "letture": 8, "forma": ["2"], "calcolo": 4, "calcolo_da": "ricevuta"}
    m.chiudi()
    senza = _manomessa(tmp_path, evento, "senza-il-primo", lambda ricevuta: ricevuta["decisione"]["regole"].pop(0))
    assert senza["esito"] == "guasto" and senza["differenze"] == [
        f"la regola «ordine non vuoto» (versione 0) su «417» (neurone:{primo}) andava consultata e nella ricevuta non c'è"]


def test_la_verifica_guarda_anche_l_esito_e_l_evento_che_la_ricevuta_dichiara(m: Memoria, tmp_path: Path) -> None:
    """I controesempi della revisione: l'esito e l'evento scritti nella ricevuta sono quelli che riceve chi
    ripete la richiesta. Se non corrispondono al giornale e alla decisione rifatta è un guasto."""
    si = m.racconta("L'ordine 417 contiene anche un ponte", [riga("ponte")], chiave="ponte")
    no = m.racconta("Chiudi l'ordine 417", [stato("chiuso")], chiave="chiudi")
    assert si["esito"] == "applicato" and no["esito"] == "violazione_regola"
    m.chiudi()

    def con(ricevuta_di: dict, nome: str, campo: str, valore: object) -> dict:
        return _manomessa(tmp_path, _numero(ricevuta_di["evento"]), nome, lambda ricevuta: ricevuta.__setitem__(campo, valore))

    assert con(si, "si-detto-no", "esito", "violazione_regola")["differenze"] == [
        "la ricevuta dichiara l'esito «violazione_regola», ma nel giornale l'evento è «scrittura»",
        "la ricevuta dichiara l'esito «violazione_regola», rifacendo la decisione è «applicato»"]
    assert con(no, "no-detto-si", "esito", "applicato")["esito"] == "guasto"
    assert con(si, "altro-evento", "evento", "evento:999")["differenze"] == [
        f"la ricevuta dice di essere di «evento:999», ma sta nel record di «{si['evento']}»"]
    assert con(no, "altre-regole", "regole", [])["differenze"] == [
        "le regole scattate scritte nella ricevuta non sono quelle che scattano rifacendo la decisione"]


def test_una_ricevuta_senza_la_decisione_e_un_guasto_non_un_evento_storico(m: Memoria, tmp_path: Path) -> None:
    """Togliere la decisione non deve rendere la verifica inapplicabile. Da quando nel giornale c'è la prima
    decisione, ogni evento ne deve avere una. Prima di allora un evento è storico, e la verifica dice che non sa."""
    r = m.racconta("L'ordine 417 contiene anche un ponte", [riga("ponte")], chiave="ponte")
    m.chiudi()
    evento = _numero(r["evento"])
    assert _manomessa(tmp_path, evento, "senza-decisione", lambda ricevuta: ricevuta.pop("decisione")) == {
        "evento": r["evento"], "tipo": "scrittura", "esito": "guasto",
        "differenze": ["l'evento non ha la decisione, ma in questo giornale le decisioni si registrano dall'evento 0"]}
    # tolta tutta la nota, cioè anche la ricevuta: resta un guasto, perché lo dice la posizione nel giornale
    assert _manomessa(tmp_path, evento, "senza-nota", None)["esito"] == "guasto"
    # un giornale di prima: le ricevute avevano l'esito ma non la decisione, nessun evento ne ha una, e non c'è il segno
    storico = tmp_path / "storico"
    shutil.copytree(tmp_path / "rete", storico)
    (storico / "formato").unlink()
    record = _record(storico)
    for x in record:
        if len(x) > 7 and x[7]:
            x[7]["ricevuta"].pop("decisione", None)
    _riscrivi(storico, record)
    vecchia = Memoria(storico, scritture=True)
    assert vecchia.verifica(evento)["esito"] == "non_verificabile"
    # aprirlo non ci mette il segno: lo mette chi scrive, prima del primo evento che scrive
    assert Rete.segno(str(storico)) is None
    nuova = vecchia.racconta("L'ordine 417 contiene anche un cavo", [riga("cavo")])
    # da qui in poi ogni evento deve avere la sua decisione; il segno dice anche da quale evento le somme dei circuiti
    # si fanno nel modo di oggi, e da quale ogni decisione dichiara il suo modo
    assert Rete.segno(str(storico)) == {"formato": 2, "dal": len(record), "calcoli": [[4, len(record)]], "calcolo_dal": len(record)}
    assert vecchia.verifica(nuova["evento"])["esito"] == "coerente" and vecchia.verifica(evento)["esito"] == "non_verificabile"
    vecchia.chiudi()


def test_il_segno_durevole_copre_anche_la_prima_decisione(m: Memoria, tmp_path: Path) -> None:
    """Il limite che restava aperto: togliendo la traccia alla prima decisione di un giornale, quell'evento tornava a
    sembrare storico. Il segno sta fuori dal giornale e dice da quale evento le decisioni ci devono essere, e in che
    formato: in un giornale nato oggi, dall'evento zero."""
    # lo ha messo la prima scrittura, quella che ha definito i tipi: dice anche con quale modo si fanno le somme dei
    # circuiti, e che da lì ogni decisione lo dichiara nel suo record
    assert Rete.segno(str(tmp_path / "rete")) == {"formato": 2, "dal": 0, "calcoli": [[4, 0]], "calcolo_dal": 0}
    r = m.racconta("L'ordine 417 contiene anche un ponte", [riga("ponte")])
    assert r["decisione"]["formato"] == 2 and r["decisione"]["calcolo"] == 4
    m.chiudi()
    assert _manomessa(tmp_path, 0, "prima-senza-traccia", None) == {
        "evento": "evento:0", "tipo": "scrittura", "esito": "guasto",
        "differenze": ["l'evento non ha la decisione, ma in questo giornale le decisioni si registrano dall'evento 0"]}
    assert _manomessa(tmp_path, 0, "intatta", lambda ricevuta: None)["esito"] == "coerente"
    # una ricevuta a cui si toglie il formato per farla passare da vecchia è un guasto: dal segno in poi il formato è quello
    senza = _manomessa(tmp_path, _numero(r["evento"]), "senza-formato", lambda ricevuta: ricevuta["decisione"].pop("formato"))
    assert senza["esito"] == "guasto" and senza["differenze"] == [
        "la ricevuta dichiara il formato 1, ma da questo punto del giornale le decisioni sono nel formato 2"]
    assert _manomessa(tmp_path, _numero(r["evento"]), "formato-strano", lambda ricevuta: ricevuta["decisione"].__setitem__("formato", 7))["esito"] == "guasto"


def test_la_verifica_legge_il_giornale_non_la_copia_in_memoria(m: Memoria, tmp_path: Path) -> None:
    """Un controesempio della revisione: la memoria riparte da un'istantanea fatta prima che il giornale venisse cambiato.
    In RAM ha la ricevuta di prima; nel giornale ce n'è un'altra. La verifica deve dare la stessa risposta comunque
    riparta: quello che confronta lo legge dal record del giornale."""
    r = m.racconta("L'ordine 417 contiene anche un ponte", [riga("ponte")], chiave="ponte")
    evento = _numero(r["evento"])
    m.rete.istantanea()
    m.chiudi()
    assert (tmp_path / "rete" / "istantanea").exists()
    altro = f"evento:{evento - 1}"  # stessa lunghezza: l'istantanea resta valida per quel giornale
    for con_istantanea in (True, False):
        for nome, cambia, atteso in (("intatta", lambda ricevuta: None, "coerente"),
                                    ("altro-evento", lambda ricevuta: ricevuta.__setitem__("evento", altro), "guasto"),
                                    ("senza-decisione", lambda ricevuta: ricevuta.pop("decisione"), "guasto")):
            esito = _manomessa(tmp_path, evento, f"{nome}-{con_istantanea}", cambia, con_istantanea)
            assert esito["esito"] == atteso, (nome, con_istantanea, esito)
            if nome == "altro-evento":
                assert esito["differenze"] == [f"la ricevuta dice di essere di «{altro}», ma sta nel record di «{r['evento']}»"]


def test_l_istante_e_quello_in_cui_la_richiesta_arriva(m: Memoria) -> None:
    """Un controesempio della revisione: una richiesta resta in coda dietro un'altra. L'istante della decisione è quello in
    cui è arrivata, non quello in cui ha ottenuto il turno; la base invece è quella che trova quando tocca a lei."""
    import threading
    import time

    ricevute = []
    with m._lock:  # un'altra scrittura sta occupando la memoria
        arrivo = time.time()
        filo = threading.Thread(target=lambda: ricevute.append(m.racconta("L'ordine 417 contiene anche un ponte", [riga("ponte")])))
        filo.start()
        time.sleep(0.3)
        turno = time.time()
    filo.join(10)
    d = ricevute[0]["decisione"]
    assert arrivo <= d["istante"] < arrivo + 0.2 < turno and d["base"] == _numero(ricevute[0]["evento"]) - 1
    assert m.rete.e_quando[_numero(ricevute[0]["evento"])] == d["istante"] and m.verifica(ricevute[0]["evento"])["esito"] == "coerente"


def test_scrittura_e_ricevuta_ci_sono_tutte_e_due_o_nessuna(m: Memoria, tmp_path: Path) -> None:
    """Un arresto a metà dell'ultimo record: al riavvio non c'è né il fatto né la ricevuta. Con il record intero ci
    sono tutti e due."""
    r = m.racconta("L'ordine 417 contiene anche un ponte", [riga("ponte")], chiave="ponte")
    evento = _numero(r["evento"])
    m.chiudi()
    giornale = (tmp_path / "rete" / "giornale").read_bytes()
    ultimo = len(giornale) - (8 + len(json.dumps(_record(tmp_path / "rete")[evento], ensure_ascii=False, separators=(",", ":")).encode()))
    for taglio in (ultimo + 4, ultimo + 60, len(giornale) - 1):
        copia = tmp_path / f"taglio-{taglio}"
        shutil.copytree(tmp_path / "rete", copia)
        (copia / "giornale").write_bytes(giornale[:taglio])
        memoria = Memoria(copia, scritture=True)
        assert len(memoria.rete.e_frase) == evento and evento not in memoria.rete.e_nota
        assert memoria.chiedi("accendi ordine = 417 in riga d'ordine leggi prodotto")["righe"] == [("pickup",)]
        # la stessa chiave ora non trova una ricevuta: la richiesta viene decisa adesso, una volta
        assert memoria.racconta("L'ordine 417 contiene anche un ponte", [riga("ponte")], chiave="ponte")["esito"] == "applicato"
        memoria.chiudi()
    intera = Memoria(tmp_path / "rete", scritture=True)
    assert len(intera.rete.e_frase) == evento + 1 and intera.rete.e_nota[evento]["ricevuta"]["decisione"] == r["decisione"]
    assert intera.racconta("L'ordine 417 contiene anche un ponte", [riga("ponte")], chiave="ponte") == {**r, "ripetuta": True}
    intera.chiudi()


def test_anche_gli_eventi_scritti_direttamente_nel_motore_hanno_la_loro_decisione(m: Memoria) -> None:
    """Un'osservazione della revisione: una regola proposta direttamente al motore dopo la prima decisione non deve sembrare
    un guasto solo perché non è passata da racconta. In una memoria ogni evento porta la sua decisione."""
    assert [m.verifica(e)["esito"] for e in range(len(m.rete.e_frase))] == ["coerente"] * 4
    n = m.rete.n
    evento = m.rete.proponi_regola("Un ordine spedito non cambia", "Nora", "ordine spedito fermo", 2, "vieta", [
        {"nome": "spedito", "modo": "presenza", "osserva": n("stato dell'ordine"), "lega": n("ordine"), "peso": 1,
         "con_ruolo": n("stato"), "con_valore": n("spedito")},
        {"nome": "con righe", "modo": "presenza", "osserva": n("riga d'ordine"), "lega": n("ordine"), "peso": 1}])
    assert m.verifica(evento)["esito"] == "coerente"
    r = m.racconta("L'ordine 417 contiene anche un ponte", [riga("ponte")])
    assert m.verifica(r["evento"])["regole_consultate"] == 3
    with pytest.raises(ValueError):
        m.verifica(99)


def test_quanto_pesa_la_ricevuta_di_un_si(m: Memoria, tmp_path: Path) -> None:
    """Il peso nel giornale della ricevuta di una scrittura accettata senza chiave, con due regole consultate e
    quattro letture. Il numero serve al contratto: la forma su disco si fissa dopo averlo misurato."""
    r = m.racconta("L'ordine 417 contiene anche un ponte", [riga("ponte")])
    record = _record(tmp_path / "rete")[_numero(r["evento"])]
    con = len(json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode())
    senza = len(json.dumps(record[:7], ensure_ascii=False, separators=(",", ":")).encode())
    print(f"record con la ricevuta: {con} byte; senza: {senza} byte; la ricevuta pesa {con - senza} byte")
    assert con - senza < 1500
    # senza regole consultate la ricevuta è poca cosa
    r = m.racconta("Greta lavora a Lugano", [{"tipo": "lavoro", "legami": {"chi": "Greta", "dove": "Lugano"}}])
    record = _record(tmp_path / "rete")[_numero(r["evento"])]
    assert r["decisione"]["regole"] == [] and len(json.dumps(record[7], separators=(",", ":"))) < 220
