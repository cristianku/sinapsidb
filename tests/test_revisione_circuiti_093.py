"""§9 su a28cd35: ammissione tardiva, semantica delle ricevute e identità nel riesame."""

import shutil
from array import array
from pathlib import Path

import pytest

from sinapsidb import lingua
from sinapsidb.memoria import Memoria
from sinapsidb.motore import NonNumerico, Rete
from test_revisione_circuiti_090 import aperta, circuito, evento, vendita
from test_validita import orologio  # noqa: F401


STORICO = Path(__file__).parent / "dati" / "storico-760859b-due-clienti"


@pytest.fixture
def m(tmp_path, orologio):
    with aperta(Memoria(tmp_path / "rete", scritture=True)) as db:
        yield db


@pytest.mark.parametrize("periodo", [
    {"vale da": "2099-01-01"}, {"vale da": "2000-01-01", "vale fino a": "2000-02-01"},
], ids=["futura", "scaduta"])
def test_il_controllo_tardivo_guarda_i_fatti_non_ritirati_non_la_validita(m, periodo):
    r = m.rete
    vendita(m, "2026-11-03", "10")
    x = m.racconta("Vendita senza data", [{"tipo": "vendita", "legami": {"documento": "S", "totale": "5", **periodo}}])
    assert x["esito"] == "applicato"
    # Non appartiene al tipo osservato e non deve mai impedire la definizione.
    m.racconta("Nota senza data", [{"tipo": "nota", "legami": {"documento": "N"}}])
    prima = ((m.cartella / "giornale").read_bytes(), list(r.nomi))
    with pytest.raises(NonNumerico, match="quando"):
        circuito(r)
    assert ((m.cartella / "giornale").read_bytes(), r.nomi) == prima
    assert not r.trova("2026-11")  # neppure il mese della vendita valida è rimasto da questo tentativo
    m.racconta("Storno di S", sostituisce=[{"tipo": "vendita", "indizi": {"documento": "S"}}])
    circuito(r)
    assert m.chiedi("circuito vendite mensili")["righe"] == [("2026-11", "10")]
    assert m.verifica_circuiti()["esito"] == "coerente"


def test_il_controllo_di_una_definizione_non_ripassa_i_fatti_estranei_per_ogni_termine(m):
    class ColonnaSpiata(array):
        """Conta le visite al fatto 0, che non appartiene a nessuna linea della definizione."""

        visite = 0

        def __getitem__(self, i):
            if i == 0:
                self.visite += 1
            return super().__getitem__(i)

        def __iter__(self):
            for i in range(len(self)):
                yield self[i]

    r, n = m.rete, m.rete.n
    r.racconta("Il fatto 0 è estraneo", "test", [(n("nota"), [(n("documento"), n("N"))])])
    vendita(m, "2026-11-03", "10", sconto="2", imposta="1", spese="3")
    termini = [{"osserva": n("vendita"), "lega": n("quando"), "somma": n(ruolo), "peso": peso, "per": "mese"}
               for ruolo, peso in (("totale", 1), ("sconto", -1), ("imposta", 1), ("spese", 1))]
    r.f_tipo = ColonnaSpiata("i", r.f_tipo)
    r.proponi_circuito("Vendite nette", "test", "netto mensile", termini)
    visite = r.f_tipo.visite
    assert m.valore("netto mensile", "2026-11")["valore"] == "12"
    assert visite <= 1, f"una definizione, quattro termini sullo stesso tipo: {visite} visite al fatto estraneo"


def test_senza_nessun_legame_rifiuta_prima_di_lasciare_un_fatto_in_ram(m):
    r = m.rete
    circuito(r)
    prima = (r.conteggi(), (m.cartella / "giornale").read_bytes())
    try:
        with pytest.raises(ValueError):
            r.racconta("Vendita senza alcun partecipante", "test", [(r.n("vendita"), [])])
    finally:
        assert (r.conteggi(), (m.cartella / "giornale").read_bytes()) == prima


def test_il_si_autentico_della_092_sul_secondo_cliente_resta_verificabile(tmp_path):
    cartella = shutil.copytree(STORICO, tmp_path / "rete")
    prima = (cartella / "giornale").read_bytes()
    with aperta(Memoria(cartella)) as db:
        x = db.ricevuta_di("ordine-B")
        assert x["esito"] == "applicato" and [v["valore"] for v in x["decisione"]["regole"][0]["letture"]] == ["70", "10"]
        verifica = db.verifica(x["evento"])
        assert verifica["esito"] == "coerente", verifica["differenze"]
        riesame = db.riesamina(x["evento"])
        assert riesame["ipotetico"] == riesame["allora"]
    assert (cartella / "giornale").read_bytes() == prima


def test_una_definizione_nuova_non_diventa_applicabile_nel_riesame_se_manca_la_data(m):
    r = m.rete
    vendita(m, "2026-11-01", "10")
    definizione = len(r.e_frase)
    c = circuito(r)
    f, = r.scatta(r.n("circuito"), [(r.n("circuito di"), c)])
    r.racconta("Toglie il circuito", "test", ritira=[f])
    rettifica = m.racconta("Vendita retroattiva senza data di fattura", [
        {"tipo": "vendita", "legami": {"cliente": "C", "totale": "5", "vale da": "2000-01-01"}}])
    assert rettifica["esito"] == "applicato" and m.verifica(definizione)["esito"] == "coerente"
    # Stessa base + stessa rettifica, con la definizione valutata normalmente: non si può definire il mensile.
    scenario, record, _, _ = Rete.scenario(str(m.cartella), definizione, [evento(rettifica)])
    proposta = [(t, list(zip(lg[::2], lg[1::2]))) for t, lg in record[5]]
    with pytest.raises(ValueError, match="quando"):
        scenario.racconta("Identica proposta", "test", proposta, ritira=record[6], prova=True,
                         quando=record[2], scritto=record[8] if len(record) > 8 else record[2])
    prima = (m.cartella / "giornale").read_bytes()
    try:
        esame = m.riesamina(definizione, [rettifica["evento"]])
    except ValueError as exc:
        assert "quando" in str(exc)  # anche un errore esplicito è preferibile a un sì inventato
    else:
        assert esame["allora"]["esito"] == "applicato" and esame["eventi_usati"] == [rettifica["evento"]]
        assert esame["ipotetico"]["esito"] != "applicato"
    assert (m.cartella / "giornale").read_bytes() == prima


def test_il_rifiuto_non_fa_rinascere_l_entita_che_il_suo_record_ha_confermato(m):
    r, n = m.rete, m.rete.n
    r.proponi_circuito("Saldo", "test", "fido", [
        {"osserva": n(t), "lega": n("cliente"), "somma": n("importo"), "peso": peso}
        for t, peso in (("accredito", 1), ("ordine", -1))])
    r.proponi_regola("Mai sotto zero", "test", "limite", 1, "vieta", [
        {"nome": "negativo", "circuito": n("fido"), "limite": 0, "peso": 1}])
    c = n("C")  # prenotato dal chiamante, precedente ai nomi provvisori della proposta
    no = m.racconta("Ordine", [{"tipo": "ordine", "legami": {"cliente": f"neurone:{c}", "importo": "10"}}])
    assert no["esito"] == "violazione_regola" and m.verifica(no["evento"])["esito"] == "coerente"
    al_no, _ = Rete.com_era(str(m.cartella), evento(no) + 1)
    assert al_no.trova("C") == [c] and not al_no.trova("10")
    a = m.racconta("Rettifica", [
        {"tipo": "accredito", "legami": {"cliente": f"neurone:{c}", "importo": "20", "vale da": "2000-01-01"}}])
    assert a["esito"] == "applicato" and no["evento"] in m.candidate(a["evento"])["candidate"]
    prima = (m.cartella / "giornale").read_bytes()
    x = m.riesamina(no["evento"], [a["evento"]])
    assert x["eventi_usati"] == [a["evento"]]
    letture = [(y["su_id"], y["valore"]) for y in x["ipotetico"]["letture"] if y["quando"] == "arrivo"]
    assert (x["ipotetico"]["esito"], letture) == ("applicato", [(c, "20"), (c, "10")])
    nessuna = m.riesamina(no["evento"])
    assert nessuna["allora"] == nessuna["ipotetico"] and not nessuna["letture_in_piu"]
    assert (m.cartella / "giornale").read_bytes() == prima


def test_la_preparata_non_chiama_mese_una_colonna_di_anni(m):
    r, n = m.rete, m.rete.n
    c = circuito(r, "vendite per periodo")
    vendita(m, "2026-11-03", "10")
    frase = "circuito vendite per periodo"
    p = lingua.prepara(r, frase)
    assert p.esegui() == [("2026-11", "10")] and p.colonne[0] == "mese"
    f, = r.scatta(n("termine"), [(n("termine di"), c)])
    ruolo, anno = n("raggruppa per"), n("anno")
    nuovi_legami = [(k, anno if k == ruolo else v) for k, v in r.legami(f)]
    r.racconta("Raggruppa per anno", "test", [(n("termine"), nuovi_legami)], ritira=[f])
    nuova = m.chiedi(frase)
    assert nuova["colonne"][0] == "anno" and nuova["righe"] == [("2026", "10")]
    try:
        righe = p.esegui()
    except lingua.ErroreDomanda:
        return  # lecito anche chiedere al chiamante di riprepararla
    assert (p.colonne, righe) == (nuova["colonne"], nuova["righe"])


def test_ritirato_il_circuito_la_preparata_non_inventa_un_valore_zero(m):
    r, n = m.rete, m.rete.n
    c = circuito(r)
    vendita(m, "2026-11-03", "10")
    frase = "circuito vendite mensili di 2026-11"
    p = lingua.prepara(r, frase)
    assert p.esegui() == [("2026-11", "10")]
    f, = r.scatta(n("circuito"), [(n("circuito di"), c)])
    r.racconta("Toglie il mensile", "test", ritira=[f])
    with pytest.raises(lingua.ErroreDomanda):
        m.chiedi(frase)
    with pytest.raises(lingua.ErroreDomanda):
        p.esegui()


def test_un_id_che_non_e_un_circuito_non_sceglie_il_nome_letterale(m):
    r = m.rete
    circuito(r)
    tipo = r.trova("vendita")[0]
    letterale = f"neurone:{tipo}"
    circuito(r, letterale)
    vendita(m, "2026-11-03", "70")
    assert m.chiedi(f'circuito "{letterale}"')["righe"] == [("2026-11", "70")]
    with pytest.raises(ValueError):
        m.valore(letterale, "2026-11")
    with pytest.raises(lingua.ErroreDomanda):
        m.chiedi(f"circuito {letterale}")


def test_id_del_cliente_dopo_di_ha_lo_stesso_significato_che_in_valore(m):
    r, n = m.rete, m.rete.n
    circuito(r, "ricavi", per=None, lega="cliente")
    a = n("A")
    letterale = f"neurone:{a}"
    for cliente, valore in ((a, "10"), (n(letterale), "90")):
        r.racconta("Vendita", "test", [(n("vendita"), [(n("cliente"), cliente), (n("totale"), n(valore))])])
    assert m.valore("ricavi", letterale)["valore"] == "10"
    assert m.chiedi(f'circuito ricavi di "{letterale}"')["righe"] == [(letterale, "90")]
    assert m.chiedi(f"circuito ricavi di {letterale}")["righe"] == [("A", "10")]
