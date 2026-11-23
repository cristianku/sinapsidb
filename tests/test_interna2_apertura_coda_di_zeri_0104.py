"""Secondo giro di revisione interna della candidata 0.10.4, lente «apertura»: la coda di zeri.

``Rete._rileggi_il_giornale`` dice: «dello spazio mai scritto, tutto a zero, è una coda come un'altra», e la prova del
primo giro (``test_una_coda_scritta_a_meta_la_taglia_chi_apre_per_scrivere``, caso «spazio-mai-scritto») la mette
accanto alla testa a metà e al corpo a metà: chi legge apre e non tocca niente, chi scrive la taglia. Ma vale solo per
l'apertura, e solo se gli zeri stanno tutti nello stesso pezzo di lettura:

- otto byte a zero sono un record di lunghezza zero con il controllo giusto (``crc32(b"") == 0``). ``Rete._rileggi`` ci
  si ferma, gli altri che camminano sul giornale no: ``Rete.com_era`` e ``Rete._record`` ci chiamano ``json.loads`` e
  sollevano, ``Rete.eventi_nel_giornale`` e ``Memoria._eventi_nel_giornale`` li contano come eventi. A chi ha aperto in
  lettura un giornale con quella coda, ``verifica_circuiti`` e ``riesamina`` sollevano ``JSONDecodeError``;
- letto a pezzi, il giornale con quella coda è «guasto»: appena un pezzo comincia con otto zeri e il file continua,
  ``_rileggi_il_giornale`` esce dal giro e trova «dell'altro» dopo. Con il pezzo di serie servono più di 64 MB di zeri.
"""

import pytest

from sinapsidb import motore
from sinapsidb.memoria import Memoria
from sinapsidb.motore import Rete
from test_revisione_circuiti_090 import aperta
from test_segno_e_disco import fido, movimento

CODE = {"testa-a-meta": b"\x10\x00\x00", "corpo-a-meta": b"\x40\x00\x00\x00\x12\x34\x56\x78[\"mezza", "spazio-mai-scritto": b"\x00" * 300}


def un_fido(c):
    """Cinque eventi: il circuito, la regola, un accredito, un ordine e un accredito arrivato dopo l'ordine."""
    with aperta(Memoria(c, scritture=True)) as m:
        fido(m)
        assert movimento(m, "accredito", "Rossi", "100", "a1")["evento"] == "evento:2"
        assert movimento(m, "ordine", "Rossi", "60", "o1")["evento"] == "evento:3"
        assert movimento(m, "accredito", "Rossi", "50", "a2")["evento"] == "evento:4"
    return (c / "giornale").read_bytes()


def quello_che_dice(c):
    """Quello che una memoria aperta in lettura dice di questo giornale, senza toccarlo."""
    with aperta(Memoria(c)) as m:
        return {"eventi": m.conteggi()["eventi"],
                "nel_giornale": m.rete.eventi_nel_giornale(),
                "contati_dalla_memoria": m._eventi_nel_giornale(10 ** 9),
                "verifica": [m.verifica(e)["esito"] for e in range(5)],
                "circuiti": m.verifica_circuiti(),
                "riesame": m.riesamina(3, [4]),
                "com_era": Rete.com_era(str(c), 5)[1]}


@pytest.mark.parametrize("coda", list(CODE))
def test_chi_legge_un_giornale_con_la_coda_a_meta_risponde_come_senza(tmp_path, coda):
    c = tmp_path / "rete"
    intero = un_fido(c)
    senza = quello_che_dice(c)
    assert senza["eventi"] == senza["nel_giornale"] == senza["contati_dalla_memoria"] == 5 and senza["circuiti"]["esito"] == "coerente"
    (c / "giornale").write_bytes(intero + CODE[coda])
    assert quello_che_dice(c) == senza  # la coda è una scrittura mai confermata: non cambia nessuna risposta
    assert (c / "giornale").read_bytes() == intero + CODE[coda]  # e chi legge non l'ha toccata


@pytest.mark.parametrize("scritture", [False, True], ids=["chi-legge", "chi-scrive"])
@pytest.mark.parametrize("pezzo", [1, 8, 37])
@pytest.mark.parametrize("coda", list(CODE))
def test_la_coda_a_meta_resta_una_coda_anche_leggendo_a_pezzi(tmp_path, monkeypatch, coda, pezzo, scritture):
    c = tmp_path / "rete"
    intero = un_fido(c)
    (c / "giornale").write_bytes(intero + CODE[coda])
    monkeypatch.setattr(motore, "PEZZO_DI_GIORNALE", pezzo)
    with aperta(Memoria(c, scritture=scritture)) as m:  # con il pezzo di serie si apre: è la prova del primo giro
        assert m.conteggi()["eventi"] == 5
    assert (c / "giornale").read_bytes() == (intero if scritture else intero + CODE[coda])
