"""Le correzioni della candidata 0.10.4 tenute ferme da una prova ciascuna.

Il critico della revisione interna ha tolto a turno venti modifiche della candidata, e sedici volte nessuna prova se ne
accorgeva. Qui c'è, per ognuna, la prova che diventa rossa se la modifica sparisce.
"""

import decimal
import errno
from decimal import Decimal

import pytest

from sinapsidb import lingua, motore, nucleo
from sinapsidb.memoria import Memoria
from sinapsidb.motore import Rete, Vietato
from test_decisioni import _record
from test_revisione_circuiti_090 import aperta
from test_segno_e_disco import fido, movimento


LETTURE = {
    "chiedi": lambda m: m.chiedi("in accredito leggi cliente"),
    "verifica": lambda m: m.verifica(2),
    "riesamina": lambda m: m.riesamina(2),
    "candidate": lambda m: m.candidate(2),
    "valore": lambda m: m.valore("fido", "A"),
    "valori": lambda m: m.valori("fido"),
    "decisioni": lambda m: m.decisioni(),
    "verifica_circuiti": lambda m: m.verifica_circuiti(),
    "ricevuta_di": lambda m: m.ricevuta_di("a"),
    "racconta": lambda m: movimento(m, "accredito", "A", "5", "b"),
    "racconta-la-stessa-chiave": lambda m: movimento(m, "accredito", "A", "100", "a"),
    "prova": lambda m: m.racconta("ordine di A", [{"tipo": "ordine", "legami": {"cliente": "A", "importo": "60"}}], prova=True),
}


@pytest.mark.parametrize("lettura", sorted(LETTURE))
def test_una_memoria_che_non_corrisponde_al_giornale_non_risponde_a_niente(tmp_path, lettura):
    with aperta(Memoria(tmp_path / "rete", scritture=True)) as m:
        fido(m)
        assert movimento(m, "accredito", "A", "100", "a")["esito"] == "applicato"
        LETTURE[lettura](m)  # finché la memoria è buona risponde
        m.rete._rotta, m.rete._memoria_guasta = "un evento è nel giornale ma la memoria non è riuscita ad allinearsi", True
        with pytest.raises(RuntimeError, match="riapert"):
            LETTURE[lettura](m)


def test_dopo_un_evento_scritto_che_la_memoria_non_conta_la_memoria_e_guasta(tmp_path, monkeypatch):
    with aperta(Memoria(tmp_path / "rete", scritture=True)) as m:
        fido(m)
        monkeypatch.setattr(m.rete, "_in_memoria", lambda *a, **k: (_ for _ in ()).throw(MemoryError("non si allinea")))
        with pytest.raises(MemoryError):
            movimento(m, "accredito", "A", "100", "a")
        assert m.rete.fuori_uso and m.rete.memoria_guasta
    with aperta(Memoria(tmp_path / "rete")) as riaperta:  # nel giornale c'è: chi riapre lo trova
        assert riaperta.ricevuta_di("a")["esito"] == "applicato"


def test_anche_un_no_scritto_che_la_memoria_non_conta_la_ferma(tmp_path, monkeypatch):
    with aperta(Memoria(tmp_path / "rete", scritture=True)) as m:
        fido(m)
        monkeypatch.setattr(m.rete, "_in_memoria", lambda *a, **k: (_ for _ in ()).throw(MemoryError("non si allinea")))
        with pytest.raises(MemoryError):
            movimento(m, "ordine", "A", "60", "o")  # il fido è zero: è un no, e il no è un evento
        assert m.rete.fuori_uso and m.rete.memoria_guasta
    with aperta(Memoria(tmp_path / "rete")) as riaperta:
        assert riaperta.ricevuta_di("o")["esito"] == "violazione_regola"


@pytest.mark.parametrize("via", ["uno", "molti"])
def test_un_ritorno_indietro_che_non_riesce_lascia_la_memoria_guasta(tmp_path, monkeypatch, via):
    with aperta(Rete(str(tmp_path / "rete"))) as r:
        tipo, ruolo, ada = r.n("nota"), r.n("cosa"), r.n("Ada")
        r.racconta("Prima", "test", [(tipo, [(ruolo, ada)])])
        monkeypatch.setattr(r, "_scrivi_record", lambda dati: (_ for _ in ()).throw(OSError(errno.ENOSPC, "disco pieno")))
        monkeypatch.setattr(r, "_annulla", lambda *a: (_ for _ in ()).throw(RuntimeError("non si annulla")))
        with pytest.raises(OSError):
            if via == "uno":
                r.racconta("Seconda", "test", [(tipo, [(ruolo, ada)])])
            else:
                r.racconta_molti("test", [("Seconda", tipo, [(ruolo, ada)])])
        assert r.fuori_uso and r.memoria_guasta


@pytest.mark.parametrize("via", ["uno", "molti"])
def test_un_fatto_che_si_ferma_mentre_nasce_ferma_la_rete(tmp_path, monkeypatch, via):
    c = tmp_path / "rete"
    with aperta(Rete(str(c))) as r:
        tipo, ruolo, ada = r.n("nota"), r.n("cosa"), r.n("Ada")
        r.racconta("Prima", "test", [(tipo, [(ruolo, ada)])])
        prima = (c / "giornale").read_bytes()
        monkeypatch.setattr(r, "_fatto", lambda *a: (_ for _ in ()).throw(MemoryError("a metà")))
        with pytest.raises(MemoryError):
            if via == "uno":
                r.racconta("Seconda", "test", [(tipo, [(ruolo, ada)])])
            else:
                r.racconta_molti("test", [("Seconda", tipo, [(ruolo, ada)])])
        assert r.fuori_uso and r.memoria_guasta and (c / "giornale").read_bytes() == prima


def test_un_gruppo_scritto_a_lotti_che_non_arriva_lascia_la_memoria_guasta(tmp_path, monkeypatch):
    with aperta(Rete(str(tmp_path / "rete"), sync="lotti")) as r:
        tipo, ruolo, ada = r.n("nota"), r.n("cosa"), r.n("Ada")
        r.racconta_molti("test", [(f"Nota {i}", tipo, [(ruolo, ada)]) for i in range(3)])
        vera = motore.os.write
        monkeypatch.setattr(motore.os, "write", lambda fd, dati: (_ for _ in ()).throw(OSError(errno.EIO, "non scrive")) if fd == r._fd else vera(fd, dati))
        with pytest.raises(OSError):
            r.scarica()
        assert r.fuori_uso and r.memoria_guasta


@pytest.mark.parametrize("esito", ["rifiuto", "scrittura"])
def test_chi_prepara_la_nota_non_puo_cambiare_la_decisione_del_motore(tmp_path, esito):
    c = tmp_path / "rete"
    with aperta(Memoria(c, scritture=True)) as m:
        fido(m)
        movimento(m, "accredito", "A", "100", "a")
        r = m.rete
        n = r.n

        def nota_del_rifiuto(scattate, decisione):
            scattate.clear()  # chi riceve prova a cambiare quello che ha ricevuto
            decisione["base"], decisione["regole"] = 99, []
            return {"ricevuta": {"esito": "violazione_regola", "evento": f"evento:{len(r.e_frase)}", "regole": []}}

        def nota_della_scrittura(decisione):
            decisione["base"], decisione["regole"] = 99, []
            return {"ricevuta": {"esito": "applicato", "evento": f"evento:{len(r.e_frase)}"}}

        importo = "500" if esito == "rifiuto" else "40"
        fatti = [(n("ordine"), [(n("cliente"), n("A")), (n("importo"), n(importo))])]
        evento = len(r.e_frase)
        if esito == "rifiuto":
            with pytest.raises(Vietato) as vietato:
                r.racconta("Ordine", "test", fatti, nota_del_rifiuto=nota_del_rifiuto)
            assert vietato.value.args[0], "le regole scattate sono quelle del motore, non quelle svuotate da chi le ha ricevute"
        else:
            r.racconta("Ordine", "test", fatti, nota_della_scrittura=nota_della_scrittura)
    scritta = _record(c)[evento][7]["ricevuta"]["decisione"]
    assert scritta["base"] == evento - 1 and [x["regola"] for x in scritta["regole"]] == ["limite"]


def test_un_numero_che_i_milionesimi_non_sanno_scrivere_lo_somma_il_python(monkeypatch):
    assert nucleo.numero_di("123456789.5") == (123456789500000, 1)
    stretto = decimal.Context(prec=5, traps=[decimal.Inexact])
    monkeypatch.setattr(nucleo, "ESATTO", stretto)  # qualunque cosa vada storta nel conto, non è un'eccezione di chi somma
    assert nucleo.numero_di("123456789.5") == (0, nucleo.FUORI_MISURA)


def test_i_conti_esatti_non_dipendono_dai_limiti_di_chi_ospita(tmp_path):
    for contesto in (lingua.ESATTO, nucleo.ESATTO):
        assert (contesto.Emin, contesto.Emax) == (decimal.MIN_EMIN, decimal.MAX_EMAX)
    with aperta(Rete(str(tmp_path / "rete"))) as r:
        tipo, ruolo = r.n("misura"), r.n("quanto")
        for nome in ("9E+999999", "1E+999999"):  # la somma ha un esponente oltre il limite di un contesto ordinario
            r.racconta(nome, "test", [(tipo, [(ruolo, r.n(nome))])])
        (somma,), = lingua.chiedi(r, "in misura somma quanto")["righe"]
        assert Decimal(somma) == Decimal("1.0E+1000000")
