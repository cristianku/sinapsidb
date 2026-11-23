"""Le zone che il terzo giro della revisione interna non aveva guardato.

Una domanda tenuta da parte dà le righe nello stesso ordine di una preparata adesso, anche se la rete è cresciuta; chi
cammina sul giornale si ferma nello stesso punto; la procedura di ripristino scritta nella guida, percorsa dall'inizio
alla fine; e «azzera» che non cancella una memoria che un altro sta scrivendo.
"""

import shutil
import struct

import pytest

from sinapsidb import attesta, blocchi, lingua
from sinapsidb.fido import Fido
from sinapsidb.memoria import Memoria
from sinapsidb.motore import GiornaleGuasto, Rete, SegnoGuasto
from test_revisione_circuiti_090 import aperta


# --- una domanda tenuta da parte e una preparata adesso ---------------------------------------------------------------------

def test_una_domanda_tenuta_da_parte_da_le_righe_nell_ordine_di_una_fresca_anche_se_la_rete_cresce(tmp_path):
    c = tmp_path / "rete"
    with aperta(Rete(str(c))) as r:
        n = r.n
        vendita, cliente, canale = n("vendita"), n("cliente"), n("canale")

        def vendi(chi, dove):
            r.racconta(f"{chi} {dove}", "test", [(vendita, [(cliente, n(chi)), (canale, n(dove))])])

        for chi in ("Ada", "Bea", "Cleo"):  # tre clienti e un canale solo: la linea più corta è quella del canale
            vendi(chi, "web")
        domanda = "in vendita leggi cliente, canale"
        tenuta = lingua.prepara(r, domanda)
        assert tenuta.esegui() == lingua.prepara(r, domanda).esegui() == [("Ada", "web"), ("Bea", "web"), ("Cleo", "web")]
        for i in range(4):  # i canali diventano più dei clienti: la linea più corta adesso è quella del cliente
            vendi("Ada" if i % 2 else "Bea", f"canale {i}")
        fresca = lingua.prepara(r, domanda)
        assert fresca.esegui()[:3] != [("Ada", "web"), ("Bea", "web"), ("Cleo", "web")]  # la fresca passa dall'altra linea
        assert tenuta.esegui() == fresca.esegui()
        assert tenuta.esegui(limite=2) == fresca.esegui(limite=2)  # con un limite: le stesse righe, non solo lo stesso insieme
        viva = fresca.esegui()
    with aperta(Rete(str(c), sola_lettura=True)) as riletta:
        assert lingua.prepara(riletta, domanda).esegui() == viva


# --- chi cammina sul giornale si ferma nello stesso punto --------------------------------------------------------------------

def nota(m, nome):
    return m.racconta(f"Nota {nome}", [{"tipo": "nota", "legami": {"cosa": nome}}], chiave=nome)


def sei_scritture(c):
    with aperta(Memoria(c, scritture=True)) as m:
        for nome in "abcdef":
            assert nota(m, nome)["esito"] == "applicato"
    return (c / "giornale").read_bytes()


def inizi(dati):
    pos, dove = 0, []
    while pos + 8 <= len(dati):
        dove.append(pos)
        pos += 8 + struct.unpack_from("<I", dati, pos)[0]
    return dove


def camminatori(c):
    """Quanti eventi buoni vede ognuno di quelli che camminano sul giornale senza aprire la rete."""
    cartella = str(c)
    return {
        "_record": sum(1 for _ in Rete._record(cartella)),
        "com_era": len(Rete.com_era(cartella, 99)[0].e_frase) if False else None,  # com_era vuole un numero che c'è: vedi sotto
        "prefisso": max(k for k in range(8) if _arriva(cartella, k)),
        "attesta": attesta.tratti(cartella)[-1]["a"] if attesta.tratti(cartella) else 0,
    }


def _arriva(cartella, eventi):
    try:
        Rete.prefisso(cartella, eventi)
        return True
    except ValueError:
        return False


@pytest.mark.parametrize("coda", [b"\x10\x00\x00", b"\x40\x00\x00\x00\x12\x34\x56\x78[\"mezza", b"\x00" * 300], ids=["testa-a-meta", "corpo-a-meta", "mai-scritto"])
def test_con_una_coda_a_meta_tutti_contano_gli_stessi_eventi(tmp_path, coda):
    c = tmp_path / "rete"
    intero = sei_scritture(c)
    (c / "giornale").write_bytes(intero + coda)
    visti = camminatori(c)
    assert visti["_record"] == visti["prefisso"] == visti["attesta"] == 6
    assert len(Rete.com_era(str(c), 6)[0].e_frase) == 6 and Rete.com_era(str(c), 6)[1] is None  # dopo il sesto non c'è un settimo
    assert sum(1 for _ in blocchi._record(c / "giornale")) == 6
    with aperta(Memoria(c)) as m:
        assert m.conteggi()["eventi"] == 6 and m.rete.eventi_nel_giornale() == 6 and m._eventi_nel_giornale(99) == 6


def test_con_un_guasto_nel_mezzo_chi_apre_lo_dice_e_chi_cammina_si_ferma_li(tmp_path):
    c = tmp_path / "rete"
    intero = sei_scritture(c)
    guasto = bytearray(intero)
    guasto[inizi(intero)[3] + 20] ^= 1  # il quarto record: tre buoni prima, due buoni dopo
    (c / "giornale").write_bytes(guasto)
    for apre in (lambda: Memoria(c), lambda: Memoria(c, scritture=True), lambda: Rete(str(c), sola_lettura=True),
                 lambda: list(blocchi._record(c / "giornale"))):
        with pytest.raises(GiornaleGuasto):  # chi apre, e chi costruisce la rete sul disco, giudicano e lo dicono
            apre()
    visti = camminatori(c)  # chi cammina senza aprire si ferma tutto nello stesso punto: davanti al record guasto
    assert visti["_record"] == visti["prefisso"] == visti["attesta"] == 3
    assert (c / "giornale").read_bytes() == bytes(guasto)


# --- la procedura di ripristino della guida, dall'inizio alla fine --------------------------------------------------------------

def test_il_ripristino_di_un_giornale_guasto_come_lo_dice_la_guida(tmp_path):
    c = tmp_path / "fido"
    f = Fido(c)
    try:
        assert f.esegui({"op": "accorda", "cliente": "Rossi", "importo": "800", "atto": "A1", "chiave": "a1"})["esito"] == "applicato"
        assert f.esegui({"op": "ordine", "cliente": "Rossi", "importo": "300", "numero": "O1", "chiave": "o1"})["esito"] == "applicato"
    finally:
        f.chiudi()
    copia = tmp_path / "copia"
    copia.mkdir()
    for nome in ("giornale", "formato"):
        shutil.copy(c / nome, copia / nome)
    f = Fido(c)
    try:  # dopo la copia la memoria va avanti, e salva un'istantanea più avanti della copia
        assert f.esegui({"op": "ordine", "cliente": "Rossi", "importo": "200", "numero": "O2", "chiave": "o2"})["esito"] == "applicato"
        f.memoria.rete.istantanea()
    finally:
        f.chiudi()
    intero = (c / "giornale").read_bytes()
    guasto = bytearray(intero)
    guasto[inizi(intero)[2] + 20] ^= 1  # un record in mezzo si rovina
    (c / "giornale").write_bytes(guasto)
    f = Fido(c)  # il servizio parte lo stesso, e ogni richiesta dice perché la memoria non si apre
    try:
        for richiesta in ({"op": "quadro"}, {"op": "ordine", "cliente": "Rossi", "importo": "1", "chiave": "x"}):
            risposta = f.esegui(richiesta)
            assert risposta["ok"] is False and risposta["tipo"] == "non_aperta" and "GiornaleGuasto" in risposta["errore"] and "byte" in risposta["errore"]
        # «fermare il portale e rimettere il file giornale da una copia»: l'istantanea non si tocca
        shutil.copy(copia / "giornale", c / "giornale")
        quadro = f.esegui({"op": "quadro"})  # alla richiesta dopo si apre: l'istantanea, più avanti del giornale, viene messa da parte
        assert quadro["ok"] and quadro["clienti"][0]["fido"] == "500" and quadro["ultimo_evento"] == 3
        assert f.esegui({"op": "stato", "chiave": "o1"})["presente"] and not f.esegui({"op": "stato", "chiave": "o2"})["presente"]
        # le chiavi della copia danno la risposta di allora; quello che era stato scritto dopo la copia non c'è più, e si può rifare
        assert f.esegui({"op": "ordine", "cliente": "Rossi", "importo": "300", "numero": "O1", "chiave": "o1"})["ripetuta"] is True
        assert f.esegui({"op": "ordine", "cliente": "Rossi", "importo": "200", "numero": "O2", "chiave": "o2"})["esito"] == "applicato"
        assert all(f.esegui({"op": "verifica", "evento": e})["esito"] == "coerente" for e in range(5))
    finally:
        f.chiudi()
    with aperta(Memoria(c)) as m:  # e l'istantanea vecchia non torna: chi riapre legge il giornale ripristinato
        assert m.conteggi()["eventi"] == 5 and m.valore("fido residuo", "Rossi")["valore"] == "300"


def test_un_segno_piu_avanti_del_giornale_ripristinato_ferma_le_scritture_finche_non_si_rimette_il_suo(tmp_path):
    """Di solito il segno di una memoria dice «dal primo evento» e non cambia più: rimettere un giornale più vecchio
    basta. Ma se il segno parla di eventi che la copia non ha (una memoria cominciata da una versione di prima, il cui
    segno comincia più avanti, o un tratto attestato dopo la copia) le scritture restano ferme finché non si rimette
    anche il segno della stessa copia."""
    import json

    c = tmp_path / "rete"
    with aperta(Memoria(c, scritture=True)) as m:
        nota(m, "a")
    copia = tmp_path / "copia"
    copia.mkdir()
    for nome in ("giornale", "formato"):
        shutil.copy(c / nome, copia / nome)
    with aperta(Memoria(c, scritture=True)) as m:
        nota(m, "b")
        nota(m, "c")
    # il segno com'è dopo che la memoria è andata avanti con una frontiera oltre la copia: parla dell'evento 2
    (c / "formato").write_text(json.dumps({"formato": 2, "dal": 0, "calcoli": [[4, 0]], "calcolo_dal": 0, "dichiarano_da": [2]}))
    assert Rete._fin_dove(Rete.segno(str(c))) == 2
    shutil.copy(copia / "giornale", c / "giornale")  # si rimette solo il giornale, che ha un evento
    with aperta(Memoria(c, scritture=True)) as m:
        assert m.conteggi()["eventi"] == 1 and m.ricevuta_di("a")["esito"] == "applicato"  # leggere si può
        assert "guasto" in m.segno()
        with pytest.raises(SegnoGuasto):
            nota(m, "d")  # scrivere no: il segno non è quello di questo giornale
    shutil.copy(copia / "formato", c / "formato")  # «va rimesso anche il formato della stessa copia»
    with aperta(Memoria(c, scritture=True)) as m:
        assert nota(m, "d")["evento"] == "evento:1" and m.verifica(1)["esito"] == "coerente"


# --- «azzera» e la memoria di un altro -----------------------------------------------------------------------------------------

def test_azzera_non_cancella_una_memoria_che_un_altro_sta_scrivendo(tmp_path):
    c = tmp_path / "fido"
    primo = Fido(c)
    secondo = Fido(c)  # un secondo servizio sulla stessa cartella: non apre la memoria, e non la deve cancellare
    try:
        assert primo.esegui({"op": "accorda", "cliente": "A", "importo": "100", "chiave": "f"})["esito"] == "applicato"
        risposta = secondo.esegui({"op": "azzera"})
        assert risposta["ok"] is False and "non è stata azzerata" in risposta["errore"]
        assert primo.esegui({"op": "ordine", "cliente": "A", "importo": "40", "chiave": "o"})["esito"] == "applicato"
    finally:
        primo.chiudi()
        secondo.chiudi()
    with aperta(Memoria(c)) as m:  # quello che il primo ha confermato c'è
        assert m.ricevuta_di("f")["esito"] == "applicato" and m.ricevuta_di("o")["esito"] == "applicato"
        assert m.valore("fido residuo", "A")["valore"] == "60"


# --- le ultime protezioni del terzo giro, ognuna con la sua prova ----------------------------------------------------------

@pytest.mark.parametrize("via", ["uno", "molti"])
def test_un_giornale_cancellato_sotto_la_rete_la_ferma(tmp_path, via):
    import os

    c = tmp_path / "rete"
    r = Rete(str(c), sync="os")
    try:
        tipo, ruolo, ada = r.n("nota"), r.n("cosa"), r.n("Ada")
        r.racconta("Prima", "test", [(tipo, [(ruolo, ada)])])
        os.unlink(c / "giornale")  # qualcuno cancella il file: chi lo tiene aperto scriverebbe in un giornale senza più nome
        with pytest.raises(RuntimeError, match="riapert"):
            if via == "uno":
                r.racconta("Seconda", "test", [(tipo, [(ruolo, ada)])])
            else:
                r.racconta_molti("test", [("Seconda", tipo, [(ruolo, ada)])])
        assert r.fuori_uso and r.memoria_guasta and r.conteggi()["eventi"] == 1
    finally:
        r.chiudi()


def test_una_coda_fatta_apposta_per_far_lavorare_non_tiene_fermo_chi_apre(tmp_path):
    """Una coda piena di pezzi che sembrano l'inizio di un record, ognuno lungo fino quasi in fondo: controllarli tutti
    costerebbe il quadrato della coda. Dal giornale scritto dal motore non ci si arriva; un file fatto così non è una
    scrittura rimasta a metà, e chi apre lo dice in fretta invece di restarci sopra."""
    import time

    c = tmp_path / "rete"
    intero = sei_scritture(c)
    lunga = 600_000
    coda = bytearray(b"\x01" * lunga)
    struct.pack_into("<I", coda, 0, lunga * 2)  # la testa: dichiara più di quello che c'è, come una coda a metà
    for q in range(16, lunga - 2000, 700):  # tanti finti inizi di record: una lunghezza che arriva quasi in fondo, poi «[»
        struct.pack_into("<II", coda, q, lunga - q - 100, 12345)
        coda[q + 8] = 0x5B
    (c / "giornale").write_bytes(intero + bytes(coda))
    inizio = time.perf_counter()
    with pytest.raises(GiornaleGuasto, match="non si lascia giudicare"):
        Memoria(c)
    assert time.perf_counter() - inizio < 5
    assert (c / "giornale").read_bytes() == intero + bytes(coda)


def test_un_banco_che_non_ha_la_cartella_per_se_apre_la_rete_solo_per_leggerla(tmp_path):
    from sinapsidb.confronto import Banco

    with aperta(Rete(str(tmp_path / "rete"), sync="os")) as r:
        n = r.n
        r.racconta("una", "prova", [(n("fattura emessa"), [(n("cliente"), n("C0")), (n("totale"), n("7"))])])
    primo = Banco(tmp_path, "host=/non-c-e dbname=nessuno")
    secondo = Banco(tmp_path, "host=/non-c-e dbname=nessuno")
    try:
        assert primo._blocco is not None and secondo._blocco is None
        with secondo._lock_rete:
            assert secondo.rete()._sola_lettura is True  # legge, e non prende il giornale per sé
        assert secondo.chiedi("in fattura emessa conta")["righe"] == [("1",)]
        with primo._lock_rete:
            assert primo.rete()._sola_lettura is False  # il primo lo apre per scrivere: l'altro non glielo ha tolto
    finally:
        primo.chiudi()
        secondo.chiudi()


@pytest.mark.parametrize("istante", [10 ** 400, float("inf"), float("nan"), "adesso", True])
def test_un_istante_che_non_e_un_numero_finito_e_rifiutato_senza_toccare_niente(tmp_path, istante):
    c = tmp_path / "rete"
    with aperta(Rete(str(c))) as r:
        tipo, ruolo, ada = r.n("nota"), r.n("cosa"), r.n("Ada")
        with pytest.raises(ValueError, match="numero finito"):
            r.racconta("Nota", "test", [(tipo, [(ruolo, ada)])], quando=istante, scritto=istante)
        assert r.conteggi()["eventi"] == 0 and not r.fuori_uso and not (c / "giornale").read_bytes()
