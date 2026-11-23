"""Secondo giro di revisione interna della candidata 0.10.4, lente «apertura»: chi legge mentre chi scrive sta scrivendo.

Chi apre per leggere non prende niente, e deve poter aprire mentre un altro scrive: se trova mezzo record in fondo è una
coda, cioè una scrittura non ancora confermata (README, «Dalla 0.9.8.1 alla 0.9.9»: quella coda «può essere un record
che chi scrive sta ancora scrivendo»). ``Rete._rileggi_il_giornale`` però decide che cos'è quel mezzo record con due
letture diverse: prima legge fino in fondo e si tiene il resto, poi chiede un altro byte («c'è dell'altro?»). Se tra le
due chi scrive finisce il suo record, il byte c'è, e un giornale buono diventa «GiornaleGuasto: dopo di lui ce ne sono
altri... va ripristinato da una copia».

La prova non usa fili né orologi: quando la lettura di chi legge arriva in fondo al file, chi scrive mette nel giornale
il resto del suo record. È un intreccio che può capitare davvero ogni volta che un record, o un gruppo scritto a lotti,
arriva nel file in più passi (``_scrivi_record`` e ``scarica`` scrivono «finché c'è da scrivere»).
"""

import struct

import pytest

from sinapsidb import motore
from sinapsidb.memoria import Memoria
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


@pytest.mark.parametrize("gia_scritti", [3, 8, 200], ids=["mezza-testa", "la-testa", "mezzo-corpo"])
@pytest.mark.parametrize("pezzo", [None, 37], ids=["pezzo-intero", "a-pezzi-di-37"])
def test_chi_legge_mentre_chi_scrive_finisce_un_record_non_trova_un_guasto(tmp_path, monkeypatch, gia_scritti, pezzo):
    c = tmp_path / "rete"
    intero = sei_scritture(c)
    inizio, _ = record(intero)[5]
    # chi scrive è a metà del sesto record: i primi byte sono già nel file, gli altri stanno arrivando
    (c / "giornale").write_bytes(intero[:inizio + gia_scritti])
    vero, finito = open, []

    def finisce_chi_scrive():
        if not finito:
            finito.append(True)
            with vero(c / "giornale", "ab") as chi_scrive:
                chi_scrive.write(intero[inizio + gia_scritti:])

    class Giornale:
        """Il giornale come lo vede chi legge. Quando la sua lettura arriva in fondo, chi scrive finisce il record."""

        def __init__(self, f):
            self.f = f

        def __enter__(self):
            return self

        def __exit__(self, *_):
            self.f.close()

        def seek(self, *dove):
            return self.f.seek(*dove)

        def fileno(self):
            return self.f.fileno()

        def read(self, quanti=-1):
            dati = self.f.read(quanti)
            # Chi scrive finisce il suo record appena chi legge arriva dove il giornale finiva quando lo ha aperto
            # (o a una lettura vuota): da lì in poi ogni altra lettura di chi legge trova un giornale più lungo.
            if not dati or self.f.tell() >= inizio + gia_scritti:
                finisce_chi_scrive()
            return dati

    def apri(percorso, modo="r", *altro, **altri):
        f = vero(percorso, modo, *altro, **altri)
        return Giornale(f) if modo == "rb" and str(percorso) == str(c / "giornale") else f

    monkeypatch.setattr(motore, "open", apri, raising=False)
    if pezzo:
        monkeypatch.setattr(motore, "PEZZO_DI_GIORNALE", pezzo)
    with aperta(Memoria(c)) as lettore:  # il giornale è buono in ogni istante: cinque record, e il sesto che arriva
        assert lettore.conteggi()["eventi"] in (5, 6)
        assert lettore.ricevuta_di("e")["esito"] == "applicato"
    monkeypatch.undo()
    finisce_chi_scrive()  # se chi legge non è mai arrivato in fondo, chi scrive finisce adesso
    assert (c / "giornale").read_bytes() == intero
    with aperta(Memoria(c)) as dopo:
        assert dopo.conteggi()["eventi"] == 6
