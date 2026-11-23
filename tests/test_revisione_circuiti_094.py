"""§9 su 8cad802: un fatto per entità, due semantiche storiche e identità nelle letture."""

import shutil
import struct
import zlib
from enum import StrEnum
from pathlib import Path

import pytest

from sinapsidb import lingua
from sinapsidb.memoria import Memoria
from sinapsidb.motore import Rete
from test_revisione_circuiti_090 import aperta, circuito, evento
from test_validita import orologio  # noqa: F401


DATI = Path(__file__).parent / "dati"
STORICO_093 = DATI / "storico-a28cd35-ricevute"


@pytest.fixture
def m(tmp_path, orologio):
    with aperta(Memoria(tmp_path / "rete", scritture=True)) as db:
        yield db


def fido(r):
    n = r.n
    r.proponi_circuito("Fido", "test", "fido", [
        {"osserva": n(t), "lega": n("cliente"), "somma": n("importo"), "peso": peso}
        for t, peso in (("accredito", 1), ("ordine", -1))])
    r.proponi_regola("Non spendere più del fido", "test", "limite", 1, "vieta", [
        {"nome": "negativo", "circuito": n("fido"), "limite": 0, "peso": 1}])
    return n("fido")


@pytest.mark.parametrize("via,seconda", [("rete", "A"), ("memoria", "A"), ("rete", "B")],
                         ids=["stessa-entita", "alias-del-ruolo", "entita-distinte"])
def test_un_accredito_di_70_non_autorizza_un_ordine_di_100(m, via, seconda):
    r, n = m.rete, m.rete.n
    c = fido(r)
    ruolo, a, b = n("cliente"), n("A"), n("B")
    prima = ((m.cartella / "giornale").read_bytes(), r.conteggi())
    try:
        if via == "rete":
            e = r.racconta("Un accredito", "test", [(n("accredito"), [
                (ruolo, a), (ruolo, a if seconda == "A" else b), (n("importo"), n("70"))])], neuroni_da=prima[1]["neuroni"])
        else:
            # Due chiavi testuali diverse, ma dopo la risoluzione indicano entrambe il ruolo «cliente».
            x = m.racconta("Un accredito", [{"tipo": "accredito", "legami": {
                "cliente": "A", f"neurone:{ruolo}": "A", "importo": "70"}}])
            if x["esito"] == "da_chiarire":
                assert ((m.cartella / "giornale").read_bytes(), r.conteggi()) == prima
                return
            assert x["esito"] == "applicato"
            e = evento(x)
    except ValueError:  # lecito anche rifiutare la coppia duplicata, prima di scrivere
        assert seconda == "A" and ((m.cartella / "giornale").read_bytes(), r.conteggi()) == prima
        return
    da_disco, _ = Rete.com_era(str(m.cartella), e + 1)
    saldi = [dict(db.somme_pronte(c, al=r.e_scritto[e])) for db in (r, da_disco)]
    ordine = m.racconta("Ordine di A", [{"tipo": "ordine", "legami": {"cliente": "A", "importo": "100"}}])
    letture = [y["valore"] for y in ordine["decisione"]["regole"][0]["letture"]]
    attesi = {cliente: 70_000_000 for cliente in {a, a if seconda == "A" else b}}
    assert (ordine["esito"], letture, m.verifica(ordine["evento"])["esito"], saldi) == (
        "violazione_regola", ["70", "-30"], "coerente", [attesi, attesi])


@pytest.mark.parametrize("storia,esito,letture", [
    ("storico-760859b-due-clienti", "applicato", ["70", "10"]),
    ("storico-a28cd35-ricevute", "violazione_regola", ["0", "-60"]),
], ids=["si-092", "no-093"])
def test_sono_integri_sia_il_si_della_092_sia_il_no_della_093(tmp_path, storia, esito, letture):
    cartella = shutil.copytree(DATI / storia, tmp_path / "rete")
    prima = (cartella / "giornale").read_bytes()
    with aperta(Memoria(cartella)) as db:
        x = db.ricevuta_di("ordine-B")  # stessa storia e orologio, giudicata e verificata allora dai due motori
        assert x["esito"] == esito and [y["valore"] for y in x["decisione"]["regole"][0]["letture"]] == letture
        v = db.verifica(x["evento"])
        assert v["esito"] == "coerente", v["differenze"]
        esame = db.riesamina(x["evento"])
        assert esame["ipotetico"] == esame["allora"]
    assert (cartella / "giornale").read_bytes() == prima


@pytest.mark.parametrize("modo", ["verifica", "senza_rettifiche", "con_rettifica"])
def test_un_fatto_storico_senza_legami_si_rilegge_ma_oggi_non_si_ammette(tmp_path, modo):
    cartella = shutil.copytree(STORICO_093, tmp_path / "rete")
    prima = (cartella / "giornale").read_bytes()
    with aperta(Memoria(cartella)) as db:
        if modo == "verifica":
            assert db.verifica(4)["esito"] == "coerente"
        elif modo == "senza_rettifiche":
            x = db.riesamina(4)
            assert x["ipotetico"] == x["allora"] and not x["cambia"]
        else:
            x = db.riesamina(4, [5])
            assert x["eventi_usati"] == ["evento:5"] and x["allora"]["esito"] == "applicato"
            assert x["ipotetico"]["esito"] == "non_applicabile" and x["ipotetico"]["spiegazione"]
    assert (cartella / "giornale").read_bytes() == prima


def test_fino_a_compreso_solo_un_evento_escluso_non_e_una_nuova_ipotesi(tmp_path):
    vecchia = DATI / "storico-24a5d89-circuiti" / "rettifica"
    cartella = tmp_path / "rete"
    cartella.mkdir()
    # Prefisso autentico: definizione mensile e vendita con data 2026-13-07, prima della sua correzione.
    with (vecchia / "giornale").open("rb") as src, (cartella / "giornale").open("wb") as dst:
        for _ in range(2):
            testa = src.read(8)
            lunghezza, crc = struct.unpack("<II", testa)
            corpo = src.read(lunghezza)
            assert len(corpo) == lunghezza and zlib.crc32(corpo) == crc
            dst.write(testa + corpo)
    shutil.copyfile(vecchia / "formato", cartella / "formato")
    with aperta(Memoria(cartella, scritture=True)) as db:
        r, d = db.rete, db.ricevuta_di("prima")["evento"]
        altro = r.proponi_circuito("Definizione indipendente", "test", "preventivi per cliente", [
            {"osserva": r.n("preventivo"), "lega": r.n("cliente"), "somma": r.n("totale"), "peso": 1}])
        prima = (cartella / "giornale").read_bytes()
        senza, fino = db.riesamina(d), db.riesamina(d, fino_a=altro)
        assert senza["ipotetico"] == senza["allora"] and not senza["cambia"]
        assert fino["eventi_usati"] == [] and [v["evento"] for v in fino["eventi_esclusi"]] == [f"evento:{altro}"]
        assert fino["ipotetico"] == senza["ipotetico"] and not fino["cambia"]
        assert (cartella / "giornale").read_bytes() == prima


def test_l_origine_testuale_non_lascia_mezzo_evento(m):
    class Fonte(StrEnum):
        CONTABILITA = "contabilità"

    prima = (m.conteggi(), (m.cartella / "giornale").read_bytes())
    try:
        x = m.racconta("Nota", [{"tipo": "nota", "legami": {"documento": "N"}}], chi=Fonte.CONTABILITA)
    except (TypeError, ValueError):
        # Anche un rifiuto anticipato non può lasciare fatti o eventi a metà.
        assert (m.conteggi(), (m.cartella / "giornale").read_bytes()) == prima
    else:
        e = evento(x)
        assert x["esito"] == "applicato" and m.rete.e_origine[e] == "contabilità"
        assert m.verifica(e)["esito"] == "coerente"
    r = m.rete
    assert len(r.e_frase) == len(r.e_origine) == len(r.e_tipo) == len(r.e_scritto)


def test_anche_lo_scenario_condivide_tipo_e_origine_per_valore(m):
    r, n = m.rete, m.rete.n
    for i in range(40):
        r.racconta(f"Nota {i}", "sportello contabile", [(n("nota"), [(n("numero"), n(str(i)))])])
    storia, _ = Rete.com_era(str(m.cartella), 40)
    scenario, _, _, _ = Rete.scenario(str(m.cartella), 39)
    for rete in (storia, scenario):
        assert rete.e_origine == ["sportello contabile"] * 40 and rete.e_tipo == ["scrittura"] * 40
        assert (len({id(x) for x in rete.e_origine}), len({id(x) for x in rete.e_tipo})) == (1, 1)


def test_la_preparata_non_passa_a_un_altro_circuito_che_ha_ripreso_il_nome(m):
    r, n = m.rete, m.rete.n
    c = circuito(r, "ricavi", per=None, lega="cliente")
    r.racconta("Vendita", "test", [(n("vendita"), [(n("cliente"), n("A")), (n("totale"), n("10"))])])
    p = lingua.prepara(r, "circuito ricavi")
    assert p.esegui() == [("A", "10")]
    f, = r.scatta(n("circuito"), [(n("circuito di"), c)])
    r.racconta("Ritira", "test", ritira=[f])
    r.proponi_circuito("Altra definizione", "test", "ricavi", [
        {"osserva": n("vendita"), "lega": n("cliente"), "somma": n("totale"), "peso": 9}])
    assert m.chiedi("circuito ricavi")["righe"] == [("A", "90")]
    with pytest.raises(lingua.ErroreDomanda):
        p.esegui()


def test_preparare_per_nome_non_fa_leggere_un_altra_persona_dopo_il_riuso_dell_id(m):
    r, n = m.rete, m.rete.n
    circuito(r, "saldo", per=None, lega="cliente")
    segno = len(r.nomi)
    a = n("Ada")  # nome non ancora confermato; la lettura preparata non lo registra
    p = lingua.prepara(r, "circuito saldo di Ada")
    assert p.esegui() == [("Ada", "0")]
    r.dimentica(segno)
    b = n("Bea")
    assert b == a
    r.racconta("Vendita di Bea", "test", [(n("vendita"), [(n("cliente"), b), (n("totale"), n("70"))])])
    with pytest.raises(lingua.ErroreDomanda):
        p.esegui()


def test_il_di_in_un_nome_quotato_non_e_il_separatore(m):
    r, n = m.rete, m.rete.n
    c = circuito(r, "ricavi", per=None, lega="cliente")
    r.racconta("Vendita", "test", [(n("vendita"), [(n("cliente"), n("Studio di Ada")), (n("totale"), n("10"))])])
    assert m.valore("ricavi", "Studio di Ada")["valore"] == "10"
    assert m.chiedi(f'circuito neurone:{c} di "Studio di Ada"')["righe"] == [("Studio di Ada", "10")]


def test_un_nome_che_sembra_tutta_la_frase_non_oscura_un_id_esplicito(m):
    r, n = m.rete, m.rete.n
    c = circuito(r, "ricavi", per=None, lega="cliente")
    r.racconta("Vendita", "test", [(n("vendita"), [(n("cliente"), n("A")), (n("totale"), n("10"))])])
    letterale = f"neurone:{c} di A"
    r.proponi_circuito("Secondo circuito", "test", letterale, [
        {"osserva": n("vendita"), "lega": n("cliente"), "somma": n("totale"), "peso": 9}])
    assert m.chiedi(f'circuito "{letterale}"')["righe"] == [("A", "90")]
    assert m.chiedi(f"circuito {letterale}")["righe"] == [("A", "10")]
