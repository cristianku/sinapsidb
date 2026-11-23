"""§9 su 760859b: aggiornamento da dati reali 0.9.0, tempo di lettura e identità dei gruppi."""

import shutil
from pathlib import Path

import pytest

from sinapsidb import lingua, memoria, motore
from sinapsidb.memoria import Memoria, Rifiutato
from sinapsidb.motore import NonNumerico, Rete, istante
from test_revisione_circuiti_090 import aperta, circuito, evento, vendita
from test_validita import Orologio, orologio  # noqa: F401


STORICO = Path(__file__).parent / "dati" / "storico-24a5d89-circuiti"
PRONTA = "circuito vendite mensili"
DAI_FATTI = "in vendita somma totale per mese(quando)"


@pytest.fixture
def m(tmp_path):
    with aperta(Memoria(tmp_path / "rete", scritture=True)) as db:
        yield db


def mensile(r, nome, peso):
    n = r.n
    r.proponi_circuito("Mensile", "test", nome, [
        {"osserva": n("vendita"), "lega": n("quando"), "somma": n("totale"), "peso": peso, "per": "mese"}])
    return r.trova(nome)[-1]


@pytest.mark.parametrize("con_istantanea", [False, True], ids=["solo-giornale", "istantanea-090"])
def test_prima_scrittura_dopo_aggiornamento_preserva_documento_e_ricevuta(tmp_path, con_istantanea):
    cartella = shutil.copytree(STORICO / "istantanea", tmp_path / "rete")
    if not con_istantanea:
        (cartella / "istantanea").unlink()
    with aperta(Memoria(cartella, scritture=True)) as db:
        x = db.racconta("Prima vendita definitiva", [
            {"tipo": "vendita", "legami": {"quando": "2027-04-09", "totale": "20", "documento": "F-42"}}])
        assert x["esito"] == "applicato"
        f, = db.rete.scatta(db.rete.n("vendita"), [(db.rete.n("documento"), db.rete.n("F-42"))])
        dal_giornale, _ = Rete.com_era(str(cartella), evento(x) + 1)
        documento, = dal_giornale.trova("documento")
        assert dal_giornale.nomi[dal_giornale.leggi(f, documento)] == "F-42", db.verifica(x["evento"])
        assert db.rete.nomi == dal_giornale.nomi
        assert db.verifica(x["evento"])["esito"] == "coerente"


def test_ricevuta_storica_con_data_gia_rettificata_resta_verificabile(tmp_path):
    cartella = shutil.copytree(STORICO / "rettifica", tmp_path / "rete")
    prima = (cartella / "giornale").read_bytes()
    with aperta(Memoria(cartella)) as db:
        # In 0.9.0 la vendita è stata accettata e poi corretta. In testa c'è solo la data valida.
        assert db.chiedi("circuito mensile")["righe"] == [("2026-12", "10")]
        rettifica = db.ricevuta_di("rettifica")
        assert rettifica["esito"] == "applicato"
        assert db.verifica(rettifica["evento"])["esito"] == "coerente"
        assert db.riesamina(rettifica["evento"])["cambia"] is False
    assert (cartella / "giornale").read_bytes() == prima


def test_un_accredito_in_coda_con_una_definizione_non_autorizza_un_ordine_precedente(m, monkeypatch):
    arrivo, turno = Orologio(), Orologio()
    arrivo.metti("2026-11-16T08:00")
    turno.metti("2026-11-16T08:00")
    monkeypatch.setattr(memoria, "time", arrivo)
    monkeypatch.setattr(motore, "time", turno)
    saldo = circuito(m.rete, "saldo per cliente", per=None, lega="cliente")
    m.rete.proponi_regola("Il saldo non può diventare negativo", "test", "limite", 1, "vieta", [
        {"nome": "negativo", "circuito": saldo, "limite": 0, "peso": 1}])
    arrivo.metti("2026-11-16T09:00")
    turno.metti("2026-11-16T11:00")
    a = m.racconta("Accredito e definizione del mensile nella stessa scrittura", [
        {"tipo": "circuito", "legami": {"circuito di": "mensile"}},
        {"tipo": "termine", "legami": {"termine di": "mensile", "osserva": "vendita", "lega": "quando",
                                      "somma": "totale", "peso": "1", "raggruppa per": "mese"}},
        {"tipo": "vendita", "legami": {"cliente": "C", "quando": "2026-11-16", "totale": "70"}}])
    assert a["esito"] == "applicato" and m.rete.e_scritto[evento(a)] == turno.adesso
    arrivo.metti("2026-11-16T10:00")
    turno.metti("2026-11-16T11:01")
    ordine = vendita(m, "2026-11-16", "-60")
    # L'accredito non ha «vale da»: alle 10 c'erano 0, non 70. La regola legge il saldo, non il mensile.
    letture = ordine["decisione"]["regole"][0]["letture"]
    assert (ordine["esito"], [x["valore"] for x in letture], m.verifica(ordine["evento"])["esito"]) == (
        "violazione_regola", ["0", "-60"], "coerente")


def test_una_domanda_che_attraversa_la_mezzanotte_non_mescola_due_istanti(m, orologio, monkeypatch):
    circuito(m.rete)
    vendita(m, "2026-11-03", "70", **{"vale fino a": "2026-11-15"})
    vendita(m, "2026-11-04", "30", **{"vale da": "2026-11-15"})
    fine = istante("2026-11-15")
    # Il primo campionamento cade prima della mezzanotte; dal successivo la mezzanotte è passata.
    prima = "2026-11-14T23:59:59.999"
    orari = iter([istante(prima)])
    monkeypatch.setattr(orologio, "time", lambda: next(orari, fine))
    risposta = m.chiedi(PRONTA)
    assert risposta["lettura"]["valido_al"] == istante(prima)
    assert m.chiedi(DAI_FATTI + " come valeva il " + prima)["righe"] == [("2026-11", "70")]
    assert risposta["righe"] == [("2026-11", "70")]
    assert m.chiedi(PRONTA)["righe"] == [("2026-11", "30")]


def test_valori_e_valore_leggono_lo_stesso_adesso_se_l_orologio_arretra(m, orologio):
    circuito(m.rete)
    orologio.metti("2026-11-16T11:00")
    vendita(m, "2026-11-16", "70")
    orologio.metti("2026-11-16T10:00")
    assert m.valore("vendite mensili", "2026-11")["valore"] == "0"
    assert m.chiedi(PRONTA)["righe"] == []
    assert m.valori("vendite mensili") == []  # il mese esiste come nome ma nessuna vendita vale ancora


@pytest.mark.parametrize("per,nome,data,alternativa,gruppo", [
    ("mese", "mensile", "2026-11-22", "20261122", "2026-11"),
    ("anno", "annuale", "2021-01-01", "2020-W53-5", "2021"),
])
def test_il_periodo_di_una_data_accettata_e_di_calendario(m, per, nome, data, alternativa, gruppo):
    circuito(m.rete, nome, per=per)
    vendita(m, data, "10")
    prima = (m.cartella / "giornale").read_bytes()
    try:
        vendita(m, alternativa, "7")  # lo stesso giorno scritto in forma compatta o come data della settimana ISO
    except Rifiutato:  # anche ammettere solo YYYY-MM-DD è coerente, se deciso prima di scrivere
        assert (m.cartella / "giornale").read_bytes() == prima
        attese = [(gruppo, "10")]
    else:
        attese = [(gruppo, "17")]
    risposte = [m.chiedi(q)["righe"] for q in (f"circuito {nome}", f"in vendita somma totale per {per}(quando)")]
    assert risposte == [attese, attese]


def test_se_la_data_manca_somma_pronta_e_somma_sui_fatti_concordano(m):
    circuito(m.rete)
    vendita(m, "2026-11-01", "10")
    prima = (m.cartella / "giornale").read_bytes()
    try:
        x = m.racconta("Senza data", [{"tipo": "vendita", "legami": {"cliente": "C", "totale": "17"}}])
    except Rifiutato:
        assert (m.cartella / "giornale").read_bytes() == prima
    else:
        assert x["esito"] == "applicato"
    assert sorted(m.chiedi(PRONTA)["righe"]) == sorted(m.chiedi(DAI_FATTI)["righe"])


@pytest.mark.parametrize("seconda", ["2026-12-02", "2026-13-02"], ids=["due-mesi", "seconda-impossibile"])
def test_il_ruolo_ripetuto_non_crea_mesi_solo_alla_rilettura(m, seconda):
    r = m.rete
    c = circuito(r)
    n = r.n
    prima = (m.cartella / "giornale").read_bytes()
    segno = len(r.nomi)
    # Rete ammette più legami con lo stesso ruolo; qui «quando» può portare due mesi.
    try:
        e = r.racconta("Vendita con due date", "test", [(n("vendita"), [
            (n("quando"), n("2026-11-10")), (n("quando"), n(seconda)), (n("totale"), n("70"))])], neuroni_da=segno)
    except (NonNumerico, motore.DefinizioneNonValida):
        assert (m.cartella / "giornale").read_bytes() == prima and len(r.nomi) == segno
        return
    dal_giornale, _ = Rete.com_era(str(m.cartella), e + 1)
    assert dal_giornale.nomi == r.nomi
    assert dal_giornale.somme_pronte(c, al=r.e_scritto[e]) == r.somme_pronte(c, al=r.e_scritto[e])


def test_una_vista_con_due_circuiti_omonimi_chiede_l_id_anche_se_oggi_ne_resta_uno(m):
    r, n = m.rete, m.rete.n
    sinistro, destro = (mensile(r, "mensile", peso) for peso in (1, 2))
    e = evento(vendita(m, "2026-11-02", "10"))
    definizione, = r.scatta(n("circuito"), [(n("circuito di"), destro)])
    r.racconta("Ritira il secondo mensile", "test", ritira=[definizione])
    assert m.chiedi("circuito mensile")["righe"] == [("2026-11", "10")]
    allora = f" come risultava all'evento {e}"
    for i, atteso in ((sinistro, "10"), (destro, "20")):
        assert m.chiedi(f"circuito neurone:{i}" + allora)["righe"] == [("2026-11", atteso)]
    with pytest.raises(lingua.ErroreDomanda) as errore:
        m.chiedi("circuito mensile" + allora)
    assert all(f"neurone:{i}" in str(errore.value) for i in (sinistro, destro))


def test_l_identificativo_proposto_non_legge_un_terzo_circuito(m):
    primo = mensile(m.rete, "mensile", 1)
    secondo = mensile(m.rete, "mensile", 2)
    try:
        mensile(m.rete, f"neurone:{primo}", 9)  # un nome letterale identico all'identificativo del primo
    except motore.DefinizioneNonValida:
        pass  # anche riservare la sintassi dell'ID alla definizione va bene
    vendita(m, "2026-11-02", "10")
    with pytest.raises(lingua.ErroreDomanda) as errore:
        m.chiedi("circuito mensile")
    for i, atteso in ((primo, "10"), (secondo, "20")):
        frase = f"circuito neurone:{i}"
        assert frase in str(errore.value)
        assert m.valore(f"neurone:{i}", "2026-11")["valore"] == atteso
        assert m.chiedi(frase)["righe"] == [("2026-11", atteso)]


def test_il_divieto_di_una_regola_sul_mese_vale_anche_nella_prova(m):
    circuito(m.rete)
    proposta = [{"tipo": "regola", "legami": {"regola di": "limite mensile", "soglia": "1", "azione": "vieta"}},
                {"tipo": "segnale", "legami": {"segnale di": "limite mensile", "nome": "negativo", "modo": "sotto",
                                               "legge il circuito": "vendite mensili", "limite": "0", "peso": "1"}}]
    prima = (m.cartella / "giornale").read_bytes()
    for prova in (False, True):
        with pytest.raises(Rifiutato, match="raggruppato"):
            m.racconta("Non sotto zero per mese", proposta, prova=prova)
        assert (m.cartella / "giornale").read_bytes() == prima and not m.rete.regole


def test_il_confronto_col_giornale_controlla_anche_la_presenza_a_zero(m):
    circuito(m.rete)
    vendita(m, "2026-11-01", "0")
    r = m.rete
    f, = r.scatta(r.n("vendita"), [(r.n("quando"), r.n("2026-11-01"))])
    # Guasto simulato: la RAM perde l'unico fatto a zero e aggiorna il suo circuito.
    # Il confronto coi fatti in RAM passa: soltanto il giornale sa che novembre c'è.
    r._ritira(f, len(r.e_frase))
    assert r.verifica_circuiti() == []
    verifica = m.verifica_circuiti()
    assert verifica["esito"] == "guasto"
    assert any(d["contro"] == "il giornale" and d["di"] == "2026-11" for d in verifica["differenze"])
