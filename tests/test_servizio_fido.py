"""Il servizio su socket locale di SinapsiDB per la prova del fido: prove/fido/servizio.py.

Il server gira dentro il test, su un socket in una cartella temporanea, e viene chiuso alla fine.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "prove" / "fido"))

from cliente import Cliente  # noqa: E402
from servizio import avvia, ferma  # noqa: E402


@pytest.fixture()
def presa(tmp_path: Path) -> str:
    """Il socket di un servizio avviato su una rete nuova."""
    percorso = str(tmp_path / "s")
    server, filo = avvia(str(tmp_path / "rete"), percorso)
    yield percorso
    ferma(server, filo)


def test_l_esempio_del_contratto_attraverso_il_socket(presa: str) -> None:
    with Cliente(presa) as c:
        assert c.chiedi("fido", cliente="Rossi") == {"id": 1, "ok": True, "valore": "0", "aggiornato_a": 1, "valido_al": None}
        accordato = c.chiedi("accorda", cliente="Rossi", importo="800.00", atto="A1", chiave="a1")
        assert accordato["ok"] and accordato["esito"] == "applicato" and accordato["evento"] == 2 and accordato["ripetuta"] is False
        assert accordato["arrivo"] <= accordato["scritto"] and accordato["base"] == accordato["evento"] - 1
        letto = c.chiedi("fido", cliente="Rossi")
        assert letto["valore"] == "800" and letto["aggiornato_a"] == 2 and letto["valido_al"] >= accordato["scritto"]
        no = c.chiedi("ordine", cliente="Rossi", importo="1000.00", numero="O1", chiave="o1")
        assert (no["esito"], no["evento"]) == ("violazione_regola", 3) and c.chiedi("fido", cliente="Rossi")["valore"] == "800"
        # la stessa chiave: la risposta di allora, senza un evento nuovo
        ancora = c.chiedi("ordine", cliente="Rossi", importo="1000.00", numero="O1", chiave="o1")
        assert ancora == {**no, "id": ancora["id"], "ripetuta": True} and c.chiedi("stato")["ultimo_evento"] == 3
        pagato = c.chiedi("pagamento", cliente="Rossi", importo="400.00", causale="P1", vale_da="2020-01-01", chiave="p1")
        assert pagato["esito"] == "applicato" and c.chiedi("fido", cliente="Rossi")["valore"] == "1200"
        trovate = c.chiedi("candidate", rettifica=pagato["evento"], riesamina=True)
        assert trovate["esiti_cambiati"] == [{"evento": "evento:3", "allora": "violazione_regola", "ipotetico": "applicato"}]
        riesame = c.chiedi("riesamina", evento=no["evento"], rettifiche=[pagato["evento"]])
        assert riesame["ok"] and riesame["ipotetico"]["esito"] == "applicato" and riesame["eventi_usati"] == ["evento:4"]
        assert [y["valore"] for y in riesame["ipotetico"]["letture"] if y["quando"] == "arrivo"] == ["1200", "200"]
        assert c.chiedi("verifica", evento=no["evento"])["esito"] == "coerente"
        # uno spostamento e uno storno
        spostato = c.chiedi("sposta", cliente="Bianchi", importo="400.00", causale="P1", vale_da="2020-01-01", chiave="s1")
        assert spostato["esito"] == "applicato" and (c.chiedi("fido", cliente="Rossi")["valore"], c.chiedi("fido", cliente="Bianchi")["valore"]) == ("800", "400")
        assert c.chiedi("storno", causale="P1", chiave="x1")["esito"] == "applicato" and c.chiedi("fido", cliente="Bianchi")["valore"] == "0"
        stato = c.chiedi("stato")
        assert stato["circuiti"] == "coerente" and stato["segno"] == {"formato": 2, "dal": 0, "calcoli": [[4, 0]], "calcolo_dal": 0} and stato["ultimo_evento"] == 6


def test_gli_errori_tornano_come_risposte(presa: str) -> None:
    with Cliente(presa) as c:
        assert c.chiedi("niente")["ok"] is False and "serve «op»" in c.chiedi("niente")["errore"]
        assert c.chiedi("ordine", cliente="Rossi") == {"id": 3, "ok": False, "tipo": "richiesta", "errore": "alla richiesta manca 'importo'"}
        scritto_male = c.chiedi("pagamento", cliente="Rossi", importo="tanto", causale="P1", chiave="p1")
        assert scritto_male["ok"] is False and scritto_male["tipo"] == "rifiutato" and "non è un numero" in scritto_male["errore"]
        assert c.chiedi("riesamina", evento=99)["ok"] is False and c.chiedi("stato")["ultimo_evento"] == 1
        # dopo un errore la connessione serve ancora
        assert c.chiedi("accorda", cliente="Rossi", importo="10", atto="A1", chiave="a1")["esito"] == "applicato"
        c.chiedi("ordine", cliente="Rossi", importo="20", numero="O1", chiave="o1")
        c.chiedi("pagamento", cliente="Rossi", importo="5", causale="P1", vale_da="2020-01-01", chiave="p2")
        storno = c.chiedi("storno", causale="P1", chiave="x1")
        incompleto = c.chiedi("riesamina", evento=3, rettifiche=[storno["evento"]])
        assert incompleto["ok"] is False and incompleto["tipo"] == "scenario_incompleto"


def test_sedici_richiedenti_insieme_non_spendono_due_volte(presa: str) -> None:
    """Sedici connessioni mandano insieme ordini da 100 a un cliente con 1.000 di fido: ne passano dieci, il fido
    arriva a zero e non sotto, e ogni scrittura ha il suo numero d'ordine di commit."""
    with Cliente(presa) as c:
        assert c.chiedi("accorda", cliente="Rossi", importo="1000", atto="A1", chiave="a1")["esito"] == "applicato"
    via = threading.Barrier(16)
    risposte: list[dict] = []

    def richiedente(i: int) -> None:
        with Cliente(presa) as mio:
            via.wait()
            for j in range(2):
                risposte.append(mio.chiedi("ordine", cliente="Rossi", importo="100", numero=f"O{i}-{j}", chiave=f"o{i}-{j}"))

    fili = [threading.Thread(target=richiedente, args=(i,)) for i in range(16)]
    for f in fili:
        f.start()
    for f in fili:
        f.join(30)
    assert len(risposte) == 32 and all(r["ok"] for r in risposte)
    assert sorted(r["esito"] for r in risposte).count("applicato") == 10
    eventi = sorted(r["evento"] for r in risposte)
    assert eventi == list(range(eventi[0], eventi[0] + 32))  # un ordine totale, senza buchi e senza doppioni
    scritti = [r["scritto"] for r in sorted(risposte, key=lambda r: r["evento"])]
    assert scritti == sorted(scritti) and all(r["arrivo"] <= r["scritto"] for r in risposte)
    with Cliente(presa) as c:
        assert c.chiedi("fido", cliente="Rossi")["valore"] == "0" and c.chiedi("stato")["circuiti"] == "coerente"
        assert all(c.chiedi("verifica", evento=e)["esito"] == "coerente" for e in eventi[::4])


def test_dopo_una_conferma_persa_si_chiede_lo_stato_e_poi_si_ripete(tmp_path: Path) -> None:
    """Il caso chiesto in revisione per il tracciato. Chi ha scritto non sa se la sua richiesta è entrata. Dopo la
    riapertura chiede lo stato della chiave, senza scrivere: assente, oppure presente con la ricevuta durevole. Poi
    ripete: cambia il tentativo, non l'evento né i suoi istanti."""
    percorso = str(tmp_path / "s")
    server, filo = avvia(str(tmp_path / "rete"), percorso)
    with Cliente(percorso) as c:
        c.chiedi("accorda", cliente="Rossi", importo="800", atto="A1", chiave="a1")
        no = c.chiedi("ordine", cliente="Rossi", importo="1000", numero="O1", chiave="o1", id_tentativo="t-1")
        assert no["id_tentativo"] == "t-1" and (no["esito"], no["evento"], no["base"]) == ("violazione_regola", 3, 2)
    ferma(server, filo)
    assert not Path(percorso).exists()
    server, filo = avvia(str(tmp_path / "rete"), percorso)
    with Cliente(percorso) as c:
        assert c.chiedi("fido", cliente="Rossi")["valore"] == "800" and c.chiedi("stato")["ultimo_evento"] == 3
        di_allora = {k: no[k] for k in ("esito", "evento", "base", "arrivo", "scritto")}
        # lo stato di una chiave: sola lettura
        assert c.chiedi("stato", chiave="mai-mandata") == {"id": 3, "ok": True, "chiave": "mai-mandata", "presente": False}
        assert c.chiedi("stato", chiave="o1", id_tentativo="t-2") == {"id": 4, "ok": True, "chiave": "o1", "presente": True, **di_allora, "id_tentativo": "t-2"}
        assert c.chiedi("stato")["ultimo_evento"] == 3  # chiedere lo stato non ha scritto niente
        # la ripetizione: il tentativo è nuovo, l'evento e gli istanti sono quelli di allora
        ancora = c.chiedi("ordine", cliente="Rossi", importo="1000", numero="O1", chiave="o1", id_tentativo="t-3")
        assert ancora == {"id": 6, "ok": True, **di_allora, "ripetuta": True, "id_tentativo": "t-3"}
        assert c.chiedi("stato")["ultimo_evento"] == 3
        # una richiesta mai entrata, ripetuta dopo aver visto che è assente, entra una volta sola
        prima = c.chiedi("pagamento", cliente="Rossi", importo="50", causale="P1", chiave="p1", id_tentativo="t-4")
        dopo = c.chiedi("pagamento", cliente="Rossi", importo="50", causale="P1", chiave="p1", id_tentativo="t-5")
        assert (prima["evento"], prima["ripetuta"], dopo["evento"], dopo["ripetuta"]) == (4, False, 4, True)
        assert c.chiedi("fido", cliente="Rossi")["valore"] == "850"
    ferma(server, filo)
