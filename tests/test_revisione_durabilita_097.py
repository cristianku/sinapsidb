"""Un solo scrittore: confrontare quello che vede chi ha ricevuto la risposta con il giornale e con la riapertura."""

import errno
import json
import os
from concurrent.futures import ThreadPoolExecutor, wait
from pathlib import Path

import pytest

from sinapsidb import motore
from sinapsidb.memoria import Memoria
from sinapsidb.motore import Vietato
from test_decisioni import _record
from test_revisione_circuiti_090 import aperta, circuito, evento, vendita
from test_segno_e_disco import fido, guasta, nota


MENSILE = "circuito vendite mensili"
DAI_FATTI = "in vendita somma totale per mese(quando)"


def test_aprire_un_lettore_durante_un_append_corto_non_perde_la_conferma(tmp_path, monkeypatch):
    c = tmp_path / "rete"

    def quanti():
        with aperta(Memoria(c, scritture=False)) as lettore:
            return lettore.conteggi()["eventi"]

    with ThreadPoolExecutor(max_workers=1) as lettori:
        with aperta(Memoria(c, scritture=True)) as m:
            a = nota(m, "A")
            scrivi = motore.os.write
            osservazione = None

            def corto(fd, dati):
                nonlocal osservazione
                if fd != m.rete._fd or osservazione is not None:
                    return scrivi(fd, dati)
                quanti_byte = scrivi(fd, dati[:9])
                osservazione = lettori.submit(quanti)  # A intero, di B soltanto la testa e un byte
                wait([osservazione], timeout=2)  # lecito che il lettore aspetti il commit: non blocchiamo lo scrittore
                return quanti_byte

            with monkeypatch.context() as mp:
                mp.setattr(motore.os, "write", corto)
                b = nota(m, "B")
            assert b["esito"] == "applicato" and m.ricevuta_di("B") == b
        assert osservazione.result() in (1, 2)
    with aperta(Memoria(c, scritture=True)) as m:
        assert (m.ricevuta_di("A"), m.ricevuta_di("B")) == (a, b)
        assert m.verifica(b["evento"])["esito"] == "coerente"
        assert nota(m, "B") == {**b, "ripetuta": True} and m.conteggi()["eventi"] == 2


def test_sync_e_taglio_falliti_non_danno_un_falso_assente_per_chiave(tmp_path, monkeypatch):
    c = tmp_path / "rete"
    with aperta(Memoria(c, scritture=True)) as m:
        nota(m, "A")
        with monkeypatch.context() as mp:
            guasta(mp, m.rete, sincronizza=1, tronca=True)  # arriva tutto il corpo; il sync e il taglio no
            with pytest.raises(OSError):
                nota(m, "B")
        incerta = False
        try:
            prima = m.ricevuta_di("B")  # lecito esigere la riapertura per risolvere la chiave
        except (RuntimeError, OSError):
            incerta = True
    with aperta(Memoria(c, scritture=True)) as m:
        b = m.ricevuta_di("B")
        if not incerta:
            assert prima == b  # «assente» prima, ma ricevuta intera dopo: la prima risposta non era certa
        if b is None:  # lecito anche un recupero che annulla davvero il record non confermato
            b = nota(m, "B")
        assert b["evento"] == "evento:1" and m.verifica(1)["esito"] == "coerente" and nota(m, "B") == {**b, "ripetuta": True}


def test_dopo_un_errore_nel_sync_della_cartella_il_nome_del_segno_non_e_ancora_durevole(tmp_path, monkeypatch):
    c = tmp_path / "rete"
    with aperta(Memoria(c, scritture=True)) as m:
        sincronizza = os.fsync
        cartella = c.stat()
        tentativi = []

        def fsync(fd):
            if os.path.samestat(cartella, os.fstat(fd)):
                tentativi.append(1)
                if len(tentativi) == 1:
                    raise OSError(errno.EIO, "il nome nuovo del segno non è sincronizzato")
            return sincronizza(fd)

        with monkeypatch.context() as mp:
            mp.setattr(motore.os, "fsync", fsync)
            with pytest.raises(OSError):
                nota(m, "A")
            assert (c / "formato").exists() and not (c / "giornale").read_bytes()
            try:
                b = nota(m, "B")  # il rename era visibile; solo un sync riuscito ne conferma la durabilità
            except (RuntimeError, OSError):
                assert m.ricevuta_di("B") is None and not (c / "giornale").read_bytes()
            else:
                assert b["esito"] == "applicato" and len(tentativi) >= 2


@pytest.mark.parametrize("dove", ["segno", "append", "sync", "lettura"])
def test_il_rollback_di_una_sola_richiesta_con_dati_ritiro_e_definizione(tmp_path, monkeypatch, dove):
    c = tmp_path / "rete"
    fatti = [{"tipo": "termine", "legami": {"termine di": "vendite mensili", "osserva": "vendita", "lega": "quando",
                                            "somma": "totale", "peso": "2", "raggruppa per": "mese"}},
             {"tipo": "vendita", "legami": {"cliente": "C", "quando": "2027-04-01", "totale": "5"}}]
    ritiri = [{"tipo": "termine", "indizi": {"termine di": "vendite mensili"}}]

    def richiesta(m):
        return m.racconta("Raddoppia il mensile e registra aprile", fatti, sostituisce=ritiri, chiave="insieme")

    with aperta(Memoria(c, scritture=True)) as m:
        circuito(m.rete)
        vendita(m, "2027-03-01", "10")
        if dove == "segno":
            # Fa servire una nuova frontiera prima della prossima scrittura.
            file = c / "formato"
            segno = json.loads(file.read_bytes())
            segno["calcoli"].append([2, 2])
            file.write_text(json.dumps(segno))
        prima, nomi, giornale = m.conteggi(), list(m.rete.nomi), (c / "giornale").read_bytes()
        with monkeypatch.context() as mp:
            if dove == "segno":
                sostituisci = motore.os.replace

                def replace(src, dst):
                    if Path(dst) == file:
                        raise OSError(errno.EIO, "pubblicazione del segno fallita")
                    return sostituisci(src, dst)

                mp.setattr(motore.os, "replace", replace)
            elif dove == "lettura":
                def senza_somma(*args, **kwargs):
                    raise OSError(errno.EIO, "non si riesce a leggere la somma per la definizione proposta")

                mp.setattr(m.rete, "_somme", senza_somma)
            else:
                guasta(mp, m.rete, scrivi=9 if dove == "append" else None, sincronizza=int(dove == "sync"))
            with pytest.raises(OSError):
                richiesta(m)
        assert m.ricevuta_di("insieme") is None and (c / "giornale").read_bytes() == giornale
        assert (m.conteggi(), m.rete.nomi) == (prima, nomi)
        assert m.chiedi(MENSILE)["righe"] == m.chiedi(DAI_FATTI)["righe"] == [("2027-03", "10")]
        assert m.verifica_circuiti()["esito"] == "coerente"
        # Sulla stessa istanza, con gli stessi numeri: questa volta scrive davvero e aggiorna entrambi i mesi.
        insieme = richiesta(m)
        assert insieme["esito"] == "applicato" and evento(insieme) == 2
        m.rete.istantanea()
    with aperta(Memoria(c)) as m:
        assert m.ricevuta_di("insieme") == insieme and m.verifica(2)["esito"] == "coerente"
        assert m.chiedi(MENSILE)["righe"] == [("2027-03", "20"), ("2027-04", "10")]
        assert m.verifica_circuiti()["esito"] == "coerente"


def test_un_ritiro_gia_registrato_non_si_annulla_con_il_rollback_di_un_altro_evento(tmp_path, monkeypatch):
    c = tmp_path / "rete"
    with aperta(Memoria(c, scritture=True)) as m:
        r, n = m.rete, m.rete.n
        circuito(r)
        vendita(m, "2027-01-01", "50")
        vendita(m, "2027-01-02", "30")
        f, = r.scatta(n("vendita"), [(n("quando"), n("2027-01-01"))])
        r.racconta("Storno dei 50", "test", ritira=[f])
        prima = (c / "giornale").read_bytes()
        assert m.chiedi(MENSILE)["righe"] == [("2027-01", "30")]
        segno = len(r.nomi)
        proposta = [(n("vendita"), [(n("quando"), n("2027-05-04")), (n("totale"), n("5"))])]
        with monkeypatch.context() as mp:
            guasta(mp, r, scrivi=9)
            with pytest.raises((ValueError, OSError)):  # lecito rifiutare lo storno già fatto prima dell'append
                r.racconta("Ripresenta lo storno e registra maggio", "test", proposta, ritira=[f], neuroni_da=segno)
        assert (c / "giornale").read_bytes() == prima and not r.trova("2027-05")
        assert (m.chiedi(MENSILE)["righe"], m.chiedi(DAI_FATTI)["righe"]) == ([("2027-01", "30")], [("2027-01", "30")])
        assert m.verifica_circuiti()["esito"] == "coerente"
        r.istantanea()
    with aperta(Memoria(c)) as m:
        assert m.chiedi(DAI_FATTI)["righe"] == [("2027-01", "30")] and m.verifica_circuiti()["esito"] == "coerente"


@pytest.mark.parametrize("rifiuto,difetto", [(False, "senza-decisione"), (True, "senza-decisione"), (False, "base-errata")],
                         ids=["si-senza-decisione", "no-senza-decisione", "si-con-base-errata"])
def test_il_callback_non_puo_scrivere_un_evento_inverificabile(tmp_path, rifiuto, difetto):
    c = tmp_path / "rete"
    with aperta(Memoria(c, scritture=True)) as m:
        r, n = m.rete, m.rete.n
        fido(m)
        e, segno, prima = m.conteggi()["eventi"], len(r.nomi), (c / "giornale").read_bytes()
        esito = "violazione_regola" if rifiuto else "applicato"

        def incompleta(*args):
            ricevuta = {"evento": f"evento:{e}", "esito": esito}
            if difetto == "base-errata":
                ricevuta["decisione"] = {**args[-1], "base": e}
            return {"ricevuta": ricevuta}

        fatti = [(n("ordine" if rifiuto else "accredito"), [(n("cliente"), n("A")), (n("importo"), n("60"))])]
        try:
            r.racconta("Movimento", "test", fatti, neuroni_da=segno, nota_della_scrittura=incompleta, nota_del_rifiuto=incompleta)
        except ValueError:  # l'adattatore non sa portare la decisione: errore senza evento
            assert (c / "giornale").read_bytes() == prima and m.conteggi()["eventi"] == e and not r.trova("A")
            return
        except Vietato:
            assert rifiuto
        with aperta(Memoria(c)) as lettore:  # se accetta la nota deve completare la ricevuta, del sì come del no
            v = lettore.verifica(e)
            assert (v["esito"], v.get("calcolo_da")) == ("coerente", "ricevuta"), v["differenze"]
            assert lettore.riesamina(e)["allora"]["esito"] == esito


def test_un_istantanea_durante_un_callback_fallito_non_pubblica_la_proposta(tmp_path):
    c = tmp_path / "rete"
    with aperta(Memoria(c, scritture=True)) as m:
        r, n = m.rete, m.rete.n
        circuito(r)
        vendita(m, "2027-01-01", "10")
        prima = (c / "giornale").read_bytes()
        r.istantanea()

        def prepara_nota(decisione):
            r.istantanea()  # anche rifiutare qui l'istantanea è corretto
            raise ValueError("il serializzatore si è fermato")

        fatti = [(n("vendita"), [(n("cliente"), n("C")), (n("quando"), n("2027-01-01")), (n("totale"), n("10"))])]
        with pytest.raises((ValueError, RuntimeError)):
            r.racconta("Seconda vendita", "test", fatti, nota_della_scrittura=prepara_nota)
        assert (c / "giornale").read_bytes() == prima and m.chiedi(MENSILE)["righe"] == [("2027-01", "10")]
    with aperta(Memoria(c)) as m:
        assert m.conteggi()["eventi"] == 2 and m.chiedi(MENSILE)["righe"] == [("2027-01", "10")]
        assert m.verifica_circuiti()["esito"] == "coerente"


@pytest.mark.parametrize("da_dove", ["racconta", "ricevuta_di", "ripetuta"])
def test_la_copia_consegnata_non_riscrive_la_ricevuta_per_chiave(tmp_path, da_dove):
    c = tmp_path / "rete"
    with aperta(Memoria(c, scritture=True)) as m:
        risposta = nota(m, "A")
        originale = _record(c)[0][7]["ricevuta"]
        if da_dove == "ricevuta_di":
            risposta = m.ricevuta_di("A")
        elif da_dove == "ripetuta":
            risposta = nota(m, "A")
        risposta["decisione"]["calcolo"] = 99  # chi usa il dict ricevuto lo arricchisce o lo cambia
        in_ram = m.ricevuta_di("A")
        ripetuta = nota(m, "A")
        m.rete.istantanea()
    with aperta(Memoria(c)) as m:
        assert m.verifica(0)["calcolo"] == originale["decisione"]["calcolo"] == 4
        assert in_ram == m.ricevuta_di("A") == originale
        assert ripetuta == {**originale, "ripetuta": True}
