"""I circuiti mantenuti: il passo C di docs/contratto-decisioni.md.

Un circuito tiene pronta una somma per ogni entità, qui il fido residuo di ogni cliente. Lo stesso commit che scrive
un fatto aggiorna la somma, e una regola la legge dentro la transazione. La somma è stato derivato: non sta nel
giornale e si deve poter rifare dai fatti.
"""

from __future__ import annotations

import random
import shutil
from decimal import Decimal
from pathlib import Path

import pytest

from sinapsidb.memoria import Memoria, Rifiutato
from sinapsidb.motore import Rete


def fido(cliente: str, importo: object) -> dict:
    return {"tipo": "fido accordato", "legami": {"cliente": cliente, "importo": importo}, "nuova_occorrenza": True}


def ordine(cliente: str, importo: object, numero: str) -> dict:
    return {"tipo": "ordine ricevuto", "legami": {"cliente": cliente, "importo": importo, "numero": numero}}


def pagamento(cliente: str, importo: object, causale: str) -> dict:
    return {"tipo": "pagamento incassato", "legami": {"cliente": cliente, "importo": importo, "causale": causale}}


def prepara(memoria: Memoria) -> int:
    """Il circuito «fido residuo»: fido accordato, meno gli ordini, più i pagamenti. E la regola dell'esempio del
    contratto: il residuo dopo la prenotazione non può essere negativo."""
    rete = memoria.rete
    n = rete.n
    rete.proponi_circuito("Il fido residuo di un cliente è il fido accordato, meno gli ordini, più i pagamenti", "Nora", "fido residuo", [
        {"osserva": n("fido accordato"), "lega": n("cliente"), "somma": n("importo"), "peso": 1},
        {"osserva": n("ordine ricevuto"), "lega": n("cliente"), "somma": n("importo"), "peso": -1},
        {"osserva": n("pagamento incassato"), "lega": n("cliente"), "somma": n("importo"), "peso": 1}])
    circuito = rete.trova("fido residuo")[0]
    rete.proponi_regola("Il fido residuo non può diventare negativo", "Nora", "fido non superato", 1, "vieta", [
        {"nome": "residuo negativo", "circuito": circuito, "limite": 0, "peso": 1}])
    return circuito


@pytest.fixture()
def m(tmp_path: Path) -> Memoria:
    memoria = Memoria(tmp_path / "rete", scritture=True)
    prepara(memoria)
    assert memoria.racconta("A Rossi è accordato un fido di 800 euro", [fido("Rossi", 800)])["esito"] == "applicato"
    yield memoria
    memoria.chiudi()


def _letture(ricevuta: dict) -> list[tuple]:
    return [(y["cosa"], y["con_proposta"], y["valore"]) for x in ricevuta["decisione"]["regole"] for y in x["letture"]]


def test_il_fido_si_legge_si_aggiorna_nel_commit_e_decide(m: Memoria, tmp_path: Path) -> None:
    """I casi 1 e 6 del contratto con i numeri veri: 800 prima e −200 dopo fanno un no; 1.200 prima e +200 dopo un sì."""
    letto = m.valore("fido residuo", "Rossi")
    assert {**letto, "valido_al": None} == {"circuito": "fido residuo", "versione": 0, "di": "Rossi", "di_id": f"neurone:{m.rete.trova('Rossi')[0]}",
                                            "valore": "800", "aggiornato_a": "evento:2", "valido_al": None}
    assert letto["valido_al"] >= m.rete.e_quando[2]
    fatti_prima = m.conteggi()["fatti"]
    no = m.racconta("Rossi ordina per 1000 euro", [ordine("Rossi", 1000, "O1")], chiave="o1")
    assert no["esito"] == "violazione_regola" and no["regole"][0]["regola"] == "fido non superato" and no["regole"][0]["su"] == "Rossi"
    assert _letture(no) == [("fido residuo", False, "800"), ("fido residuo", True, "-200")]
    assert m.valore("fido residuo", "Rossi")["valore"] == "800"  # un no non sposta niente
    assert m.conteggi()["fatti"] == fatti_prima and m.rete.trova("O1") == []
    # il pagamento entra, e chi legge il fido lo vede subito: è lo stesso commit
    pagato = m.racconta("Rossi paga 400 euro", [pagamento("Rossi", 400, "P1")])
    assert pagato["esito"] == "applicato" and _letture(pagato) == [("fido residuo", False, "800"), ("fido residuo", True, "1200")]
    assert m.valore("fido residuo", "Rossi")["valore"] == "1200" and m.valore("fido residuo", "Rossi")["aggiornato_a"] == pagato["evento"]
    # la stessa chiave restituisce il no di allora, anche se adesso passerebbe; con una chiave nuova passa
    assert m.racconta("Rossi ordina per 1000 euro", [ordine("Rossi", 1000, "O1")], chiave="o1") == {**no, "ripetuta": True}
    si = m.racconta("Rossi ordina per 1000 euro", [ordine("Rossi", 1000, "O1")], chiave="o1-di-nuovo")
    assert si["esito"] == "applicato" and _letture(si) == [("fido residuo", False, "1200"), ("fido residuo", True, "200")]
    assert m.valore("fido residuo", "Rossi")["valore"] == "200"
    # ogni lettura dice quale circuito, in quale versione, di chi, su quale base e per quale momento
    lettura = si["decisione"]["regole"][0]["letture"][0]
    assert lettura["circuito_id"] == m.rete.trova("fido residuo")[0] and lettura["versione"] == 0 and lettura["limite"] == "0"
    assert lettura["di"] == m.rete.trova("Rossi")[0] and lettura["base"] == si["decisione"]["base"] and lettura["valido_al"] == si["decisione"]["istante"]
    # le decisioni si rifanno dal giornale, con i loro numeri, anche dopo un riavvio e con un'istantanea
    assert [m.verifica(x["evento"])["esito"] for x in (no, pagato, si)] == ["coerente"] * 3
    assert m.verifica_circuiti() == {"esito": "coerente", "differenze": [], "circuiti": 1, "valori": 1,
                                     "verificato_fino_a": si["evento"], "giornale_oltre": False}
    m.rete.istantanea()
    m.chiudi()
    for con_istantanea in (True, False):
        copia = tmp_path / f"copia-{con_istantanea}"
        shutil.copytree(tmp_path / "rete", copia)
        if not con_istantanea:
            (copia / "istantanea").unlink()
        riaperta = Memoria(copia, scritture=True)
        assert riaperta.valore("fido residuo", "Rossi")["valore"] == "200" and riaperta.verifica_circuiti()["esito"] == "coerente"
        assert [riaperta.verifica(x["evento"])["esito"] for x in (no, pagato, si)] == ["coerente"] * 3
        assert riaperta.racconta("Rossi ordina ancora per 300 euro", [ordine("Rossi", 300, "O2")])["esito"] == "violazione_regola"
        riaperta.chiudi()


def test_la_regola_e_consultata_ogni_volta_che_il_valore_si_sposta(m: Memoria) -> None:
    """La regola legge il circuito, non un tipo di fatto: viene consultata per ogni cliente di cui la scrittura sposta
    il fido, qualunque sia il fatto che lo sposta. Togliere un pagamento può mandare il fido sotto zero quanto un ordine."""
    assert m.racconta("Rossi paga 400 euro", [pagamento("Rossi", 400, "P1")])["esito"] == "applicato"
    assert m.racconta("Rossi ordina per 1000 euro", [ordine("Rossi", 1000, "O1")])["esito"] == "applicato"
    storno = m.racconta("Il pagamento P1 di Rossi era un errore", sostituisce=[{"tipo": "pagamento incassato", "indizi": {"causale": "P1"}}])
    assert storno["esito"] == "violazione_regola" and _letture(storno) == [("fido residuo", False, "200"), ("fido residuo", True, "-200")]
    assert m.valore("fido residuo", "Rossi")["valore"] == "200" and m.verifica(storno["evento"])["esito"] == "coerente"
    # una correzione che nello stesso evento ritira e riscrive conta una volta sola
    corretto = m.racconta("Il pagamento P1 di Rossi era di 350 euro", [pagamento("Rossi", 350, "P1")],
                          sostituisce=[{"tipo": "pagamento incassato", "indizi": {"causale": "P1"}}])
    assert corretto["esito"] == "applicato" and _letture(corretto) == [("fido residuo", False, "200"), ("fido residuo", True, "150")]
    # una scrittura che tocca due clienti consulta la regola per tutti e due, e basta uno sotto zero per fermarla
    assert m.racconta("A Bianchi è accordato un fido di 100 euro", [fido("Bianchi", 100)])["esito"] == "applicato"
    due = m.racconta("Rossi e Bianchi ordinano", [ordine("Rossi", 100, "O2"), ordine("Bianchi", 200, "O3")])
    assert due["esito"] == "violazione_regola" and [(x["su"], x["verdetto"]) for x in due["decisione"]["regole"]] == [
        ("Rossi", "passa"), ("Bianchi", "vieta")]
    assert (m.valore("fido residuo", "Rossi")["valore"], m.valore("fido residuo", "Bianchi")["valore"]) == ("150", "100")
    # una scrittura che non sposta nessun fido non consulta la regola
    assert m.racconta("Rossi ha sede a Lugano", [{"tipo": "sede", "legami": {"cliente": "Rossi", "dove": "Lugano"}}])["decisione"]["regole"] == []
    assert m.verifica_circuiti()["esito"] == "coerente"


def test_una_prova_e_un_valore_che_non_e_un_numero_non_lasciano_traccia(m: Memoria) -> None:
    prima = (m.conteggi(), m.valore("fido residuo", "Rossi")["valore"])
    r = m.racconta("E se Rossi ordinasse per 500 euro?", [ordine("Rossi", 500, "O1")], prova=True)
    assert r["esito"] == "prova" and r["regole_scattate"] == [] and (m.conteggi(), m.valore("fido residuo", "Rossi")["valore"]) == prima
    with pytest.raises(Rifiutato, match="non è un numero: «tanto»"):
        m.racconta("Rossi ordina per tanto", [ordine("Rossi", "tanto", "O1")])
    with pytest.raises(Rifiutato, match="non è un numero"):
        m.racconta("Rossi ordina", [{"tipo": "ordine ricevuto", "legami": {"cliente": "Rossi", "numero": "O1"}}])
    assert (m.conteggi(), m.valore("fido residuo", "Rossi")["valore"]) == prima and m.rete.trova("tanto") == []
    assert m.verifica_circuiti()["esito"] == "coerente"
    # i decimali si sommano esatti
    for importo in ("0.10", "0.20"):
        assert m.racconta(f"Rossi paga {importo}", [pagamento("Rossi", importo, f"P{importo}")])["esito"] == "applicato"
    assert m.valore("fido residuo", "Rossi")["valore"] == "800.3"
    with pytest.raises(ValueError, match="non è un circuito"):
        m.valore("Rossi", "Rossi")
    with pytest.raises(ValueError, match="non c'è"):
        m.valore("fido residuo", "Verdi")


def test_due_clienti_con_lo_stesso_nome_hanno_due_fidi(m: Memoria) -> None:
    altro = m.racconta("C'è un altro Rossi, con un fido di 50 euro", [fido("Rossi", 50)], nuovi=["Rossi"])
    primo, secondo = m.rete.trova("Rossi")
    assert altro["esito"] == "applicato"
    assert (m.valore("fido residuo", f"neurone:{primo}")["valore"], m.valore("fido residuo", f"neurone:{secondo}")["valore"]) == ("800", "50")
    with pytest.raises(ValueError, match="indica più neuroni"):
        m.valore("fido residuo", "Rossi")
    r = m.racconta("Il secondo Rossi ordina per 60 euro", [ordine(f"neurone:{secondo}", 60, "O1")])
    assert r["esito"] == "violazione_regola" and _letture(r) == [("fido residuo", False, "50"), ("fido residuo", True, "-10")]
    assert m.verifica(r["evento"])["esito"] == "coerente"


def test_un_circuito_nato_dopo_i_fatti_parte_dal_valore_giusto(tmp_path: Path) -> None:
    memoria = Memoria(tmp_path / "rete", scritture=True)
    for frase, fatto in (("Fido di Rossi", fido("Rossi", 800)), ("Rossi ordina", ordine("Rossi", 300, "O1")), ("Rossi paga", pagamento("Rossi", 100, "P1"))):
        assert memoria.racconta(frase, [fatto])["esito"] == "applicato"
    prepara(memoria)
    assert memoria.valore("fido residuo", "Rossi")["valore"] == "600" and memoria.verifica_circuiti()["esito"] == "coerente"
    assert [memoria.verifica(e)["esito"] for e in range(len(memoria.rete.e_frase))] == ["coerente"] * 5
    memoria.chiudi()
    # un circuito non può nascere su fatti che in quel ruolo non hanno un numero
    altra = Memoria(tmp_path / "altra", scritture=True)
    assert altra.racconta("Rossi ordina per tanto", [ordine("Rossi", "tanto", "O1")])["esito"] == "applicato"
    with pytest.raises(ValueError, match="non è un numero"):
        prepara(altra)
    assert altra.rete.circuiti == [] and altra.rete.trova("fido residuo") == []
    altra.chiudi()


def test_il_controllo_si_accorge_di_un_circuito_che_ha_perso_il_passo(m: Memoria) -> None:
    """Un circuito sbagliato darebbe risposte sbagliate alla velocità di una cache. Il controllo rifà le somme dai
    fatti e dal giornale e trova la differenza."""
    circuito, rossi = m.rete.trova("fido residuo")[0], m.rete.trova("Rossi")[0]
    m.rete.valori[circuito][rossi] += 1_000_000  # il guasto: il valore tenuto pronto non è più quello dei fatti (un euro in più)
    esito = m.verifica_circuiti()
    assert esito["esito"] == "guasto" and esito["differenze"] == [
        {"circuito": "fido residuo", "di": "Rossi", "di_id": rossi, "tenuto": "801", "dai_fatti": "800", "contro": "i fatti in memoria"},
        {"circuito": "fido residuo", "di": "Rossi", "di_id": rossi, "tenuto": "801", "dai_fatti": "800", "contro": "il giornale"}]


def test_mille_scritture_a_caso_e_il_circuito_resta_uguale_ai_fatti(tmp_path: Path) -> None:
    """Scritture, correzioni, rifiuti, prove e riavvii a caso su venti clienti. Un modello tenuto a parte, fuori dal
    motore, dice quanto deve valere il fido di ciascuno: il circuito deve coincidere sempre, e ogni decisione rifatta
    dal giornale deve dare la ricevuta di allora."""
    caso = random.Random(417)
    memoria = Memoria(tmp_path / "rete", scritture=True)
    prepara(memoria)
    clienti = [f"cliente {i}" for i in range(20)]
    modello = {c: 0 for c in clienti}  # in centesimi interi: un riferimento che non usa l'aritmetica del circuito
    pagamenti: dict[str, tuple[str, int]] = {}  # causale -> (cliente, centesimi)
    euro = lambda centesimi: f"{centesimi // 100}.{centesimi % 100:02d}"
    esiti = {"applicato": 0, "violazione_regola": 0, "prova": 0}
    eventi = []
    for passo in range(1000):
        cliente = caso.choice(clienti)
        importo = caso.randint(1, 50000)
        mossa = caso.random()
        prova = caso.random() < 0.05
        if mossa < 0.25:
            atteso, fatti, sostituisce, delta = "applicato", [fido(cliente, euro(importo))], None, importo
        elif mossa < 0.65:
            fatti, sostituisce, delta = [ordine(cliente, euro(importo), f"O{passo}")], None, -importo
            atteso = "applicato" if modello[cliente] + delta >= 0 else "violazione_regola"
        elif mossa < 0.85 or not pagamenti:
            causale = f"P{passo}"
            atteso, fatti, sostituisce, delta = "applicato", [pagamento(cliente, euro(importo), causale)], None, importo
        else:  # si corregge o si toglie un pagamento già registrato
            causale = caso.choice(sorted(pagamenti))
            cliente, vecchio = pagamenti[causale]
            sostituisce = [{"tipo": "pagamento incassato", "indizi": {"causale": causale}}]
            fatti = [pagamento(cliente, euro(importo), causale)] if caso.random() < 0.5 else []
            delta = (importo if fatti else 0) - vecchio
            atteso = "applicato" if modello[cliente] + delta >= 0 else "violazione_regola"
        r = memoria.racconta(f"passo {passo}", fatti, sostituisce=sostituisce, prova=prova)
        if prova:
            assert r["esito"] == "prova" and bool(r["regole_scattate"]) == (atteso == "violazione_regola")
        else:
            assert r["esito"] == atteso, (passo, r)
            eventi.append(r["evento"])
            if atteso == "applicato":
                modello[cliente] += delta
                if mossa >= 0.65:
                    if fatti:
                        pagamenti[causale] = (cliente, importo)
                    else:
                        del pagamenti[causale]
        esiti[r["esito"]] += 1
        if memoria.rete.trova(cliente):
            assert Decimal(memoria.valore("fido residuo", cliente)["valore"]) * 100 == modello[cliente], passo
        else:  # un cliente di cui è stato scritto solo un no non esiste ancora
            assert modello[cliente] == 0
        if passo % 250 == 249:  # un riavvio, una volta con l'istantanea e una senza
            if passo % 500 == 249:
                memoria.rete.istantanea()
            memoria.chiudi()
            memoria = Memoria(tmp_path / "rete", scritture=True)
    assert min(esiti.values()) > 20, esiti  # il caso ha davvero prodotto di tutto
    assert memoria.verifica_circuiti()["esito"] == "coerente"
    assert {c: Decimal(memoria.valore("fido residuo", c)["valore"]) * 100 for c in clienti if memoria.rete.trova(c)} == \
           {c: v for c, v in modello.items() if memoria.rete.trova(c)}
    assert all(v >= 0 for v in modello.values())
    for evento in caso.sample(eventi, 60):
        assert memoria.verifica(evento)["esito"] == "coerente", evento
    # la rete rifatta da capo dal solo giornale ha gli stessi valori
    dal_giornale, _ = Rete.com_era(str(tmp_path / "rete"), len(memoria.rete.e_frase))
    assert dal_giornale.valori == memoria.rete.valori or dal_giornale.verifica_circuiti() == []
    memoria.chiudi()


@pytest.mark.parametrize("precisione", [4, 28])
def test_un_centesimo_di_scoperto_e_un_no_qualunque_sia_la_precisione_di_chi_chiama(m: Memoria, precisione: int) -> None:
    """Un controesempio della revisione: con una precisione decimale di quattro cifre, 800,00 − 800,01 si arrotondava a zero
    e l'ordine passava. Il circuito somma in interi: il risultato non dipende dal contesto di chi chiama."""
    import decimal

    with decimal.localcontext() as contesto:
        contesto.prec = precisione
        no = m.racconta("Rossi ordina per 800,01 euro", [ordine("Rossi", "800.01", "O1")])
        assert no["esito"] == "violazione_regola" and _letture(no) == [("fido residuo", False, "800"), ("fido residuo", True, "-0.01")]
        assert m.valore("fido residuo", "Rossi")["valore"] == "800" and m.verifica(no["evento"])["esito"] == "coerente"
        si = m.racconta("Rossi ordina per 800 euro", [ordine("Rossi", "800.00", "O2")])
        assert si["esito"] == "applicato" and m.valore("fido residuo", "Rossi")["valore"] == "0"
        assert m.verifica_circuiti()["esito"] == "coerente"


def test_importi_grandi_esatti_e_fuori_misura_rifiutati_senza_scrivere(tmp_path: Path) -> None:
    """Con ventisette cifre intere il conto in centesimi interi dice −0,01: deve essere un no. Fuori dal dominio del
    circuito (più di sei decimali, più di trenta cifre intere, non finito) la scrittura è rifiutata tutta, prima di
    toccare qualcosa: mai un sì arrotondato."""
    memoria = Memoria(tmp_path / "rete", scritture=True)
    prepara(memoria)
    grande = 10 ** 28  # centesimi
    scrivi = lambda centesimi: f"{centesimi // 100}.{centesimi % 100:02d}"
    assert memoria.racconta("Un fido enorme", [fido("Rossi", scrivi(grande))])["esito"] == "applicato"
    no = memoria.racconta("Un ordine di un centesimo in più", [ordine("Rossi", scrivi(grande + 1), "O1")])
    assert grande - (grande + 1) == -1 and no["esito"] == "violazione_regola" and _letture(no)[1] == ("fido residuo", True, "-0.01")
    assert memoria.valore("fido residuo", "Rossi")["valore"] == str(grande // 100)
    prima = (memoria.conteggi(), memoria.valore("fido residuo", "Rossi")["valore"])
    for fuori in ("0.0000001", "1" + "0" * 31, "1e50", "inf", "nan", "-inf", "1e999999999"):
        with pytest.raises(Rifiutato, match="non è un numero"):
            memoria.racconta("Fuori misura", [ordine("Rossi", fuori, "O2")])
        assert (memoria.conteggi(), memoria.valore("fido residuo", "Rossi")["valore"]) == prima
    assert memoria.racconta("Sei decimali vanno bene", [pagamento("Rossi", "0.000001", "P1")])["esito"] == "applicato"
    assert memoria.valore("fido residuo", "Rossi")["valore"] == f"{grande // 100}.000001"
    assert memoria.verifica_circuiti()["esito"] == "coerente"
    memoria.chiudi()


def test_una_definizione_che_non_sta_in_piedi_non_entra_nel_giornale(m: Memoria, tmp_path: Path) -> None:
    """Un controesempio della revisione: un peso che non è un numero faceva fallire la chiamata dopo aver già scritto l'evento,
    e il giornale non si riapriva più. Ora la definizione è letta prima di scrivere: se non sta in piedi non resta niente."""
    from sinapsidb.motore import DefinizioneNonValida

    rete = m.rete
    n = rete.n
    termine = {"osserva": n("fido accordato"), "lega": n("cliente"), "somma": n("importo"), "peso": 1}
    prima = (m.conteggi(), len(rete.nomi), m.valore("fido residuo", "Rossi")["valore"], len(rete.regole), len(rete.circuiti))
    for termini, dice in (([{**termine, "peso": "uno"}], "«peso» deve essere un numero"), ([{**termine, "peso": "0.5"}], "«peso» deve essere un numero"),
                          ([{**termine, "peso": "inf"}], "«peso» deve essere un numero"), ([], "non ha termini")):
        with pytest.raises(DefinizioneNonValida, match=dice):
            rete.proponi_circuito("Un circuito sbagliato", "prova", "circuito sbagliato", termini)
        assert (m.conteggi(), len(rete.nomi), m.valore("fido residuo", "Rossi")["valore"], len(rete.regole), len(rete.circuiti)) == prima
    for segnale, dice in (({"nome": "x", "circuito": rete.trova("Rossi")[0], "limite": 0, "peso": 1}, "legge un circuito che non c'è"),
                          ({"nome": "x", "circuito": rete.trova("fido residuo")[0], "limite": "zero", "peso": 1}, "limite non è un numero"),
                          ({"nome": "x", "circuito": rete.trova("fido residuo")[0], "limite": 0, "peso": "tanto"}, "«peso» deve essere un numero")):
        with pytest.raises(DefinizioneNonValida, match=dice):
            rete.proponi_regola("Una regola sbagliata", "prova", "regola sbagliata", 1, "vieta", [segnale])
        assert (m.conteggi(), len(rete.nomi), len(rete.regole)) == (prima[0], prima[1], prima[3])  # nemmeno un nome resta in memoria
    for peso in ("inf", "nan"):  # anche i pesi dei segnali e le soglie devono essere numeri finiti
        with pytest.raises(DefinizioneNonValida, match="deve essere un numero"):
            rete.proponi_regola("Una regola sbagliata", "prova", "regola sbagliata", 1, "vieta", [
                {"nome": "x", "circuito": rete.trova("fido residuo")[0], "limite": 0, "peso": peso}])
        with pytest.raises(DefinizioneNonValida, match="deve essere un numero"):
            rete.proponi_regola("Una regola sbagliata", "prova", "regola sbagliata", peso, "vieta", [
                {"nome": "x", "circuito": rete.trova("fido residuo")[0], "limite": 0, "peso": 1}])
    # un circuito definito due volte, scritto a mano come fatti, è rifiutato da racconta allo stesso modo
    with pytest.raises(Rifiutato, match="definito due volte"):
        m.racconta("Lo stesso circuito un'altra volta", [{"tipo": "circuito", "legami": {"circuito di": "fido residuo"}, "nuova_occorrenza": True}])
    assert (m.conteggi()["eventi"], m.valore("fido residuo", "Rossi")["valore"], len(rete.regole), len(rete.circuiti)) == (prima[0]["eventi"], prima[2], prima[3], prima[4])
    # il giornale si riapre, e il circuito di prima funziona
    m.chiudi()
    riaperta = Memoria(tmp_path / "rete", scritture=True)
    assert riaperta.conteggi()["eventi"] == prima[0]["eventi"] and riaperta.valore("fido residuo", "Rossi")["valore"] == "800"
    assert riaperta.racconta("Rossi ordina per 900 euro", [ordine("Rossi", 900, "O1")])["esito"] == "violazione_regola"
    assert Rete.com_era(str(tmp_path / "rete"), riaperta.conteggi()["eventi"])[0].valori == riaperta.rete.valori
    riaperta.chiudi()


def test_il_controllo_dice_fin_dove_ha_controllato(m: Memoria, tmp_path: Path) -> None:
    """Un'osservazione della revisione: una memoria che legge soltanto, rimasta indietro rispetto al giornale, ha somme giuste
    fino al suo ultimo evento ma non aggiornate. Il controllo lo dice, distinto da una somma sbagliata."""
    lettrice = Memoria(tmp_path / "rete")
    assert lettrice.verifica_circuiti()["esito"] == "coerente"
    r = m.racconta("Rossi paga 400 euro", [pagamento("Rossi", 400, "P1")])
    indietro = lettrice.verifica_circuiti()
    assert indietro["esito"] == "indietro" and indietro["differenze"] == [] and indietro["giornale_oltre"] is True
    assert indietro["verificato_fino_a"] == f"evento:{int(r['evento'].removeprefix('evento:')) - 1}"
    assert lettrice.valore("fido residuo", "Rossi")["valore"] == "800" and m.valore("fido residuo", "Rossi")["valore"] == "1200"
    lettrice.chiudi()


# --- un circuito raggruppato per mese, o per anno ---------------------------------


def _fattura(rete: Rete, numero: str, giorno: str, totale: str, **altro) -> int:
    n = rete.n
    rete.racconta(f"Fattura {numero} del {giorno}, totale {totale}", "prova",
                  [(n("fattura emessa"), [(n("fattura"), n(numero)), (n("quando"), n(giorno)), (n("totale"), n(totale))])], **altro)
    return len(rete.f_tipo) - 1


def _per_mese(rete: Rete, nome: str = "fatturato per mese", per: str = "mese") -> int:
    n = rete.n
    rete.proponi_circuito("Il fatturato di ogni mese è la somma dei totali delle fatture di quel mese", "prova", nome,
                          [{"osserva": n("fattura emessa"), "lega": n("quando"), "somma": n("totale"), "peso": 1, "per": per}])
    return rete.trova(nome)[0]


def _dai_fatti(rete: Rete, taglio: int = 7) -> dict[str, Decimal]:
    """Le somme fatte nel modo lungo: tutte le fatture in vigore, una per una, raggruppate per l'inizio della data."""
    n = rete.n
    somme: dict[str, Decimal] = {}
    for f in range(len(rete.f_tipo)):
        if rete.f_tipo[f] == n("fattura emessa") and rete.f_morto[f] < 0:
            chiave = rete.nomi[rete.leggi(f, n("quando"))][:taglio]
            somme[chiave] = somme.get(chiave, Decimal(0)) + Decimal(rete.nomi[rete.leggi(f, n("totale"))])
    return somme


def _pronte(rete: Rete, circuito: int) -> dict[str, Decimal]:
    from sinapsidb.motore import da_unita

    return {rete.nomi[x]: Decimal(da_unita(v)) for x, v in rete.somme_pronte(circuito).items()}


def test_un_circuito_per_mese_segue_le_scritture_e_si_rifa_dal_giornale(tmp_path: Path) -> None:
    rete = Rete(str(tmp_path / "rete"), sync="os")
    c = _per_mese(rete)
    assert _pronte(rete, c) == {}
    rng = random.Random(5)
    fatte = []
    for i in range(300):
        giorno = f"202{rng.randint(4, 6)}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"
        fatte.append(_fattura(rete, f"F{i}", giorno, f"{rng.randint(1, 5000)}.{rng.randint(0, 99):02d}"))
        if i % 7 == 3:  # ogni tanto una fattura viene annullata: il suo mese torna indietro
            rete.racconta(f"Annulla la fattura {fatte[rng.randrange(len(fatte))]}", "prova", [], ritira=[fatte.pop(rng.randrange(len(fatte)))])
        if i % 50 == 0:
            assert _pronte(rete, c) == _dai_fatti(rete), i  # anche gli stessi mesi: uno rimasto senza fatture non c'è più
    assert _pronte(rete, c) == _dai_fatti(rete) and len(_dai_fatti(rete)) > 30
    assert rete.verifica_circuiti() == []
    # ogni mese è un neurone come gli altri, uno solo, nato dentro la scrittura della sua prima fattura
    mesi = sorted(_pronte(rete, c))
    assert all(len(rete.trova(m)) == 1 for m in mesi)
    numeri = {m: rete.trova(m)[0] for m in mesi}
    prima = _pronte(rete, c)
    rete.chiudi()
    # riletta dal giornale: stessi neuroni con gli stessi numeri, stesse somme
    riletta = Rete(str(tmp_path / "rete"), sync="os")
    assert {m: riletta.trova(m)[0] for m in mesi} == numeri and _pronte(riletta, c) == prima and riletta.verifica_circuiti() == []
    # e dall'istantanea, dove le somme non ci sono e si rifanno dai fatti
    riletta.istantanea()
    _fattura(riletta, "F-dopo", "2026-11-22", "10.50")
    dopo = _pronte(riletta, c)
    riletta.chiudi()
    terza = Rete(str(tmp_path / "rete"), sync="os")
    assert _pronte(terza, c) == dopo and dopo["2026-11"] == prima.get("2026-11", 0) + Decimal("10.50") and terza.verifica_circuiti() == []
    terza.chiudi()


def test_un_circuito_per_mese_nato_dopo_i_fatti_e_per_anno(tmp_path: Path) -> None:
    rete = Rete(str(tmp_path / "rete"), sync="os")
    for i, (giorno, totale) in enumerate((("2025-12-30", "100.00"), ("2025-12-31", "0.01"), ("2026-01-01", "250.50"), ("2026-11-22", "9.99"))):
        _fattura(rete, f"F{i}", giorno, totale)
    neuroni = len(rete.nomi)
    assert not rete.trova("2025-12")
    c = _per_mese(rete)
    # la definizione parte dai fatti che ci sono già, e i mesi nascono con lei
    assert _pronte(rete, c) == {"2025-12": Decimal("100.01"), "2026-01": Decimal("250.5"), "2026-11": Decimal("9.99")} == _dai_fatti(rete)
    assert all(rete.trova(m)[0] >= neuroni for m in ("2025-12", "2026-01", "2026-11"))
    a = _per_mese(rete, "fatturato per anno", "anno")
    assert _pronte(rete, a) == {"2025": Decimal("100.01"), "2026": Decimal("260.49")} == _dai_fatti(rete, 4)
    _fattura(rete, "F9", "2026-01-15", "0.50")
    assert _pronte(rete, c)["2026-01"] == Decimal("251") and _pronte(rete, a)["2026"] == Decimal("260.99")
    assert rete.verifica_circuiti() == []
    prima = (_pronte(rete, c), _pronte(rete, a), len(rete.nomi))
    rete.chiudi()
    riletta = Rete(str(tmp_path / "rete"), sync="os")
    assert (_pronte(riletta, c), _pronte(riletta, a), len(riletta.nomi)) == prima
    riletta.chiudi()


def test_un_circuito_per_mese_che_non_sta_in_piedi_non_entra_nel_giornale(tmp_path: Path) -> None:
    from sinapsidb.motore import DefinizioneNonValida, NonNumerico

    rete = Rete(str(tmp_path / "rete"), sync="os")
    _fattura(rete, "F1", "2026-01-10", "120.00")
    eventi, neuroni = len(rete.e_frase), len(rete.nomi)
    with pytest.raises(DefinizioneNonValida, match="per mese o per anno"):
        _per_mese(rete, "fatturato per settimana", "settimana")
    assert (len(rete.e_frase), len(rete.nomi)) == (eventi, neuroni) and not rete.circuiti  # niente scritto, nessun nome lasciato
    c = _per_mese(rete)
    # una regola non sa ancora leggere un circuito raggruppato: si rifiuta, invece di decidere su un numero che non è il suo
    eventi = len(rete.e_frase)
    with pytest.raises(DefinizioneNonValida, match="raggruppato per mese"):
        rete.proponi_regola("Il fatturato non può essere negativo", "prova", "fatturato positivo", 1, "vieta",
                            [{"nome": "sotto zero", "circuito": c, "limite": 0, "peso": 1}])
    assert len(rete.e_frase) == eventi and not rete.regole
    # un totale che non è un numero non entra, e non lascia un mese a metà
    neuroni = len(rete.nomi)
    with pytest.raises(NonNumerico):
        _fattura(rete, "F2", "2027-05-05", "da definire", neuroni_da=neuroni)
    assert len(rete.nomi) == neuroni and not rete.trova("2027-05") and _pronte(rete, c) == {"2026-01": Decimal("120")}
    assert rete.verifica_circuiti() == []
    rete.chiudi()
