"""Controesempi sulle istantanee: il file dell'istantanea non porta una prova di sé. Se un byte cambia viene usato lo
stesso, e la rete riaperta non è quella del giornale; se è troncato la memoria non si apre più, né per leggere né per
scrivere, con il giornale intatto accanto.

Il giornale ha un controllo per ogni record e il segno si convalida a ogni lettura: l'istantanea è facoltativa, e
quando non si può provare che vale si rilegge il giornale (``istantanea_scartata``).
"""

import pytest

from sinapsidb.memoria import Memoria
from sinapsidb.motore import Rete
from test_revisione_circuiti_090 import aperta
from test_segno_e_disco import fido, movimento


def _con_istantanea(c):
    with aperta(Memoria(c, scritture=True)) as m:
        fido(m)
        ricevuta = movimento(m, "accredito", "Anna", "100", "credito")
        m.rete.istantanea()
    return ricevuta, (c / "istantanea").read_bytes()


def _com_e(m):
    r = m.rete
    return (r.conteggi(), list(r.nomi), list(r.e_frase), m.valori("fido"), m.ricevuta_di("credito"))


def test_un_istantanea_con_un_byte_cambiato_non_si_usa(tmp_path):
    c = tmp_path / "rete"
    ricevuta, buona = _con_istantanea(c)
    dove = buona.index(b"Anna")
    (c / "istantanea").write_bytes(buona[:dove] + b"Arna" + buona[dove + 4:])  # stessa lunghezza, un byte diverso
    giornale = (c / "giornale").read_bytes()
    with aperta(Memoria(c)) as m:  # come per le istantanee di prima: se non prova di valere non si usa, si rilegge il giornale
        dalla_foto = _com_e(m)
    assert (c / "giornale").read_bytes() == giornale
    (c / "istantanea").unlink()
    with aperta(Memoria(c)) as m:
        dal_giornale = _com_e(m)
    assert dal_giornale[3] == [{"di": "Anna", "di_id": dal_giornale[3][0]["di_id"], "valore": "100"}] and dal_giornale[4] == ricevuta
    assert dalla_foto == dal_giornale


@pytest.mark.parametrize("quanto", ["meta", "meno-un-byte", "vuota"])
@pytest.mark.parametrize("scritture", [False, True], ids=["lettore", "scrittore"])
def test_un_istantanea_troncata_non_impedisce_di_aprire_il_giornale(tmp_path, quanto, scritture):
    c = tmp_path / "rete"
    ricevuta, buona = _con_istantanea(c)
    (c / "istantanea").write_bytes(buona[:{"meta": len(buona) // 2, "meno-un-byte": len(buona) - 1, "vuota": 0}[quanto]])
    eventi = len(list(Rete._record(str(c))))
    with aperta(Memoria(c, scritture=scritture)) as m:  # il giornale è intatto: è lui la verità
        assert m.conteggi()["eventi"] == eventi and m.ricevuta_di("credito") == ricevuta
        assert m.valore("fido", "Anna")["valore"] == "100"
