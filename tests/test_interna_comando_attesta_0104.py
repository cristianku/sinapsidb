"""Revisione interna della candidata 0.10.4 (contratto §9, «Il segno si confronta con il giornale vero»).

Un segno che parla di eventi che il giornale non ha non è il suo: chi verifica risponde «non verificabile» invece di
usarlo. Il comando ``python -m sinapsidb.attesta <cartella>``, che mostra da dove viene il modo di ogni decisione, lo
usa lo stesso: esce con 0 e attribuisce un tratto all'attestazione di quel segno.
"""

import json
import shutil
from pathlib import Path

import pytest

from sinapsidb import attesta
from sinapsidb.memoria import Memoria
from sinapsidb.motore import Rete, SegnoGuasto
from test_revisione_circuiti_090 import aperta


MISTA = Path(__file__).parent / "dati" / "storico-096-093-096"  # sette eventi


def test_il_comando_non_attribuisce_niente_con_un_segno_che_parla_di_eventi_inesistenti(tmp_path, capsys):
    c = shutil.copytree(MISTA, tmp_path / "rete")
    Rete.attesta(str(c), 2, 1, 6, "registro degli aggiornamenti")
    file = c / "formato"
    file.write_text(json.dumps({**json.loads(file.read_bytes()), "dichiarano_da": [50]}))  # il giornale ha 7 eventi
    prima = file.read_bytes(), (c / "giornale").read_bytes()
    with aperta(Memoria(c)) as m:  # chi verifica non lo usa, e lo dice
        assert "guasto" in m.segno() and m.verifica(4)["esito"] == "non_verificabile"
        with pytest.raises(SegnoGuasto):
            m.riesamina(4, [5])
    capsys.readouterr()
    esito = attesta.main([str(c)])
    visto = capsys.readouterr()
    assert (file.read_bytes(), (c / "giornale").read_bytes()) == prima
    # Lo stesso segno, lo stesso giornale: o il comando si ferma e lo dice, o non attribuisce niente con quel segno.
    attribuiti = [t for t in json.loads(visto.out)["tratti"] if t.get("da_dove") == "attestazione"] if esito == 0 else []
    assert esito != 0 or not attribuiti, f"il comando esce con {esito} e attribuisce {attribuiti}"
