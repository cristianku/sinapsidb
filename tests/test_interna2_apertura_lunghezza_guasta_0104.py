"""Secondo giro di revisione interna della candidata 0.10.4, lente «apertura»: la lunghezza di un record guastata.

Il contratto (paragrafo 9) dice: «Un record intero che non si rilegge, o uno seguito da altri, è un guasto: il giornale
non si apre, né per scrivere né per leggere». La candidata lo riconosce se il bit cambiato è nel corpo o nel controllo
del record. Se è nella sua LUNGHEZZA, e la fa arrivare oltre la fine del file, ``Rete._rileggi_il_giornale`` prende il
record per una coda scritta a metà: non guarda che dopo di lui, ai loro posti, ci sono altri record interi e buoni.
Chi legge vede due eventi su sei e non lo dice; chi apre per scrivere taglia le quattro scritture confermate.
"""

import struct

import pytest

from sinapsidb import motore
from sinapsidb.memoria import Memoria
from sinapsidb.motore import GiornaleGuasto
from test_revisione_circuiti_090 import aperta


def nota(m, nome):
    return m.racconta(f"Nota {nome}", [{"tipo": "nota", "legami": {"cosa": nome}}], chiave=nome)


def sei_scritture(c):
    with aperta(Memoria(c, scritture=True)) as m:
        for nome in "abcdef":
            assert nota(m, nome)["esito"] == "applicato"
    return (c / "giornale").read_bytes()


def record(dati):
    """Dove comincia e dove finisce ogni record del giornale."""
    pos, confini = 0, []
    while pos + 8 <= len(dati):
        lunghezza = struct.unpack_from("<I", dati, pos)[0]
        confini.append((pos, pos + 8 + lunghezza))
        pos += 8 + lunghezza
    return confini


@pytest.mark.parametrize("scritture", [True, False], ids=["chi-scrive", "chi-legge"])
@pytest.mark.parametrize("bit", [16, 24, 31])
@pytest.mark.parametrize("pezzo", [None, 37], ids=["pezzo-intero", "a-pezzi-di-37"])
def test_un_bit_nella_lunghezza_di_un_record_in_mezzo_non_ne_fa_una_coda(tmp_path, monkeypatch, scritture, bit, pezzo):
    c = tmp_path / "rete"
    intero = sei_scritture(c)
    inizio, _ = record(intero)[2]
    guasto = bytearray(intero)
    # un bit cambiato nella lunghezza del terzo record: dopo di lui ce ne sono altri tre, interi, buoni e confermati
    struct.pack_into("<I", guasto, inizio, struct.unpack_from("<I", guasto, inizio)[0] ^ (1 << bit))
    assert guasto[inizio + 4:] == intero[inizio + 4:] and len(record(intero)) == 6
    (c / "giornale").write_bytes(guasto)
    if pezzo:
        monkeypatch.setattr(motore, "PEZZO_DI_GIORNALE", pezzo)
    try:
        m = Memoria(c, scritture=scritture)
    except GiornaleGuasto:
        pass  # è quello che il contratto promette
    else:
        with aperta(m):
            visti, ultima = m.conteggi()["eventi"], m.ricevuta_di("f")
        rimasti = len((c / "giornale").read_bytes())
        pytest.fail(f"il giornale guasto nel mezzo si è aperto: {visti} eventi su 6, la scrittura confermata «f» "
                    f"{'c’è' if ultima else 'non c’è più'}, e il file è lungo {rimasti} byte su {len(guasto)}")
    assert (c / "giornale").read_bytes() == bytes(guasto)  # nessuno ha tagliato niente
    (c / "giornale").write_bytes(intero)  # ripristinato da una copia, si riapre
    with aperta(Memoria(c, scritture=scritture)) as m:
        assert m.conteggi()["eventi"] == 6 and m.ricevuta_di("f")["esito"] == "applicato"
