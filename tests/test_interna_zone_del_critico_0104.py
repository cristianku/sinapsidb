"""Le zone che la revisione interna della candidata 0.10.4 non aveva guardato, e quello che ci si è trovato.

Un record guasto in mezzo al giornale non è una coda scritta a metà; uno solo scrive; il servizio del fido riapre dal
giornale una memoria che si è fermata; un tipo di fatto nato troppo tardi non si scrive; il giornale si rilegge a pezzi;
una richiesta con chiave che non cambia niente lascia la sua ricevuta nel giornale.
"""

import struct
import zlib

import pytest
from starlette.testclient import TestClient

from sinapsidb import motore, portale
from sinapsidb.fido import Fido
from sinapsidb.memoria import Memoria
from sinapsidb.motore import GiornaleGuasto, Rete
from sinapsidb.portale import ConfigPortale
from test_revisione_circuiti_090 import aperta


def nota(m, nome, **altro):
    return m.racconta(f"Nota {nome}", [{"tipo": "nota", "legami": {"cosa": nome}}], chiave=nome, **altro)


def sei_scritture(c):
    with aperta(Memoria(c, scritture=True)) as m:
        for nome in "abcdef":
            assert nota(m, nome)["esito"] == "applicato"
    return (c / "giornale").read_bytes()


def record(dati):
    """Dove comincia e dove finisce ogni record del giornale."""
    pos, confini = 0, []
    while pos + 8 <= len(dati):
        lunghezza = struct.unpack_from("<I", dati, pos)[0]
        confini.append((pos, pos + 8 + lunghezza))
        pos += 8 + lunghezza
    return confini


# --- un record guasto in mezzo al giornale ------------------------------------------------------------------------------

@pytest.mark.parametrize("scritture", [True, False], ids=["chi-scrive", "chi-legge"])
def test_un_record_guasto_in_mezzo_non_si_taglia_e_non_si_nasconde(tmp_path, scritture):
    c = tmp_path / "rete"
    intero = sei_scritture(c)
    inizio, fine = record(intero)[2]
    guasto = bytearray(intero)
    guasto[inizio + 20] ^= 1  # un bit cambiato nel corpo del terzo record: dopo ce ne sono altri tre, confermati
    (c / "giornale").write_bytes(guasto)
    with pytest.raises(GiornaleGuasto, match="non è una coda"):
        Memoria(c, scritture=scritture)
    assert (c / "giornale").read_bytes() == bytes(guasto)  # nessuno ha tagliato niente
    (c / "giornale").write_bytes(intero)  # ripristinato da una copia, si riapre
    with aperta(Memoria(c, scritture=scritture)) as m:
        assert m.conteggi()["eventi"] == 6 and m.ricevuta_di("f")["esito"] == "applicato"


def test_l_ultimo_record_intero_che_non_si_rilegge_non_e_una_coda(tmp_path):
    c = tmp_path / "rete"
    intero = sei_scritture(c)
    inizio, fine = record(intero)[5]
    guasto = bytearray(intero)
    guasto[fine - 3] ^= 1  # l'ultimo record c'è tutto, ma non torna: una scrittura a metà lascia un pezzo, non un record intero
    (c / "giornale").write_bytes(guasto)
    with pytest.raises(GiornaleGuasto, match="è intero"):
        Memoria(c, scritture=True)
    assert (c / "giornale").read_bytes() == bytes(guasto)


@pytest.mark.parametrize("coda", [b"\x10\x00\x00", b"\x40\x00\x00\x00\x12\x34\x56\x78[\"mezza", b"\x00" * 300], ids=["testa-a-meta", "corpo-a-meta", "spazio-mai-scritto"])
def test_una_coda_scritta_a_meta_la_taglia_chi_apre_per_scrivere(tmp_path, coda):
    c = tmp_path / "rete"
    intero = sei_scritture(c)
    (c / "giornale").write_bytes(intero + coda)
    with aperta(Memoria(c)) as lettore:  # chi legge non tocca niente
        assert lettore.conteggi()["eventi"] == 6
    assert (c / "giornale").read_bytes() == intero + coda
    with aperta(Memoria(c, scritture=True)) as m:
        assert m.conteggi()["eventi"] == 6 and nota(m, "g")["evento"] == "evento:6"
    assert (c / "giornale").read_bytes()[:len(intero)] == intero and len(record((c / "giornale").read_bytes())) == 7


def test_il_giornale_si_rilegge_a_pezzi_e_da_la_stessa_rete(tmp_path, monkeypatch):
    c = tmp_path / "rete"
    sei_scritture(c)
    with aperta(Memoria(c)) as m:
        tutta = (m.conteggi(), list(m.rete.nomi), m.chiedi("in nota leggi cosa")["righe"])
    monkeypatch.setattr(motore, "PEZZO_DI_GIORNALE", 37)  # più corto di un record: ogni record arriva in più pezzi
    with aperta(Memoria(c)) as m:
        assert (m.conteggi(), list(m.rete.nomi), m.chiedi("in nota leggi cosa")["righe"]) == tutta
    guasto = bytearray((c / "giornale").read_bytes())
    guasto[record(bytes(guasto))[1][0] + 30] ^= 1
    (c / "giornale").write_bytes(guasto)
    with pytest.raises(GiornaleGuasto):  # e il guasto nel mezzo si riconosce anche leggendo a pezzi
        Memoria(c)


# --- uno solo scrive ------------------------------------------------------------------------------------------------------

def test_il_giornale_lo_apre_per_scrivere_uno_solo(tmp_path):
    c = tmp_path / "rete"
    primo = Memoria(c, scritture=True)
    try:
        assert nota(primo, "a")["evento"] == "evento:0"
        prima = (c / "giornale").read_bytes()
        with pytest.raises(RuntimeError, match="uno solo scrive"):
            Memoria(c, scritture=True)
        with pytest.raises(RuntimeError, match="uno solo scrive"):
            Rete(str(c))
        assert (c / "giornale").read_bytes() == prima
        with aperta(Memoria(c)) as lettore:  # leggere si può sempre
            assert lettore.ricevuta_di("a")["esito"] == "applicato"
        with pytest.raises(ValueError, match="giornale fermo"):  # e un tratto non si attesta mentre qualcuno scrive
            Rete.attesta(str(c), 4, 0, 1, "prova")
        assert nota(primo, "b")["evento"] == "evento:1"
    finally:
        primo.chiudi()
    with aperta(Memoria(c, scritture=True)) as secondo:  # chiuso il primo, il giornale è libero
        assert nota(secondo, "c")["evento"] == "evento:2"


def test_chi_non_riesce_ad_aprire_lascia_libero_il_giornale(tmp_path):
    c = tmp_path / "rete"
    intero = sei_scritture(c)
    guasto = bytearray(intero)
    guasto[record(intero)[1][0] + 20] ^= 1
    (c / "giornale").write_bytes(guasto)
    with pytest.raises(GiornaleGuasto):
        Memoria(c, scritture=True)
    (c / "giornale").write_bytes(intero)
    with aperta(Memoria(c, scritture=True)) as m:  # l'apertura fallita non ha tenuto il giornale per sé
        assert m.conteggi()["eventi"] == 6


# --- il servizio del fido riapre dal giornale ---------------------------------------------------------------------------------

def ferma_al_prossimo_evento(monkeypatch, rete):
    """L'evento arriva nel giornale, ma la memoria non riesce a contarlo: una volta sola."""
    vera, fatto = rete._in_memoria, []

    def in_memoria(*a, **k):
        if not fatto:
            fatto.append(True)
            raise MemoryError("la memoria non riesce ad allinearsi")
        return vera(*a, **k)

    monkeypatch.setattr(rete, "_in_memoria", in_memoria)


def test_il_fido_non_solleva_e_riapre_dal_giornale_una_memoria_che_si_e_fermata(tmp_path, monkeypatch):
    f = Fido(tmp_path / "fido")
    try:
        assert f.esegui({"op": "accorda", "cliente": "A", "importo": "100", "chiave": "fido"})["esito"] == "applicato"
        ferma_al_prossimo_evento(monkeypatch, f.memoria.rete)
        ferma = f.memoria
        risposta = f.esegui({"op": "ordine", "cliente": "A", "importo": "60", "chiave": "ordine-1"})
        # non solleva, non conferma e non nega: l'esito lo dice il giornale
        assert risposta["ok"] is False and risposta["tipo"] == "da_ripetere" and "stessa chiave" in risposta["errore"]
        assert f.memoria is not ferma and not f.memoria.rete.fuori_uso  # riaperta dal giornale
        # la stessa chiave: la ricevuta che c'è nel giornale, senza scrivere due volte
        stato = f.esegui({"op": "stato", "chiave": "ordine-1"})
        assert stato["ok"] and stato["presente"] and stato["esito"] == "applicato"
        ancora = f.esegui({"op": "ordine", "cliente": "A", "importo": "60", "chiave": "ordine-1"})
        assert ancora["ok"] and ancora["esito"] == "applicato" and ancora["ripetuta"] is True and ancora["evento"] == stato["evento"]
        assert f.esegui({"op": "fido", "cliente": "A"})["valore"] == "40"
        # e il secondo ordine da 60 è deciso sul saldo del giornale: non passa
        assert f.esegui({"op": "ordine", "cliente": "A", "importo": "60", "chiave": "ordine-2"})["esito"] == "violazione_regola"
        assert f.esegui({"op": "quadro"})["ok"]
    finally:
        f.chiudi()
    assert f.esegui({"op": "quadro"}) == {"id": None, "ok": False, "tipo": "chiuso", "errore": "il servizio è stato chiuso"}


def test_ogni_operazione_del_fido_risponde_anche_con_la_memoria_ferma(tmp_path):
    f = Fido(tmp_path / "fido")
    try:
        f.esegui({"op": "accorda", "cliente": "A", "importo": "100", "chiave": "fido"})
        for richiesta in ({"op": "quadro"}, {"op": "fido", "cliente": "A"}, {"op": "stato"}, {"op": "stato", "chiave": "fido"},
                          {"op": "ordine", "cliente": "A", "importo": "10", "chiave": "o"}, {"op": "verifica", "evento": 2}):
            f.memoria.rete._rotta, f.memoria.rete._memoria_guasta = "guasto messo a mano", True
            risposta = f.esegui(richiesta)  # la riapre prima di servire: risponde con quello che dice il giornale
            assert risposta["ok"], (richiesta, risposta)
            assert not f.memoria.rete.fuori_uso
    finally:
        f.chiudi()


def test_il_fido_parte_anche_se_la_memoria_non_si_apre_e_lo_dice(tmp_path):
    c = tmp_path / "fido"
    f = Fido(c)
    f.esegui({"op": "esempio"})
    f.chiudi()
    intero = (c / "giornale").read_bytes()
    guasto = bytearray(intero)
    guasto[record(intero)[2][0] + 20] ^= 1
    (c / "giornale").write_bytes(guasto)
    f = Fido(c)  # non solleva: chi lo ospita non cade con lui
    try:
        for richiesta in ({"op": "quadro"}, {"op": "ordine", "cliente": "Rossi", "importo": "1"}):
            risposta = f.esegui(richiesta)
            assert risposta["ok"] is False and risposta["tipo"] == "non_aperta" and "GiornaleGuasto" in risposta["errore"]
        assert (c / "giornale").read_bytes() == bytes(guasto)
        (c / "giornale").write_bytes(intero)  # ripristinato: alla richiesta dopo si apre
        assert f.esegui({"op": "quadro"})["ok"]
    finally:
        f.chiudi()


def test_il_portale_parte_e_risponde_anche_se_la_memoria_delle_decisioni_non_si_apre(tmp_path):
    c = tmp_path / "decisioni"
    f = Fido(c)
    f.esegui({"op": "esempio"})
    f.chiudi()
    intero = (c / "giornale").read_bytes()
    guasto = bytearray(intero)
    guasto[record(intero)[2][0] + 20] ^= 1
    (c / "giornale").write_bytes(guasto)
    config = ConfigPortale(port=8419, metriche_db=tmp_path / "metriche.db", sonda_intervallo=0, decisioni_dir=c,
                           prova_pg=f"host={tmp_path}/nessuno port=1 dbname=x")
    with TestClient(portale.crea_app(config), base_url="http://127.0.0.1:8419", client=("127.0.0.1", 5000)) as cliente:
        assert cliente.get("/healthz").status_code == 200 and cliente.get("/decisioni").status_code == 200
        risposta = cliente.get("/api/decisioni")
        assert risposta.status_code == 503 and "GiornaleGuasto" in risposta.json()["error"] and risposta.json()["tipo"] == "non_aperta"
        (c / "giornale").write_bytes(intero)
        assert cliente.get("/api/decisioni").status_code == 200


# --- un tipo di fatto nato troppo tardi ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("via", ["uno", "molti"])
def test_un_tipo_di_fatto_oltre_il_limite_delle_linee_non_si_scrive(tmp_path, monkeypatch, via):
    c = tmp_path / "rete"
    with aperta(Rete(str(c))) as r:
        tipo, ruolo, ada = r.n("nota"), r.n("cosa"), r.n("Ada")
        r.racconta("Prima", "test", [(tipo, [(ruolo, ada)])])
        monkeypatch.setattr(motore, "MASSIMO_TIPO", len(r.nomi))  # come se i primi due milioni di neuroni fossero finiti qui
        tardi, ruolo_tardi, bea = r.n("reso"), r.n("di che cosa"), r.n("Bea")
        prima = ((c / "giornale").read_bytes(), r.conteggi()["fatti"], dict(r.forma), len(r.vivi))
        with pytest.raises(ValueError, match="tra i primi"):
            if via == "uno":
                r.racconta("Un reso", "test", [(tardi, [(ruolo, ada)])])
            else:
                r.racconta_molti("test", [("Un reso", tardi, [(ruolo, ada)])])
        assert ((c / "giornale").read_bytes(), r.conteggi()["fatti"], dict(r.forma), len(r.vivi)) == prima and not r.fuori_uso
        # il limite è dei tipi: un ruolo e un partecipante nati tardi vanno bene
        scrivi = (lambda: r.racconta("Seconda", "test", [(tipo, [(ruolo_tardi, bea)])])) if via == "uno" else (
            lambda: r.racconta_molti("test", [("Seconda", tipo, [(ruolo_tardi, bea)])]))
        scrivi()
        assert r.scatta(tipo, [(ruolo_tardi, bea)])
    with aperta(Rete(str(c))) as riletta:
        assert riletta.conteggi()["fatti"] == 2


# --- una richiesta con chiave che non cambia niente ----------------------------------------------------------------------------

def test_la_ricevuta_di_una_richiesta_che_non_cambia_niente_si_verifica_e_non_si_riesamina(tmp_path):
    c = tmp_path / "rete"
    with aperta(Memoria(c, scritture=True)) as m:
        assert nota(m, "a")["esito"] == "applicato"
        senza_chiave = m.racconta("Nota a", [{"tipo": "nota", "legami": {"cosa": "a"}}])
        assert senza_chiave["esito"] == "nessun_cambiamento" and "evento" not in senza_chiave and m.conteggi()["eventi"] == 1
        prova = m.racconta("Nota a", [{"tipo": "nota", "legami": {"cosa": "a"}}], chiave="p", prova=True)
        assert prova["esito"] == "nessun_cambiamento" and m.conteggi()["eventi"] == 1 and m.ricevuta_di("p") is None
        con_chiave = m.racconta("Nota a", [{"tipo": "nota", "legami": {"cosa": "a"}}], chiave="di nuovo")
        assert con_chiave["esito"] == "nessun_cambiamento" and con_chiave["evento"] == "evento:1" and con_chiave["scritture_applicate"] == 0
        assert m.conteggi() == {**m.conteggi(), "eventi": 2, "fatti": 1}  # un evento in più, nessun fatto in più
        assert m.verifica(1)["esito"] == "coerente"
        with pytest.raises(ValueError, match="non ha cambiato niente"):
            m.riesamina(1)
        assert m.verifica_circuiti()["esito"] == "coerente"
    # un evento con dei fatti non può dichiarare di non aver cambiato niente
    from test_decisioni import _record, _riscrivi

    righe = _record(c)
    righe[0][7]["ricevuta"]["esito"] = "nessun_cambiamento"
    _riscrivi(c, righe)
    with aperta(Memoria(c)) as m:
        assert m.verifica(0)["esito"] == "guasto" and m.verifica(1)["esito"] == "coerente"


# --- il passaggio dalla 0.9.8 installata -----------------------------------------------------------------------------------

SCRITTO_DALLA_098 = __import__("pathlib").Path(__file__).parent / "dati" / "scritto-dalla-098"
DOMANDE_098 = ("in vendita conta per cliente", "in vendita somma totale per cliente", "accendi cliente = Ada in vendita leggi numero, totale",
               "in vendita dove totale > 15 leggi numero", "vocabolario")


def test_i_dati_scritti_dalla_098_si_aprono_e_dicono_le_stesse_cose(tmp_path):
    """Una memoria del fido e una rete con la sua istantanea, scritte dal codice della 0.9.8 (quella installata prima di
    questa candidata) e tenute in ``tests/dati``. La candidata le apre senza toccare i giornali, risponde come
    rispondeva la 0.9.8, verifica ogni decisione, e l'istantanea di allora, che non porta il sigillo, la rifà."""
    import json
    import shutil

    from sinapsidb import lingua

    c = shutil.copytree(SCRITTO_DALLA_098, tmp_path / "dati")
    allora = json.loads((c / "risposte-della-098.json").read_text(encoding="utf-8"))
    giornali = {nome: (c / nome / "giornale").read_bytes() for nome in ("fido", "rete")}
    # la memoria del fido: un sì, un no, un pagamento con la sua decorrenza, un altro sì, uno storno
    f = Fido(c / "fido")
    try:
        quadro = f.esegui({"op": "quadro"})
        assert quadro["ok"] and quadro["circuiti"] == "coerente" and quadro["clienti"] == allora["fido"]["clienti"]
        assert [[d["evento"], d["esito"]] for d in quadro["decisioni"]] == allora["fido"]["decisioni"]
        assert [f.esegui({"op": "verifica", "evento": e})["esito"] for e in range(quadro["ultimo_evento"] + 1)] == allora["fido"]["verifiche"]
        assert {k: f.esegui({"op": "stato", "chiave": k})["esito"] for k in allora["fido"]["chiavi"]} == allora["fido"]["chiavi"]
        # la stessa chiave di allora riceve la risposta di allora, senza scrivere
        assert f.esegui({"op": "ordine", "cliente": "Rossi", "importo": "1000", "numero": "O1", "chiave": "ordine-r"})["ripetuta"] is True
    finally:
        f.chiudi()
    assert (c / "fido" / "giornale").read_bytes() == giornali["fido"]
    # la rete: chi legge non usa l'istantanea di allora e non tocca niente
    with aperta(Rete(str(c / "rete"), sola_lettura=True)) as r:
        assert r.istantanea_scartata and r.conteggi() == allora["rete"]["conteggi"]
        assert {d: [list(x) for x in lingua.chiedi(r, d)["righe"]] for d in DOMANDE_098} == allora["rete"]["righe"]
    vecchia = (c / "rete" / "istantanea").read_bytes()
    assert (c / "rete" / "giornale").read_bytes() == giornali["rete"]
    # chi apre per scrivere ne salva una nuova, con il sigillo, e da lì si riapre senza rileggere il giornale
    with aperta(Rete(str(c / "rete"), sync="os")) as r:
        assert r.istantanea_scartata
        r.istantanea()
    assert (c / "rete" / "istantanea").read_bytes() != vecchia and (c / "rete" / "giornale").read_bytes() == giornali["rete"]
    with aperta(Rete(str(c / "rete"), sola_lettura=True)) as r:
        assert not r.istantanea_scartata and r.conteggi() == allora["rete"]["conteggi"]
        assert {d: [list(x) for x in lingua.chiedi(r, d)["righe"]] for d in DOMANDE_098} == allora["rete"]["righe"]
