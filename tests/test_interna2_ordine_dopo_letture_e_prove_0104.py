"""Secondo giro interno sulla 0.10.4: l'ordine delle righe dopo una lettura e dopo una prova, tenuto fermo.

La candidata promette che una scrittura che non avviene e una lettura non lasciano tracce, nemmeno nell'ordine. Due
delle correzioni che lo garantiscono non avevano una prova che diventasse rossa togliendole:

- ``Memoria.chiedi`` senza clausola mette da parte i fatti che adesso non valgono e poi li rimette. Se un'entità ha
  solo fatti così, il suo posto sulla linea sparisce e torna in fondo: lo rimette dov'era ``Rete._riordina``. Le prove
  che c'erano guardavano solo un'entità che teneva un altro fatto in vigore, e quindi il suo posto.
- ``Rete._riaccendi`` rimette un fatto ritirato tra quelli della sua entità in ordine di nascita. Le prove che c'erano
  avevano due fatti per entità (la coppia); con tre il ramo è un altro, e accodando invece di inserire nessuna prova
  se ne accorgeva.

In tutti e due i casi nel giornale non va niente: chi riapre ritrova l'ordine di prima, la memoria che ha solo letto o
solo provato no.
"""

import pytest

from sinapsidb import nucleo
from sinapsidb.memoria import Memoria
from test_revisione_circuiti_090 import aperta

DOMANDE = ("in vendita conta per cliente come è registrato",
           "in vendita leggi cliente, totale come è registrato",
           "in vendita somma totale per cliente come è registrato",
           "in vendita conta per totale come è registrato",
           "accendi cliente = A in vendita leggi totale come è registrato")


@pytest.fixture(autouse=True, params=["python", "nucleo"])
def motore_scelto(request, monkeypatch):
    monkeypatch.setenv("SINAPSIDB_NUCLEO", "0" if request.param == "python" else "1")
    nucleo.ricarica()
    yield
    monkeypatch.undo()
    nucleo.ricarica()


def vendita(m, cliente, totale, da):
    esito = m.racconta(f"Vendita a {cliente}: {totale}", [{"tipo": "vendita", "legami": {"cliente": cliente, "totale": totale, "vale da": da}}])
    assert esito["esito"] == "applicato"


def letture(m):
    return [(m.chiedi(d)["righe"], m.chiedi(d, limite=1)["righe"]) for d in DOMANDE]


def test_una_lettura_di_adesso_non_sposta_chi_adesso_non_ha_fatti_in_vigore(tmp_path):
    with aperta(Memoria(tmp_path / "rete", scritture=True)) as m:
        vendita(m, "A", "10", "2999-01-01")  # l'unica vendita di A vale dal 2999: adesso A non ha fatti in vigore
        vendita(m, "B", "20", "2000-01-01")
        vendita(m, "C", "30", "2000-01-01")
        prima = letture(m)
        assert prima[0][0] == [("A", "1"), ("B", "1"), ("C", "1")] and prima[1] == ([("A", "10"), ("B", "20"), ("C", "30")], [("A", "10")])
        assert m.chiedi("in vendita conta per cliente")["righe"] == [("B", "1"), ("C", "1")]  # una lettura di quello che vale adesso
        dopo = letture(m)
    with aperta(Memoria(tmp_path / "rete")) as riaperta:
        dal_giornale = letture(riaperta)
    assert dal_giornale == prima  # il giornale non è cambiato
    assert dopo == dal_giornale  # e la memoria che ha solo letto dice lo stesso, nello stesso ordine


def tre_vendite_di_a(m):
    for totale in ("10", "20", "30"):
        vendita(m, "A", totale, "2000-01-01")


@pytest.mark.parametrize("quale", ["10", "20"])
def test_una_prova_che_sostituisce_uno_di_tre_fatti_lo_rimette_dov_era(tmp_path, quale):
    with aperta(Memoria(tmp_path / "rete", scritture=True)) as m:
        tre_vendite_di_a(m)
        prima = letture(m)
        assert prima[-1] == ([("10",), ("20",), ("30",)], [("10",)])
        prova = m.racconta("E se fosse da 11?", [{"tipo": "vendita", "legami": {"cliente": "A", "totale": "11", "vale da": "2000-01-01"}}],
                           sostituisce=[{"tipo": "vendita", "indizi": {"cliente": "A", "totale": quale}}], prova=True)
        assert prova["esito"] == "prova"
        dopo = letture(m)
    with aperta(Memoria(tmp_path / "rete")) as riaperta:
        dal_giornale = letture(riaperta)
    assert dal_giornale == prima
    assert dopo == dal_giornale  # una prova non scrive niente: la memoria resta quella del giornale, anche nell'ordine


def test_una_lettura_di_adesso_rimette_in_ordine_il_primo_di_tre_fatti(tmp_path):
    with aperta(Memoria(tmp_path / "rete", scritture=True)) as m:
        vendita(m, "A", "10", "2999-01-01")  # la prima delle tre adesso non vale: la lettura la mette da parte e la rimette
        vendita(m, "A", "20", "2000-01-01")
        vendita(m, "A", "30", "2000-01-01")
        prima = letture(m)
        assert prima[-1] == ([("10",), ("20",), ("30",)], [("10",)])
        assert m.chiedi("accendi cliente = A in vendita leggi totale")["righe"] == [("20",), ("30",)]
        dopo = letture(m)
    with aperta(Memoria(tmp_path / "rete")) as riaperta:
        dal_giornale = letture(riaperta)
    assert dal_giornale == prima
    assert dopo == dal_giornale
