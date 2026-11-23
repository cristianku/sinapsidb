"""§§6, 8 e 9 su e854471: la fonte riguarda un tratto preciso del giornale che si sta verificando."""

import json
import shutil

import pytest

from sinapsidb import attesta
from sinapsidb.memoria import Memoria
from sinapsidb.motore import Rete, SegnoGuasto
from test_decisioni import _record, _riscrivi
from test_revisione_circuiti_090 import aperta
from test_revisione_frontiera_096 import MISTA


def test_a_parita_di_evento_la_frontiera_del_dichiarante_non_attribuisce_un_no_senza_modo(tmp_path):
    c = shutil.copytree(MISTA, tmp_path / "rete")
    file = c / "formato"
    file.write_text(json.dumps({**json.loads(file.read_bytes()),
                                "calcoli": [[4, 0], [2, 4], [99, 6]], "dichiarano_da": [4, 6]}))
    prima = (c / "giornale").read_bytes()
    with aperta(Memoria(c)) as m:
        # L'evento 4 è un vero no della 0.9.3, ma non è la frontiera a dimostrarlo.
        v = m.verifica(4)
        x = m.riesamina(4, [5])
        assert "calcolo" not in v and v["calcoli_compatibili"] == [4, 3, 2, 1]
        assert x["cambia"] is None and x["ipotetico"]["esito"] == "indeterminato"
        # La ricevuta 6, invece, parla da sé: nemmeno la riga «99 da 6» può renderla non supportata.
        v = m.verifica(6)
        assert (v["esito"], v["calcolo"], v["calcolo_da"]) == ("coerente", 4, "ricevuta")
    assert attesta.main([str(c), "2", "4", "5", "--fonte", "generatore della storia mista, evento 4"]) == 0
    with aperta(Memoria(c)) as m:
        x = m.riesamina(4, [5])
        assert (x["calcolo_da"], x["calcolo"], x["ipotetico"]["esito"], x["cambia"]) == (
            "attestazione", 2, "violazione_regola", False)
        assert "calcolo" not in m.verifica(5)  # la fine del tratto è esclusa
    assert (c / "giornale").read_bytes() == prima


def test_la_stessa_memoria_ricontrolla_il_prefisso_dopo_uno_scambio_di_copie(tmp_path):
    c = shutil.copytree(MISTA, tmp_path / "rete")
    altro = shutil.copytree(c, tmp_path / "altra-copia")
    record = _record(altro)
    record[0][0] = "avanti"  # stessa lunghezza di «inizio», diverso contenuto, CRC valido
    _riscrivi(altro, record)
    assert (c / "giornale").stat().st_size == (altro / "giornale").stat().st_size
    Rete.attesta(str(c), 2, 1, 6, "registro della prima copia")
    with aperta(Memoria(c)) as calda:
        assert calda.verifica(4)["calcolo_da"] == "attestazione"
        (altro / "giornale").replace(c / "giornale")
        with aperta(Memoria(c)) as fredda:
            assert fredda.verifica(4)["esito"] == "non_verificabile"
        assert calda.verifica(4)["esito"] == "non_verificabile"
        with pytest.raises(SegnoGuasto):
            calda.riesamina(4, [5])


def test_una_memoria_gia_aperta_non_scambia_la_sua_vecchia_testa_per_la_lunghezza_reale(tmp_path):
    c = shutil.copytree(MISTA, tmp_path / "rete")
    corta = shutil.copytree(c, tmp_path / "copia-corta")
    _riscrivi(corta, _record(corta)[:6])
    file = c / "formato"
    file.write_text(json.dumps({**json.loads(file.read_bytes()), "dichiarano_da": [7]}))
    with aperta(Memoria(c)) as calda:
        assert calda.conteggi()["eventi"] == 7 and calda.verifica(0)["esito"] == "coerente"
        (corta / "giornale").replace(c / "giornale")
        with aperta(Memoria(c)) as fredda:
            assert fredda.verifica(0)["esito"] == "non_verificabile"
        # Gli eventi 0, 4 e 5 ci sono ancora. Il difetto è del segno: la frontiera 7 non può stare dopo soli 6 eventi.
        assert calda.verifica(0)["esito"] == "non_verificabile" and "guasto" in calda.segno()
        with pytest.raises(SegnoGuasto):
            calda.riesamina(4, [5])


def test_l_impronta_senza_la_fonte_non_testimonia_chi_ha_scritto(tmp_path):
    c = shutil.copytree(MISTA, tmp_path / "rete")
    Rete.attesta(str(c), 2, 1, 6, "registro aggiornamenti")
    file = c / "formato"
    segno = json.loads(file.read_bytes())
    del segno["attestati"][0]["fonte"]  # tratto, byte, impronta e data sono rimasti veri
    file.write_text(json.dumps(segno))
    with aperta(Memoria(c)) as m:
        v = m.verifica(4)
        if v["esito"] == "non_verificabile":  # segno incompleto, oppure nessuna attribuzione
            with pytest.raises(SegnoGuasto):
                m.riesamina(4, [5])
        else:
            assert "calcolo" not in v and v["calcoli_compatibili"] == [4, 3, 2, 1]
            x = m.riesamina(4, [5])
            assert x["cambia"] is None and x["ipotetico"]["esito"] == "indeterminato"


def test_il_comando_non_aggiunge_un_attestazione_se_il_segno_parla_di_eventi_inesistenti(tmp_path, capsys):
    c = shutil.copytree(MISTA, tmp_path / "rete")
    file = c / "formato"
    file.write_text(json.dumps({**json.loads(file.read_bytes()), "dichiarano_da": [50]}))
    prima = file.read_bytes(), (c / "giornale").read_bytes()
    assert len(_record(c)) == 7
    with aperta(Memoria(c)) as m:
        assert "guasto" in m.segno() and m.verifica(4)["esito"] == "non_verificabile"
    assert attesta.main([str(c), "2", "1", "6", "--fonte", "registro aggiornamenti"]) != 0
    assert capsys.readouterr().err and (file.read_bytes(), (c / "giornale").read_bytes()) == prima
