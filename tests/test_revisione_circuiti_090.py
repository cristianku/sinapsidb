"""Revisione della 0.9.0: stessi ID, fatti e risposte in RAM, dal giornale e negli scenari."""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from sinapsidb import lingua, memoria, motore
from sinapsidb.memoria import Memoria
from sinapsidb.motore import Rete, istante


@contextmanager
def aperta(rete):
    try:
        yield rete
    finally:
        rete.chiudi()


def circuito(r, nome="vendite mensili", *, per="mese", lega="quando"):
    n = r.n
    r.proponi_circuito(nome, "test", nome, [{"osserva": n("vendita"), "lega": n(lega), "somma": n("totale"),
                                             "peso": 1, **({"per": per} if per else {})}])
    return n(nome)


def vendita(m, giorno, totale, *, prova=False, **periodo):
    return m.racconta(f"Vendita del {giorno}: {totale}", [
        {"tipo": "vendita", "legami": {"cliente": "C", "quando": giorno, "totale": totale, **periodo}}
    ], prova=prova)


def evento(ricevuta):
    return int(ricevuta["evento"].removeprefix("evento:"))


def valori_arrivo(riesame, lato):
    return [x["valore"] for x in riesame[lato]["letture"] if x["quando"] == "arrivo"]


@pytest.mark.parametrize("per,gruppo", [("mese", "2027-03"), ("anno", "2027")])
def test_mese_nato_con_il_si_stessi_id_in_rifai_e_nello_scenario(tmp_path, per, gruppo):
    cartella = tmp_path / "rete"
    with aperta(Memoria(cartella, scritture=True)) as m:
        r = m.rete
        aggregato = circuito(r, "totali", per=per)
        cliente = circuito(r, "vendite del cliente", per=None, lega="cliente")
        r.proponi_regola("Non sotto zero", "test", "limite", 1, "vieta", [
            {"nome": "negativo", "circuito": cliente, "limite": 0, "peso": 1}])
        assert not r.trova(gruppo)
        d = evento(vendita(m, "2027-03-01", "10"))
        identificativo, = r.trova(gruppo)
        nomi_dopo_il_si = list(r.nomi)
        vendita(m, "2028-12-01", "5000")  # evento intermedio escluso
        a = evento(vendita(m, "2027-03-20", "5", **{"vale da": "2000-01-01"}))
        prima = (cartella / "giornale").read_bytes()
        # rifai_decisione della scrittura che ha fatto nascere il gruppo
        base, record = Rete.com_era(str(cartella), d)
        assert not base.trova(gruppo)
        assert base.rifai_decisione(record)["esito"] == "scrittura"
        assert base.nomi == nomi_dopo_il_si and base.trova(gruppo) == [identificativo]
        # Lo scenario lascia fuori sia il sì stesso sia la vendita da 5000:
        # il nome del gruppo mantiene il numero, ma il saldo include solo la rettifica.
        s, _, _, _ = Rete.scenario(str(cartella), d, [a])
        assert s.nomi == r.nomi and s.trova(gruppo) == [identificativo]
        assert s.somme_pronte(aggregato, al=r.e_quando[d]) == {identificativo: 5_000_000}
        assert m.verifica(d)["esito"] == "coerente"
        x = m.riesamina(d, [a])
        assert x["eventi_usati"] == [f"evento:{a}"]
        assert valori_arrivo(x, "allora") == ["0", "10"] and valori_arrivo(x, "ipotetico") == ["5", "15"]
        assert (cartella / "giornale").read_bytes() == prima
        r.istantanea()
    with aperta(Memoria(cartella)) as riaperta:
        assert riaperta.rete.trova(gruppo) == [identificativo] and riaperta.riesamina(d, [a]) == x


def test_mese_solo_proposto_da_un_no_nel_riesame_con_una_rettifica(tmp_path):
    with aperta(Memoria(tmp_path / "rete", scritture=True)) as m:
        circuito(m.rete)
        c = circuito(m.rete, "vendite del cliente", per=None, lega="cliente")
        m.rete.proponi_regola("Non sotto zero", "test", "limite", 1, "vieta", [
            {"nome": "negativo", "circuito": c, "limite": 0, "peso": 1}])
        vendita(m, "2026-10-10", "20")
        no = vendita(m, "2027-02-01", "-30")
        assert no["esito"] == "violazione_regola" and m.rete.trova("2027-02") == []
        vendita(m, "2027-03-01", "5000")  # esclusa: i nomi possono occupare gli ID abbandonati dal no
        a = vendita(m, "2027-04-01", "15", **{"vale da": "2000-01-01"})
        nomi = list(m.rete.nomi)
        assert m.verifica(no["evento"])["esito"] == "coerente"
        x = m.riesamina(no["evento"], [a["evento"]])
        assert (x["allora"]["esito"], x["ipotetico"]["esito"]) == ("violazione_regola", "applicato")
        assert valori_arrivo(x, "allora") == ["20", "-10"] and valori_arrivo(x, "ipotetico") == ["35", "5"]
        assert m.rete.nomi == nomi and not m.rete.trova("2027-02")


def test_un_mese_che_esiste_solo_nello_scenario_non_entra_nella_storia(tmp_path):
    with aperta(Memoria(tmp_path / "rete", scritture=True)) as m:
        r = m.rete
        c = circuito(r)
        d = evento(vendita(m, "2027-01-04", "10"))
        definizione, = r.scatta(r.n("circuito"), [(r.n("circuito di"), c)])
        r.racconta("Ritira il mensile", "test", ritira=[definizione])
        a = evento(vendita(m, "2027-04-09", "15", **{"vale da": "2000-01-01"}))
        m.racconta("Evento successivo", [{"tipo": "nota neutra", "legami": {"documento": "F-42"}}])
        prima = (list(r.nomi), (m.cartella / "giornale").read_bytes())
        assert not r.trova("2027-04")  # quel mese non esiste come gruppo alla registrazione della rettifica
        s, _, _, _ = Rete.scenario(str(m.cartella), d, [a])
        ipotetico, = s.trova("2027-04")
        assert s.somme_pronte(c, al=r.e_quando[d]) == {ipotetico: 15_000_000}
        assert m.riesamina(d, [a])["eventi_usati"] == [f"evento:{a}"]
        assert (r.nomi, (m.cartella / "giornale").read_bytes()) == prima


def test_istantanea_dopo_prova_non_sposta_i_riferimenti_del_giornale(tmp_path):
    """Gli argomenti della prova sono tutti già confermati. Solo il motore inventa un nuovo nome."""
    cartella = str(tmp_path / "rete")
    with aperta(Rete(cartella, decisioni=True)) as r:
        circuito(r)
        t, q, v, rif = (r.n(x) for x in ("vendita", "quando", "totale", "documento"))
        # Una regola ordinaria di presenza, NON una regola sul circuito raggruppato.
        r.proponi_regola("Presenza del documento", "test", "verifica documento", 2, "vieta", [
            {"nome": "esiste", "modo": "presenza", "osserva": t, "lega": rif, "peso": 1}])
        data, importo = r.n("2027-04-09"), r.n("20")
        # Registra data e importo senza usarli in un fatto osservato dal circuito.
        r.racconta("Calendario", "test", [(r.n("promemoria"), [(q, data), (v, importo)])])
        legami = [(q, data), (v, importo)]
        assert not r.trova("2027-04")
        r.racconta("Solo una prova", "test", [(t, legami)], prova=True)
        r.istantanea()
    with aperta(Memoria(cartella, scritture=True)) as m:
        r = m.rete
        doc = r.n("F-42")
        e = r.racconta("Vendita definitiva", "test", [(t, [*legami, (rif, doc)])])
        f = len(r.f_tipo) - 1
        assert r.nomi[r.leggi(f, rif)] == "F-42"
        gruppi_in_ram = r.trova("2027-04")
        verifica = m.verifica(e)
    solo_giornale, _ = Rete.com_era(cartella, e + 1)
    assert solo_giornale.nomi[solo_giornale.leggi(f, rif)] == "F-42", verifica["differenze"]
    assert solo_giornale.trova("F-42") == [doc] and solo_giornale.trova("2027-04") == gruppi_in_ram
    assert verifica["esito"] == "coerente"


@pytest.mark.parametrize("operazione", ["prova", "rifiuto"])
def test_un_mese_annullato_non_compare_nelle_somme(tmp_path, operazione):
    with aperta(Memoria(tmp_path / "rete", scritture=True)) as m:
        circuito(m.rete)
        c = circuito(m.rete, "vendite del cliente", per=None, lega="cliente")
        m.rete.proponi_regola("Non sotto zero", "test", "limite", 1, "vieta", [
            {"nome": "negativo", "circuito": c, "limite": 0, "peso": 1}])
        vendita(m, "2026-10-09", "20")
        attese = m.chiedi("circuito vendite mensili")["righe"]
        x = vendita(m, "2027-04-09", "-30", prova=operazione == "prova")
        assert x["esito"] == ("prova" if operazione == "prova" else "violazione_regola")
        assert not m.rete.trova("2027-04")
        assert m.chiedi("circuito vendite mensili")["righe"] == attese


def test_gruppi_esistenti_a_zero_e_gruppi_ritirati_non_sono_la_stessa_cosa(tmp_path):
    with aperta(Memoria(tmp_path / "rete", scritture=True)) as m:
        circuito(m.rete)
        vendita(m, "2026-01-01", "10")
        vendita(m, "2026-01-02", "-10")  # gennaio esiste, totale zero
        vendita(m, "2026-02-01", "7")
        m.racconta("Ritira l'unica vendita di febbraio", sostituisce=[{"tipo": "vendita", "indizi": {"quando": "2026-02-01"}}])
        attese = [("2026-01", "0")]
        assert m.chiedi("in vendita somma totale per mese(quando)")["righe"] == attese
        r, _ = Rete.com_era(str(m.cartella), len(m.rete.e_frase))
        assert lingua.chiedi(r, "circuito vendite mensili")["righe"] == attese
        assert m.chiedi("circuito vendite mensili")["righe"] == attese


def test_la_domanda_preparata_segue_decorrenza_e_scadenza_senza_scritture(tmp_path, monkeypatch):
    ora = [istante("2026-11-14")]
    orologio = SimpleNamespace(time=lambda: ora[0])
    for modulo in (memoria, motore):
        monkeypatch.setattr(modulo, "time", orologio)
    with aperta(Memoria(tmp_path / "rete", scritture=True)) as m:
        circuito(m.rete)
        vendita(m, "2026-11-10", "70", **{"vale da": "2026-11-15", "vale fino a": "2026-11-16"})
        p = lingua.prepara(m.rete, "circuito vendite mensili di 2026-11")
        osservate = []
        for data in ("2026-11-14", "2026-11-15", "2026-11-16"):
            ora[0] = istante(data)
            osservate.append(p.esegui()[0][1])  # non chiamare valore() per far avanzare il tempo al suo posto
        assert osservate == ["0", "70", "0"]


@pytest.mark.parametrize("lettura", ["come è registrato", "come risultava all'evento {e}"])
def test_circuito_senza_filtro_di_validita_concorda_con_i_fatti(tmp_path, lettura):
    with aperta(Memoria(tmp_path / "rete", scritture=True)) as m:
        circuito(m.rete)
        e = evento(vendita(m, "2026-02-07", "70", **{"vale da": "2000-02-01", "vale fino a": "2000-03-01"}))
        assert m.chiedi("circuito vendite mensili come valeva il 2000-02-15")["righe"] == [("2026-02", "70")]
        suffisso = " " + lettura.format(e=e)
        fatti = m.chiedi("in vendita somma totale per mese(quando)" + suffisso)
        pronta = m.chiedi("circuito vendite mensili" + suffisso)
        assert pronta["lettura"] == fatti["lettura"] and fatti["righe"] == [("2026-02", "70")]
        assert pronta["righe"] == fatti["righe"]


@pytest.mark.parametrize("definito_prima", [True, False])
def test_il_ruolo_raggruppato_deve_essere_una_data(tmp_path, definito_prima):
    """§9: non creare il mese 2026-13 da una data impossibile, nemmeno definendo il circuito dopo i fatti."""
    with aperta(Memoria(tmp_path / "rete", scritture=True)) as m:
        if definito_prima:
            circuito(m.rete)
        else:
            vendita(m, "2026-13-07", "10")
        prima = (m.cartella / "giornale").read_bytes()
        with pytest.raises(ValueError):
            if definito_prima:
                vendita(m, "2026-13-07", "10")
            else:
                circuito(m.rete)
        assert (m.cartella / "giornale").read_bytes() == prima and not m.rete.trova("2026-13")


def test_il_nome_di_due_circuiti_non_sceglie_in_silenzio_l_ultimo(tmp_path):
    with aperta(Memoria(tmp_path / "rete", scritture=True)) as m:
        for peso in (1, 2):
            n = m.rete.n
            m.rete.proponi_circuito("Somma", "test", "mensile", [
                {"osserva": n("vendita"), "lega": n("quando"), "somma": n("totale"), "peso": peso, "per": "mese"}])
        vendita(m, "2026-11-01", "10")
        assert len(m.rete.trova("mensile")) == 2
        with pytest.raises(lingua.ErroreDomanda):
            m.chiedi("circuito mensile")
