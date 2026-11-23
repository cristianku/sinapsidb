"""0.10.5: le tre correzioni chieste dalla revisione della 0.10.4, viste da vicino.

1. L'istantanea certifica i byte del giornale come la rete li ha riletti o scritti, non come il file è adesso: se il
   giornale è cambiato sotto la rete, l'istantanea non si salva e la rete smette di scrivere.
2. I blocchi della rete di prova portano l'identità del giornale da cui vengono (byte e impronta) e non rispondono
   finché non sono stati confrontati con il giornale che c'è.
3. Il fido è zero solo per un nome mai visto: un nome che indica più clienti chiede quale, un identificativo che
   non c'è e un circuito che manca sono errori.
"""

import hashlib
import json
import threading

import pytest

from sinapsidb import blocchi, confronto, fido as modulo_fido
from sinapsidb.confronto import Banco, Occupato
from sinapsidb.fido import Fido
from sinapsidb.memoria import Memoria, NomeAmbiguo, NomeAssente
from sinapsidb.motore import GiornaleGuasto, Rete
from test_revisione_circuiti_090 import aperta
from test_revisione_lotti_0100 import nota

DOMANDA = "in nota leggi frase, cosa"


def gira_un_bit(percorso, dove: bytes) -> None:
    """Cambia un bit dentro i dati di un record, sul posto: stesso file, stessa lunghezza."""
    dati = percorso.read_bytes()
    i = dati.index(dove)
    with percorso.open("r+b") as f:
        f.seek(i)
        f.write(bytes([dati[i] ^ 1]))


# --- 1. l'impronta dell'istantanea -----------------------------------------------------------------------------------


def test_l_istantanea_non_certifica_un_giornale_cambiato_dopo_la_lettura(tmp_path):
    c, g = str(tmp_path), tmp_path / "giornale"
    with aperta(Rete(c)) as r:
        nota(r, "Ada")
        nota(r, "Bea")
        gira_un_bit(g, b"Ada")
        with pytest.raises(GiornaleGuasto, match="non salvo l'istantanea"):
            r.istantanea()
        assert not (tmp_path / "istantanea").exists()
        assert r.fuori_uso  # la memoria è giusta, ma in coda a un giornale guasto non si scrive più
        with pytest.raises(RuntimeError, match="non scrive più"):
            nota(r, "Cora")
    with pytest.raises(GiornaleGuasto):
        Rete(c, sola_lettura=True)


def test_l_impronta_continua_dall_istantanea_e_dai_record_riletti_dopo(tmp_path):
    c, g = str(tmp_path), tmp_path / "giornale"
    with aperta(Rete(c)) as r:
        nota(r, "Ada")
        r.istantanea()
        nota(r, "Bea")  # dopo l'istantanea: chi riapre lo rilegge dal giornale
    with aperta(Rete(c)) as r:
        assert not r.istantanea_scartata
        assert r._impronta.hexdigest() == hashlib.sha256(g.read_bytes()).hexdigest()
        nota(r, "Cora")
        r.istantanea()  # l'impronta è quella del file: si salva
    with aperta(Rete(c, sola_lettura=True)) as r:
        assert not r.istantanea_scartata
        assert [r.nomi[i] for i in (r.trova("Ada") + r.trova("Bea") + r.trova("Cora"))] == ["Ada", "Bea", "Cora"]


def test_gli_eventi_scritti_a_lotti_entrano_nell_impronta(tmp_path):
    c, g = str(tmp_path), tmp_path / "giornale"
    with aperta(Rete(c)) as r:
        nota(r, "Ada")
        with r.a_lotti():
            nota(r, "Bea")
            nota(r, "Cora")
        r.istantanea()
        assert r._impronta.hexdigest() == hashlib.sha256(g.read_bytes()).hexdigest()
    with aperta(Rete(c, sola_lettura=True)) as r:
        assert not r.istantanea_scartata and r.trova("Cora")


def test_una_coda_scritta_a_meta_non_entra_nell_impronta(tmp_path):
    c, g = str(tmp_path), tmp_path / "giornale"
    with aperta(Rete(c)) as r:
        nota(r, "Ada")
    buoni = g.read_bytes()
    with g.open("ab") as f:
        f.write(b"\x40\x00\x00\x00\x00\x00")  # una testa a metà: un arresto brusco durante una scrittura
    with aperta(Rete(c)) as r:  # chi apre per scrivere la taglia
        assert g.read_bytes() == buoni
        assert r._impronta.hexdigest() == hashlib.sha256(buoni).hexdigest()
        r.istantanea()
    with aperta(Rete(c, sola_lettura=True)) as r:
        assert not r.istantanea_scartata


def test_un_istantanea_salvata_dalla_0104_vale_ancora_accanto_al_suo_giornale(tmp_path):
    """La 0.10.4 scriveva come impronta quella dei byte del file: per un giornale sano è la stessa di adesso."""
    c, g = str(tmp_path), tmp_path / "giornale"
    with aperta(Rete(c)) as r:
        nota(r, "Ada")
        r.istantanea()
        assert r._impronta_del_giornale(r._scritti).hexdigest() == hashlib.sha256(g.read_bytes()).hexdigest()
    with aperta(Rete(c, sola_lettura=True)) as r:
        assert not r.istantanea_scartata


# --- 2. i blocchi e il giornale da cui vengono -------------------------------------------------------------------------


def banco(cartella):
    return Banco(cartella, "host=/nessun-socket")


def test_i_blocchi_portano_l_identita_del_giornale_letto(tmp_path):
    cartella = tmp_path / "banco"
    with aperta(banco(cartella)) as b:
        nota(b.rete(), "Ada")
        b._disco()
    g = (cartella / "rete" / "giornale").read_bytes()
    assert blocchi.giornale_dei_blocchi(cartella / "blocchi") == {"byte": len(g), "sha256": hashlib.sha256(g).hexdigest()}
    assert blocchi.sono_del_giornale(cartella / "blocchi", cartella / "rete" / "giornale")


def test_blocchi_senza_identita_non_rispondono_e_si_rifanno(tmp_path):
    """Quelli costruiti prima della 0.10.5 non dicono da quale giornale vengono: non si riusano."""
    cartella = tmp_path / "banco"
    with aperta(banco(cartella)) as b:
        nota(b.rete(), "Ada")
        b._disco()
    testa = cartella / "blocchi" / "testa.json"
    vecchia = json.loads(testa.read_text(encoding="utf-8"))
    del vecchia["giornale"]
    testa.write_text(json.dumps(vecchia), encoding="utf-8")
    with aperta(banco(cartella)) as b:
        assert b._modo == "memoria"
        assert b.chiedi(DOMANDA)["righe"] == [("Ada", "Ada")]
        b._disco()  # chiesto di nuovo, il modo «disco» ricostruisce
        assert blocchi.giornale_dei_blocchi(cartella / "blocchi") is not None
        assert b.chiedi(DOMANDA)["righe"] == [("Ada", "Ada")]


def test_tornare_sul_disco_ricostruisce_se_il_giornale_e_un_altro_lungo_uguale(tmp_path):
    cartella = tmp_path / "banco"
    g = cartella / "rete" / "giornale"
    with aperta(banco(cartella)) as b:
        nota(b.rete(), "Ada")
        b._disco()
        b._memoria()  # i blocchi restano sul disco
    altra = tmp_path / "copia"
    with aperta(Rete(str(altra))) as r:
        nota(r, "Bea")
    dati = (altra / "giornale").read_bytes()
    assert len(dati) == g.stat().st_size and dati != g.read_bytes()
    g.write_bytes(dati)
    (cartella / "rete" / "istantanea").unlink(missing_ok=True)
    with aperta(banco(cartella)) as b:
        assert b.chiedi(DOMANDA)["righe"] == [("Bea", "Bea")]
        b._disco()
        assert b.chiedi(DOMANDA)["righe"] == [("Bea", "Bea")]


def test_sul_disco_non_si_risponde_prima_del_confronto_con_il_giornale(tmp_path, monkeypatch):
    cartella = tmp_path / "banco"
    with aperta(banco(cartella)) as b:
        nota(b.rete(), "Ada")
        b._disco()
    vero, via = blocchi.sono_del_giornale, threading.Event()

    def lento(*a, **k):
        via.wait(30)
        return vero(*a, **k)

    monkeypatch.setattr(blocchi, "sono_del_giornale", lento)
    with aperta(banco(cartella)) as b:
        assert b._modo == "disco" and not b._verificati
        with pytest.raises(Occupato):
            b.chiedi(DOMANDA)
        via.set()
        b._filo.join(30)
        assert b._verificati
        assert b.chiedi(DOMANDA)["righe"] == [("Ada", "Ada")]


def test_la_domanda_non_legge_dai_blocchi_non_confrontati(tmp_path, monkeypatch):
    """Anche se la riapertura non c'è stata (o non è partita), chi chiede non riceve risposte dai blocchi."""
    cartella = tmp_path / "banco"
    with aperta(banco(cartella)) as b:
        nota(b.rete(), "Ada")
        b._disco()
    with aperta(banco(cartella)) as b:
        monkeypatch.setattr(b, "riapri", lambda: False)
        with pytest.raises(Occupato, match="confrontando"):
            b.chiedi(DOMANDA)


# --- 3. il fido: zero solo per un nome mai visto --------------------------------------------------------------------------


def due_rossi(tmp_path):
    with aperta(Fido(tmp_path)) as f:
        assert f.esegui({"op": "accorda", "cliente": "Rossi", "importo": "100", "chiave": "a1"})["esito"] == "applicato"
    with aperta(Memoria(tmp_path, scritture=True)) as m:
        assert m.racconta("Un altro Rossi", [{"tipo": "nota", "legami": {"cosa": "Rossi"}}], nuovi=["Rossi"])["esito"] == "applicato"
        return [f"neurone:{i}" for i in m.rete.trova("Rossi")]


def test_un_nome_ambiguo_dice_quali_sono_i_candidati(tmp_path):
    ids = due_rossi(tmp_path)
    with aperta(Fido(tmp_path)) as f:
        risposta = f.esegui({"op": "fido", "cliente": "Rossi"})
        assert risposta["ok"] is False and risposta["tipo"] == "ambiguo"
        assert risposta["candidati"] == ids and "valore" not in risposta
        assert f.esegui({"op": "fido", "cliente": ids[0]})["valore"] == "100"
    with aperta(Memoria(tmp_path)) as m:
        with pytest.raises(NomeAmbiguo) as preso:
            m.valore(modulo_fido.CIRCUITO, "Rossi")
        assert preso.value.candidati == ids


def test_un_identificativo_che_non_c_e_non_vale_zero(tmp_path):
    with aperta(Fido(tmp_path)) as f:
        assert f.esegui({"op": "fido", "cliente": "Mai visto"})["valore"] == "0"
        risposta = f.esegui({"op": "fido", "cliente": "neurone:999999"})
        assert risposta["ok"] is False and risposta["tipo"] == "richiesta" and "valore" not in risposta


def test_un_circuito_che_manca_non_vale_zero(tmp_path, monkeypatch):
    with aperta(Fido(tmp_path)) as f:
        monkeypatch.setattr(modulo_fido, "CIRCUITO", "un circuito che non c'è")
        risposta = f.esegui({"op": "fido", "cliente": "Mai visto"})
        assert risposta["ok"] is False and risposta["tipo"] == "richiesta" and "valore" not in risposta
    with aperta(Memoria(tmp_path)) as m:
        with pytest.raises(NomeAssente) as preso:
            m.valore("un circuito che non c'è", "Rossi")
        assert preso.value.che == "il circuito"
