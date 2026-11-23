"""Ricontrollo: il giornale decide anche quando a fallire è l'ultimo aggiornamento in memoria."""

import shutil
from types import SimpleNamespace

import pytest

from sinapsidb import memoria, motore
from sinapsidb.memoria import Memoria
from sinapsidb.motore import Rete
from test_revisione_circuiti_090 import aperta
from test_segno_e_disco import fido, movimento, nota


def test_dopo_il_commit_di_una_definizione_non_si_autorizza_con_il_vecchio_saldo(tmp_path, monkeypatch):
    c = tmp_path / "rete"
    clock = SimpleNamespace(time=lambda: 1000.5)
    monkeypatch.setattr(memoria, "time", clock)
    monkeypatch.setattr(motore, "time", clock)
    with aperta(Memoria(c, scritture=True)) as m:
        fido(m)
        assert movimento(m, "accredito", "A", "100", "credito")["esito"] == "applicato"
        termine = [{"tipo": "termine", "legami": {"termine di": "fido", "osserva": "accredito", "lega": "cliente",
                                                  "somma": "importo", "peso": "-1"}}]
        prima = m.conteggi()["eventi"]
        somme = m.rete._somme

        def leggi(*args, **kwargs):
            # L'ammissione riesce. Fallisce solo il ricalcolo che trova già il nuovo record sul disco.
            if len(list(Rete._record(str(c)))) > prima:
                raise OSError("lettura della somma fallita dopo il commit")
            return somme(*args, **kwargs)

        with monkeypatch.context() as mp:
            mp.setattr(m.rete, "_somme", leggi)
            try:
                m.racconta("Compensa il credito", termine, chiave="compensa")
            except (OSError, RuntimeError):
                pass  # lecito esigere la riapertura; il fallimento non è un no registrato
        with aperta(Memoria(c)) as lettore:
            ricevuta = lettore.ricevuta_di("compensa")
            assert ricevuta["esito"] == "applicato" and lettore.valore("fido", "A")["valore"] == "0"
            assert lettore.verifica(ricevuta["evento"])["esito"] == "coerente"
        try:
            calda = m.ricevuta_di("compensa")
        except RuntimeError:
            assert m.rete.fuori_uso
            chiave_coerente = True  # esige prima una riapertura
        else:
            chiave_coerente = calda == ricevuta
        try:
            ordine = movimento(m, "ordine", "A", "60", "ordine")
        except RuntimeError:
            assert m.rete.fuori_uso
        else:
            assert ordine["esito"] == "violazione_regola" and m.verifica(ordine["evento"])["esito"] == "coerente"
        assert chiave_coerente, "il giornale ha già la chiave: «assente» non è una risposta valida"
    with aperta(Memoria(c, scritture=True)) as riaperta:
        ordine = movimento(riaperta, "ordine", "A", "60", "ordine")
        assert ordine["esito"] == "violazione_regola" and riaperta.verifica(ordine["evento"])["esito"] == "coerente"


@pytest.mark.parametrize("stessa_lunghezza", [False, True], ids=["foto-piu-lunga", "foto-avanti-di-un-evento"])
def test_il_lettore_con_istantanea_piu_avanti_non_consegna_ricevute_assenti(tmp_path, stessa_lunghezza):
    c = tmp_path / "rete"
    with aperta(Memoria(c, scritture=True)) as m:
        a = nota(m, "A")
    altra = shutil.copytree(c, tmp_path / "altra")
    prefisso = (c / "giornale").read_bytes()
    with aperta(Memoria(c, scritture=True)) as m:
        nota(m, "B")
        m.rete.istantanea()
    lunghezza_foto = (c / "giornale").stat().st_size
    # Due copie autentiche con la stessa A. Nell'altra storia il secondo record era più lungo e non è arrivato intero.
    with aperta(Memoria(altra, scritture=True)) as m:
        nota(m, "C" * 100)
    originale = (altra / "giornale").read_bytes()
    lunghezza = lunghezza_foto if stessa_lunghezza else len(prefisso) + 3
    assert len(prefisso) < lunghezza < len(originale)
    (c / "giornale").write_bytes(originale[:lunghezza])
    assert len(list(Rete._record(str(c)))) == 1
    prima = {p.name: p.read_bytes() for p in c.iterdir()}
    try:
        with aperta(Memoria(c)) as m:
            assert m.conteggi()["eventi"] == 1 and m.ricevuta_di("A") == a and m.ricevuta_di("B") is None
    except (ValueError, OSError):
        assert stessa_lunghezza  # è lecito segnalare esplicitamente che le copie non corrispondono
    finally:
        assert {p.name: p.read_bytes() for p in c.iterdir()} == prima


def test_il_callback_non_modifica_in_posto_la_decisione_canonica(tmp_path):
    c = tmp_path / "rete"
    with aperta(Memoria(c, scritture=True)) as m:
        r, n = m.rete, m.rete.n

        def prepara(d):
            d["base"] = 42  # il serializzatore modifica l'argomento, non costruisce un altro dizionario
            return {"ricevuta": {"evento": "evento:0", "esito": "applicato"}}

        try:
            e = r.racconta("Nota", "test", [(n("nota"), [(n("cosa"), n("A"))])], nota_della_scrittura=prepara)
        except (ValueError, TypeError):
            assert not list(Rete._record(str(c)))
        else:
            v = m.verifica(e)
            assert v["esito"] == "coerente", v["differenze"]
