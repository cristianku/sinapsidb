"""Le zone che il secondo giro della revisione interna non aveva guardato, e quello che ci si è trovato.

La rete sul disco non si costruisce da un giornale guasto; un programma che scrive nel giornale senza prenderlo per sé
(come fa la versione di prima) viene scoperto alla scrittura dopo; un banco solo per cartella; i blocchi valgono per il
giornale da cui sono stati fatti; il ritorno in memoria non lascia il banco a metà; e le prove che tengono ferme le
ultime correzioni.
"""

import errno
import os
import struct
import zlib

import pytest

from sinapsidb import blocchi, lingua, motore
from sinapsidb.confronto import Banco
from sinapsidb.fido import Fido
from sinapsidb.memoria import Memoria, Rifiutato
from sinapsidb.motore import GiornaleGuasto, Rete
from test_revisione_circuiti_090 import aperta

NESSUN_PG = "host=/non-c-e dbname=nessuno"


def rete_di_fatture(c, quante=40):
    """Una rete di prova scritta e chiusa, con dove comincia ogni record del suo giornale."""
    inizi = []
    with aperta(Rete(str(c), sync="os")) as r:
        n = r.n
        fattura, cliente, totale = n("fattura emessa"), n("cliente"), n("totale")
        for i in range(quante):
            inizi.append(os.path.getsize(c / "giornale") if (c / "giornale").exists() else 0)
            r.racconta(f"fattura {i}", "prova", [(fattura, [(cliente, n(f"C{i % 5}")), (totale, n(str(100 + i)))])])
    return inizi


def lancia(banco, nome):
    banco._lavoro = {**banco._lavoro, "in_corso": True, "iniziato": 0}
    banco._esegui(nome, None)
    return banco._lavoro["errore"]


# --- la rete sul disco e il giornale guasto --------------------------------------------------------------------------------

def test_la_rete_sul_disco_non_si_costruisce_da_un_giornale_guasto_nel_mezzo(tmp_path):
    inizi = rete_di_fatture(tmp_path / "rete")
    giornale = tmp_path / "rete" / "giornale"
    dati = bytearray(giornale.read_bytes())
    dati[inizi[20] + 12] ^= 1  # un bit nel corpo del record 20: dopo ce ne sono altri diciannove, confermati
    giornale.write_bytes(dati)
    with pytest.raises(GiornaleGuasto):
        blocchi.costruisci(giornale, tmp_path / "fuori")
    banco = Banco(tmp_path, NESSUN_PG)
    try:
        assert "GiornaleGuasto" in lancia(banco, "apertura")
        assert "GiornaleGuasto" in lancia(banco, "disco")  # non finisce «senza errore» con venti fatti
        assert banco._modo == "memoria" and not (tmp_path / "modo").exists()
    finally:
        banco.chiudi()
    assert giornale.read_bytes() == bytes(dati)


def test_la_rete_sul_disco_si_costruisce_anche_con_una_coda_mai_scritta(tmp_path):
    rete_di_fatture(tmp_path / "rete", 5)
    giornale = tmp_path / "rete" / "giornale"
    with open(giornale, "ab") as f:
        f.write(b"\0" * 64)  # spazio mai scritto in fondo: per il motore è una coda come un'altra
    with aperta(Rete(str(tmp_path / "rete"), sola_lettura=True)) as r:
        assert r.conteggi()["fatti"] == 5 and r.eventi_nel_giornale() == 5
        assert len(list(Rete._record(str(tmp_path / "rete")))) == 5 and Rete.com_era(str(tmp_path / "rete"), 5)[1] is None
    assert blocchi.costruisci(giornale, tmp_path / "fuori")["fatti"] == 5


# --- un programma che scrive senza prendere il giornale per sé -----------------------------------------------------------------

def record_di_un_altro(frase="scritto da un altro"):
    corpo = motore._codifica([frase, "altro", 1.0, "scrittura", [], [], []]).encode()
    return struct.pack("<II", len(corpo), zlib.crc32(corpo)) + corpo


@pytest.mark.parametrize("che", ["aggiunge", "accorcia"])
@pytest.mark.parametrize("via", ["uno", "molti", "lotti"])
def test_se_il_giornale_cambia_sotto_la_rete_la_scrittura_dopo_si_ferma(tmp_path, via, che):
    c = tmp_path / "rete"
    r = Rete(str(c), sync="os")
    try:
        tipo, ruolo, ada = r.n("nota"), r.n("cosa"), r.n("Ada")
        r.racconta("Prima", "test", [(tipo, [(ruolo, ada)])])
        r.racconta("Seconda", "test", [(tipo, [(ruolo, ada)])])
        # come un programma di prima, che non prende il giornale per sé: ci scrive, o lo accorcia, sotto i piedi
        if che == "aggiunge":
            with open(c / "giornale", "ab") as f:
                f.write(record_di_un_altro())
        else:
            os.truncate(c / "giornale", len((c / "giornale").read_bytes()) - 5)
        cosi = (c / "giornale").read_bytes()
        with pytest.raises(RuntimeError, match="riapert"):
            if via == "uno":
                r.racconta("Terza", "test", [(tipo, [(ruolo, ada)])])
            elif via == "molti":
                r.racconta_molti("test", [("Terza", tipo, [(ruolo, ada)])])
            else:
                with r.a_lotti():
                    r.racconta_molti("test", [("Terza", tipo, [(ruolo, ada)])])
        # non ha confermato niente con un numero che il giornale non le dà, e non ha toccato il giornale
        assert r.fuori_uso and r.memoria_guasta and (c / "giornale").read_bytes() == cosi
    finally:
        r.chiudi()


# --- il banco del confronto ---------------------------------------------------------------------------------------------------

def test_un_banco_solo_per_cartella_lancia_i_lavori(tmp_path):
    primo = Banco(tmp_path, NESSUN_PG)
    secondo = Banco(tmp_path, NESSUN_PG)
    try:
        assert "un altro portale" in secondo.avvia("azzera")  # non parte: la cartella è del primo
        assert secondo._lavoro["in_corso"] is False
    finally:
        primo.chiudi()
        secondo.chiudi()
    terzo = Banco(tmp_path, NESSUN_PG)  # chiuso il primo, la cartella è libera
    try:
        assert terzo._blocco is not None
    finally:
        terzo.chiudi()


def sul_disco(tmp_path):
    rete_di_fatture(tmp_path / "rete", 12)
    banco = Banco(tmp_path, NESSUN_PG)
    try:
        assert lancia(banco, "apertura") is None and lancia(banco, "disco") is None and banco._modo == "disco"
    finally:
        banco.chiudi()


def test_i_blocchi_valgono_solo_per_il_giornale_da_cui_sono_stati_fatti(tmp_path):
    sul_disco(tmp_path)
    giornale = tmp_path / "rete" / "giornale"
    copia = giornale.read_bytes()
    with aperta(Rete(str(tmp_path / "rete"), sync="os")) as r:  # il giornale cambia dopo che i blocchi sono stati fatti
        n = r.n
        r.racconta("una in più", "prova", [(n("fattura emessa"), [(n("cliente"), n("C0")), (n("totale"), n("999"))])])
    banco = Banco(tmp_path, NESSUN_PG)
    try:
        assert banco._modo == "memoria"  # non risponde dai blocchi, che hanno dodici fatture e non tredici
        assert lancia(banco, "apertura") is None and banco.chiedi("in fattura emessa conta")["righe"] == [("13",)]
    finally:
        banco.chiudi()
    giornale.write_bytes(copia)  # e se il giornale torna quello di allora (un ripristino), i blocchi tornano buoni
    (tmp_path / "rete" / "istantanea").unlink(missing_ok=True)
    (tmp_path / "modo").write_text("disco")
    banco = Banco(tmp_path, NESSUN_PG)
    try:
        assert banco._modo == "disco" and banco.chiedi("in fattura emessa conta")["righe"] == [("12",)]
    finally:
        banco.chiudi()


def test_il_ritorno_in_memoria_non_lascia_il_banco_a_meta(tmp_path):
    sul_disco(tmp_path)
    banco = Banco(tmp_path, NESSUN_PG)
    try:
        assert banco._modo == "disco"
        with aperta(Rete(str(tmp_path / "rete"), sync="os")):  # un altro tiene il giornale per scrivere
            assert "uno solo scrive" in lancia(banco, "memoria")
            assert banco._modo == "disco" and banco._rete is None
            assert banco.chiedi("in fattura emessa conta")["righe"] == [("12",)]  # i blocchi rispondono ancora
        (tmp_path / "rete" / "istantanea").unlink(missing_ok=True)  # come dopo un aggiornamento: nessuna istantanea buona
        assert lancia(banco, "memoria") is None and banco._modo == "memoria"
        assert banco.chiedi("in fattura emessa conta")["righe"] == [("12",)]
    finally:
        banco.chiudi()


def test_tornando_in_memoria_con_un_istantanea_da_buttare_se_ne_salva_una_nuova(tmp_path):
    sul_disco(tmp_path)
    istantanea = tmp_path / "rete" / "istantanea"
    istantanea.write_bytes(b"una di prima, senza sigillo")
    banco = Banco(tmp_path, NESSUN_PG)
    try:
        assert lancia(banco, "memoria") is None
        assert banco._rete.istantanea_scartata is False and istantanea.read_bytes().startswith(b"una di prima") is False
    finally:
        banco.chiudi()
    with aperta(Rete(str(tmp_path / "rete"), sola_lettura=True)) as r:
        assert not r.istantanea_scartata and r.conteggi()["fatti"] == 12


def test_un_istantanea_piu_avanti_del_giornale_si_dice_e_si_rifa(tmp_path):
    c = tmp_path / "rete"
    rete_di_fatture(c, 5)
    copia = (c / "giornale").read_bytes()
    with aperta(Rete(str(c), sync="os")) as r:
        n = r.n
        r.racconta("una in più", "prova", [(n("fattura emessa"), [(n("cliente"), n("C0")), (n("totale"), n("999"))])])
        r.istantanea()
    (c / "giornale").write_bytes(copia)  # il giornale ripristinato da una copia di prima: l'istantanea è più avanti
    with aperta(Rete(str(c), sync="os")) as r:
        assert r.istantanea_scartata and r.conteggi()["fatti"] == 5
        r.istantanea()
    with aperta(Rete(str(c), sola_lettura=True)) as r:
        assert not r.istantanea_scartata and r.conteggi()["fatti"] == 5


# --- le prove che tengono ferme le ultime correzioni ------------------------------------------------------------------------

def test_un_fido_aperto_a_meta_tra_il_circuito_e_la_regola_si_completa_alla_riapertura(tmp_path, monkeypatch):
    vera = Rete.proponi_regola
    monkeypatch.setattr(Rete, "proponi_regola", lambda *a, **k: (_ for _ in ()).throw(OSError(errno.EIO, "il disco non risponde")))
    f = Fido(tmp_path / "fido")  # il primo avvio si ferma dopo il circuito e prima della regola: non solleva
    assert f.esegui({"op": "quadro"})["tipo"] == "non_aperta"
    monkeypatch.setattr(Rete, "proponi_regola", vera)
    try:
        assert f.esegui({"op": "accorda", "cliente": "A", "importo": "100", "chiave": "f"})["esito"] == "applicato"
        assert f.memoria.rete.regole  # la regola c'è: non resta una memoria che dichiara una regola che non ha
        assert f.esegui({"op": "ordine", "cliente": "A", "importo": "500", "chiave": "o"})["esito"] == "violazione_regola"
    finally:
        f.chiudi()


def test_un_fatto_dei_molti_che_torna_indietro_non_porta_via_la_forma_di_uno_gia_scritto(tmp_path, monkeypatch):
    c = tmp_path / "rete"
    with aperta(Rete(str(c), sync="os")) as r:
        tipo, altro, ruolo, in_piu, ada = r.n("nota"), r.n("avviso"), r.n("cosa"), r.n("perché"), r.n("Ada")
        vero, quanti = r._scrivi_record, []

        def al_secondo_non_scrive(dati):
            quanti.append(1)
            if len(quanti) == 2:
                raise OSError(errno.ENOSPC, "disco pieno")
            return vero(dati)

        monkeypatch.setattr(r, "_scrivi_record", al_secondo_non_scrive)
        with pytest.raises(OSError):  # il primo fatto è scritto e dà la forma al suo tipo; il secondo, di un altro tipo, torna indietro
            r.racconta_molti("test", [("Prima", tipo, [(ruolo, ada)]), ("Seconda", altro, [(ruolo, ada), (in_piu, ada)])])
        assert not r.fuori_uso and r.forma.get(tipo) == (ruolo,) and altro not in r.forma
        viva = (dict(r.forma), lingua.chiedi(r, "in nota leggi cosa")["righe"])
    with aperta(Rete(str(c), sola_lettura=True)) as riletta:
        assert (dict(riletta.forma), lingua.chiedi(riletta, "in nota leggi cosa")["righe"]) == viva


def test_un_evento_dei_molti_scritto_che_la_memoria_non_conta_la_lascia_guasta(tmp_path):
    with aperta(Rete(str(tmp_path / "rete"), sync="os")) as r:
        tipo, ruolo, ada = r.n("nota"), r.n("cosa"), r.n("Ada")

        class NonSiAllunga(list):
            def append(self, x):
                raise MemoryError("la memoria non si allinea")

        r.e_tipo = NonSiAllunga(r.e_tipo)
        with pytest.raises(MemoryError):
            r.racconta_molti("test", [("Prima", tipo, [(ruolo, ada)])])
        assert r.fuori_uso and r.memoria_guasta
    assert len(list(Rete._record(str(tmp_path / "rete")))) == 1  # nel giornale c'è


def test_un_nome_che_il_giornale_non_sa_scrivere_non_nasce(tmp_path):
    with aperta(Rete(str(tmp_path / "rete"))) as r:
        tipo, ruolo = r.n("nota"), r.n("cosa")
        prima = list(r.nomi)
        with pytest.raises(ValueError):
            r.n("spaiato \ud800")
        assert list(r.nomi) == prima
        assert r.racconta("Dopo", "test", [(tipo, [(ruolo, r.n("Ada"))])]) == 0  # e le scritture dopo non restano bloccate


@pytest.mark.parametrize("via", ["uno", "molti"])
@pytest.mark.parametrize("frase", [("una", "tupla"), 7, None], ids=["tupla", "numero", "niente"])
def test_la_frase_di_un_evento_e_un_testo(tmp_path, via, frase):
    c = tmp_path / "rete"
    with aperta(Rete(str(c))) as r:
        tipo, ruolo, ada = r.n("nota"), r.n("cosa"), r.n("Ada")
        with pytest.raises(ValueError, match="testi"):
            if via == "uno":
                r.racconta(frase, "test", [(tipo, [(ruolo, ada)])])
            else:
                r.racconta_molti("test", [(frase, tipo, [(ruolo, ada)])])
        assert r.conteggi()["eventi"] == 0 and not r.fuori_uso and not (c / "giornale").read_bytes()


def test_una_chiave_che_non_resta_uguale_a_se_stessa_e_rifiutata(tmp_path):
    with aperta(Memoria(tmp_path / "rete", scritture=True)) as m:
        for chiave in (float("nan"), float("inf")):
            with pytest.raises(Rifiutato, match="chiave"):
                m.racconta("Nota", [{"tipo": "nota", "legami": {"cosa": "A"}}], chiave=chiave)
        assert m.conteggi()["eventi"] == 0


def test_un_evento_troppo_lungo_per_un_record_non_si_scrive(tmp_path, monkeypatch):
    c = tmp_path / "rete"
    with aperta(Rete(str(c))) as r:
        tipo, ruolo, ada = r.n("nota"), r.n("cosa"), r.n("Ada")
        monkeypatch.setattr(motore, "MASSIMO_RECORD", 200)
        with pytest.raises(ValueError, match="al massimo"):
            r.racconta("Una frase lunga " * 20, "test", [(tipo, [(ruolo, ada)])])
        assert r.conteggi()["eventi"] == 0 and not r.fuori_uso and not (c / "giornale").read_bytes()
        assert r.racconta("Corta", "t", [(tipo, [(ruolo, ada)])]) == 0
