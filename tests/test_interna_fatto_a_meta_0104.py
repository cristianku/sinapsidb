"""Un fatto che fallisce mentre nasce (dentro ``Rete._fatto``) non è ancora tra i «creati»: l'annullamento non lo vede.

La colonna ``f_n`` conta i legami in un byte con segno: un fatto con 128 partecipanti la fa traboccare dopo che le
prime due colonne hanno già la loro riga. La scrittura fallisce, ed è lecito; ma le colonne dei fatti restano sfalsate,
la rete non si dichiara fuori uso, continua a scrivere, e un'istantanea porta lo sfalsamento oltre la riapertura."""

import os

import pytest

from sinapsidb.motore import Rete
from test_revisione_circuiti_090 import aperta


def colonne(r):
    return {k: len(getattr(r, k)) for k in ("f_tipo", "f_inizio", "f_n", "f_nato", "f_morto", "s_ruolo", "s_a")}


def fatti(r):
    return [r.in_parole(f) for f in range(len(r.f_tipo))]


def nota(r, nome):
    n = r.n
    return r.racconta(f"Nota {nome}", "test", [(n("nota"), [(n("cosa"), n(nome))])])


def larga(r, percorso):
    n = r.n
    tipo, legami = n("scheda"), [(n(f"campo {i}"), n(f"valore {i}")) for i in range(128)]
    if percorso == "racconta":
        r.racconta("Scheda larga", "test", [(tipo, legami)])
    else:
        r.racconta_molti("test", [("Scheda larga", tipo, legami)])


@pytest.mark.parametrize("percorso", ["racconta", "racconta_molti"])
def test_una_scrittura_fallita_mentre_il_fatto_nasce_non_lascia_mezzo_fatto(tmp_path, percorso):
    c = tmp_path / "rete"
    with aperta(Rete(str(c))) as r:
        nota(r, "A")
        prima = colonne(r), r.conteggi()["fatti"], r.conteggi()["eventi"]
        try:
            larga(r, percorso)
        except (OverflowError, ValueError):
            pass  # lecito rifiutare un fatto con più partecipanti di quanti la colonna ne sa contare
        else:
            return  # oppure scriverlo: allora non c'è niente da annullare
        if r.fuori_uso:
            return  # lecito anche fermarsi e chiedere la riapertura
        assert (colonne(r), r.conteggi()["fatti"], r.conteggi()["eventi"]) == prima, "la scrittura non è avvenuta: la rete deve essere com'era"


@pytest.mark.parametrize("percorso", ["racconta", "racconta_molti"])
def test_dopo_il_fallimento_memoria_istantanea_e_giornale_dicono_gli_stessi_fatti(tmp_path, percorso):
    c = tmp_path / "rete"
    with aperta(Rete(str(c))) as r:
        nota(r, "A")
        with pytest.raises((OverflowError, ValueError)):
            larga(r, percorso)
        if r.fuori_uso:
            return
        assert nota(r, "B") == 1  # confermata a chi chiama
        r.istantanea()
    dal_giornale, _ = Rete.com_era(str(c), 2)
    attesi = fatti(dal_giornale)
    assert attesi == ["nota (cosa: A)", "nota (cosa: B)"]
    with aperta(Rete(str(c))) as riaperta:  # riparte dall'istantanea
        assert not riaperta.istantanea_scartata
        assert riaperta.conteggi()["fatti"] == len(attesi) and fatti(riaperta) == attesi
    os.remove(c / "istantanea")
    with aperta(Rete(str(c))) as riletta:
        assert fatti(riletta) == attesi
