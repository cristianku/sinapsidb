"""Il riesame di una decisione e la ricerca delle decisioni candidate: il passo E0, paragrafi 6 e 7 e casi 1-11 di
docs/contratto-decisioni.md.

Lunedì Rossi ha 800 euro di fido e chiede un ordine da 1.000: no. Martedì si registra un pagamento da 400 fatto
domenica. Che cosa avremmo deciso sapendolo? La decisione di lunedì resta com'era.
"""

from __future__ import annotations

import json
import random
import shutil
from pathlib import Path

import pytest

from sinapsidb.memoria import Memoria, ScenarioIncompleto
from test_decisioni import _record, _riscrivi
from test_validita import DOMENICA, GIOVEDI, LUNEDI, MARTEDI, MERCOLEDI, SABATO, Orologio, fatto, orologio, prepara  # noqa: F401


def _n(ricevuta: dict) -> int:
    return int(ricevuta["evento"].removeprefix("evento:"))


def _valori(riesame: dict, quale: str) -> list[str]:
    return [y["valore"] for y in riesame[quale]["letture"] if y["quando"] == "arrivo"]


def ordine(m: Memoria, cliente: str, importo: int, numero: str, **altro) -> dict:
    return m.racconta(f"{cliente} ordina per {importo} euro", [fatto("ordine ricevuto", cliente, importo, numero=numero)], **altro)


def pagamento(m: Memoria, cliente: str, importo: int, causale: str, **periodo: str) -> dict:
    r = m.racconta(f"{cliente} paga {importo} euro", [fatto("pagamento incassato", cliente, importo, causale=causale, **periodo)])
    assert r["esito"] == "applicato", r
    return r


def _come_prima(cartella: Path, versioni: dict | None = None) -> None:
    """Riporta un giornale alla forma in cui lo scriveva il motore di prima: ricevute senza il formato dichiarato, letture
    di presenza senza l'identificativo del tipo, nessun segno durevole. ``versioni`` rimette la numerazione di allora,
    in cui la versione era l'evento che aveva scritto la definizione."""
    record = _record(cartella)
    for x in record:
        ricevuta = x[7]["ricevuta"] if len(x) > 7 and x[7] else None
        if not ricevuta or "decisione" not in ricevuta:
            continue
        decisione = ricevuta["decisione"]
        decisione.pop("formato", None)
        decisione.pop("calcolo", None)  # e nemmeno il modo di fare le somme, che si dichiara dalla 0.9.6
        gruppi = [decisione["regole"], (decisione.get("alla_scrittura") or {}).get("regole", []), ricevuta.get("regole", [])]
        for regola in (r for gruppo in gruppi for r in gruppo):
            regola["versione"] = (versioni or {}).get(regola["regola"], regola["versione"])
            for lettura in regola["letture"]:
                lettura.pop("cosa_id", None)
                if "circuito_id" in lettura:
                    lettura["versione"] = (versioni or {}).get(lettura["cosa"], lettura["versione"])
    _riscrivi(cartella, record)
    (cartella / "formato").unlink()


def pagamento_rifiutato(m: Memoria, cliente: str, importo: int, causale: str, **periodo: str) -> bool:
    r = m.racconta(f"{cliente} paga {importo} euro", [fatto("pagamento incassato", cliente, importo, causale=causale, **periodo)])
    return r["esito"] == "violazione_regola"


@pytest.fixture()
def m(tmp_path: Path, orologio: Orologio) -> Memoria:
    """Sabato: a Rossi sono accordati 750 euro di fido e ne paga 50. Lunedì ha 800 euro di fido residuo."""
    memoria = Memoria(tmp_path / "rete", scritture=True)
    prepara(memoria)
    assert memoria.racconta("Fido di Rossi", [fatto("fido accordato", "Rossi", 750)])["esito"] == "applicato"
    pagamento(memoria, "Rossi", 50, "P0")
    yield memoria
    memoria.chiudi()


def test_i_casi_del_contratto_da_uno_a_cinque(m: Memoria, tmp_path: Path, orologio: Orologio) -> None:
    orologio.metti(f"{LUNEDI}T09:00")
    no = ordine(m, "Rossi", 1000, "O1", chiave="o1")  # caso 1
    assert no["esito"] == "violazione_regola" and [y["valore"] for y in no["decisione"]["regole"][0]["letture"]] == ["800", "-200"]
    orologio.metti(f"{MARTEDI}T10:00")
    a = pagamento(m, "Rossi", 400, "A", **{"vale da": DOMENICA})
    orologio.metti(f"{MERCOLEDI}T10:00")
    b = pagamento(m, "Rossi", -500, "B", **{"vale da": DOMENICA})
    giornale = (tmp_path / "rete" / "giornale").read_bytes()
    stato = (m.conteggi(), m.valore("fido residuo", "Rossi")["valore"], m.rete.somme_dai_fatti())

    # senza rettifiche: la decisione com'era
    comera = m.riesamina(no["evento"])
    assert (comera["allora"]["esito"], comera["ipotetico"]["esito"], comera["cambia"]) == ("violazione_regola", "violazione_regola", False)
    assert _valori(comera, "allora") == _valori(comera, "ipotetico") == ["800", "-200"] and comera["eventi_usati"] == []
    # caso 2: con la sola A
    con_a = m.riesamina(no["evento"], [a["evento"]])
    assert (con_a["ipotetico"]["esito"], con_a["cambia"], _valori(con_a, "ipotetico")) == ("applicato", True, ["1200", "200"])
    assert con_a["allora"]["esito"] == "violazione_regola" and _valori(con_a, "allora") == ["800", "-200"]
    assert con_a["eventi_usati"] == [a["evento"]] and con_a["modo"] == "per decisione" and con_a["base"] == f"evento:{_n(no) - 1}"
    assert con_a["regole"] == [("fido non superato", 1)] and con_a["letture_in_piu"] == []
    # caso 3: con A e B, in qualunque ordine e anche ripetute
    con_ab = m.riesamina(no["evento"], [b["evento"], a["evento"], _n(a)])
    assert (con_ab["ipotetico"]["esito"], con_ab["cambia"], _valori(con_ab, "ipotetico")) == ("violazione_regola", False, ["700", "-300"])
    assert con_ab["eventi_usati"] == [a["evento"], b["evento"]]
    assert m.riesamina(no["evento"], fino_a=b["evento"]) == con_ab  # «fino a» è una comodità: dice comunque quali eventi ha usato
    # caso 4: giovedì, con il fido di oggi a 700, il riesame con la sola A dà ancora 1.200, identico byte per byte
    orologio.metti(f"{GIOVEDI}T10:00")
    assert m.valore("fido residuo", "Rossi")["valore"] == "700"
    assert json.dumps(m.riesamina(no["evento"], [a["evento"]]), sort_keys=True) == json.dumps(con_a, sort_keys=True)
    # caso 5: niente è cambiato: giornale, fatti, fido reale, ricevuta
    assert (tmp_path / "rete" / "giornale").read_bytes() == giornale
    assert (m.conteggi(), m.valore("fido residuo", "Rossi")["valore"], m.rete.somme_dai_fatti()) == stato
    assert ordine(m, "Rossi", 1000, "O1", chiave="o1") == {**no, "ripetuta": True} and m.verifica(no["evento"])["esito"] == "coerente"
    # un sì ipotetico non ha autorizzato niente: l'ordine non c'è
    assert m.rete.trova("O1") == [] and m.conteggi()["fatti"] == stato[0]["fatti"]
    # un pagamento vero invece il fido lo cambia
    pagamento(m, "Rossi", 10, "C")
    assert m.valore("fido residuo", "Rossi")["valore"] == "710"


def test_un_si_riesaminato_non_conta_due_volte_il_suo_ordine(m: Memoria, orologio: Orologio) -> None:
    """Caso 6: fido 1.200, ordine da 1.000 accettato. Il riesame senza rettifiche riparte dallo stato di prima
    dell'ordine: ritrova 1.200 e 200, non 200 e −800."""
    orologio.metti(f"{LUNEDI}T09:00")
    pagamento(m, "Rossi", 400, "P1")
    si = ordine(m, "Rossi", 1000, "O1")
    assert si["esito"] == "applicato" and m.valore("fido residuo", "Rossi")["valore"] == "200"
    orologio.metti(f"{MARTEDI}T09:00")
    pagamento(m, "Rossi", 5, "P2")
    riesame = m.riesamina(si["evento"])
    assert (riesame["allora"]["esito"], riesame["ipotetico"]["esito"], riesame["cambia"]) == ("applicato", "applicato", False)
    assert _valori(riesame, "allora") == _valori(riesame, "ipotetico") == ["1200", "200"]
    # un addebito che valeva solo da domenica a martedì lo trasforma in un no ipotetico; l'ordine vero resta dov'è.
    # (Un addebito senza scadenza manderebbe sotto zero il fido di oggi, e sarebbe rifiutato lui: la regola vale anche per le rettifiche.)
    assert pagamento_rifiutato(m, "Rossi", -300, "S0", **{"vale da": DOMENICA})
    storno = pagamento(m, "Rossi", -300, "S1", **{"vale da": DOMENICA, "vale fino a": MARTEDI})
    senza = m.riesamina(si["evento"], [storno["evento"]])
    assert (senza["ipotetico"]["esito"], senza["cambia"], _valori(senza, "ipotetico")) == ("violazione_regola", True, ["900", "-100"])
    assert m.chiedi("in ordine ricevuto conta")["righe"] == [("1",)]


def test_due_richieste_rifiutate_sono_riesaminate_ognuna_da_sola(m: Memoria, orologio: Orologio) -> None:
    """Caso 7: due ordini da 1.000 rifiutati con 800 di fido, poi il pagamento A. Per decisione sono due sì ipotetici:
    nessuno dei due consuma il fido dell'altro, e la risposta lo dichiara."""
    orologio.metti(f"{LUNEDI}T09:00")
    primo, secondo = ordine(m, "Rossi", 1000, "O1"), ordine(m, "Rossi", 1000, "O2")
    assert primo["esito"] == secondo["esito"] == "violazione_regola"
    orologio.metti(f"{MARTEDI}T10:00")
    a = pagamento(m, "Rossi", 400, "A", **{"vale da": DOMENICA})
    riesami = [m.riesamina(x["evento"], [a["evento"]]) for x in (primo, secondo)]
    assert [(x["ipotetico"]["esito"], _valori(x, "ipotetico"), x["modo"]) for x in riesami] == [("applicato", ["1200", "200"], "per decisione")] * 2
    assert "da sola" in riesami[0]["spiegazione"]
    # un pagamento che non dichiara da quando vale conta da quando è scritto: martedì. Lunedì non c'era, e non cambia niente
    senza_data = pagamento(m, "Rossi", 400, "Z")
    fermo = m.riesamina(primo["evento"], [senza_data["evento"]])
    assert (fermo["ipotetico"]["esito"], fermo["cambia"], _valori(fermo, "ipotetico")) == ("violazione_regola", False, ["800", "-200"])


def test_le_candidate_si_trovano_dalle_dipendenze(m: Memoria, orologio: Orologio) -> None:
    """Casi 8, 9 e 10. Un pagamento valido da domenica e un reso valido da martedì lasciano il fido di oggi uguale,
    ma la decisione di lunedì è candidata. Un pagamento valido da domenica senza scadenza rende candidata anche una
    decisione di mercoledì. Un pagamento spostato da Rossi a Bianchi tocca le decisioni di tutti e due."""
    assert m.racconta("Fido di Bianchi", [fatto("fido accordato", "Bianchi", 100)])["esito"] == "applicato"
    orologio.metti(f"{LUNEDI}T09:00")
    lunedi = ordine(m, "Rossi", 1000, "O1")
    di_bianchi = ordine(m, "Bianchi", 300, "O2")
    altro = m.racconta("Fido di Verdi", [fatto("fido accordato", "Verdi", 10)])
    di_verdi = ordine(m, "Verdi", 50, "O3")
    assert [x["esito"] for x in (lunedi, di_bianchi, altro, di_verdi)] == ["violazione_regola", "violazione_regola", "applicato", "violazione_regola"]
    orologio.metti(f"{MERCOLEDI}T09:00")
    mercoledi = ordine(m, "Rossi", 900, "O4")
    assert mercoledi["esito"] == "violazione_regola"
    orologio.metti(f"{GIOVEDI}T09:00")
    oggi = m.valore("fido residuo", "Rossi")["valore"]
    entra = pagamento(m, "Rossi", 400, "P1", **{"vale da": DOMENICA})
    esce = pagamento(m, "Rossi", -400, "R1", **{"vale da": MARTEDI})
    assert m.valore("fido residuo", "Rossi")["valore"] == oggi  # caso 8: il valore di oggi non è cambiato
    # caso 9: il pagamento vale da domenica senza scadenza: sono candidate la decisione di lunedì e quella di mercoledì
    trovate = m.candidate(entra["evento"], riesamina=True)
    assert trovate["candidate"] == [lunedi["evento"], mercoledi["evento"]] and trovate["riesaminate"] == 2
    assert trovate["esiti_cambiati"] == [{"evento": x["evento"], "allora": "violazione_regola", "ipotetico": "applicato"} for x in (lunedi, mercoledi)]
    # il reso vale da martedì: tocca la decisione di mercoledì e non quella di lunedì. Tocca anche la scrittura del
    # pagamento di giovedì: anche un sì è una decisione, e ha letto il fido di Rossi a un istante in cui il reso vale
    assert m.candidate(esce["evento"])["candidate"] == [mercoledi["evento"], entra["evento"]]
    # caso 8: con tutti e due lunedì è un sì ipotetico, mercoledì resta un no
    assert [m.riesamina(x["evento"], [entra["evento"], esce["evento"]])["ipotetico"]["esito"] for x in (lunedi, mercoledi)] == ["applicato", "violazione_regola"]
    # caso 10: il pagamento P1 era di Bianchi, non di Rossi. Bianchi non aveva pagamenti: sono candidate le decisioni di tutti e due
    spostato = m.racconta("Il pagamento P1 era di Bianchi", [fatto("pagamento incassato", "Bianchi", 400, causale="P1", **{"vale da": DOMENICA})],
                          sostituisce=[{"tipo": "pagamento incassato", "indizi": {"causale": "P1"}}])
    assert spostato["esito"] == "applicato"
    tutti_e_due = m.candidate(spostato["evento"], riesamina=True)
    assert tutti_e_due["candidate"] == [lunedi["evento"], di_bianchi["evento"], mercoledi["evento"], entra["evento"], esce["evento"]]
    # Lo spostamento è un evento solo: ritira il pagamento di Rossi e scrive quello di Bianchi. Preso da solo, per le
    # decisioni prese prima che P1 fosse scritto ritira un fatto che non era nella loro base: lo scenario è incompleto,
    # anche per Bianchi, e viene detto. Per il reso, scritto dopo P1, è completo, e non cambia esito.
    assert [x["evento"] for x in tutti_e_due["scenari_incompleti"]] == [lunedi["evento"], di_bianchi["evento"], mercoledi["evento"], entra["evento"]]
    assert tutti_e_due["riesaminate"] == 1 and tutti_e_due["esiti_cambiati"] == []
    # con il pagamento e il suo spostamento insieme lo scenario è completo: Bianchi avrebbe avuto il fido, Rossi no
    completo = [entra["evento"], spostato["evento"]]
    bianchi, rossi = m.riesamina(di_bianchi["evento"], completo), m.riesamina(lunedi["evento"], completo)
    assert (bianchi["ipotetico"]["esito"], bianchi["cambia"], _valori(bianchi, "ipotetico")) == ("applicato", True, ["500", "200"])
    assert (rossi["ipotetico"]["esito"], rossi["cambia"], _valori(rossi, "ipotetico")) == ("violazione_regola", False, ["800", "-200"])
    # la decisione di Verdi non è mai candidata
    assert all(di_verdi["evento"] not in m.candidate(x["evento"])["candidate"] for x in (entra, esce, spostato))
    with pytest.raises(ValueError, match="è un rifiuto"):
        m.candidate(lunedi["evento"])


def test_uno_scenario_incompleto_non_viene_completato_di_nascosto(m: Memoria, tmp_path: Path, orologio: Orologio) -> None:
    """Caso 11, e gli altri modi sbagliati di indicare una rettifica."""
    orologio.metti(f"{LUNEDI}T09:00")
    no = ordine(m, "Rossi", 1000, "O1")
    altro_no = ordine(m, "Rossi", 2000, "O2")
    orologio.metti(f"{MARTEDI}T10:00")
    a = pagamento(m, "Rossi", 400, "A", **{"vale da": DOMENICA})
    storno = m.racconta("Il pagamento A era un errore", sostituisce=[{"tipo": "pagamento incassato", "indizi": {"causale": "A"}}])
    assert storno["esito"] == "applicato"
    with pytest.raises(ScenarioIncompleto, match=f"l'evento {_n(storno)} ritira un fatto scritto dall'evento {_n(a)}"):
        m.riesamina(no["evento"], [storno["evento"]])
    # con il pagamento e il suo storno insieme lo scenario è completo, e torna quello di lunedì
    insieme = m.riesamina(no["evento"], [a["evento"], storno["evento"]])
    assert (insieme["ipotetico"]["esito"], _valori(insieme, "ipotetico")) == ("violazione_regola", ["800", "-200"])
    # uno storno di un pagamento che era già nella base non ha bisogno d'altro
    via_p0 = m.racconta("Anche il pagamento P0 era un errore", sostituisce=[{"tipo": "pagamento incassato", "indizi": {"causale": "P0"}}])
    assert via_p0["esito"] == "applicato"
    assert _valori(m.riesamina(no["evento"], [via_p0["evento"]]), "ipotetico") == ["750", "-250"]
    for sbagliata, dice in (([altro_no["evento"]], "è un rifiuto"), ([_n(no) - 1], "deve essere scritto dopo"), ([_n(no)], "deve essere scritto dopo"), ([999], "deve essere scritto dopo")):
        with pytest.raises(ValueError, match=dice):
            m.riesamina(no["evento"], sbagliata)
    # una rettifica non può cambiare le regole: restano quelle di allora
    n = m.rete.n
    regola = m.rete.proponi_regola("Niente ordini a Rossi", "prova", "niente ordini", 1, "vieta", [
        {"nome": "ordine", "modo": "presenza", "osserva": n("ordine ricevuto"), "lega": n("cliente"), "peso": 1}])
    with pytest.raises(ValueError, match="cambia una regola o un circuito"):
        m.riesamina(no["evento"], [a["evento"], regola])
    largo = m.riesamina(no["evento"], fino_a=regola)  # con «fino a» quell'evento è lasciato fuori, e la risposta lo dice
    assert largo["eventi_esclusi"] == [{"evento": f"evento:{regola}", "perche": "cambia una regola o un circuito"}]
    assert largo["eventi_usati"] == [a["evento"], storno["evento"], via_p0["evento"]] and _valori(largo, "ipotetico") == ["750", "-250"]
    with pytest.raises(ValueError, match="non tutte e due"):
        m.riesamina(no["evento"], [a["evento"]], fino_a=regola)
    # un evento che nel giornale non ha la ricevuta non si può riesaminare
    m.chiudi()
    copia = tmp_path / "senza-ricevuta"
    shutil.copytree(tmp_path / "rete", copia)
    record = _record(copia)
    record[_n(no)] = record[_n(no)][:7]
    _riscrivi(copia, record)
    senza = Memoria(copia)
    with pytest.raises(ValueError, match="non ha una decisione"):
        senza.riesamina(no["evento"])
    senza.chiudi()


def test_le_candidate_coincidono_con_una_scansione_di_tutte_le_decisioni(tmp_path: Path, orologio: Orologio) -> None:
    """Il criterio della revisione: prima di misurare, la ricerca dalle dipendenze si confronta con una scansione che non usa
    l'indice. Sei clienti, ordini accettati e rifiutati per una settimana, poi rettifiche di ogni tipo. Per ogni
    rettifica si riesaminano **tutte** le decisioni precedenti: quelle in cui cambia una lettura o l'esito devono
    essere tutte tra le candidate, e gli esiti cambiati devono essere gli stessi."""
    caso = random.Random(417)
    memoria = Memoria(tmp_path / "rete", scritture=True)
    prepara(memoria)
    clienti = [f"cliente {i}" for i in range(6)]
    for c in clienti:
        assert memoria.racconta("fido", [fatto("fido accordato", c, 500)])["esito"] == "applicato"
    giorni = [f"2026-11-{g}" for g in range(16, 23)]
    decisioni, pagamenti = [], []
    for g, giorno in enumerate(giorni):
        for ora in range(9, 18):
            orologio.metti(f"{giorno}T{ora:02d}:00")
            c = caso.choice(clienti)
            if caso.random() < 0.7:
                r = ordine(memoria, c, caso.randint(50, 400), f"O{g}-{ora}")
                decisioni.append(_n(r))
            else:
                r = pagamento(memoria, c, caso.randint(20, 200), f"P{g}-{ora}")
                pagamenti.append((f"P{g}-{ora}", c))
    assert {memoria.rete.e_tipo[d] for d in decisioni} == {"scrittura", "rifiuto"}
    orologio.metti("2026-11-23T09:00")
    rettifiche = []
    for i in range(12):
        c, mossa = caso.choice(clienti), caso.random()
        da = caso.choice(giorni)
        if mossa < 0.4:  # un pagamento fatto prima e registrato adesso
            r = pagamento(memoria, c, caso.randint(50, 300), f"R{i}", **{"vale da": da})
        elif mossa < 0.6:  # un addebito valido solo per un periodo
            a = caso.choice([x for x in giorni + ["2026-11-23"] if x > da])
            r = pagamento(memoria, c, -caso.randint(50, 300), f"R{i}", **{"vale da": da, "vale fino a": a})
        elif mossa < 0.8:  # un pagamento spostato a un altro cliente
            causale, di = pagamenti.pop(caso.randrange(len(pagamenti)))
            r = memoria.racconta("spostato", [fatto("pagamento incassato", c, 100, causale=causale, **{"vale da": da})],
                                 sostituisce=[{"tipo": "pagamento incassato", "indizi": {"causale": causale}}])
            pagamenti.append((causale, c))
        else:  # un pagamento tolto
            causale, di = pagamenti.pop(caso.randrange(len(pagamenti)))
            r = memoria.racconta("tolto", sostituisce=[{"tipo": "pagamento incassato", "indizi": {"causale": causale}}])
        if r["esito"] == "applicato":
            rettifiche.append(_n(r))
    assert len(rettifiche) >= 8
    def confronta() -> list:
        """Per ogni rettifica: le candidate trovate dall'indice contro la scansione di tutti gli eventi precedenti."""
        esiti = []
        for r in rettifiche:
            trovate = memoria.candidate(r, riesamina=True)
            assert trovate["copertura"] == {"completa": True, "eventi_senza_letture": 0}
            candidate = {int(x.removeprefix("evento:")) for x in trovate["candidate"]}
            toccate, cambiate = set(), []
            for d in range(r):  # la scansione: tutti gli eventi scritti prima, uno per uno, senza indice
                try:
                    x = memoria.riesamina(d, [r])
                except ScenarioIncompleto:
                    continue
                if x["ipotetico"]["letture"] != x["allora"]["letture"] or x["cambia"]:
                    toccate.add(d)
                if x["cambia"]:
                    cambiate.append({"evento": x["evento"], "allora": x["allora"]["esito"], "ipotetico": x["ipotetico"]["esito"]})
            assert toccate <= candidate, (r, toccate - candidate)
            assert cambiate == trovate["esiti_cambiati"], r
            esiti.append(cambiate)
        return esiti

    prima = confronta()
    assert sum(len(x) for x in prima) > 0  # il caso ha davvero prodotto rettifiche che cambiano un esito
    # Poi il circuito cambia: i pagamenti non contano più, e conta un tipo nuovo. Le decisioni di prima si riesaminano
    # con il circuito di allora: le candidate e gli esiti cambiati devono essere gli stessi, anche dopo una riapertura.
    tolto = memoria.racconta("I pagamenti non contano più nel fido", sostituisce=[{"tipo": "termine", "indizi": {"termine di": "fido residuo", "osserva": "pagamento incassato"}}])
    aggiunto = memoria.racconta("Nel fido contano le garanzie", [{"tipo": "termine", "legami": {
        "termine di": "fido residuo", "osserva": "garanzia", "lega": "cliente", "somma": "importo", "peso": 1}}])
    assert tolto["esito"] == aggiunto["esito"] == "applicato" and memoria.valore("fido residuo", clienti[0])["versione"] == _n(aggiunto)
    assert confronta() == prima
    memoria.chiudi()
    memoria = Memoria(tmp_path / "rete", scritture=True)
    assert confronta() == prima and memoria.verifica_circuiti()["esito"] == "coerente"
    memoria.chiudi()


def test_una_modifica_al_circuito_non_fa_perdere_una_candidata(m: Memoria, tmp_path: Path, orologio: Orologio) -> None:
    """Un controesempio della revisione: ordine da 1.000 rifiutato con 800 di fido, poi 400 retroattivi. Più tardi dal circuito
    si toglie il termine dei pagamenti. Il riesame usa il circuito di allora e dà ancora sì; la ricerca delle candidate
    usava i termini di oggi e non trovava più la decisione. Deve trovarla, anche dopo una riapertura."""
    orologio.metti(f"{LUNEDI}T09:00")
    no = ordine(m, "Rossi", 1000, "O1")
    orologio.metti(f"{MARTEDI}T10:00")
    a = pagamento(m, "Rossi", 400, "A", **{"vale da": DOMENICA})
    atteso = {"candidate": [no["evento"]], "esiti_cambiati": [{"evento": no["evento"], "allora": "violazione_regola", "ipotetico": "applicato"}]}
    cerca = lambda memoria: {k: v for k, v in memoria.candidate(a["evento"], riesamina=True).items() if k in atteso}
    assert cerca(m) == atteso
    versione_di_prima = m.valore("fido residuo", "Rossi")["versione"]
    orologio.metti(f"{MERCOLEDI}T10:00")
    tolto = m.racconta("I pagamenti non contano più nel fido", sostituisce=[{"tipo": "termine", "indizi": {"termine di": "fido residuo", "osserva": "pagamento incassato"}}])
    assert tolto["esito"] == "applicato" and m.valore("fido residuo", "Rossi")["valore"] == "750"
    # la versione del circuito è l'ultimo evento che lo ha cambiato, anche togliendo un termine
    assert versione_di_prima == 0 and m.valore("fido residuo", "Rossi")["versione"] == _n(tolto)
    dopo = ordine(m, "Rossi", 100, "O2")
    assert dopo["decisione"]["regole"][0]["letture"][0]["versione"] == _n(tolto)
    # il riesame diretto continua a dare sì, con il circuito di allora; e la ricerca trova ancora la decisione
    assert _valori(m.riesamina(no["evento"], [a["evento"]]), "ipotetico") == ["1200", "200"]
    assert cerca(m) == atteso and m.verifica(no["evento"])["esito"] == "coerente" and m.verifica(dopo["evento"])["esito"] == "coerente"
    m.chiudi()
    riaperta = Memoria(tmp_path / "rete", scritture=True)
    assert cerca(riaperta) == atteso
    # un termine aggiunto dopo: una garanzia retroattiva è una candidata in più per la decisione di lunedì, ma con il
    # circuito di allora non la cambia; cambia invece una decisione presa dopo che il termine c'era
    assert riaperta.racconta("Nel fido contano le garanzie", [{"tipo": "termine", "legami": {
        "termine di": "fido residuo", "osserva": "garanzia", "lega": "cliente", "somma": "importo", "peso": 1}}])["esito"] == "applicato"
    orologio.metti(f"{GIOVEDI}T10:00")
    grosso = ordine(riaperta, "Rossi", 5000, "O3")
    assert grosso["esito"] == "violazione_regola"
    garanzia = riaperta.racconta("Rossi aveva dato una garanzia", [fatto("garanzia", "Rossi", 9000, **{"vale da": DOMENICA})])
    trovate = riaperta.candidate(garanzia["evento"], riesamina=True)
    assert no["evento"] in trovate["candidate"] and grosso["evento"] in trovate["candidate"]
    assert trovate["esiti_cambiati"] == [{"evento": grosso["evento"], "allora": "violazione_regola", "ipotetico": "applicato"}]
    riaperta.chiudi()


def test_le_ricevute_scritte_prima_restano_verificabili_e_trovabili(tmp_path: Path, orologio: Orologio) -> None:
    """Un controesempio della revisione: le letture di presenza scritte prima di oggi non hanno «cosa_id». Una decisione così
    non deve risultare guasta, e una sospensione retroattiva che la cambia deve trovarla tra le candidate. Se nel
    giornale ci sono eventi senza letture, la ricerca dice che la sua copertura non è completa."""
    memoria = Memoria(tmp_path / "rete", scritture=True)
    n = memoria.rete.n
    memoria.rete.proponi_regola("Un cliente sospeso non fa richieste", "Nora", "niente richieste da sospeso", 2, "vieta", [
        {"nome": "sospeso", "modo": "presenza", "osserva": n("sospensione"), "lega": n("cliente"), "peso": 1},
        {"nome": "richiesta", "modo": "presenza", "osserva": n("richiesta"), "lega": n("cliente"), "peso": 1}])
    orologio.metti(f"{LUNEDI}T09:00")
    si = memoria.racconta("Rossi fa una richiesta", [{"tipo": "richiesta", "legami": {"cliente": "Rossi", "numero": "R1"}}])
    assert si["esito"] == "applicato" and all("cosa_id" in y for y in si["decisione"]["regole"][0]["letture"])
    memoria.chiudi()
    # il giornale com'era scritto prima: le stesse ricevute nel formato 1, senza «cosa_id», e senza il segno durevole
    _come_prima(tmp_path / "rete")
    vecchia = Memoria(tmp_path / "rete", scritture=True)
    assert vecchia.verifica(si["evento"])["esito"] == "coerente"
    orologio.metti(f"{MARTEDI}T09:00")
    # una sospensione senza scadenza varrebbe anche adesso, con la richiesta ancora lì: la regola rifiuta lei
    assert vecchia.racconta("Rossi è sospeso da domenica", [{"tipo": "sospensione", "legami": {"cliente": "Rossi", "vale da": DOMENICA}}])["esito"] == "violazione_regola"
    sospeso = vecchia.racconta("Rossi era sospeso da domenica a martedì", [{"tipo": "sospensione", "legami": {"cliente": "Rossi", "vale da": DOMENICA, "vale fino a": MARTEDI}}])
    assert sospeso["esito"] == "applicato"
    riesame = vecchia.riesamina(si["evento"], [sospeso["evento"]])
    assert (riesame["allora"]["esito"], riesame["ipotetico"]["esito"], riesame["cambia"]) == ("applicato", "violazione_regola", True)
    trovate = vecchia.candidate(sospeso["evento"], riesamina=True)
    assert trovate["candidate"] == [si["evento"]] and trovate["copertura"] == {"completa": True, "eventi_senza_letture": 0}
    assert trovate["esiti_cambiati"] == [{"evento": si["evento"], "allora": "applicato", "ipotetico": "violazione_regola"}]
    vecchia.chiudi()
    # un giornale in cui un evento non ha la ricevuta: la ricerca lo dice, non presenta la sua lista come completa
    record = _record(tmp_path / "rete")
    record[0] = record[0][:7]
    _riscrivi(tmp_path / "rete", record)
    senza = Memoria(tmp_path / "rete")
    assert senza.candidate(sospeso["evento"])["copertura"] == {"completa": False, "eventi_senza_letture": 1}
    senza.chiudi()


def test_a_parita_di_istante_decide_l_ordine_degli_eventi(m: Memoria, orologio: Orologio) -> None:
    """Il pareggio trovato in revisione: l'ordine da 1.000 su 800 e, subito dopo, un pagamento da 400 senza periodo, con
    l'orologio fermo sullo stesso istante. Il pagamento è scritto dopo la decisione: nel riesame non la cambia, anche se
    l'istante è lo stesso. Un pagamento che dichiara di valere da quell'istante, invece, la cambia."""
    orologio.metti(f"{LUNEDI}T09:00")
    no = ordine(m, "Rossi", 1000, "O1")
    senza_periodo = pagamento(m, "Rossi", 400, "A")
    con_periodo = pagamento(m, "Rossi", 400, "B", **{"vale da": f"{LUNEDI}T09:00"})
    rete = m.rete
    assert rete.e_scritto[_n(no)] == rete.e_scritto[_n(senza_periodo)] == rete.e_quando[_n(no)]  # lo stesso istante, davvero
    fermo = m.riesamina(no["evento"], [senza_periodo["evento"]])
    assert (fermo["ipotetico"]["esito"], fermo["cambia"], _valori(fermo, "ipotetico")) == ("violazione_regola", False, ["800", "-200"])
    cambia = m.riesamina(no["evento"], [con_periodo["evento"]])
    assert (cambia["ipotetico"]["esito"], cambia["cambia"], _valori(cambia, "ipotetico")) == ("applicato", True, ["1200", "200"])
    # fuori dal riesame il pagamento senza periodo vale da quell'istante, compreso: chi legge adesso lo vede
    assert m.valore("fido residuo", "Rossi")["valore"] == "1600"


def test_una_ricevuta_di_prima_con_due_valutazioni_resta_coerente(tmp_path: Path, orologio: Orologio) -> None:
    """Un controesempio della revisione: una sospensione vale dalle 10; una richiesta arriva alle 9 ed è servita alle 11. Passa
    all'arrivo ed è rifiutata alla scrittura. Scritta dal motore di prima, la ricevuta porta le due valutazioni senza
    l'identificativo del tipo: deve restare coerente, senza che il confronto dei valori si allenti."""
    memoria = Memoria(tmp_path / "rete", scritture=True)
    rete = memoria.rete
    n = rete.n
    rete.proponi_regola("Un cliente sospeso non fa richieste", "Nora", "niente richieste da sospeso", 2, "vieta", [
        {"nome": "sospeso", "modo": "presenza", "osserva": n("sospensione"), "lega": n("cliente"), "peso": 1},
        {"nome": "richiesta", "modo": "presenza", "osserva": n("richiesta"), "lega": n("cliente"), "peso": 1}])
    orologio.metti(f"{LUNEDI}T08:00")
    assert memoria.racconta("Rossi sarà sospeso dalle 10", [{"tipo": "sospensione", "legami": {"cliente": "Rossi", "vale da": f"{LUNEDI}T10:00"}}])["esito"] == "applicato"
    orologio.metti(f"{LUNEDI}T11:00")
    segno = len(rete.nomi)
    with pytest.raises(Exception):
        rete.racconta("richiesta arrivata alle 9 e servita alle 11", "sito", [(n("richiesta"), [(n("cliente"), n("Rossi")), (n("numero"), n("R1"))])],
                      quando=rete.e_scritto[-1] + 3600, neuroni_da=segno)
    evento = len(rete.e_frase) - 1
    decisione = rete.e_nota[evento]["ricevuta"]["decisione"]
    assert rete.e_tipo[evento] == "rifiuto" and decisione["regole"][0]["verdetto"] == "passa" and decisione["alla_scrittura"]["regole"][0]["verdetto"] == "vieta"
    assert memoria.verifica(evento)["esito"] == "coerente"
    memoria.chiudi()
    _come_prima(tmp_path / "rete")
    vecchia = Memoria(tmp_path / "rete")
    assert "formato" not in vecchia.rete.e_nota[evento]["ricevuta"]["decisione"]
    # un giornale di prima non dichiara con quale modo si facevano le somme: la verifica dice con quali la ricevuta
    # coincide, e non ne attribuisce uno
    verificata = vecchia.verifica(evento)
    assert "non quale programma" in verificata.pop("spiegazione")
    assert verificata == {"evento": f"evento:{evento}", "tipo": "rifiuto", "esito": "coerente", "differenze": [], "regole_consultate": 1, "letture": 2,
                          "forma": ["1a"], "calcoli_compatibili": [4, 3, 2, 1]}
    vecchia.chiudi()
    # il confronto resta stretto sui valori: cambiando una lettura della seconda valutazione è un guasto
    record = _record(tmp_path / "rete")
    record[evento][7]["ricevuta"]["decisione"]["alla_scrittura"]["regole"][0]["letture"][0]["valore"] = 0
    _riscrivi(tmp_path / "rete", record)
    manomessa = Memoria(tmp_path / "rete")
    assert manomessa.verifica(evento)["esito"] == "guasto" and "alla scrittura" in manomessa.verifica(evento)["differenze"][0]
    manomessa.chiudi()


def test_un_evento_lasciato_fuori_non_cambia_la_versione_dichiarata(m: Memoria, tmp_path: Path, orologio: Orologio) -> None:
    """Un controesempio della revisione: dopo la decisione una regola riceve un segnale in più e il circuito perde un termine.
    Nel riesame quegli eventi sono lasciati fuori: la definizione usata è quella di allora, e anche il suo numero di
    versione. Vale indicando le rettifiche una per una e con «fino a»."""
    rete = m.rete
    n = rete.n
    orologio.metti(f"{LUNEDI}T09:00")
    si = ordine(m, "Rossi", 100, "O1")
    versioni = lambda letture: sorted({(y["regola"], y["versione"]) for y in letture})
    assert si["esito"] == "applicato" and versioni(m._letture(si["decisione"])) == [("fido non superato", 1)]
    orologio.metti(f"{MARTEDI}T09:00")
    # la regola riceve un segnale in più, e il circuito perde il termine dei pagamenti
    regola = m.racconta("Il fido non vale per i clienti sospesi", [{"tipo": "segnale", "legami": {
        "segnale di": "fido non superato", "nome": "sospeso", "modo": "presenza", "osserva": "sospensione", "lega": "cliente", "peso": 0}}])
    circuito = m.racconta("I pagamenti non contano più nel fido", sostituisce=[{"tipo": "termine", "indizi": {"termine di": "fido residuo", "osserva": "pagamento incassato"}}])
    neutro = m.racconta("Rossi ha sede a Lugano", [{"tipo": "sede", "legami": {"cliente": "Rossi", "dove": "Lugano"}}])
    assert [x["esito"] for x in (regola, circuito, neutro)] == ["applicato"] * 3
    dopo = ordine(m, "Rossi", 10, "O2")  # una decisione di oggi usa le definizioni di oggi, con le versioni di oggi
    assert versioni(m._letture(dopo["decisione"])) == [("fido non superato", _n(regola))]
    assert {y["versione"] for x in dopo["decisione"]["regole"] for y in x["letture"] if "circuito_id" in y} == {_n(circuito)}
    for riesame in (m.riesamina(si["evento"], [neutro["evento"]]), m.riesamina(si["evento"], fino_a=neutro["evento"])):
        assert riesame["regole"] == [("fido non superato", 1)]  # una sola versione, quella di allora
        assert riesame["ipotetico"]["letture"] == riesame["allora"]["letture"] and riesame["cambia"] is False
        assert {y["segnale"] for y in riesame["ipotetico"]["letture"]} == {"residuo negativo"}  # il segnale aggiunto dopo non c'è
    assert m.riesamina(si["evento"], fino_a=neutro["evento"])["eventi_esclusi"] == [
        {"evento": regola["evento"], "perche": "cambia una regola o un circuito"}, {"evento": circuito["evento"], "perche": "cambia una regola o un circuito"}]
    assert [m.verifica(x["evento"])["esito"] for x in (si, regola, circuito, neutro, dopo)] == ["coerente"] * 5


def test_la_numerazione_di_prima_si_verifica_con_la_numerazione_di_prima(m: Memoria, tmp_path: Path, orologio: Orologio) -> None:
    """Un controesempio della revisione: il motore di prima numerava la versione di un circuito con l'evento che lo aveva scritto.
    Un termine tolto prima di una decisione non la cambiava: la ricevuta dice versione 0. Il motore di oggi la numera
    con l'evento che ha tolto il termine. La ricevuta vecchia resta coerente, senza essere riscritta; una nuova con la
    versione sbagliata è un guasto."""
    orologio.metti(f"{LUNEDI}T09:00")
    tolto = m.racconta("I pagamenti non contano più nel fido", sostituisce=[{"tipo": "termine", "indizi": {"termine di": "fido residuo", "osserva": "pagamento incassato"}}])
    si = ordine(m, "Rossi", 100, "O1")
    evento = _n(si)
    assert si["esito"] == "applicato" and si["decisione"]["regole"][0]["letture"][0]["versione"] == _n(tolto)
    m.chiudi()
    nuova = tmp_path / "nuova"
    shutil.copytree(tmp_path / "rete", nuova)
    _come_prima(tmp_path / "rete", {"fido residuo": 0})
    vecchia = Memoria(tmp_path / "rete")
    assert vecchia.rete.e_nota[evento]["ricevuta"]["decisione"]["regole"][0]["letture"][0]["versione"] == 0
    assert vecchia.verifica(evento)["esito"] == "coerente"
    assert vecchia.riesamina(evento)["allora"]["letture"] == vecchia.riesamina(evento)["ipotetico"]["letture"]  # nella stessa numerazione
    vecchia.chiudi()
    # nel giornale di oggi la stessa ricevuta con la versione di prima è un guasto: lì la numerazione è quella nuova
    record = _record(nuova)
    for lettura in record[evento][7]["ricevuta"]["decisione"]["regole"][0]["letture"]:
        lettura["versione"] = 0
    _riscrivi(nuova, record)
    oggi = Memoria(nuova)
    assert oggi.verifica(evento)["esito"] == "guasto"
    oggi.chiudi()
