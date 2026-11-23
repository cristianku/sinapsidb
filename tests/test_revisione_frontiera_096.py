"""§9 su a7b4eab: distinguere quello che dichiara il writer da quello che la memoria ha il diritto di confermare."""

import errno
import json
import shutil
from pathlib import Path

import pytest

from sinapsidb import attesta, motore
from sinapsidb.memoria import Memoria
from sinapsidb.motore import Rete, SegnoGuasto
from test_revisione_circuiti_090 import aperta, evento


DATI = Path(__file__).parent / "dati"
MISTA = DATI / "storico-096-093-096"


def nota(m, nome):
    return m.racconta(f"Nota {nome}", [{"tipo": "nota", "legami": {"cosa": nome}}], chiave=nome)


def test_il_writer_095_poteva_usare_il_modo_4_senza_metterlo_nel_segno(tmp_path):
    c = shutil.copytree(DATI / "storico-10c5ab7-senza-tratto", tmp_path / "rete")
    prima = [(c / f).read_bytes() for f in ("giornale", "formato")]
    with aperta(Memoria(c)) as m:
        assert m.segno() == {"formato": 2, "dal": 0}
        d = m.rete.e_nota[3]["ricevuta"]["decisione"]
        assert "calcolo" not in d
        # B,B,C: tutte le entità una volta sola. Nessuno dei modi incrementali 1, 2 e 3 dà entrambe le letture.
        assert [(r["su"], [v["valore"] for v in r["letture"]]) for r in d["regole"]] == [("B", ["0", "10"]), ("C", ["0", "10"])]
        v = m.verifica(3)
        assert (v["esito"], v.get("calcoli_compatibili")) == ("coerente", [4]), v["differenze"]
        assert "calcolo" not in v  # lo sappiamo dal generatore, non dai byte del tratto
        x = m.riesamina(3)
        assert x["ipotetico"] == x["allora"] and x["cambia"] is False
    assert [(c / f).read_bytes() for f in ("giornale", "formato")] == prima


def test_un_ritorno_reale_alla_093_non_rende_guasto_il_record_senza_modo(tmp_path):
    c = shutil.copytree(MISTA, tmp_path / "rete")
    with aperta(Memoria(c)) as m:
        assert m.segno()["calcolo_dal"] == 0
        assert [rec[7]["ricevuta"]["decisione"].get("calcolo") for rec in Rete._record(str(c))] == [4, None, None, None, None, None, 4]
        assert m.verifica(0)["calcolo"] == m.verifica(6)["calcolo"] == 4
        v = m.verifica(4)
        assert v["esito"] == "coerente" and v["calcoli_compatibili"] == [4, 3, 2, 1] and "calcolo" not in v
        x = m.riesamina(4, [5])
        assert x["cambia"] is None and x["ipotetico"]["esito"] == "indeterminato"
        assert {tuple(a["calcoli"]): a["esito"] for a in x["ipotetico"]["alternative"]} == {
            (4, 3, 1): "applicato", (2,): "violazione_regola"}


def test_chi_conosce_l_intervallo_di_rollback_puo_attestarlo_anche_dopo_calcolo_dal(tmp_path):
    c = shutil.copytree(MISTA, tmp_path / "rete")
    prima = (c / "giornale").read_bytes()
    # Il generatore sa chi scrive il tratto [1, 6); il giornale da solo no. Uso il comando ``sinapsidb.attesta``.
    assert attesta.main([str(c), "2", "1", "6", "--fonte", "tests/dati/genera_scrittori_096.py: rollback alla 0.9.3"]) == 0
    with aperta(Memoria(c)) as m:
        v = m.verifica(4)
        x = m.riesamina(4, [5])
        assert (v["esito"], v.get("calcolo"), x["ipotetico"]["esito"], x["cambia"]) == (
            "coerente", 2, "violazione_regola", False)
        assert v["calcolo_da"] == x["calcolo_da"] == "attestazione"
        assert [(m.verifica(e)["calcolo"], m.verifica(e)["calcolo_da"]) for e in (0, 6)] == [(4, "ricevuta")] * 2
    assert (c / "giornale").read_bytes() == prima


@pytest.mark.parametrize("completa", [True, False], ids=["ricevuta-intera", "serializzatore-pre-096"])
def test_il_callback_non_puo_far_perdere_la_dichiarazione_del_writer_di_oggi(tmp_path, completa):
    c = tmp_path / "rete"
    with aperta(Memoria(c, scritture=True)) as m:
        r, n = m.rete, m.rete.n
        nota(m, "A")
        prima = (c / "giornale").read_bytes()
        e = m.conteggi()["eventi"]
        segno = len(r.nomi)

        def serializza(d):
            assert d["calcolo"] == 4
            campi = d if completa else ("formato", "base", "istante", "regole")
            return {"ricevuta": {"evento": f"evento:{e}", "esito": "applicato",
                                 "decisione": {k: d[k] for k in campi}}}

        try:
            r.racconta("Ricevuta via adattatore", "test", [(n("nota"), [(n("cosa"), n("B"))])],
                       neuroni_da=segno, nota_della_scrittura=serializza)
        except ValueError:  # lecito rifiutare il serializzatore incompleto, prima di dare il sì
            assert not completa and (c / "giornale").read_bytes() == prima and m.conteggi()["eventi"] == e and not r.trova("B")
        else:  # se il motore la accetta, la dichiarazione del writer deve essere durevole
            v = m.verifica(e)
            assert (v["esito"], v.get("calcolo")) == ("coerente", 4), v


def test_il_segno_diventato_illeggibile_ferma_anche_il_writer_gia_aperto(tmp_path):
    c = tmp_path / "rete"
    with aperta(Memoria(c, scritture=True)) as m:
        nota(m, "A")  # il writer ha già scritto la sua frontiera
        prima = (m.conteggi(), (c / "giornale").read_bytes())
        (c / "formato").write_bytes(b"{")
        assert m.verifica(0)["esito"] == "non_verificabile" and "guasto" in m.segno()
        with pytest.raises(SegnoGuasto):
            nota(m, "B")
        assert (m.conteggi(), (c / "giornale").read_bytes()) == prima
        assert (c / "formato").read_bytes() == b"{" and m.ricevuta_di("B") is None


def test_un_segno_certamente_oltre_la_fine_non_da_un_riesame_certo(tmp_path):
    c = shutil.copytree(DATI / "storico-092-093-indistinguibile", tmp_path / "rete")
    # Non è il caso ammesso di due giornali lunghi uguale: qui la fine reale è nota e non arriva a 50.
    (c / "formato").write_text(json.dumps({"formato": 2, "dal": 0, "calcoli": [[2, 0], [4, 50]]}))
    with aperta(Memoria(c, scritture=True)) as m:
        assert len(list(Rete._record(str(c)))) == 5
        with pytest.raises(SegnoGuasto):
            nota(m, "N")
        assert m.verifica(3)["esito"] == "non_verificabile"
        with pytest.raises(SegnoGuasto):
            m.riesamina(3, [4])


@pytest.mark.parametrize("guasto", ["pubblicazione-segno", "append-giornale"])
def test_un_errore_prima_del_record_non_alimenta_la_scrittura_successiva_o_l_istantanea(tmp_path, monkeypatch, guasto):
    c = tmp_path / "rete"
    b = None
    with aperta(Memoria(c, scritture=True)) as m:
        r = m.rete
        with monkeypatch.context() as mp:
            if guasto == "pubblicazione-segno":
                sostituisci = motore.os.replace

                def replace(src, dst):
                    if Path(dst) == c / "formato":
                        raise OSError(errno.EIO, "pubblicazione del segno fallita")
                    return sostituisci(src, dst)
                mp.setattr(motore.os, "replace", replace)
            else:
                scrivi = motore.os.write

                def write(fd, dati):
                    if fd == r._fd:
                        raise OSError(errno.ENOSPC, "nessun byte del record scritto")
                    return scrivi(fd, dati)
                mp.setattr(motore.os, "write", write)
            with pytest.raises(OSError):
                nota(m, "A")
        assert (c / "giornale").read_bytes() == b""
        try:
            b = nota(m, "B")
        except (RuntimeError, OSError):
            pass  # ammissibile mettere fuori uso l'istanza fino alla riapertura
        else:
            r.istantanea()  # una scrittura confermata deve essere leggibile anche da qui

    with aperta(Memoria(c, scritture=True)) as riaperta:
        if b is None:
            b = nota(riaperta, "B")
        assert b["esito"] == "applicato" and evento(b) == 0
        assert riaperta.ricevuta_di("A") is None and riaperta.ricevuta_di("B") == b
        assert riaperta.chiedi("in nota leggi cosa come è registrato")["righe"] == [("B",)]
        dal_giornale, dopo = Rete.com_era(str(c), 1)
        assert dopo is None and dal_giornale.conteggi() == riaperta.conteggi()
        assert riaperta.verifica(0)["esito"] == "coerente"


def test_un_append_corto_non_conferma_una_ricevuta_che_sparisce_al_riavvio(tmp_path, monkeypatch):
    c = tmp_path / "rete"
    with aperta(Memoria(c, scritture=True)) as m:
        r = m.rete
        scrivi = motore.os.write
        prima = True

        def corto(fd, dati):
            nonlocal prima
            if fd == r._fd and prima:
                prima = False
                return scrivi(fd, dati[:9])  # ritorno POSIX valido: non è detto che tutti i byte siano scritti
            return scrivi(fd, dati)

        with monkeypatch.context() as mp:
            mp.setattr(motore.os, "write", corto)
            try:
                confermata = nota(m, "A")
            except OSError:
                confermata = None  # l'altra risposta onesta è fermarsi senza confermare
    with aperta(Memoria(c)) as riaperta:
        if confermata is not None:
            assert confermata["esito"] == "applicato" and riaperta.ricevuta_di("A") == confermata
            assert riaperta.verifica(confermata["evento"])["esito"] == "coerente"
        else:
            assert riaperta.ricevuta_di("A") is None and not riaperta.conteggi()["eventi"]
