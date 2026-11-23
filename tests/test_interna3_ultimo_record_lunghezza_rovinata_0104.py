"""Terzo giro di revisione interna della candidata 0.10.4, lente «mutazioni»: una protezione che nessuna
prova tiene ferma.

Contratto, paragrafo 9: «Alla lunghezza che un record guasto dichiara non si crede: può essere proprio lei quella
rovinata». ``Rete.giudica_la_coda`` ha un caso apposta per l'ULTIMO record del giornale: se la lunghezza dichiarata va
oltre la fine del file ma tutto quello che c'è dopo la testa è un corpo a cui il controllo torna, il record c'è tutto e
la lunghezza è rovinata: è un guasto, non una coda scritta a metà.

Le prove del secondo giro (``test_interna2_apertura_lunghezza_guasta_0104.py``) guastano la lunghezza di un record IN
MEZZO, che viene riconosciuto perché dopo ce ne sono altri buoni. Togliendo il caso dell'ultimo record

    if resta <= len(finestra) and finestra[8:9] == b"[" and zlib.crc32(vista[8:]) == crc:   ->   if False:

tutta la suite resta verde: l'ultima scrittura confermata, con un bit cambiato nella sua lunghezza, tornerebbe a essere
presa per una coda. Chi legge vedrebbe cinque eventi su sei senza dirlo; chi apre per scrivere la taglierebbe.
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
@pytest.mark.parametrize("bit", [12, 16, 24, 31])
@pytest.mark.parametrize("pezzo", [None, 37], ids=["pezzo-intero", "a-pezzi-di-37"])
def test_un_bit_nella_lunghezza_dell_ultimo_record_non_ne_fa_una_coda(tmp_path, monkeypatch, scritture, bit, pezzo):
    c = tmp_path / "rete"
    intero = sei_scritture(c)
    confini = record(intero)
    inizio, fine = confini[-1]
    assert len(confini) == 6 and fine == len(intero)
    guasto = bytearray(intero)
    # un bit cambiato nella lunghezza dell'ultimo record: adesso dichiara di arrivare oltre la fine del file
    lunghezza = struct.unpack_from("<I", guasto, inizio)[0]
    assert not lunghezza >> bit & 1 and lunghezza < 1 << 12
    struct.pack_into("<I", guasto, inizio, lunghezza ^ (1 << bit))
    assert guasto[inizio + 4:] == intero[inizio + 4:]  # il controllo e il corpo sono quelli della scrittura confermata
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
        pytest.fail(f"il giornale con l'ultimo record guasto si è aperto: {visti} eventi su 6, la scrittura confermata «f» "
                    f"{'c’è' if ultima else 'non c’è più'}, e il file è lungo {rimasti} byte su {len(guasto)}")
    assert (c / "giornale").read_bytes() == bytes(guasto)  # nessuno ha tagliato niente
    (c / "giornale").write_bytes(intero)  # ripristinato da una copia, si riapre
    with aperta(Memoria(c, scritture=scritture)) as m:
        assert m.conteggi()["eventi"] == 6 and m.ricevuta_di("f")["esito"] == "applicato"


def test_una_coda_scritta_a_meta_si_taglia_come_prima(tmp_path):
    """Il rovescio, perché la prova di sopra non si accontenti di un motore che rifiuta tutto: lo stesso ultimo record
    arrivato solo in parte è una scrittura mai confermata, e chi apre per scrivere la taglia."""
    c = tmp_path / "rete"
    intero = sei_scritture(c)
    inizio, fine = record(intero)[-1]
    (c / "giornale").write_bytes(intero[:fine - 5])
    with aperta(Memoria(c, scritture=True)) as m:
        assert m.conteggi()["eventi"] == 5 and m.ricevuta_di("e")["esito"] == "applicato" and not m.ricevuta_di("f")
    assert (c / "giornale").read_bytes() == intero[:inizio]
