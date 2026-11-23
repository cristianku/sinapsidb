"""Test della memoria: la rete vera, a cui si racconta e a cui si chiede.

Gli ultimi quattro sono il collaudo proposto in revisione prima di accendere le scritture: i casi in cui chi
racconta ha capito bene la frase e scriverebbe comunque il dato sbagliato.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sinapsidb import memoria as modulo
from sinapsidb.lingua import ErroreDomanda
from sinapsidb.memoria import Memoria, Rifiutato, SoloLettura

VOLO = {"tipo": "viaggio", "soggetto": "chi", "legami": {"chi": "Greta", "verso": "Polonia", "quando": "22 novembre"}}
FRASE = "Greta il 22 novembre è volata verso la Polonia"
VUOTA = {"neuroni": 0, "fatti": 0, "fatti_in_vigore": 0, "sinapsi": 0, "eventi": 0}
RIGA_417 = {"tipo": "riga d'ordine", "indizi": {"ordine": "417", "prodotto": "pickup"}}


def riga(quantita: int, ordine: str = "417", prodotto: str = "pickup") -> dict:
    return {"tipo": "riga d'ordine", "soggetto": "ordine", "legami": {"ordine": ordine, "prodotto": prodotto, "quantità": quantita}}


@pytest.fixture()
def m(tmp_path: Path) -> Memoria:
    memoria = Memoria(tmp_path / "rete", scritture=True)
    yield memoria
    memoria.chiudi()


def _righe(m: Memoria, domanda: str) -> list[tuple[str, ...]]:
    return sorted(tuple(r) for r in m.chiedi(domanda)["righe"])


def _fatti(voci: list[dict]) -> list[str]:
    return [v["fatto"] for v in voci]


def test_raccontare_e_chiedere(m: Memoria, tmp_path: Path) -> None:
    r = m.racconta(FRASE, [VOLO], chi="Nora")
    assert r["esito"] == "applicato" and r["evento"] == "evento:0" and r["scritture_applicate"] == 1
    assert r["creati"] == [{"id": "fatto:0", "fatto": "viaggio (chi: Greta, verso: Polonia, quando: 22 novembre)"}] and r["ritirati"] == []
    # con il primo racconto nascono il tipo, i ruoli e i nomi: la ricevuta lo dice, con gli identificativi
    assert r["tipi_nuovi"] == ["viaggio"] and r["ruoli_nuovi"] == ["chi", "quando", "verso"]
    assert sorted(n["nome"] for n in r["neuroni_nuovi"]) == ["22 novembre", "Greta", "Polonia", "chi", "quando", "verso", "viaggio"]
    assert all(n["id"].startswith("neurone:") for n in r["neuroni_nuovi"])
    assert r["avvisi"] == [] and r["gia_presenti"] == []
    # la data senza anno resta com'è stata detta, e la frase si ritrova così com'era
    assert _righe(m, "accendi verso = Polonia in viaggio leggi chi, quando, frase") == [("Greta", "22 novembre", FRASE)]
    assert _righe(m, "accendi chi = greta leggi tipo, verso") == [("viaggio", "Polonia")]
    assert m.rete.e_origine == ["Nora"]

    # un secondo racconto riusa quello che c'è: nascono solo i nomi nuovi
    r = m.racconta("Marco è andato in Polonia a marzo", [{"tipo": "viaggio", "legami": {"chi": "Marco", "verso": "Polonia", "quando": "marzo"}}])
    assert r["esito"] == "applicato" and sorted(n["nome"] for n in r["neuroni_nuovi"]) == ["Marco", "marzo"]
    assert r["tipi_nuovi"] == [] and r["ruoli_nuovi"] == []
    assert _righe(m, "accendi verso = Polonia in viaggio leggi chi") == [("Greta",), ("Marco",)]
    assert m.conteggi()["fatti"] == 2 and m.conteggi()["eventi"] == 2

    # chiusa e riaperta dal disco, la memoria è la stessa
    m.chiudi()
    riaperta = Memoria(tmp_path / "rete", scritture=False)
    assert _righe(riaperta, "accendi verso = Polonia in viaggio leggi chi, frase") == [("Greta", FRASE), ("Marco", "Marco è andato in Polonia a marzo")]
    assert _righe(riaperta, "vocabolario") == [("viaggio", "chi, verso, quando", "2")]
    riaperta.chiudi()


def test_il_soggetto_va_al_primo_posto_comunque_siano_scritti_i_legami(m: Memoria) -> None:
    m.racconta(FRASE, [{"tipo": "viaggio", "soggetto": "chi", "legami": {"quando": "22 novembre", "verso": "Polonia", "chi": "Greta"}}])
    assert _righe(m, "vocabolario") == [("viaggio", "chi, quando, verso", "1")]
    with pytest.raises(Rifiutato, match="non è tra i ruoli"):
        m.racconta(FRASE, [{"tipo": "viaggio", "soggetto": "passeggero", "legami": {"chi": "Greta"}}])
    # senza soggetto vale il primo legame
    m.racconta("Il pickup costa 89", [{"tipo": "prezzo", "legami": {"prodotto": "pickup", "importo": 89}}])
    assert ("prezzo", "prodotto, importo", "1") in _righe(m, "vocabolario")


def test_lo_stesso_fatto_non_si_scrive_due_volte(m: Memoria) -> None:
    assert m.racconta(FRASE, [VOLO])["esito"] == "applicato"
    r = m.racconta("Greta è volata in Polonia il 22 novembre", [VOLO])
    assert r["esito"] == "nessun_cambiamento" and r["scritture_applicate"] == 0 and "nuova_occorrenza" in r["spiegazione"]
    assert r["gia_presenti"] == [{"id": "fatto:0", "fatto": "viaggio (chi: Greta, verso: Polonia, quando: 22 novembre)", "frase": FRASE}]
    assert m.conteggi()["fatti"] == 1 and m.conteggi()["eventi"] == 1
    # un fatto che ha qualcosa in più o in meno non è lo stesso fatto
    assert m.racconta("Greta è volata in Polonia", [{"tipo": "viaggio", "legami": {"chi": "Greta", "verso": "Polonia"}}])["esito"] == "applicato"
    # in un racconto con un fatto nuovo e uno che c'è già, si scrive solo quello nuovo
    r = m.racconta("Greta è volata in Polonia e poi a Praga", [VOLO, {"tipo": "viaggio", "legami": {"chi": "Greta", "verso": "Praga"}}])
    assert r["esito"] == "applicato" and _fatti(r["creati"]) == ["viaggio (chi: Greta, verso: Praga)"] and len(r["gia_presenti"]) == 1
    assert m.conteggi()["fatti"] == 3


def test_un_altro_accadimento_non_e_lo_stesso_raccontato_di_nuovo(m: Memoria) -> None:
    """«Greta ha fatto un altro volo verso la Polonia»: stessi legami, ma è un secondo volo."""
    senza_data = {"tipo": "viaggio", "soggetto": "chi", "legami": {"chi": "Greta", "verso": "Polonia"}}
    assert m.racconta("Greta è volata in Polonia", [senza_data])["esito"] == "applicato"
    assert m.racconta("Greta è volata in Polonia", [senza_data])["esito"] == "nessun_cambiamento"
    r = m.racconta("Greta ha fatto un altro volo verso la Polonia", [{**senza_data, "nuova_occorrenza": True}])
    assert r["esito"] == "applicato" and _fatti(r["creati"]) == ["viaggio (chi: Greta, verso: Polonia)"] and r["gia_presenti"] == []
    assert _righe(m, "accendi chi = Greta in viaggio conta") == [("2",)]
    assert _righe(m, "accendi chi = Greta in viaggio leggi frase") == [("Greta ha fatto un altro volo verso la Polonia",), ("Greta è volata in Polonia",)]


def test_una_data_senza_anno_resta_parziale_ma_si_trova_per_mese(m: Memoria) -> None:
    """La frase dice «22 novembre»: l'anno non si inventa. La data parziale è un fatto in più, con giorno e mese."""
    data = {"tipo": "data parziale", "soggetto": "data", "legami": {"data": "22 novembre", "giorno": 22, "mese": 11}}
    assert m.racconta(FRASE, [VOLO, data])["esito"] == "applicato"
    m.racconta("Marco è volato in Polonia il 3 novembre 2026", [{"tipo": "viaggio", "soggetto": "chi", "legami": {"chi": "Marco", "verso": "Polonia", "quando": "2026-11-03"}}])
    # tutti i voli di novembre, di qualsiasi anno: si passa dalla data parziale
    assert _righe(m, "accendi mese = 11 in data parziale leggi data poi in viaggio come quando leggi chi, quando") == [("Greta", "22 novembre")]
    # novembre 2026: solo il volo di cui l'anno è stato detto
    assert _righe(m, "in viaggio conta per mese(quando)") == [("2026-11", "1"), ("22 nove", "1")]
    assert _righe(m, "accendi quando = 2026-11-03 in viaggio leggi chi") == [("Marco",)]


def test_la_chiave_contro_i_doppioni(m: Memoria) -> None:
    prima = m.racconta(FRASE, [VOLO], chiave="r-250")
    assert prima["esito"] == "applicato" and "ripetuta" not in prima
    # la stessa richiesta ripetuta (la risposta si era persa) restituisce la ricevuta di prima, senza scrivere
    assert m.racconta(FRASE, [VOLO], chiave="r-250") == {**prima, "ripetuta": True} and m.conteggi()["eventi"] == 1
    # anche se chi ritenta scrive i legami in un altro ordine: è lo stesso racconto
    rimescolato = {"tipo": "viaggio", "soggetto": "chi", "legami": {"quando": "22 novembre", "chi": "Greta", "verso": "Polonia"}}
    assert m.racconta(FRASE, [rimescolato], chiave="r-250") == {**prima, "ripetuta": True}
    # la stessa chiave per un racconto diverso è un conflitto
    r = m.racconta("Marco è andato in Polonia", [{"tipo": "viaggio", "legami": {"chi": "Marco", "verso": "Polonia"}}], chiave="r-250")
    assert r["esito"] == "conflitto" and r["scritture_applicate"] == 0 and m.conteggi()["eventi"] == 1
    # un chiarimento non consuma la chiave: dopo aver scelto, la stessa chiave scrive
    m.racconta("Un'altra Greta lavora a Lugano", [{"tipo": "lavoro", "legami": {"chi": "Greta", "dove": "Lugano"}}], nuovi=["Greta"])
    volo = {"tipo": "viaggio", "legami": {"chi": "Greta", "verso": "Praga"}}
    dubbio = m.racconta("Greta è andata a Praga", [volo], chiave="r-251")
    assert dubbio["esito"] == "da_chiarire"
    prima_greta = dubbio["nomi"][0]["candidati"][0]["id"]
    scelta = m.racconta("Greta è andata a Praga", [{"tipo": "viaggio", "legami": {"chi": prima_greta, "verso": "Praga"}}], chiave="r-251")
    assert scelta["esito"] == "applicato" and _righe(m, f"accendi chi = {prima_greta} in viaggio leggi verso") == [("Polonia",), ("Praga",)]


def test_correggere_ritira_il_fatto_di_prima(m: Memoria) -> None:
    m.racconta("L'ordine 417 contiene due pickup", [riga(2)])
    r = m.racconta("Porta a tre i pickup dell'ordine 417", [riga(3)], sostituisce=[RIGA_417])
    assert r["esito"] == "applicato" and r["scritture_applicate"] == 2
    assert r["ritirati"] == [{"id": "fatto:0", "fatto": "riga d'ordine (ordine: 417, prodotto: pickup, quantità: 2)",
                              "frase": "L'ordine 417 contiene due pickup"}]  # il prima
    assert r["creati"] == [{"id": "fatto:1", "fatto": "riga d'ordine (ordine: 417, prodotto: pickup, quantità: 3)"}]  # il dopo
    assert _righe(m, "accendi ordine = 417 e prodotto = pickup in riga d'ordine leggi quantità, id_fatto, id_evento") == [("3", "fatto:1", "evento:1")]
    # niente si cancella: il valore di prima resta nella rete, con l'evento che l'ha scritto e quello che l'ha ritirato
    rete = m.rete
    storia = rete.storia(rete.n("riga d'ordine"), rete.n("ordine"), rete.n("417"), rete.n("quantità"))
    assert [(rete.nomi[v], nato, morto) for v, nato, morto in storia] == [("2", 0, 1), ("3", 1, -1)]
    assert rete.e_frase == ["L'ordine 417 contiene due pickup", "Porta a tre i pickup dell'ordine 417"]
    # impostare a tre quando è già tre non cambia niente... se chi racconta non chiede di ritirare e riscrivere
    assert m.racconta("I pickup dell'ordine 417 sono tre", [riga(3)])["esito"] == "nessun_cambiamento"
    # si può anche solo ritirare: «non è più vero»
    r = m.racconta("L'ordine 417 non contiene più pickup", sostituisce=[RIGA_417])
    assert r["esito"] == "applicato" and r["creati"] == [] and len(r["ritirati"]) == 1
    assert _righe(m, "accendi ordine = 417 in riga d'ordine leggi quantità") == []
    # sostituire qualcosa che non c'è, con nomi che la rete conosce, non è un errore: resta solo il fatto nuovo
    r = m.racconta("L'ordine 500 contiene un ponte", [riga(1, "500", "ponte")], sostituisce=[{"tipo": "riga d'ordine", "indizi": {"ordine": "500", "prodotto": "ponte"}}])
    assert r["esito"] == "applicato" and r["ritirati"] == [] and len(r["creati"]) == 1


def test_un_ruolo_che_somiglia_a_uno_che_c_e_gia(m: Memoria) -> None:
    m.racconta(FRASE, [{"tipo": "viaggio", "legami": {"viaggiatrice": "Greta", "destinazione": "Polonia"}}])
    # in un'aggiunta un tipo o un ruolo nuovo può nascere: la ricevuta avvisa se somiglia a uno che c'è già
    r = m.racconta("Marco è andato a Praga", [{"tipo": "viaggi", "legami": {"viaggiatore": "Marco", "destinazione": "Praga"}}], prova=True)
    assert r["esito"] == "prova" and r["tipi_nuovi"] == ["viaggi"] and r["ruoli_nuovi"] == ["viaggiatore"]
    assert len(r["avvisi"]) == 2 and "«viaggio»" in r["avvisi"][0] and "«viaggiatrice»" in r["avvisi"][1]
    # un ruolo del tutto diverso è nuovo e basta: nessun avviso
    r = m.racconta("Il pickup ha l'avvolgimento da 42 AWG", [{"tipo": "scheda tecnica", "legami": {"prodotto": "pickup", "calibro avvolgimento": "42 AWG"}}])
    assert r["esito"] == "applicato" and r["ruoli_nuovi"] == ["calibro avvolgimento", "prodotto"] and r["avvisi"] == []


@pytest.mark.parametrize(
    ("argomenti", "dice"),
    [
        ({"frase": "", "fatti": [VOLO]}, "la frase"),
        ({"frase": "x" * 2001, "fatti": [VOLO]}, "troppo lungo"),
        ({"frase": FRASE, "fatti": []}, "non contiene fatti"),
        ({"frase": FRASE, "fatti": [{"tipo": "viaggio"}]}, "deve avere «tipo» e «legami»"),
        ({"frase": FRASE, "fatti": [{"tipo": "viaggio", "legami": {}}]}, "nessun partecipante"),
        ({"frase": FRASE, "fatti": [{"tipo": "", "legami": {"chi": "Greta"}}]}, "il tipo"),
        ({"frase": FRASE, "fatti": [{"tipo": "viaggio", "legami": {"chi": ""}}]}, "il nome nel ruolo «chi»"),
        ({"frase": FRASE, "fatti": [{"tipo": "viaggio", "legami": {"chi": "Greta", "Chi": "Marco"}}]}, "lo stesso ruolo"),
        ({"frase": FRASE, "fatti": [{"tipo": "viaggio", "legami": {"chi": ["Greta"]}}]}, "il nome nel ruolo «chi»"),
        ({"frase": FRASE, "fatti": [VOLO] * 21}, "troppi fatti"),
        ({"frase": FRASE, "fatti": [{"tipo": "viaggio", "legami": {f"r{i}": "x" for i in range(17)}}]}, "troppi partecipanti"),
        ({"frase": FRASE, "fatti": [VOLO], "sostituisce": [{"tipo": "viaggio", "legami": {"chi": "Greta"}}]}, "«indizi»"),
        ({"frase": FRASE, "fatti": [VOLO], "sostituisce": [{"tipo": "viaggio", "indizi": {"chi": "Greta"}, "attesi": ["51"]}]}, "fatto:51"),
        ({"frase": FRASE, "fatti": [VOLO], "chiave": "   "}, "la chiave"),
        ({"frase": FRASE, "fatti": [{"tipo": "viaggio", "legami": {"chi": "neurone:99999"}}]}, "non esiste"),
    ],
)
def test_racconti_che_non_stanno_in_piedi(m: Memoria, argomenti: dict, dice: str) -> None:
    with pytest.raises(Rifiutato) as e:
        m.racconta(**argomenti)
    assert dice in str(e.value)
    assert m.conteggi() == VUOTA


def test_una_regola_della_rete_vieta_la_scrittura(m: Memoria) -> None:
    """Le regole non si possono ancora raccontare dal connettore, ma se nella rete ce n'è una vale anche qui."""
    rete = m.rete
    n = rete.n
    rete.proponi_regola("Un ordine confermato non può essere annullato", "prova", "confermato non annullabile", 2, "vieta", [
        {"nome": "confermato", "modo": "presenza", "osserva": n("stato dell'ordine"), "lega": n("ordine"), "peso": 1, "con_ruolo": n("stato"), "con_valore": n("confermato")},
        {"nome": "annullato", "modo": "presenza", "osserva": n("annullamento"), "lega": n("ordine"), "peso": 1},
    ])
    assert m.racconta("L'ordine 417 è confermato", [{"tipo": "stato dell'ordine", "legami": {"ordine": "417", "stato": "confermato"}}])["esito"] == "applicato"
    eventi, neuroni = m.conteggi()["eventi"], m.conteggi()["neuroni"]
    r = m.racconta("Annulla l'ordine 417 per il motivo X", [{"tipo": "annullamento", "legami": {"ordine": "417", "motivo": "motivo X"}}])
    assert r["esito"] == "violazione_regola" and r["scritture_applicate"] == 0
    assert r["regole"][0]["regola"] == "confermato non annullabile" and r["regole"][0]["su"] == "417"
    assert _righe(m, "accendi ordine = 417 leggi tipo") == [("stato dell'ordine",)]
    assert m.conteggi()["eventi"] == eventi + 1 and m.rete.e_tipo[-1] == "rifiuto"  # il tentativo resta scritto come rifiuto
    assert m.conteggi()["neuroni"] == neuroni and m.rete.trova("motivo X") == []  # ma i nomi del racconto rifiutato non restano
    # un ordine non confermato si può annullare
    assert m.racconta("Annulla l'ordine 500", [{"tipo": "annullamento", "legami": {"ordine": "500"}}])["esito"] == "applicato"


def test_limiti_di_chiedi(m: Memoria) -> None:
    for i in range(30):
        m.racconta(f"Viaggio numero {i}", [{"tipo": "viaggio", "legami": {"chi": f"persona {i}", "verso": "Polonia"}}])
    assert m.chiedi("accendi verso = Polonia in viaggio leggi chi")["quante"] == 30
    r = m.chiedi("accendi verso = Polonia in viaggio leggi chi", limite=5)
    assert r["quante"] == 5 and r["troncata"]
    assert m.chiedi("accendi verso = Polonia in viaggio leggi chi", limite=100000)["quante"] == 30  # il limite non supera duecento
    with pytest.raises(ErroreDomanda, match="troppo lunga"):
        m.chiedi("accendi verso = " + "x" * modulo.MASSIMO_FRASE + " in viaggio leggi chi")
    with pytest.raises(ErroreDomanda, match="non è un tipo di fatto"):
        m.chiedi("accendi verso = Polonia in viagio leggi chi")
    with pytest.raises(ErroreDomanda, match="non c'è nei fatti «viaggio»"):
        m.chiedi("accendi verso = Polonia in viaggio leggi passeggero")  # un ruolo che non c'è non è una risposta vuota


# --- il collaudo della revisione ---------------------------------------------


def test_collaudo_chiarisco_greta_una_volta_e_riuso_il_suo_identificativo(m: Memoria) -> None:
    m.racconta("Greta K. lavora a Varsavia", [{"tipo": "lavoro", "legami": {"chi": "Greta", "dove": "Varsavia"}}])
    # un'altra Greta: va detto, altrimenti sarebbe la stessa
    r = m.racconta("C'è anche un'altra Greta, che lavora a Lugano", [{"tipo": "lavoro", "legami": {"chi": "Greta", "dove": "Lugano"}}], nuovi=["Greta"])
    assert r["esito"] == "applicato" and sorted(n["nome"] for n in r["neuroni_nuovi"]) == ["Greta", "Lugano"]
    # da qui in poi «Greta» non basta più: nessuna scrittura, e la ricevuta dice dove sta il dubbio e chi sono le due
    r = m.racconta(FRASE, [VOLO])
    assert r["esito"] == "da_chiarire" and r["scritture_applicate"] == 0 and m.conteggi()["eventi"] == 2
    (dubbio,) = r["nomi"]
    assert dubbio["nome"] == "Greta" and dubbio["campi"] == ["fatti[0].legami.chi"]
    assert [c["fatti"] for c in dubbio["candidati"]] == [["lavoro (chi: Greta, dove: Varsavia)"], ["lavoro (chi: Greta, dove: Lugano)"]]
    di_lugano = dubbio["candidati"][1]["id"]
    assert di_lugano.startswith("neurone:")
    # stessa frase, cambia solo il riferimento: un solo volo, della persona giusta
    r = m.racconta(FRASE, [{**VOLO, "legami": {**VOLO["legami"], "chi": di_lugano}}])
    assert r["esito"] == "applicato" and m.conteggi()["fatti"] == 3
    # lo stesso identificativo vale per chiedere
    assert _righe(m, f"accendi chi = {di_lugano} in viaggio leggi verso, frase") == [("Polonia", FRASE)]
    assert _righe(m, f"accendi chi = {di_lugano} leggi tipo, id_fatto, id_evento") == [("lavoro", "fatto:1", "evento:1"), ("viaggio", "fatto:2", "evento:2")]
    assert _righe(m, f"accendi chi = {dubbio['candidati'][0]['id']} in viaggio leggi verso") == []
    with pytest.raises(ErroreDomanda, match="indica 2 cose diverse"):
        m.chiedi("accendi chi = Greta in viaggio leggi verso")
    with pytest.raises(ErroreDomanda, match="non esiste"):
        m.chiedi("accendi chi = neurone:99999 in viaggio leggi verso")
    # se lo stesso nome compare in due posti, la ricevuta li dice tutti e due
    r = m.racconta("Greta ha salutato Greta", [{"tipo": "saluto", "legami": {"chi": "Greta", "a chi": "Greta"}}])
    assert r["nomi"][0]["campi"] == ["fatti[0].legami.chi", "fatti[0].legami.a chi"]


def test_collaudo_simulo_un_omonima_senza_crearla(m: Memoria, tmp_path: Path) -> None:
    m.racconta("Greta lavora a Varsavia", [{"tipo": "lavoro", "legami": {"chi": "Greta", "dove": "Varsavia"}}])
    prima = m.conteggi()
    altra = {"tipo": "lavoro", "legami": {"chi": "Greta", "dove": "Lugano"}}
    r = m.racconta("Un'altra Greta lavora a Lugano", [altra], nuovi=["Greta"], prova=True)
    assert r["esito"] == "prova" and r["scritture_applicate"] == 0
    assert _fatti(r["creerebbe"]) == ["lavoro (chi: Greta, dove: Lugano)"] and r["creerebbe"][0]["id"] is None
    assert sorted(n["nome"] for n in r["neuroni_nuovi"]) == ["Greta", "Lugano"] and all(n["id"] is None for n in r["neuroni_nuovi"])
    # dopo la prova c'è ancora una sola Greta, e niente è cambiato
    assert m.conteggi() == prima and len(m.rete.trova("Greta")) == 1 and m.rete.trova("Lugano") == []
    assert m.racconta("Greta è andata a Praga", [{"tipo": "viaggio", "legami": {"chi": "Greta", "verso": "Praga"}}])["esito"] == "applicato"

    # lo stesso con una regola attiva nella rete, che obbliga a valutare la prova sul serio
    rete = m.rete
    n = rete.n
    rete.proponi_regola("Chi lavora a Lugano non può lavorare a Varsavia", "prova", "una sede sola", 2, "vieta", [
        {"nome": "a Lugano", "modo": "presenza", "osserva": n("lavoro"), "lega": n("chi"), "peso": 1, "con_ruolo": n("dove"), "con_valore": n("Lugano")},
        {"nome": "a Varsavia", "modo": "presenza", "osserva": n("lavoro"), "lega": n("chi"), "peso": 1, "con_ruolo": n("dove"), "con_valore": n("Varsavia")},
    ])
    prima = m.conteggi()
    r = m.racconta("Un'altra Greta lavora a Lugano con Zoe", [altra, {"tipo": "collega", "legami": {"chi": "Greta", "di": "Zoe"}}], nuovi=["Greta"], prova=True)
    assert r["esito"] == "prova" and r["regole_scattate"] == [] and r["tipi_nuovi"] == ["collega"]
    assert m.conteggi() == prima and len(m.rete.trova("Greta")) == 1 and m.rete.trova("Zoe") == [] and m.rete.trova("collega") == []
    # e la prova dice anche quando una regola scatterebbe
    r = m.racconta("Greta lavora anche a Lugano", [altra], prova=True)
    assert r["esito"] == "prova" and r["regole_scattate"][0]["regola"] == "una sede sola" and m.conteggi() == prima
    # riaperta dal disco la rete è quella di prima delle prove: le prove non hanno lasciato niente nemmeno nel giornale
    m.chiudi()
    riaperta = Memoria(tmp_path / "rete", scritture=True)
    assert riaperta.conteggi() == prima and len(riaperta.rete.trova("Greta")) == 1
    riaperta.chiudi()


def test_collaudo_ritento_dopo_un_riavvio_senza_duplicare(m: Memoria, tmp_path: Path) -> None:
    m.racconta("Greta lavora a Varsavia", [{"tipo": "lavoro", "legami": {"chi": "Greta", "dove": "Varsavia"}}])
    altra = {"tipo": "lavoro", "legami": {"chi": "Greta", "dove": "Lugano"}}
    prima = m.racconta("Un'altra Greta lavora a Lugano", [altra], nuovi=["Greta"], chiave="r-300")
    assert prima["esito"] == "applicato" and len(m.rete.trova("Greta")) == 2
    conteggi = m.conteggi()
    # il processo riparte: la chiave e la ricevuta stavano nel giornale insieme ai fatti, quindi si ritrovano
    m.chiudi()
    for giro in range(2):
        riaperta = Memoria(tmp_path / "rete", scritture=True)
        assert riaperta.racconta("Un'altra Greta lavora a Lugano", [altra], nuovi=["Greta"], chiave="r-300") == {**prima, "ripetuta": True}
        assert riaperta.conteggi() == conteggi and len(riaperta.rete.trova("Greta")) == 2  # non è nata una terza Greta
        r = riaperta.racconta("Tutt'altro", [{"tipo": "viaggio", "legami": {"chi": "Marco", "verso": "Praga"}}], chiave="r-300")
        assert r["esito"] == "conflitto" and riaperta.conteggi() == conteggi
        if giro == 0:
            riaperta.rete.istantanea()  # al secondo giro si riparte dall'istantanea: le chiavi devono esserci anche lì
        riaperta.chiudi()
    # un giornale tagliato a metà dell'ultimo evento: o c'è tutto (fatti, chiave e ricevuta) o non c'è niente
    giornale = tmp_path / "rete" / "giornale"
    (tmp_path / "rete" / "istantanea").unlink()
    giornale.write_bytes(giornale.read_bytes()[:-5])
    tagliata = Memoria(tmp_path / "rete", scritture=True)
    assert len(tagliata.rete.trova("Greta")) == 1 and tagliata.conteggi()["eventi"] == 1
    assert tagliata.racconta("Un'altra Greta lavora a Lugano", [altra], nuovi=["Greta"], chiave="r-300")["esito"] == "applicato"
    assert len(tagliata.rete.trova("Greta")) == 2
    tagliata.chiudi()


def test_collaudo_sostituisco_una_riga_cambiata_nel_frattempo_e_vengo_fermato(m: Memoria) -> None:
    m.racconta("L'ordine 417 contiene due pickup", [riga(2)])
    # chi racconta prova la correzione e vede quale fatto ritirerebbe
    prova = m.racconta("Porta a tre i pickup dell'ordine 417", [riga(3)], sostituisce=[RIGA_417], prova=True)
    assert prova["esito"] == "prova" and [x["id"] for x in prova["ritirerebbe"]] == ["fatto:0"]
    # nel frattempo il magazzino aggiunge una seconda riga di pickup allo stesso ordine
    assert m.racconta("Il magazzino aggiunge all'ordine 417 una riga con un pickup", [{**riga(1), "nuova_occorrenza": True}], chi="magazzino")["esito"] == "applicato"
    prima = m.conteggi()
    # la correzione che si aspettava una riga sola viene fermata: non ritira due righe al posto di una
    r = m.racconta("Porta a tre i pickup dell'ordine 417", [riga(3)], sostituisce=[{**RIGA_417, "attesi": ["fatto:0"]}])
    assert r["esito"] == "conflitto" and r["scritture_applicate"] == 0 and [x["id"] for x in r["trovati"]] == ["fatto:0", "fatto:1"]
    assert m.conteggi() == prima
    # anche senza dire quali si aspetta, il motore non sceglie: chiede
    r = m.racconta("Porta a tre i pickup dell'ordine 417", [riga(3)], sostituisce=[RIGA_417])
    assert r["esito"] == "da_chiarire" and r["scritture_applicate"] == 0 and m.conteggi() == prima
    (dubbio,) = r["sostituzioni"]
    assert dubbio["campo"] == "sostituisce[0]" and [c["id"] for c in dubbio["candidati"]] == ["fatto:0", "fatto:1"]
    assert [c["frase"] for c in dubbio["candidati"]] == ["L'ordine 417 contiene due pickup", "Il magazzino aggiunge all'ordine 417 una riga con un pickup"]
    # si può indicare la riga giusta...
    r = m.racconta("Porta a tre i pickup della prima riga dell'ordine 417", [riga(3)], sostituisce=[{**RIGA_417, "attesi": ["fatto:0", "fatto:1"]}], prova=True)
    assert r["esito"] == "prova" and len(r["ritirerebbe"]) == 2
    # ...oppure dire che vanno ritirate tutte: «in tutto i pickup dell'ordine 417 sono tre»
    r = m.racconta("In tutto i pickup dell'ordine 417 sono tre", [riga(3)], sostituisce=[{**RIGA_417, "tutti": True}])
    assert r["esito"] == "applicato" and len(r["ritirati"]) == 2 and len(r["creati"]) == 1
    assert _righe(m, "accendi ordine = 417 e prodotto = pickup in riga d'ordine leggi quantità") == [("3",)]

    # un refuso negli indizi di una correzione non trova niente: si ritirerebbe nulla e il valore nuovo si aggiungerebbe al vecchio
    prima = m.conteggi()
    r = m.racconta("Porta a quattro i pickup dell'ordine 417", [riga(4)], sostituisce=[{"tipo": "riga d'ordine", "indizi": {"ordne": "417", "prodotto": "pickup"}}])
    assert r["esito"] == "da_chiarire" and m.conteggi() == prima
    assert r["vocabolario"] == [{"campo": "sostituisce[0].indizi.ordne", "nome": "ordne", "che": "ruolo dei fatti «riga d'ordine»", "forse": ["ordine"]}]
    # lo stesso per un refuso nel ruolo del fatto nuovo, in una correzione
    quantia = {"tipo": "riga d'ordine", "legami": {"ordine": "417", "prodotto": "pickup", "quantià": 4}}
    r = m.racconta("Porta a quattro i pickup dell'ordine 417", [quantia], sostituisce=[RIGA_417])
    assert r["esito"] == "da_chiarire" and m.conteggi() == prima
    assert r["vocabolario"] == [{"campo": "fatti[0].legami.quantià", "nome": "quantià", "che": "ruolo", "forse": ["quantità"]}]
    # se invece il ruolo nuovo è voluto, lo si conferma
    assert m.racconta("Porta a quattro i pickup dell'ordine 417", [quantia], sostituisce=[RIGA_417], accetta_nuovi=True, prova=True)["esito"] == "prova"


def test_collaudo_417_due_rimozioni_insieme_un_solo_rifiuto_e_la_stessa_ricevuta_al_retry(m: Memoria, tmp_path: Path) -> None:
    """L'ordine 417 è confermato e ha due righe. Due richieste insieme tolgono ciascuna una riga: una passa, l'altra
    lascerebbe vuoto un ordine confermato e viene rifiutata. Il processo cade prima di consegnare le ricevute; chi
    ritenta con la stessa chiave deve ritrovare lo stesso rifiuto, una volta sola, anche se nel frattempo lo stato è
    cambiato. È l'esperimento fatto in revisione su una versione precedente, dove il rifiuto veniva rivalutato."""
    import threading

    rete = m.rete
    n = rete.n
    rete.proponi_regola("Un ordine confermato non può restare vuoto", "Nora", "ordine non vuoto", 2, "vieta", [
        {"nome": "confermato", "modo": "presenza", "osserva": n("stato dell'ordine"), "lega": n("ordine"), "peso": 1, "con_ruolo": n("stato"), "con_valore": n("confermato")},
        {"nome": "senza righe", "modo": "assenza", "osserva": n("riga d'ordine"), "lega": n("ordine"), "peso": 1}])
    assert m.racconta("L'ordine 417 contiene un pickup", [riga(1)])["esito"] == "applicato"
    assert m.racconta("L'ordine 417 contiene un ponte", [riga(1, prodotto="ponte")])["esito"] == "applicato"
    assert m.racconta("L'ordine 417 è confermato", [{"tipo": "stato dell'ordine", "legami": {"ordine": "417", "stato": "confermato"}}])["esito"] == "applicato"

    def richiesta(prodotto: str) -> dict:
        return {"frase": f"Togli {prodotto} dall'ordine 417", "chiave": f"togli-{prodotto}",
                "sostituisce": [{"tipo": "riga d'ordine", "indizi": {"ordine": "417", "prodotto": prodotto}}]}

    via = threading.Barrier(2)
    ricevute: dict[str, dict] = {}

    def togli(prodotto: str) -> None:
        via.wait()
        ricevute[prodotto] = m.racconta(**richiesta(prodotto))

    fili = [threading.Thread(target=togli, args=(p,)) for p in ("pickup", "ponte")]
    for f in fili:
        f.start()
    for f in fili:
        f.join(10)
    # un solo scrittore: le due richieste sono passate una dopo l'altra, e la seconda ha visto lo stato lasciato dalla prima
    assert sorted(r["esito"] for r in ricevute.values()) == ["applicato", "violazione_regola"]
    rifiutato = next(p for p, r in ricevute.items() if r["esito"] == "violazione_regola")
    rifiuto = ricevute[rifiutato]
    assert rifiuto["scritture_applicate"] == 0 and rifiuto["evento"].startswith("evento:")
    assert rifiuto["regole"][0]["regola"] == "ordine non vuoto" and rifiuto["regole"][0]["su"] == "417" and rifiuto["regole"][0]["attivazione"] == 2
    assert _righe(m, "accendi ordine = 417 in riga d'ordine leggi prodotto") == [(rifiutato,)]  # un ordine confermato con una riga
    assert rete.e_tipo.count("rifiuto") == 1

    # Il processo cade dopo il rifiuto durevole, prima di rispondere: si riparte dal giornale, senza aver chiuso niente.
    # Di un processo caduto il sistema lascia libero il giornale, che uno solo può tenere aperto per scrivere: qui il
    # processo è lo stesso, e quel rilascio si fa a mano. Nient'altro viene chiuso né messo al sicuro.
    import fcntl

    fcntl.flock(m.rete._fd, fcntl.LOCK_UN)
    dopo = Memoria(tmp_path / "rete", scritture=True)
    assert dopo.racconta(**richiesta(rifiutato)) == {**rifiuto, "ripetuta": True}
    assert dopo.racconta(**richiesta(rifiutato)) == {**rifiuto, "ripetuta": True}
    assert dopo.rete.e_tipo.count("rifiuto") == 1  # un unico rifiuto: il retry non ne scrive un secondo
    # Lo stato cambia: all'ordine si aggiunge un cavo. La stessa chiave restituisce ancora il primo rifiuto, e non toglie niente.
    assert dopo.racconta("L'ordine 417 contiene anche un cavo", [riga(1, prodotto="cavo")])["esito"] == "applicato"
    assert dopo.racconta(**richiesta(rifiutato)) == {**rifiuto, "ripetuta": True}
    assert dopo.rete.e_tipo.count("rifiuto") == 1
    assert _righe(dopo, "accendi ordine = 417 in riga d'ordine leggi prodotto") == sorted([("cavo",), (rifiutato,)])
    # La stessa chiave per una richiesta diversa resta un conflitto.
    assert dopo.racconta("Togli tutto dall'ordine 417", chiave=f"togli-{rifiutato}", sostituisce=[{"tipo": "riga d'ordine", "indizi": {"ordine": "417"}, "tutti": True}])["esito"] == "conflitto"
    # Per riprovare davvero serve una chiave nuova: ora la rimozione lascia una riga, e passa.
    nuova = dopo.racconta(**{**richiesta(rifiutato), "chiave": "togli-di-nuovo"})
    assert nuova["esito"] == "applicato" and _righe(dopo, "accendi ordine = 417 in riga d'ordine leggi prodotto") == [("cavo",)]
    # Anche l'altra richiesta, quella applicata, ritentata dopo il riavvio dà la sua ricevuta di allora.
    applicato = next(p for p in ricevute if p != rifiutato)
    assert dopo.racconta(**richiesta(applicato)) == {**ricevute[applicato], "ripetuta": True}
    # Senza chiave un rifiuto resta un tentativo come un altro: viene rivalutato ogni volta.
    solo_cavo = {"frase": "Togli il cavo dall'ordine 417", "sostituisce": [{"tipo": "riga d'ordine", "indizi": {"ordine": "417", "prodotto": "cavo"}}]}
    assert dopo.racconta(**solo_cavo)["esito"] == "violazione_regola" and dopo.racconta(**solo_cavo)["esito"] == "violazione_regola"
    assert dopo.rete.e_tipo.count("rifiuto") == 3
    dopo.chiudi()


def test_scritture_spente(tmp_path: Path) -> None:
    sola = Memoria(tmp_path / "rete", scritture=False)
    assert sola.racconta(FRASE, [VOLO], prova=True)["esito"] == "prova"  # la prova si può fare anche qui
    with pytest.raises(SoloLettura):
        sola.racconta(FRASE, [VOLO])
    assert sola.conteggi() == VUOTA
    assert sola.chiedi("vocabolario")["righe"] == []
    sola.chiudi()
