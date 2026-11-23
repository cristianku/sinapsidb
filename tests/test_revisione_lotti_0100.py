"""0.10.0: un'importazione interrotta può perdere una coda, non inventare o duplicare eventi."""

import errno
import struct

import pytest

from sinapsidb import motore
from sinapsidb.memoria import Memoria
from sinapsidb.motore import DefinizioneNonValida, Rete
from test_revisione_circuiti_090 import aperta


def nota(r, nome):
    return r.racconta(nome, "test", [(r.n("nota"), [(r.n("cosa"), r.n(nome))])], quando=1234.5, scritto=1234.5)


def frasi(c):
    return [record[0] for record in Rete._record(str(c))]


def ferma_dopo_un_record(mp, r):
    """A arriva intero, poi un solo ENOSPC. Il disco torna disponibile senza riaprire la rete."""
    vero = motore.os.write
    chiamate = []

    def write(fd, dati):
        if fd == r._fd:
            chiamate.append(len(dati))
            if len(chiamate) == 1:
                primo = 8 + struct.unpack_from("<I", dati)[0]
                assert primo < len(dati)  # ci sono almeno A e B nel gruppo
                return vero(fd, dati[:primo])
            if len(chiamate) == 2:
                raise OSError(errno.ENOSPC, "non c'è posto per B")
        return vero(fd, dati)

    mp.setattr(motore.os, "write", write)
    return chiamate


def test_il_finally_del_lotto_non_rimanda_da_capo_un_prefisso_gia_scritto(tmp_path, monkeypatch):
    c = tmp_path / "rete"
    with aperta(Rete(str(c), sync="os")) as r:
        nota(r, "Prima")  # un evento già nel giornale prima dell'importazione
        with monkeypatch.context() as mp:
            chiamate = ferma_dopo_un_record(mp, r)
            try:
                with r.a_lotti():
                    nota(r, "A")
                    mp.setattr(motore, "LOTTO", 1)  # la scrittura di B fa scaricare [A, B]
                    nota(r, "B")
            except OSError:
                pass
            else:
                assert frasi(c) == ["Prima", "A", "B"]  # se il lotto è riuscito, è arrivato intero
        assert len(chiamate) >= 2
    # Non imponiamo che il lotto sia una transazione. Qualunque prefisso sopravviva, ogni evento ci sta una volta.
    with aperta(Rete(str(c))) as riletta:
        scritte = frasi(c)
        assert scritte and scritte == ["Prima", "A", "B"][:len(scritte)]
        assert riletta.e_frase == scritte and riletta.conteggi()["fatti"] == len(scritte)


@pytest.mark.parametrize("operazione", ["scarica", "eventi_nel_giornale"])
def test_dopo_un_lotto_rotto_non_si_riappende_il_buffer_nemmeno_chiedendo_la_lunghezza(tmp_path, monkeypatch, operazione):
    c = tmp_path / "rete"
    with aperta(Rete(str(c), sync="lotti")) as r:
        nota(r, "A")
        nota(r, "B")
        with monkeypatch.context() as mp:
            ferma_dopo_un_record(mp, r)
            with pytest.raises(OSError):
                r.scarica()
        prefisso = (c / "giornale").read_bytes()
        quanti = len(frasi(c))
        try:
            risposta = getattr(r, operazione)()
        except (RuntimeError, OSError):
            pass  # rete fuori uso: si riapre
        else:
            if operazione == "eventi_nel_giornale":
                assert risposta == quanti
        assert (c / "giornale").read_bytes() == prefisso
    with aperta(Rete(str(c))) as riletta:
        assert riletta.e_frase == ["A", "B"][:quanti]


def test_una_write_corta_senza_errore_completa_il_lotto_una_volta_sola(tmp_path, monkeypatch):
    with aperta(Rete(str(tmp_path / "singoli"))) as r:
        for nome in ("A", "B", "C"):
            nota(r, nome)
    c = tmp_path / "lotti"
    with aperta(Rete(str(c), sync="os")) as r:
        vero = motore.os.write
        with monkeypatch.context() as mp:
            mp.setattr(motore.os, "write", lambda fd, b: vero(fd, b[:7] if fd == r._fd else b))
            with r.a_lotti():
                for nome in ("A", "B", "C"):
                    nota(r, nome)
        assert (c / "giornale").read_bytes() == (tmp_path / "singoli" / "giornale").read_bytes()
        r.istantanea()
    with aperta(Rete(str(c))) as riletta:
        assert riletta.e_frase == ["A", "B", "C"]


def test_se_si_cambia_sync_su_una_memoria_non_si_conferma_un_evento_solo_in_ram(tmp_path):
    c = tmp_path / "memoria"
    with aperta(Memoria(c, scritture=True)) as m:
        try:
            m.rete.sync = "lotti"  # attributo pubblico, dopo il costruttore
            b = m.racconta("B", [{"tipo": "nota", "legami": {"cosa": "B"}}], chiave="B")
        except (AttributeError, ValueError, RuntimeError):
            assert not frasi(c) and m.ricevuta_di("B") is None
        else:
            # Guardare PRIMA di chiudi(), che svuota il buffer e nasconderebbe la falsa conferma.
            assert b["esito"] == "applicato" and frasi(c) == ["B"] and m.segno() is not None
            assert m.verifica(b["evento"])["esito"] == "coerente"


@pytest.mark.parametrize("modo", ["costruttore", "contesto"])
def test_il_divieto_dei_lotti_appartiene_anche_al_giornale_che_registra_decisioni(tmp_path, modo):
    c = tmp_path / "memoria"
    with aperta(Memoria(c, scritture=True)) as m:
        a = m.racconta("A", [{"tipo": "nota", "legami": {"cosa": "A"}}], chiave="A")
        assert m.segno()["dal"] == 0
    prima = (c / "giornale").read_bytes()
    try:
        # Una nuova istanza, con il default decisioni=False, sul giornale che dichiara durevole quel contratto.
        with aperta(Rete(str(c), sync="lotti" if modo == "costruttore" else "os")) as r:
            if modo == "costruttore":
                nota(r, "B")
            else:
                with r.a_lotti():
                    nota(r, "B")
    except (ValueError, RuntimeError):
        assert (c / "giornale").read_bytes() == prima
    else:
        # Se sceglie di scrivere comunque, la decisione di B non può mancare.
        with aperta(Memoria(c)) as m:
            assert m.verifica(1)["esito"] == "coerente" and m.ricevuta_di("A") == a


@pytest.mark.parametrize("chiave", ["circuito", "CIRCUITO"], ids=["ridotta", "non-ridotta"])
def test_una_chiave_passata_a_nuovo_non_puo_nascondere_una_definizione_al_controllo(tmp_path, chiave):
    c = tmp_path / "rete"
    with aperta(Rete(str(c), decisioni=True)) as r:
        nota(r, "Prima")  # ha già deciso con una rete senza definizioni
        prima, segno = (c / "giornale").read_bytes(), len(r.nomi)
        # Sbagliare il suggerimento opzionale può dare errore; non può autorizzare un circuito senza termini.
        with pytest.raises((ValueError, TypeError)):
            # Anche tenere l'ottimizzazione dietro un metodo privato, senza questo argomento, è corretto.
            t = r.nuovo("CIRCUITO", chiave)
            r.racconta("Mensile senza termini", "test", [(t, [(r.n("circuito di"), r.n("Mensile"))])], neuroni_da=segno)
        assert (c / "giornale").read_bytes() == prima and r.e_frase == ["Prima"]
    with aperta(Memoria(c)) as riletta:
        assert riletta.verifica(0)["esito"] == "coerente"


def test_un_termine_dimenticato_e_rinato_va_controllato_anche_dopo_l_istantanea(tmp_path):
    c = tmp_path / "rete"
    with aperta(Rete(str(c), decisioni=True)) as r:
        nota(r, "Prima")
        segno = len(r.nomi)
        # La prova riconosce un circuito valido; nulla di questa definizione sopravvive.
        circuito, termine, ricavi = r.n("CÌRCUITO"), r.n("TERMINE"), r.n("Ricavi")
        definizione = [(circuito, [(r.n("circuito di"), ricavi)]),
                       (termine, [(r.n("termine di"), ricavi), (r.n("osserva"), r.n("vendita")), (r.n("lega"), r.n("cliente")),
                                  (r.n("somma"), r.n("totale")), (r.n("peso"), r.n("1"))])]
        assert r.racconta("Solo una prova", "test", definizione, prova=True, neuroni_da=segno)["esito"] == "prova"
        assert not r.trova("circuito") and not r.trova("termine")
        assert r.n("colore") == circuito and r.n("blu") == termine
        r.racconta("Colore", "test", [(r.n("nota"), [(r.n("colore"), r.n("blu"))])])
        r.istantanea()
    prima = (c / "giornale").read_bytes()
    with aperta(Rete(str(c), decisioni=True)) as r:
        # Con un altro ID, dopo una riapertura: la proposta senza termini non deve entrare.
        segno = len(r.nomi)
        rinato = r.n(" circuito ")
        assert rinato != circuito
        with pytest.raises(DefinizioneNonValida):
            r.racconta("Circuito incompleto", "test", [(rinato, [(r.n("circuito di"), r.n("Ricavi"))])], neuroni_da=segno)
        assert (c / "giornale").read_bytes() == prima and r.conteggi()["eventi"] == 2
