"""Il tempo di validità dei fatti e le due letture del passato: il passo D di docs/contratto-decisioni.md.

Ogni fatto ha due tempi: quando è stato registrato e da quando a quando vale. Un pagamento registrato martedì può
valere da domenica. Il circuito tiene il valore che vale adesso; i valori del passato si rifanno dal giornale.
"""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from sinapsidb import memoria as modulo_memoria
from sinapsidb import motore as modulo_motore
from sinapsidb.lingua import ErroreDomanda
from sinapsidb.memoria import Memoria, Rifiutato
from sinapsidb.motore import Rete, istante

SABATO, DOMENICA, LUNEDI, MARTEDI, MERCOLEDI, GIOVEDI = (f"2026-11-{g}" for g in (14, 15, 16, 17, 18, 19))


class Orologio:
    """L'ora che il motore e la memoria credono che sia: i test la spostano a mano."""

    def __init__(self) -> None:
        self.adesso = 0.0

    def time(self) -> float:
        return self.adesso

    def metti(self, quando: str) -> None:
        self.adesso = istante(quando)


@pytest.fixture()
def orologio(monkeypatch: pytest.MonkeyPatch) -> Orologio:
    finto = Orologio()
    finto.metti(f"{SABATO}T08:00")
    monkeypatch.setattr(modulo_memoria, "time", finto)
    monkeypatch.setattr(modulo_motore, "time", finto)
    return finto


def fatto(tipo: str, cliente: str, importo: object, **altro: str) -> dict:
    return {"tipo": tipo, "legami": {"cliente": cliente, "importo": importo, **altro}}


def prepara(memoria: Memoria) -> None:
    rete = memoria.rete
    n = rete.n
    rete.proponi_circuito("Il fido residuo è il fido accordato, meno gli ordini, più i pagamenti", "Nora", "fido residuo", [
        {"osserva": n("fido accordato"), "lega": n("cliente"), "somma": n("importo"), "peso": 1},
        {"osserva": n("ordine ricevuto"), "lega": n("cliente"), "somma": n("importo"), "peso": -1},
        {"osserva": n("pagamento incassato"), "lega": n("cliente"), "somma": n("importo"), "peso": 1}])
    rete.proponi_regola("Il fido residuo non può diventare negativo", "Nora", "fido non superato", 1, "vieta", [
        {"nome": "residuo negativo", "circuito": rete.trova("fido residuo")[0], "limite": 0, "peso": 1}])


@pytest.fixture()
def m(tmp_path: Path, orologio: Orologio) -> Memoria:
    """Sabato: a Rossi è accordato un fido di 750 euro e paga 50 euro. Lunedì ha 800 euro di fido residuo."""
    memoria = Memoria(tmp_path / "rete", scritture=True)
    prepara(memoria)
    assert memoria.racconta("A Rossi è accordato un fido di 750 euro", [fatto("fido accordato", "Rossi", 750)])["esito"] == "applicato"
    assert memoria.racconta("Rossi paga 50 euro", [fatto("pagamento incassato", "Rossi", 50, causale="P0")])["esito"] == "applicato"
    yield memoria
    memoria.chiudi()


def _fido(m: Memoria) -> str:
    return m.valore("fido residuo", "Rossi")["valore"]


def _pagato(m: Memoria, clausola: str = "") -> list:
    return m.chiedi(f"accendi cliente = Rossi in pagamento incassato somma importo{clausola}")["righe"]


def test_registrato_martedi_valido_da_domenica(m: Memoria, orologio: Orologio) -> None:
    """L'esempio del contratto. Lunedì il fido è 800 e l'ordine da 1.000 è un no. Martedì si registra un pagamento
    da 400 fatto domenica. «Come risultava lunedì» non lo vede; «come valeva lunedì» lo vede."""
    orologio.metti(f"{LUNEDI}T09:00")
    assert _fido(m) == "800"
    no = m.racconta("Rossi ordina per 1000 euro", [fatto("ordine ricevuto", "Rossi", 1000, numero="O1")], chiave="o1")
    assert no["esito"] == "violazione_regola"
    letture = no["decisione"]["regole"][0]["letture"]
    assert [(x["con_proposta"], x["valore"], x["valido_al"], x["base"]) for x in letture] == [
        (False, "800", istante(f"{LUNEDI}T09:00"), 3), (True, "-200", istante(f"{LUNEDI}T09:00"), 3)]
    orologio.metti(f"{MARTEDI}T10:00")
    pagato = m.racconta("Rossi ha pagato 400 euro domenica", [fatto("pagamento incassato", "Rossi", 400, causale="P1", **{"vale da": DOMENICA})])
    assert pagato["esito"] == "applicato" and _fido(m) == "1200"
    ultimo = int(pagato["evento"].removeprefix("evento:"))
    # le tre letture: quello che è registrato, quello che risultava lunedì, quello che valeva lunedì
    assert _pagato(m) == [("450",)]
    risultava = m.chiedi(f"accendi cliente = Rossi in pagamento incassato somma importo come risultava il {LUNEDI}T12:00")
    assert risultava["righe"] == [("50",)]
    assert risultava["lettura"] == {"modo": "risultava", "registrato_fino_a": no["evento"], "valido_al": istante(f"{LUNEDI}T12:00")}
    valeva = m.chiedi(f"accendi cliente = Rossi in pagamento incassato somma importo come valeva il {LUNEDI}T12:00")
    assert valeva["righe"] == [("450",)] and valeva["lettura"]["registrato_fino_a"] == pagato["evento"]
    # il criterio della revisione: per domenica, con la frontiera di domenica il pagamento non c'è; con quella di oggi c'è
    assert _pagato(m, f" come risultava il {DOMENICA}T12:00") == [("50",)] and _pagato(m, f" come valeva il {DOMENICA}T12:00") == [("450",)]
    # sabato mattina, prima di tutto: niente era registrato, e niente di quello che è registrato oggi valeva già
    assert m.chiedi(f"in pagamento incassato conta come risultava il {SABATO}T07:00")["righe"] == []
    assert _pagato(m, " come risultava all'evento 2") == []  # il fido c'era già, i pagamenti non ancora
    with pytest.raises(ErroreDomanda, match="non è un tipo di fatto"):
        m.chiedi("in pagamento mai visto conta come risultava all'evento 2")
    assert _pagato(m, f" come risultava all'evento {ultimo - 1}") == [("50",)] and _pagato(m, f" come risultava all'evento {ultimo}") == [("450",)]
    assert _pagato(m, " come valeva adesso") == [("450",)]
    # la decisione di lunedì resta com'era, e si rifà dal giornale con il valore di allora
    assert m.racconta("Rossi ordina per 1000 euro", [fatto("ordine ricevuto", "Rossi", 1000, numero="O1")], chiave="o1") == {**no, "ripetuta": True}
    assert m.verifica(no["evento"])["esito"] == "coerente" and m.verifica(pagato["evento"])["esito"] == "coerente"
    assert m.verifica_circuiti()["esito"] == "coerente"
    with pytest.raises(ErroreDomanda, match="non c'è ancora"):
        _pagato(m, " come risultava all'evento 99")
    with pytest.raises(ErroreDomanda, match="si usa con «come risultava»"):
        _pagato(m, " come valeva all'evento 3")


def test_una_decorrenza_e_una_scadenza_cambiano_il_fido_senza_nessuna_scrittura(m: Memoria, tmp_path: Path, orologio: Orologio) -> None:
    """Un esempio della revisione: un pagamento di 400 valido da martedì a mercoledì, fine esclusa. Il fido si legge 800,
    poi 1.200, poi di nuovo 800, senza che nessuno scriva ai due confini, e anche dopo una riapertura."""
    orologio.metti(f"{LUNEDI}T09:00")
    r = m.racconta("Un anticipo di 400 euro che vale solo martedì", [
        fatto("pagamento incassato", "Rossi", 400, causale="P1", **{"vale da": MARTEDI, "vale fino a": MERCOLEDI})])
    assert r["esito"] == "applicato" and _fido(m) == "800"
    eventi = m.conteggi()["eventi"]
    for quando, atteso in ((f"{LUNEDI}T23:59:59", "800"), (MARTEDI, "1200"), (f"{MARTEDI}T23:59:59", "1200"), (MERCOLEDI, "800"),
                           (GIOVEDI, "800"), (f"{MARTEDI}T12:00", "1200"), (f"{LUNEDI}T10:00", "800")):
        orologio.metti(quando)
        assert _fido(m) == atteso, quando
        assert m.verifica_circuiti()["esito"] == "coerente", quando
    assert m.conteggi()["eventi"] == eventi  # nessuna scrittura
    # martedì il fido c'è, e un ordine da 1.000 passa; mercoledì l'anticipo è scaduto e il residuo è sotto zero
    orologio.metti(f"{MARTEDI}T15:00")
    si = m.racconta("Rossi ordina per 1000 euro", [fatto("ordine ricevuto", "Rossi", 1000, numero="O1")])
    assert si["esito"] == "applicato" and [x["valore"] for x in si["decisione"]["regole"][0]["letture"]] == ["1200", "200"]
    orologio.metti(f"{GIOVEDI}T09:00")
    assert _fido(m) == "-200"
    # la decisione di martedì si rifà giovedì con il valore di martedì, non con quello di oggi
    assert m.verifica(si["evento"])["esito"] == "coerente"
    m.rete.istantanea()
    m.chiudi()
    riaperta = Memoria(tmp_path / "rete", scritture=True)
    assert _fido(riaperta) == "-200" and riaperta.verifica(si["evento"])["esito"] == "coerente"
    orologio.metti(f"{MARTEDI}T16:00")
    assert _fido(riaperta) == "200" and riaperta.verifica_circuiti()["esito"] == "coerente"
    riaperta.chiudi()


def test_una_richiesta_rimasta_in_coda_decide_sul_suo_istante(tmp_path: Path, orologio: Orologio) -> None:
    """Una richiesta arriva alle 9 e resta in coda. Alle 10 si registra un pagamento, che vale dalle 10. Quando tocca
    alla richiesta, la sua base contiene il pagamento, ma il suo istante è le 9: il pagamento non valeva ancora."""
    rete = Rete(str(tmp_path / "rete"), decisioni=True)
    n = rete.n
    orologio.metti(f"{SABATO}T08:00")
    rete.proponi_circuito("fido residuo", "prova", "fido residuo", [
        {"osserva": n("fido accordato"), "lega": n("cliente"), "somma": n("importo"), "peso": 1},
        {"osserva": n("ordine ricevuto"), "lega": n("cliente"), "somma": n("importo"), "peso": -1},
        {"osserva": n("pagamento incassato"), "lega": n("cliente"), "somma": n("importo"), "peso": 1}])
    rete.proponi_regola("fido non superato", "prova", "fido non superato", 1, "vieta", [
        {"nome": "residuo negativo", "circuito": rete.trova("fido residuo")[0], "limite": 0, "peso": 1}])
    cliente, importo, rossi = n("cliente"), n("importo"), n("Rossi")
    rete.racconta("fido", "prova", [(n("fido accordato"), [(cliente, rossi), (importo, n("800"))])])
    alle_9, alle_10 = istante(f"{LUNEDI}T09:00"), istante(f"{LUNEDI}T10:00")
    rete.racconta("pagamento delle 10", "prova", [(n("pagamento incassato"), [(cliente, rossi), (importo, n("400"))])], quando=alle_10)
    un_ordine = [(n("ordine ricevuto"), [(cliente, rossi), (importo, n("1000"))])]
    prova = rete.racconta("ordine arrivato alle 9", "prova", un_ordine, prova=True, quando=alle_9)
    letture = prova["decisione"]["regole"][0]["letture"]
    assert prova["regole_scattate"] and [(x["valore"], x["valido_al"], x["base"]) for x in letture] == [("800", alle_9, 3), ("-200", alle_9, 3)]
    # la stessa richiesta arrivata alle 11 vede il pagamento
    dopo = rete.racconta("ordine arrivato alle 11", "prova", un_ordine, prova=True, quando=istante(f"{LUNEDI}T11:00"))
    assert not dopo["regole_scattate"] and [x["valore"] for x in dopo["decisione"]["regole"][0]["letture"]] == ["1200", "200"]
    assert rete.valore(rete.trova("fido residuo")[0], rossi, alle_9) == 800 and rete.verifica_circuiti() == []
    rete.chiudi()


def test_il_periodo_di_validita_deve_essere_fatto_di_date_intere(m: Memoria) -> None:
    prima = (m.conteggi(), _fido(m))
    for periodo, dice in (({"vale da": "22 novembre"}, "deve essere una data intera"), ({"vale fino a": "2026-11"}, "deve essere una data intera"),
                          ({"vale da": MARTEDI, "vale fino a": MARTEDI}, "deve venire dopo"), ({"vale da": MERCOLEDI, "vale fino a": LUNEDI}, "deve venire dopo")):
        with pytest.raises(Rifiutato, match=dice):
            m.racconta("Un pagamento datato male", [fatto("pagamento incassato", "Rossi", 10, causale="P9", **periodo)])
        assert (m.conteggi(), _fido(m)) == prima
    with pytest.raises(ErroreDomanda):
        _pagato(m, " come valeva il 2026-13-40")
    # il motore, anche chiamato senza la memoria, non somma un fatto con un periodo che non sta in piedi
    rete = m.rete
    n = rete.n
    segno = len(rete.nomi)
    with pytest.raises(ValueError, match="non è una data intera"):
        rete.racconta("un pagamento datato male", "prova", [
            (n("pagamento incassato"), [(n("cliente"), n("Rossi")), (n("importo"), n("10")), (n("vale da"), n("ieri"))])], neuroni_da=segno)
    assert (m.conteggi(), _fido(m)) == prima and rete.trova("ieri") == []


def test_periodi_a_caso_il_circuito_vale_quanto_i_fatti_a_ogni_istante(tmp_path: Path, orologio: Orologio) -> None:
    """Trecento pagamenti con periodi a caso su dieci clienti, alcuni poi ritirati. A istanti a caso, avanti e indietro
    nel tempo, il valore del circuito è confrontato con un conto in centesimi interi fatto qui, fuori dal motore."""
    caso = random.Random(417)
    giorno = 86400
    zero = istante("2026-11-01")
    orologio.adesso = zero  # il circuito nasce prima di tutti i pagamenti: l'istante di scrittura non torna indietro
    memoria = Memoria(tmp_path / "rete", scritture=True)
    prepara(memoria)
    giorni = [f"2026-11-{g:02d}" for g in range(1, 31)]
    modello: list[dict] = []  # ogni pagamento in vigore: cliente, centesimi, da, a
    orologio.adesso = zero + 3600
    for i in range(300):
        orologio.adesso += caso.randint(60, 7200)  # il tempo passa tra una scrittura e l'altra, fino a metà mese
        cliente, centesimi = f"cliente {caso.randint(0, 9)}", caso.randint(1, 99999)
        periodo, da, a = {}, orologio.adesso, float("inf")
        if caso.random() < 0.7:
            primo = caso.randint(0, 28)
            periodo["vale da"], da = giorni[primo], zero + primo * giorno
            if caso.random() < 0.6:
                ultimo = caso.randint(primo + 1, 29)
                periodo["vale fino a"], a = giorni[ultimo], zero + ultimo * giorno
        elif caso.random() < 0.3:
            ultimo = caso.randint(20, 29)  # dopo metà mese: la scadenza viene sempre dopo la registrazione
            periodo["vale fino a"], a = giorni[ultimo], zero + ultimo * giorno
        r = memoria.racconta(f"pagamento {i}", [fatto("pagamento incassato", cliente, f"{centesimi // 100}.{centesimi % 100:02d}", causale=f"P{i}", **periodo)])
        assert r["esito"] == "applicato", r
        modello.append({"cliente": cliente, "centesimi": centesimi, "da": da, "a": a, "causale": f"P{i}"})
        if modello and caso.random() < 0.15:  # si ritira un pagamento a caso
            tolto = modello.pop(caso.randrange(len(modello)))
            assert memoria.racconta("storno", sostituisce=[{"tipo": "pagamento incassato", "indizi": {"causale": tolto["causale"]}}])["esito"] == "applicato"
        if i % 10 == 9:
            adesso = orologio.adesso
            orologio.adesso = zero + caso.uniform(-2, 32) * giorno  # una lettura a un istante qualunque, anche nel passato
            for cliente in {x["cliente"] for x in modello}:
                atteso = sum(x["centesimi"] for x in modello if x["cliente"] == cliente and x["da"] <= orologio.adesso < x["a"])
                assert round(float(memoria.valore("fido residuo", cliente)["valore"]) * 100) == atteso, (i, cliente)
            assert memoria.verifica_circuiti()["esito"] == "coerente"
            orologio.adesso = adesso
        if i == 150:
            memoria.rete.istantanea()
            memoria.chiudi()
            memoria = Memoria(tmp_path / "rete", scritture=True)
    memoria.chiudi()


def test_un_dato_che_non_si_puo_sommare_e_rifiutato_anche_se_adesso_non_vale(m: Memoria, tmp_path: Path, orologio: Orologio) -> None:
    """Un controesempio della revisione: sabato si registra per martedì un pagamento con un importo che non è un numero. Il
    periodo decide se sommare, non se il dato è buono: deve essere rifiutato subito, non quando arriva martedì."""
    prima = (m.conteggi(), _fido(m))
    for periodo in ({"vale da": MARTEDI}, {"vale da": "2026-11-01", "vale fino a": "2026-11-02"}, {}):
        with pytest.raises(Rifiutato, match="non è un numero"):
            m.racconta("Un pagamento scritto male", [fatto("pagamento incassato", "Rossi", "non numerico", causale="P9", **periodo)])
        assert (m.conteggi(), _fido(m)) == prima
    # il giornale si riapre e si rifà da capo, e a martedì il fido si legge
    orologio.metti(f"{MARTEDI}T09:00")
    assert _fido(m) == "800" and Rete.com_era(str(tmp_path / "rete"), prima[0]["eventi"])[0].valori == m.rete.valori
    m.chiudi()
    riaperta = Memoria(tmp_path / "rete", scritture=True)
    assert _fido(riaperta) == "800" and riaperta.verifica_circuiti()["esito"] == "coerente"
    riaperta.chiudi()


def test_una_regola_vede_gli_stessi_fatti_della_lettura_allo_stesso_istante(m: Memoria, orologio: Orologio) -> None:
    """Un controesempio della revisione: una sospensione vale il 14, fine esclusa il 15. Il 16 la lettura «adesso» non la
    vede, e nemmeno la regola «cliente sospeso con una richiesta» la deve vedere. Lo stesso per una sospensione che
    comincia domani, per il confine escluso e per un segnale di assenza."""
    rete = m.rete
    n = rete.n
    sospeso = {"nome": "sospeso", "modo": "presenza", "osserva": n("sospensione"), "lega": n("cliente"), "peso": 1}
    richiesta = {"nome": "richiesta", "modo": "presenza", "osserva": n("richiesta"), "lega": n("cliente"), "peso": 1}
    rete.proponi_regola("Un cliente sospeso non fa richieste", "Nora", "niente richieste da sospeso", 2, "vieta", [sospeso, richiesta])
    rete.proponi_regola("Una richiesta vuole un contratto in corso", "Nora", "contratto in corso", 2, "vieta", [
        richiesta, {"nome": "senza contratto", "modo": "assenza", "osserva": n("contratto"), "lega": n("cliente"), "peso": 1}])
    una_richiesta = lambda i: [{"tipo": "richiesta", "legami": {"cliente": "Rossi", "numero": f"R{i}"}}]
    periodo = lambda da, a: {"vale da": da, "vale fino a": a}
    assert m.racconta("Rossi ha un contratto fino a fine mese", [{"tipo": "contratto", "legami": {"cliente": "Rossi", **periodo(SABATO, "2026-11-30")}}])["esito"] == "applicato"
    assert m.racconta("Rossi è sospeso per sabato", [{"tipo": "sospensione", "legami": {"cliente": "Rossi", **periodo(SABATO, DOMENICA)}}])["esito"] == "applicato"
    assert m.racconta("Rossi sarà sospeso mercoledì", [{"tipo": "sospensione", "legami": {"cliente": "Rossi", **periodo(MERCOLEDI, GIOVEDI)}}])["esito"] == "applicato"

    def prova(quando: str, i: int) -> tuple[str, dict, list]:
        orologio.metti(quando)
        r = m.racconta("Rossi fa una richiesta", una_richiesta(i), prova=True)
        segnali = {y["segnale"]: y["valore"] for x in r["decisione"]["regole"] for y in x["letture"]}
        sospesi = m.chiedi("in sospensione leggi cliente")["righe"]
        return ("no" if r["regole_scattate"] else "sì"), segnali, sospesi

    # sabato la sospensione vale: la regola la vede, e la lettura pure
    assert prova(f"{SABATO}T12:00", 1) == ("no", {"sospeso": 1, "richiesta": 1, "senza contratto": 0}, [("Rossi",)])
    # domenica a mezzanotte la fine è esclusa: non vale più
    assert prova(DOMENICA, 2) == ("sì", {"sospeso": 0, "richiesta": 1, "senza contratto": 0}, [])
    assert prova(f"{LUNEDI}T09:00", 3) == ("sì", {"sospeso": 0, "richiesta": 1, "senza contratto": 0}, [])
    # mercoledì comincia la seconda; un istante prima no
    assert prova(f"{MARTEDI}T23:59:59", 4)[0] == "sì" and prova(MERCOLEDI, 5) == ("no", {"sospeso": 1, "richiesta": 1, "senza contratto": 0}, [("Rossi",)])
    # dicembre: il contratto è scaduto, e il segnale di assenza si accende anche se il fatto è ancora registrato
    assert prova("2026-12-01", 6) == ("no", {"sospeso": 0, "richiesta": 1, "senza contratto": 1}, [])
    # «come è registrato» mostra tutto, anche quello che adesso non vale; la risposta dice sempre che lettura ha fatto
    registrato = m.chiedi("in sospensione conta come è registrato")
    assert registrato["righe"] == [("2",)] and registrato["lettura"]["modo"] == "registrato" and registrato["lettura"]["valido_al"] is None
    adesso = m.chiedi("in contratto conta")
    assert adesso["righe"] == [] or adesso["righe"] == [("0",)]
    assert adesso["lettura"] == {"modo": "adesso", "registrato_fino_a": f"evento:{m.conteggi()['eventi'] - 1}", "valido_al": istante("2026-12-01")}
    # mettere da parte i fatti scaduti per rispondere non ha cambiato niente nella rete
    assert m.chiedi("in contratto conta come è registrato")["righe"] == [("1",)] and m.verifica_circuiti()["esito"] == "coerente"
    # una decisione vera presa sabato si rifà a dicembre con i segnali di sabato
    orologio.metti(f"{SABATO}T13:00")
    no = m.racconta("Rossi fa una richiesta", una_richiesta(7))
    orologio.metti("2026-12-02")
    assert no["esito"] == "violazione_regola" and m.verifica(no["evento"])["esito"] == "coerente"


def test_due_prenotazioni_servite_in_ordine_inverso_non_spendono_due_volte(tmp_path: Path, orologio: Orologio) -> None:
    """Un controesempio della revisione: fido 800, due ordini da 600. Quello arrivato alle 10 è servito per primo; quello
    arrivato alle 9 dopo. Sul suo istante di arrivo il secondo vede 800 e passerebbe: ma viene rivalutato all'istante
    di scrittura, dove il primo ordine c'è già, ed è rifiutato. Il fido non scende sotto zero."""
    memoria = Memoria(tmp_path / "rete", scritture=True)
    prepara(memoria)
    assert memoria.racconta("Fido di Rossi", [fatto("fido accordato", "Rossi", 800)])["esito"] == "applicato"
    rete = memoria.rete
    n = rete.n
    un_ordine = lambda numero: [(n("ordine ricevuto"), [(n("cliente"), n("Rossi")), (n("importo"), n("600")), (n("numero"), n(numero))])]
    alle_9, alle_10 = istante(f"{LUNEDI}T09:00"), istante(f"{LUNEDI}T10:00")
    orologio.metti(f"{LUNEDI}T10:00:05")  # il primo viene servito cinque secondi dopo il suo arrivo
    primo = rete.racconta("ordine arrivato alle 10", "sito", un_ordine("O10"), quando=alle_10)
    assert rete.e_quando[primo] == alle_10 and rete.e_scritto[primo] == istante(f"{LUNEDI}T10:00:05")
    orologio.metti(f"{LUNEDI}T10:00:06")
    with pytest.raises(modulo_motore.Vietato):
        rete.racconta("ordine arrivato alle 9", "sito", un_ordine("O9"), quando=alle_9)
    secondo = len(rete.e_frase) - 1
    assert rete.e_tipo[secondo] == "rifiuto" and _fido(memoria) == "200"
    decisione = rete.e_nota[secondo]["ricevuta"]["decisione"]
    assert [x["valore"] for x in decisione["regole"][0]["letture"]] == ["800", "200"] and decisione["regole"][0]["verdetto"] == "passa"
    scrittura = decisione["alla_scrittura"]
    assert scrittura["istante"] == istante(f"{LUNEDI}T10:00:06") and scrittura["regole"][0]["verdetto"] == "vieta"
    assert [x["valore"] for x in scrittura["regole"][0]["letture"]] == ["200", "-400"]
    # il primo, servito poco dopo il suo arrivo senza niente in mezzo, dice solo che la seconda valutazione è uguale
    assert rete.e_nota[primo]["ricevuta"]["decisione"]["alla_scrittura"] == {"istante": rete.e_scritto[primo], "come_all_arrivo": True}
    assert [memoria.verifica(e)["esito"] for e in (primo, secondo)] == ["coerente", "coerente"]
    # se l'orologio torna indietro, l'istante di scrittura non lo segue
    orologio.metti(f"{DOMENICA}T10:00")
    dopo = memoria.racconta("Rossi paga 10 euro", [fatto("pagamento incassato", "Rossi", 10, causale="P1")])
    e = int(dopo["evento"].removeprefix("evento:"))
    assert rete.e_scritto[e] == rete.e_scritto[secondo] and rete.e_quando[e] == istante(f"{DOMENICA}T10:00")
    assert list(rete.e_scritto) == sorted(rete.e_scritto) and memoria.verifica(e)["esito"] == "coerente"
    memoria.chiudi()
    riaperta = Memoria(tmp_path / "rete")
    assert list(riaperta.rete.e_scritto) == list(rete.e_scritto) and [riaperta.verifica(x)["esito"] for x in (primo, secondo, e)] == ["coerente"] * 3
    riaperta.chiudi()


def test_come_risultava_guarda_quando_e_stato_scritto_non_quando_e_arrivato(m: Memoria, orologio: Orologio) -> None:
    """Un controesempio della revisione: un pagamento di 400 arriva alle 9, viene scritto alle 11 e vale da
    domenica. «Come risultava alle 10» deve rispondere 50, non 450: alle 10 non era ancora scritto."""
    rete = m.rete
    n = rete.n
    orologio.metti(f"{LUNEDI}T11:00")
    e = rete.racconta("pagamento arrivato alle 9", "banca", [
        (n("pagamento incassato"), [(n("cliente"), n("Rossi")), (n("importo"), n("400")), (n("causale"), n("P1")), (n("vale da"), n(DOMENICA))])],
        quando=istante(f"{LUNEDI}T09:00"))
    assert (rete.e_quando[e], rete.e_scritto[e]) == (istante(f"{LUNEDI}T09:00"), istante(f"{LUNEDI}T11:00"))
    assert _pagato(m, f" come risultava il {LUNEDI}T10:00") == [("50",)]
    assert _pagato(m, f" come risultava il {LUNEDI}T11:00") == [("450",)] and _pagato(m, f" come valeva il {LUNEDI}T10:00") == [("450",)]
    assert m.chiedi(f"in pagamento incassato conta come risultava il {LUNEDI}T10:00")["lettura"]["registrato_fino_a"] == f"evento:{e - 1}"


@pytest.mark.parametrize("precisione", [4, 28])
def test_la_somma_nella_lingua_e_il_circuito_danno_lo_stesso_numero(tmp_path: Path, orologio: Orologio, precisione: int) -> None:
    """Un controllo della revisione: due pagamenti da 800,000001 e 400,000001. Con una precisione di quattro cifre la somma
    nella lingua dava 1200 e il circuito 1200,000002. Devono coincidere qualunque sia la precisione di chi chiama."""
    import decimal

    memoria = Memoria(tmp_path / "rete", scritture=True)
    prepara(memoria)
    with decimal.localcontext() as contesto:
        contesto.prec = precisione
        for i, importo in enumerate(("800.000001", "400.000001")):
            assert memoria.racconta("un pagamento", [fatto("pagamento incassato", "Rossi", importo, causale=f"P{i}")])["esito"] == "applicato"
        for clausola in ("", " come valeva adesso", " come è registrato"):
            assert _pagato(memoria, clausola) == [("1200.000002",)]
        assert _fido(memoria) == "1200.000002"
    memoria.chiedi("in pagamento incassato somma importo per cliente")["righe"] == [("Rossi", "1200.000002")]
    memoria.chiudi()


def test_un_fatto_senza_periodo_vale_da_quando_e_scritto_in_tutte_le_letture(tmp_path: Path, orologio: Orologio) -> None:
    """I due criteri di chiusura della revisione. Primo: due pagamenti senza periodo, alle 9 e alle 11; riportando
    l'orologio alle 10, la domanda senza clausola, «come valeva adesso» e il circuito rispondono tutti con il solo
    primo; soltanto «come è registrato» conta anche il secondo. Secondo: un pagamento senza periodo arrivato alle 9 e
    scritto alle 11 vale dalle 11, perché vale da quando è scritto: alle 10 non figura."""
    memoria = Memoria(tmp_path / "rete", scritture=True)
    prepara(memoria)
    orologio.metti(f"{LUNEDI}T09:00")
    assert memoria.racconta("primo pagamento", [fatto("pagamento incassato", "Rossi", "800.000001", causale="P1")])["esito"] == "applicato"
    orologio.metti(f"{LUNEDI}T11:00")
    assert memoria.racconta("secondo pagamento", [fatto("pagamento incassato", "Rossi", "400.000001", causale="P2")])["esito"] == "applicato"
    eventi = memoria.conteggi()

    def letture() -> tuple:
        return (_pagato(memoria), _pagato(memoria, " come valeva adesso"), _fido(memoria), _pagato(memoria, " come è registrato"))

    assert letture() == ([("1200.000002",)], [("1200.000002",)], "1200.000002", [("1200.000002",)])
    orologio.metti(f"{LUNEDI}T10:00")
    assert letture() == ([("800.000001",)], [("800.000001",)], "800.000001", [("1200.000002",)])
    assert memoria.chiedi("in pagamento incassato leggi causale")["righe"] == [("P1",)]
    assert memoria.chiedi("in pagamento incassato leggi causale")["lettura"]["modo"] == "adesso"
    orologio.metti(f"{LUNEDI}T08:00")
    assert letture()[2:] == ("0", [("1200.000002",)]) and letture()[0] in ([], [("0",)]) and letture()[1] in ([], [("0",)])
    orologio.metti(f"{LUNEDI}T12:00")
    assert letture() == ([("1200.000002",)], [("1200.000002",)], "1200.000002", [("1200.000002",)])
    assert memoria.conteggi() == eventi and memoria.verifica_circuiti()["esito"] == "coerente"  # leggere non ha scritto né spostato niente
    # secondo criterio: arrivato alle 9 di martedì, scritto alle 11
    rete = memoria.rete
    n = rete.n
    orologio.metti(f"{MARTEDI}T11:00")
    e = rete.racconta("pagamento arrivato alle 9 e scritto alle 11", "banca", [
        (n("pagamento incassato"), [(n("cliente"), n("Rossi")), (n("importo"), n("100")), (n("causale"), n("P3"))])], quando=istante(f"{MARTEDI}T09:00"))
    assert (rete.e_quando[e], rete.e_scritto[e]) == (istante(f"{MARTEDI}T09:00"), istante(f"{MARTEDI}T11:00"))
    assert _pagato(memoria, f" come valeva il {MARTEDI}T10:00") == [("1200.000002",)] and _pagato(memoria, f" come valeva il {MARTEDI}T11:00") == [("1300.000002",)]
    assert _pagato(memoria, f" come risultava il {MARTEDI}T10:00") == [("1200.000002",)]
    orologio.metti(f"{MARTEDI}T10:00")
    assert letture() == ([("1200.000002",)], [("1200.000002",)], "1200.000002", [("1300.000002",)])
    orologio.metti(f"{MARTEDI}T11:00")
    assert letture() == ([("1300.000002",)], [("1300.000002",)], "1300.000002", [("1300.000002",)])
    assert memoria.verifica(e)["esito"] == "coerente" and memoria.verifica_circuiti()["esito"] == "coerente"
    memoria.rete.istantanea()
    memoria.chiudi()
    riaperta = Memoria(tmp_path / "rete")
    orologio.metti(f"{MARTEDI}T10:00")
    assert (_pagato(riaperta), _fido(riaperta)) == ([("1200.000002",)], "1200.000002")
    riaperta.chiudi()


def test_una_scadenza_che_viene_prima_della_scrittura_e_rifiutata(tmp_path: Path, orologio: Orologio) -> None:
    """Un controesempio della revisione: un pagamento arriva alle 9 con solo «vale fino a» le 10, e viene scritto alle 11.
    All'arrivo il periodo era buono; ma un fatto senza inizio vale da quando è scritto, cioè dalle 11, dopo la sua
    scadenza. Va rifiutato prima di scrivere: altrimenti il giornale non si rilegge più."""
    memoria = Memoria(tmp_path / "rete", scritture=True)
    prepara(memoria)
    orologio.metti(f"{LUNEDI}T08:00")
    assert memoria.racconta("Fido di Rossi", [fatto("fido accordato", "Rossi", 800)])["esito"] == "applicato"
    rete = memoria.rete
    n = rete.n
    prima = (memoria.conteggi(), _fido(memoria))
    orologio.metti(f"{LUNEDI}T11:00")
    segno = len(rete.nomi)
    un_pagamento = lambda: [(n("pagamento incassato"), [(n("cliente"), n("Rossi")), (n("importo"), n("100")), (n("vale fino a"), n(f"{LUNEDI}T10:00"))])]
    with pytest.raises(ValueError, match="finisce prima che il fatto venga scritto"):
        rete.racconta("arrivato alle 9, scritto alle 11, vale fino alle 10", "banca", un_pagamento(), quando=istante(f"{LUNEDI}T09:00"), neuroni_da=segno)
    orologio.metti(f"{LUNEDI}T08:30")
    assert (memoria.conteggi(), _fido(memoria)) == prima
    assert Rete.com_era(str(tmp_path / "rete"), prima[0]["eventi"])[0].valori == rete.valori  # il giornale si rilegge
    # lo stesso pagamento servito subito, alle 9, ha un periodo buono: dalle 9 alle 10
    orologio.metti(f"{LUNEDI}T09:00")
    e = rete.racconta("arrivato e scritto alle 9, vale fino alle 10", "banca", un_pagamento())
    assert _fido(memoria) == "900" and memoria.verifica(e)["esito"] == "coerente"
    orologio.metti(f"{LUNEDI}T10:00")
    assert _fido(memoria) == "800"
    # attraverso la memoria: una scadenza già passata è un racconto rifiutato, e non lascia niente
    conteggi = memoria.conteggi()
    with pytest.raises(Rifiutato, match="periodo di validità"):
        memoria.racconta("Un pagamento già scaduto", [fatto("pagamento incassato", "Rossi", 5, causale="P9", **{"vale fino a": f"{LUNEDI}T09:30"})])
    assert memoria.conteggi() == conteggi and memoria.verifica_circuiti()["esito"] == "coerente"
    memoria.chiudi()
    riaperta = Memoria(tmp_path / "rete")
    assert _fido(riaperta) == "800"
    riaperta.chiudi()
