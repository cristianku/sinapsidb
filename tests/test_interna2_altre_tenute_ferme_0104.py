"""Secondo giro interno sulla 0.10.4: altre quattro correzioni che nessuna prova teneva ferme.

Tolte una alla volta, nessuna prova diventava rossa: la nota tenuta in memoria come si rilegge dal giornale
(``Rete._nota_scritta``), la prova rifiutata su una memoria guasta (in ``Rete.racconta``, sotto ``Memoria._viva``),
la conferma sul disco uscendo dai lotti (in ``Rete.sync`` e in ``Rete.scarica``), l'istantanea rifiutata da una rete
chiusa.
"""

import os

import pytest

from sinapsidb import motore
from sinapsidb.motore import Rete
from test_revisione_circuiti_090 import aperta


def test_la_nota_in_memoria_e_quella_del_giornale_non_l_oggetto_di_chi_chiama(tmp_path):
    c = tmp_path / "rete"
    with aperta(Rete(str(c))) as r:
        tipo, ruolo, ada = (r.n(s) for s in ("nota", "cosa", "Ada"))
        nota = {"chi": ("Ada", "Bea"), "passi": [1]}
        e = r.racconta("Prima", "test", [(tipo, [(ruolo, ada)])], nota=nota)
        nota["passi"].append(2)  # chi chiama riusa il suo oggetto: il giornale è già scritto
        nota["dopo"] = True
        in_memoria = r.e_nota[e]
        r.istantanea()
        assert r.e_nota[e] == {"chi": ["Ada", "Bea"], "passi": [1]}
    with aperta(Rete(str(c), sola_lettura=True)) as riaperta:
        assert not riaperta.istantanea_scartata
        dall_istantanea = riaperta.e_nota[e]
    os.remove(c / "istantanea")
    with aperta(Rete(str(c), sola_lettura=True)) as riletta:
        dal_giornale = riletta.e_nota[e]
    assert dal_giornale == {"chi": ["Ada", "Bea"], "passi": [1]}
    assert in_memoria == dal_giornale and dall_istantanea == dal_giornale


def test_una_rete_con_la_memoria_guasta_non_prova_niente(tmp_path):
    with aperta(Rete(str(tmp_path / "rete"))) as r:
        tipo, ruolo, ada = (r.n(s) for s in ("nota", "cosa", "Ada"))
        r.racconta("Prima", "test", [(tipo, [(ruolo, ada)])])
        assert r.racconta("E se?", "test", [(tipo, [(ruolo, ada)])], prova=True)["esito"] == "prova"  # finché è buona prova
        r._rotta, r._memoria_guasta = "un evento è nel giornale ma la memoria non è riuscita ad allinearsi", True
        with pytest.raises(RuntimeError, match="riapert"):  # deciderebbe su uno stato che non è quello del giornale
            r.racconta("E se?", "test", [(tipo, [(ruolo, ada)])], prova=True)


@pytest.mark.parametrize("come", ["a_lotti", "sync"])
def test_uscendo_dai_lotti_quello_che_era_in_attesa_viene_confermato_sul_disco(tmp_path, monkeypatch, come):
    c = tmp_path / "rete"
    with aperta(Rete(str(c))) as r:
        tipo, ruolo, ada = (r.n(s) for s in ("nota", "cosa", "Ada"))
        vera, confermati = motore.os.fdatasync, []
        monkeypatch.setattr(motore.os, "fdatasync", lambda fd: (confermati.append(fd), vera(fd))[1])
        if come == "a_lotti":
            with r.a_lotti():
                r.racconta("Prima", "test", [(tipo, [(ruolo, ada)])])
                assert not confermati and os.path.getsize(c / "giornale") == 0  # a lotti: ancora in attesa
        else:
            r.sync = "lotti"
            r.racconta("Prima", "test", [(tipo, [(ruolo, ada)])])
            assert not confermati and os.path.getsize(c / "giornale") == 0
            r.sync = "frase"
        # da qui ogni evento è confermato sul disco, anche quelli che erano in attesa
        assert os.path.getsize(c / "giornale") > 0
        assert confermati and set(confermati) == {r._fd}


def test_una_rete_chiusa_non_salva_istantanee(tmp_path):
    c = tmp_path / "rete"
    r = Rete(str(c))
    tipo, ruolo, ada = (r.n(s) for s in ("nota", "cosa", "Ada"))
    r.racconta("Prima", "test", [(tipo, [(ruolo, ada)])])
    r.chiudi()
    with pytest.raises(RuntimeError, match="chiusa"):
        r.istantanea()
    assert not (c / "istantanea").exists()


def test_il_peso_sul_disco_non_cade_se_un_file_sparisce_tra_l_elenco_e_la_misura(tmp_path, monkeypatch):
    """La prova che c'era (``test_lo_stato_non_cade_se_un_file_sparisce_mentre_lo_si_misura``) fa fallire ``Path.stat``
    sempre: ``is_file`` allora dice già di no, e anche la somma semplice di prima dà dieci. Qui il file c'è quando lo
    si guarda e non c'è più quando lo si misura, che è il caso per cui la funzione è stata scritta."""
    from pathlib import Path

    from sinapsidb.confronto import Banco

    cartella = tmp_path / "rete"
    cartella.mkdir()
    (cartella / "giornale").write_bytes(b"x" * 10)
    (cartella / "istantanea").write_bytes(b"y" * 7)
    assert Banco._peso_sul_disco(cartella) == 17
    vero_file, vera_misura = Path.is_file, Path.stat
    sparita = lambda p: p.name == "istantanea" and p.parent == cartella

    def misura(self, *a, **k):
        if sparita(self):
            raise FileNotFoundError(2, "No such file or directory", str(self))
        return vera_misura(self, *a, **k)

    monkeypatch.setattr(Path, "is_file", lambda self, *a, **k: True if sparita(self) else vero_file(self, *a, **k))
    monkeypatch.setattr(Path, "stat", misura)
    assert Banco._peso_sul_disco(cartella) == 10
