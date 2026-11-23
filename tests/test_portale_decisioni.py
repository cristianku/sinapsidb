"""La pagina «Decisioni» del portale e i suoi percorsi: una memoria di prova con il fido dei clienti."""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

from sinapsidb import portale
from sinapsidb.portale import ConfigPortale

BASE = "http://127.0.0.1:8419"
LOCALE = ("127.0.0.1", 5000)
AZIONE = {"x-sinapsidb-azione": "1"}


@pytest.fixture()
def c(tmp_path: Path) -> TestClient:
    config = ConfigPortale(metriche_db=tmp_path / "m.db", sonda_intervallo=0, prova_pg=f"host={tmp_path}/nessuno port=1 dbname=x")
    with TestClient(portale.crea_app(config), base_url=BASE, client=LOCALE) as cliente:
        yield cliente


def manda(c: TestClient, op: str, **corpo: object) -> dict:
    risposta = c.post(f"/api/decisioni/{op}", json=corpo, headers=AZIONE)
    assert risposta.status_code == 200, risposta.text
    return risposta.json()


def test_la_pagina_e_i_suoi_file(c: TestClient) -> None:
    pagina = c.get("/decisioni")
    assert pagina.status_code == 200 and "SinapsiDB Decisioni" in pagina.text and 'href="/decisioni" aria-current="page"' in pagina.text
    for percorso in ("/dati", "/confronto"):
        assert '<a href="/decisioni">Decisioni</a>' in c.get(percorso).text
    assert c.get("/").text == pagina.text  # l'inizio porta qui
    codice = c.get("/decisioni.js").content
    assert b"innerHTML" not in codice and b"x-sinapsidb-azione" in codice and b"/api/decisioni" in codice
    assert c.get("/decisioni.css").status_code == 200
    assert "default-src 'self'" in pagina.headers["content-security-policy"]


def test_l_esempio_del_contratto_dal_portale(c: TestClient, tmp_path: Path) -> None:
    vuoto = c.get("/api/decisioni").json()
    assert vuoto["clienti"] == [] and vuoto["decisioni"] == [] and vuoto["circuiti"] == "coerente" and vuoto["segno"] == {"formato": 2, "dal": 0, "calcoli": [[4, 0]], "calcolo_dal": 0}
    assert c.get("/api/decisioni").headers["cache-control"] == "no-store"
    assert manda(c, "accorda", cliente="Rossi", importo="800")["esito"] == "applicato"
    no = manda(c, "ordine", cliente="Rossi", importo="1000")
    assert no["esito"] == "violazione_regola"
    pagato = manda(c, "pagamento", cliente="Rossi", importo="400", vale_da="2020-01-01")
    assert pagato["esito"] == "applicato"
    quadro = c.get("/api/decisioni").json()
    assert quadro["clienti"] == [{"nome": "Rossi", "id": quadro["clienti"][0]["id"], "fido": "1200"}]
    # le decisioni, dalla più recente; le definizioni del circuito e della regola non compaiono
    assert [(d["evento"], d["tipo"], d["esito"]) for d in quadro["decisioni"]] == [
        (4, "scrittura", "applicato"), (3, "rifiuto", "violazione_regola"), (2, "scrittura", "applicato")]
    rifiuto = quadro["decisioni"][1]
    assert rifiuto["frase"] == "Rossi ordina per 1000" and rifiuto["letture"] == [
        {"regola": "fido non superato", "cosa": "fido residuo", "di": "Rossi", "prima": "800", "dopo": "-200"}]
    # il riesame: com'era, e sapendo del pagamento
    comera = manda(c, "riesamina", evento=no["evento"], rettifiche=[])
    assert (comera["allora"]["esito"], comera["ipotetico"]["esito"], comera["cambia"]) == ("violazione_regola", "violazione_regola", False)
    sapendolo = manda(c, "riesamina", evento=no["evento"], rettifiche=[pagato["evento"]])
    assert (sapendolo["ipotetico"]["esito"], sapendolo["cambia"], sapendolo["eventi_usati"]) == ("applicato", True, ["evento:4"])
    toccate = manda(c, "candidate", rettifica=pagato["evento"])
    # anche l'accordo del fido ha letto il fido di Rossi: è tra le decisioni toccate, ma il suo esito non cambia
    assert toccate["candidate"] == ["evento:2", "evento:3"] and toccate["esiti_cambiati"] == [{"evento": "evento:3", "allora": "violazione_regola", "ipotetico": "applicato"}]
    assert manda(c, "verifica", evento=no["evento"])["esito"] == "coerente"
    # riesaminare non ha scritto niente
    assert c.get("/api/decisioni").json()["conteggi"] == quadro["conteggi"]
    # la memoria di prova sta nella sua cartella, accanto alle metriche
    assert (tmp_path / "decisioni" / "giornale").exists() and (tmp_path / "decisioni" / "formato").exists()


def test_l_esempio_guidato_e_azzera(c: TestClient) -> None:
    primo = manda(c, "esempio")
    assert primo["cliente"] == "Rossi" and primo["esiti"] == ["applicato", "violazione_regola", "applicato"]
    assert manda(c, "riesamina", evento=primo["ordine"], rettifiche=[primo["pagamento"]])["cambia"] is True
    secondo = manda(c, "esempio")  # una seconda volta usa un cliente nuovo, senza sommare al primo
    assert secondo["cliente"] == "Rossi 2"
    assert sorted((x["nome"], x["fido"]) for x in c.get("/api/decisioni").json()["clienti"]) == [("Rossi", "1200"), ("Rossi 2", "1200")]
    assert manda(c, "azzera") == {"esito": "azzerato"}
    dopo = c.get("/api/decisioni").json()
    assert dopo["clienti"] == [] and dopo["decisioni"] == [] and dopo["conteggi"]["eventi"] == 2
    assert manda(c, "esempio")["cliente"] == "Rossi"


def test_le_richieste_sbagliate_sono_respinte(c: TestClient) -> None:
    # senza l'intestazione della nostra pagina, o da un altro sito, niente
    assert c.post("/api/decisioni/esempio", json={}).status_code == 403
    assert c.post("/api/decisioni/esempio", json={}, headers={**AZIONE, "origin": "https://altro.example"}).status_code == 403
    assert c.post("/api/decisioni/inventata", json={}, headers=AZIONE).status_code == 404
    assert c.get("/api/decisioni/esempio").status_code == 405
    for corpo in ({"cliente": "Rossi"}, {"cliente": "Rossi", "importo": "tanto"}, {"cliente": "x" * 200, "importo": "1"}, {"cliente": ["Rossi"], "importo": "1"}):
        risposta = c.post("/api/decisioni/ordine", json=corpo, headers=AZIONE)
        assert risposta.status_code == 400 and risposta.json()["error"], corpo
    assert c.post("/api/decisioni/ordine", content=b"non json", headers={**AZIONE, "content-type": "application/json"}).status_code == 400
    assert c.post("/api/decisioni/riesamina", json={"evento": 99}, headers=AZIONE).status_code == 400
    assert c.post("/api/decisioni/pagamento", json={"cliente": "Rossi", "importo": "5", "vale_da": "ieri"}, headers=AZIONE).status_code == 400
    assert c.get("/api/decisioni").json()["decisioni"] == []  # niente di tutto questo ha scritto qualcosa
    # uno scenario incompleto torna con il suo tipo, perché la pagina lo spieghi
    manda(c, "accorda", cliente="Rossi", importo="800")
    no = manda(c, "ordine", cliente="Rossi", importo="1000")
    manda(c, "pagamento", cliente="Rossi", importo="400", vale_da="2020-01-01", causale="P1")
    storno = manda(c, "storno", causale="P1")
    incompleto = c.post("/api/decisioni/riesamina", json={"evento": no["evento"], "rettifiche": [storno["evento"]]}, headers=AZIONE)
    assert incompleto.status_code == 400 and incompleto.json()["tipo"] == "scenario_incompleto"


def test_ogni_pagina_dice_quale_versione_e_installata(c: TestClient) -> None:
    """Sul portale deve essere ben visibile che versione è installata. La scrive, accanto al nome in cima a ogni
    pagina, il file comune a tutte, leggendola dal portale stesso: così è quella che sta girando."""
    from sinapsidb import __version__

    assert c.get("/healthz").json() == {"status": "ok", "version": __version__}
    codice = c.get("/tema.js").text
    assert "/healthz" in codice and "versione" in codice and ".marchio h1" in codice and "innerHTML" not in codice
    assert ".versione" in c.get("/portale.css").text
    for percorso in ("/dati", "/confronto", "/decisioni"):  # tutte caricano quel file e hanno il nome a cui accostarla
        pagina = c.get(percorso).text
        assert '<script src="/tema.js" defer></script>' in pagina and '<div class="marchio">' in pagina and "<h1>SinapsiDB" in pagina
