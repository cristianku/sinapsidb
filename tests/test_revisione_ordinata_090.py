"""L'indice caldo e quello ricostruito devono leggere gli stessi fatti."""

from types import SimpleNamespace

import pytest

from sinapsidb import lingua, memoria, motore
from sinapsidb.memoria import Memoria
from sinapsidb.motore import istante


@pytest.fixture
def m(tmp_path):
    db = Memoria(tmp_path / "rete", scritture=True)
    try:
        yield db
    finally:
        db.chiudi()


def vendita(m, totale, *, prova=False, **altro):
    return m.racconta(f"Vendita {totale}", [{"tipo": "vendita", "legami": {"totale": totale, **altro}}], prova=prova)


SOPRA = "in vendita dove totale > 50 leggi totale"


def test_un_id_numerico_riutilizzato_dopo_una_prova_non_eredita_il_valore(m):
    vendita(m, "10")
    assert m.chiedi(SOPRA)["righe"] == []  # indice caldo
    assert vendita(m, "100", prova=True)["esito"] == "prova"
    assert not m.rete.trova("100") and vendita(m, "2")["esito"] == "applicato"
    nuova = Memoria(m.cartella)
    try:
        assert nuova.chiedi(SOPRA)["righe"] == []
        assert m.chiedi(SOPRA)["righe"] == []
        assert sorted(m.chiedi("in vendita dove totale < 50 leggi totale")["righe"]) == [("10",), ("2",)]
    finally:
        nuova.chiudi()


def test_un_indice_nato_durante_la_lettura_non_perde_un_fatto_futuro(m, monkeypatch):
    ora = [istante("2026-11-14")]
    orologio = SimpleNamespace(time=lambda: ora[0])
    for modulo in (memoria, motore):
        monkeypatch.setattr(modulo, "time", orologio)
    vendita(m, "10")
    vendita(m, "100", **{"vale da": "2026-11-15"})
    assert m.chiedi(SOPRA)["righe"] == []
    ora[0] = istante("2026-11-15")
    assert m.chiedi(SOPRA + " come valeva adesso")["righe"] == [("100",)]  # vista ricostruita
    assert m.chiedi(SOPRA)["righe"] == [("100",)]  # stessi fatti, stessa frontiera e stesso istante


def test_una_soglia_nan_non_seleziona_fatti(m):
    vendita(m, "10")
    try:
        righe = lingua.prepara(m.rete, "in vendita dove totale >= ? leggi totale").esegui("nan")
    except lingua.ErroreDomanda:
        return  # Il linguaggio può anche rifiutare NaN esplicitamente.
    assert righe == []
