"""Il giudice legge solo ciò che il client ha visto. Compresi un ACK perso e un vero riavvio."""

import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from prove.fido.tracciato import carico
from prove.fido.verificatore import Divergenza, Giudice


@pytest.fixture(scope="module")
def esecuzione(tmp_path_factory):
    cartella = tmp_path_factory.mktemp("archivio")
    socket = str(cartella / "s")

    def avvia():
        p = subprocess.Popen([sys.executable, "-u", str(ROOT / "prove/fido/servizio.py"),
                              "--cartella", str(cartella / "rete"), "--socket", socket],
                             env={**os.environ, "PYTHONPATH": str(ROOT / "src")}, stdout=subprocess.PIPE, text=True)
        if not p.stdout.readline().startswith("in ascolto"):
            p.wait(5)
            pytest.fail("servizio di prova non partito")
        return p

    servizio = avvia()
    def comando(modulo, *args):
        # Senza site-packages: niente motori, né psycopg né Redis nel giudice.
        return subprocess.run([sys.executable, "-S", "-m", f"prove.fido.{modulo}", *map(str, args)],
                              cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT)}, check=True,
                              capture_output=True, text=True, timeout=20)
    piano, prima, dopo, obs, registro = [cartella / f"{s}.jsonl" for s in ("piano", "prima", "dopo", "obs", "registro")]
    try:
        comando("tracciato", "genera", piano)
        comando("tracciato", "registra", "--socket", socket, "--prima-del-riavvio", piano, prima)
        servizio.kill()
        servizio.wait(5)
        servizio.stdout.close()
        servizio = avvia()
        comando("tracciato", "registra", "--socket", socket, "--riprendi-da", prima, piano, dopo)
    finally:
        servizio.terminate()
        servizio.wait(5)
        servizio.stdout.close()
    obs.write_bytes(prima.read_bytes() + dopo.read_bytes())
    comando("registro", "--giornale", cartella / "rete", registro)
    risultato = comando("verificatore", "--piano", piano, "--registro", registro, obs)
    assert json.loads(risultato.stdout)["registro_controllato"] is True
    return tuple([json.loads(x) for x in path.read_text().splitlines()] for path in (piano, obs, registro))


def test_il_collaudo_rilegge_le_ricevute_dopo_il_crash(esecuzione):
    piano, righe, registro = esecuzione
    # Il giudice riceve la serializzazione JSON, non oggetti o riferimenti al motore.
    risultato = Giudice(json.loads(json.dumps(righe)), piano).verifica(registro)
    assert risultato == {"esito": "coerente", "tentativi": len(piano), "decisioni": 27, "conferme_perse": 2}
    assert all(l["risposta"] is None for l in righe if l.get("perdita"))
    for k, presente in (("ACK", True), ("PERSA", False)):
        probes = [l["risposta"]["presente"] for l in righe if l["richiesta"]["op"] == "stato" and l["richiesta"].get("chiave") == k]
        assert probes == [presente, True]
    conto = next(l for l in righe if l["richiesta"]["op"] == "fido" and l["richiesta"]["cliente"] == "Conteso")
    assert conto["risposta"]["valore"] == "40"  # sei ordini da 160 su 1000, due no


def test_le_conferme_possono_arrivare_in_ordine_inverso_ma_i_commit_devono_essere_serializzabili(esecuzione):
    piano, originali, _ = esecuzione
    righe = copy.deepcopy(originali)
    gruppo = sorted((l for l in righe if l["richiesta"].get("cliente") == "Conteso" and l["richiesta"]["op"] == "ordine"),
                    key=lambda l: l["risposta"]["evento"])
    inizio, fine = min(l["invio_ns"] for l in gruppo), max(l["ritorno_ns"] for l in gruppo)
    for i, l in enumerate(gruppo):
        l.update(invio_ns=inizio, ritorno_ns=fine-i)
    assert Giudice(righe, piano).verifica()["esito"] == "coerente"
    # La risposta dell'evento più alto ora precede addirittura l'invio di quello più basso.
    gruppo[0].update(invio_ns=fine-1, ritorno_ns=fine)
    with pytest.raises(Divergenza, match="contrario al tempo reale"):
        Giudice(righe, piano).verifica()


@pytest.mark.parametrize("difetto", ["saldo", "lettura", "candidata", "doppio", "perduta", "omessa", "frontiera", "ripetizione"])
def test_il_giudice_non_prende_per_buona_la_risposta_del_motore(esecuzione, difetto):
    piano, originali, _ = esecuzione
    righe = copy.deepcopy(originali)
    def trova(op, **campi):
        return next(l for l in righe if all(l["richiesta"].get(k) == v for k, v in {"op": op, **campi}.items()))
    if difetto == "saldo":
        trova("fido", cliente="Verdi")["risposta"]["valore"] = "200"  # perde la sesta cifra
    elif difetto == "lettura":
        trova("riesamina")["risposta"]["ipotetico"]["letture"][0]["valore"] = "800"
    elif difetto == "candidata":
        r = trova("candidate")["risposta"]
        r["candidate"].pop()
        r["quante"] -= 1
    elif difetto == "doppio":
        trova("ordine", chiave="O2")["risposta"]["evento"] = trova("ordine", chiave="O1")["risposta"]["evento"]
    elif difetto == "perduta":
        # Dopo il riavvio la sonda dichiara assente la chiave, ma il retry dice di averla ritrovata.
        x = trova("stato", chiave="ACK")["risposta"]
        x["presente"] = False
    elif difetto == "omessa":
        righe.remove(trova("riesamina"))
    elif difetto == "frontiera":
        x = trova("fido", cliente="Rossi")["risposta"]
        x["aggiornato_a"] = 2
        x["valore"] = "800"  # saldo corretto alla base dichiarata, ma lettura stantia
    else:
        [l for l in righe if l["richiesta"].get("chiave") == "ACK" and l["richiesta"]["op"] == "ordine"][-1]["risposta"]["arrivo"] += 1
    with pytest.raises(Divergenza):
        Giudice(righe, piano).verifica()


@pytest.mark.parametrize("guasto", ["ricevuta", "fatto_estraneo", "rifiuto_con_fatto", "ritiro", "segno"])
def test_un_servizio_che_risponde_bene_ma_scrive_altro_non_passa(esecuzione, guasto):
    piano, righe, dati = esecuzione
    registro = copy.deepcopy(dati)
    if guasto == "ricevuta":
        next(x for x in registro if x.get("chiave") == "O1")["letture"][0]["valore"] = "801"
    elif guasto == "fatto_estraneo":
        registro.append(registro[-1])
    elif guasto == "rifiuto_con_fatto":
        x = next(x for x in registro if x.get("chiave") == "ACK")
        x["scritti"] = x["proposti"]
    elif guasto == "ritiro":
        next(x for x in registro if x.get("chiave") == "S")["ritirati"] = []
    else:
        registro[0]["segno"]["dal"] = 100
    with pytest.raises(Divergenza):
        Giudice(righe, piano).verifica(registro)


def test_seme_e_contenzione_fanno_parte_del_piano():
    args = (91, 50, 350, 50, 4, "rettifiche")
    p = list(carico(*args))
    assert p == list(carico(*args))
    assert len([x for x in p if x.get("fase") == "carico"]) == 350
    rettifiche = [x["richiesta"] for x in p if "vale_da" in x["richiesta"]]
    assert rettifiche and all(x["cliente"] == "C0" for x in rettifiche)
