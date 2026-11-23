"""Terzo giro interno sulla 0.10.4: ``racconta_molti`` non prende i legami una volta sola.

``racconta`` adesso prende i legami di un fatto una volta e li tiene come coppie: quello che controlla è quello che
entra nella rete e nel giornale. ``racconta_molti``, per il caso semplice, no: controlla una passata dei legami
(``piatti``), con quella compone il record del giornale, e poi dà a ``Rete._fatto`` l'oggetto di chi ha chiamato, che
lo scorre altre tre volte. Se le passate non sono uguali la rete in memoria riceve dei legami e il giornale degli altri:

- un legame che ogni volta che lo si scorre dà i valori del momento: la scrittura è confermata, in memoria c'è un fatto
  e nel giornale un altro. Chi riapre legge un'altra rete; i numeri che entrano in memoria non li ha controllati nessuno.
- un legame che ha una lunghezza ma si scorre una volta sola: ``racconta`` lo scrive e lo conferma, ``racconta_molti``
  ferma la rete (fuori uso, memoria guasta) a metà di ``_fatto``.

Le regole (AGENTS.md, il secondo giro): «I legami di un fatto si prendono una volta e si controllano come coppie nei due
percorsi». E la promessa di ``racconta_molti``: fa esattamente quello che farebbe ``racconta`` chiamata una volta per
fatto, e scrive nel giornale gli stessi byte.
"""

from types import SimpleNamespace

import pytest

from sinapsidb import motore
from sinapsidb.motore import Rete
from test_revisione_circuiti_090 import aperta
from test_velocita import _uno_alla_volta

VIE = (("uno", _uno_alla_volta), ("molti", Rete.racconta_molti))


class DelMomento:
    """Un legame lungo due che ogni volta che lo si scorre dà i valori di quel momento: una passata dopo l'altra."""

    def __init__(self, *passate):
        self.passate, self.quale = passate, 0

    def __len__(self):
        return 2

    def __iter__(self):
        passata = self.passate[min(self.quale, len(self.passate) - 1)]
        self.quale += 1
        return iter(passata)


class UnaVolta:
    """Un legame lungo due che si scorre una volta sola."""

    def __init__(self, ruolo, neurone):
        self.resto = iter((ruolo, neurone))

    def __len__(self):
        return 2

    def __iter__(self):
        return self.resto


def fatti(r):
    return [r.in_parole(f) for f in range(len(r.f_tipo))]


def linee(r):
    return {nome: [(k, list(d.items())) for k, d in getattr(r, nome).items()] for nome in ("vivi", "morti")}, dict(r.forma)


@pytest.fixture(autouse=True)
def orologio_fermo(monkeypatch):
    monkeypatch.setattr(motore, "time", SimpleNamespace(time=lambda: 1_790_000_000.25))


def prepara(c):
    r = Rete(str(c))
    tipo, cosa, chi, ada, bea = (r.n(s) for s in ("nota", "cosa", "chi", "Ada", "Bea"))
    assert r.racconta("Prima", "test", [(tipo, [(cosa, ada)])]) == 0
    return r, tipo, cosa, chi, ada, bea


@pytest.mark.parametrize("via,scrivi", VIE, ids=[v for v, _ in VIE])
def test_quello_che_entra_in_memoria_e_quello_che_va_nel_giornale(tmp_path, via, scrivi):
    c = tmp_path / "rete"
    r, tipo, cosa, chi, ada, bea = prepara(c)
    with aperta(r):
        legame = DelMomento((cosa, ada), (cosa, bea), (chi, bea), (chi, 10 ** 6))  # l'ultimo non è nemmeno un neurone
        assert scrivi(r, "test", [("Nota", tipo, [legame])]) == 1  # la scrittura è confermata
        assert r.fuori_uso is None
        in_memoria, come = fatti(r), linee(r)
    with aperta(Rete(str(c), sola_lettura=True)) as riletta:
        assert fatti(riletta) == in_memoria, "chi riapre dal giornale deve leggere il fatto che la rete viva ha confermato"
        assert linee(riletta) == come


@pytest.mark.parametrize("come", ["del-momento", "una-volta"])
def test_i_due_percorsi_scrivono_lo_stesso_giornale_e_nessuno_ferma_la_rete(tmp_path, come):
    esiti = {}
    for via, scrivi in VIE:
        c = tmp_path / via
        r, tipo, cosa, chi, ada, bea = prepara(c)
        with aperta(r):
            legame = DelMomento((cosa, ada), (cosa, bea), (chi, bea)) if come == "del-momento" else UnaVolta(cosa, bea)
            try:
                esito = scrivi(r, "test", [("Nota", tipo, [legame])])
            except ValueError as rifiuto:  # se è un ingresso da rifiutare lo è per tutti e due, e la rete resta in servizio
                esito = type(rifiuto)
            assert r.fuori_uso is None and not r.memoria_guasta, f"{via}: la rete si è fermata per un legame: {r.fuori_uso}"
            esiti[via] = (esito, fatti(r), (c / "giornale").read_bytes())
    assert esiti["molti"][0] == esiti["uno"][0]
    assert esiti["molti"][1] == esiti["uno"][1]
    assert esiti["molti"][2] == esiti["uno"][2]
