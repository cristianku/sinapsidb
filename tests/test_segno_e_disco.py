"""Quello che il sesto giro di revisione ha aperto, oltre ai suoi casi: una scrittura che non arriva
intera sul disco non lascia niente in memoria; il segno si rilegge a ogni scrittura; un tratto si attesta legandolo al
contenuto del giornale; le domande preparate restano legate a chi avevano trovato."""

import errno
import json
import shutil
from pathlib import Path

import pytest

from sinapsidb import attesta, lingua, motore
from sinapsidb.memoria import Memoria
from sinapsidb.motore import Rete, SegnoGuasto, Vietato
from test_decisioni import _record, _riscrivi
from test_revisione_circuiti_090 import aperta, evento


DATI = Path(__file__).parent / "dati"
MISTA = DATI / "storico-096-093-096"  # 0.9.6 scrive l'evento 0, la 0.9.3 gli eventi 1-5, la 0.9.6 l'evento 6


def nota(m, nome, **altro):
    return m.racconta(f"Nota {nome}", [{"tipo": "nota", "legami": {"cosa": nome}}], chiave=nome, **altro)


def fido(m):
    r, n = m.rete, m.rete.n
    r.proponi_circuito("Saldo", "test", "fido", [
        {"osserva": n(t), "lega": n("cliente"), "somma": n("importo"), "peso": k} for t, k in (("accredito", 1), ("ordine", -1))])
    r.proponi_regola("Fido", "test", "limite", 1, "vieta", [{"nome": "negativo", "circuito": n("fido"), "limite": 0, "peso": 1}])


def movimento(m, tipo, cliente, importo, chiave):
    return m.racconta(f"{tipo} di {cliente}", [{"tipo": tipo, "legami": {"cliente": cliente, "importo": importo}}], chiave=chiave)


def stato(m):
    """Quello che deve restare identico se una scrittura non avviene."""
    r = m.rete
    return (m.conteggi(), list(r.nomi), len(r.f_tipo), dict(r.valori) if isinstance(r.valori, dict) else r.valori, len(r.e_nota))


# --- una scrittura arriva intera sul disco, oppure non è successa ----------------------------------------------------

def guasta(monkeypatch, r, *, scrivi=None, sincronizza=None, tronca=None):
    """Fa fallire le chiamate al disco sul giornale di questa rete. ``scrivi``: quanti byte lasciar passare prima
    dell'errore (None: nessun errore). ``sincronizza``: quante sincronizzazioni far fallire."""
    vere = (motore.os.write, motore.os.fdatasync, motore.os.ftruncate)
    passati, da_fallire = 0, int(sincronizza or 0)

    def write(fd, dati):
        nonlocal passati
        if fd != r._fd or scrivi is None:
            return vere[0](fd, dati)
        if passati >= scrivi:
            raise OSError(errno.ENOSPC, "disco pieno")
        passati += (quanti := vere[0](fd, dati[:scrivi - passati]))
        return quanti

    def fdatasync(fd):
        nonlocal da_fallire
        if fd == r._fd and da_fallire:
            da_fallire -= 1
            raise OSError(errno.EIO, "non si sincronizza")
        return vere[1](fd)

    def ftruncate(fd, lunghezza):
        if fd == r._fd and tronca:
            raise OSError(errno.EIO, "non si tronca")
        return vere[2](fd, lunghezza)

    monkeypatch.setattr(motore.os, "write", write)
    monkeypatch.setattr(motore.os, "fdatasync", fdatasync)
    monkeypatch.setattr(motore.os, "ftruncate", ftruncate)


@pytest.mark.parametrize("dove", ["a-meta-record", "sincronizzando"])
def test_una_scrittura_fallita_non_lascia_niente_né_in_memoria_né_nel_giornale(tmp_path, monkeypatch, dove):
    c = tmp_path / "rete"
    with aperta(Memoria(c, scritture=True)) as m:
        fido(m)
        assert movimento(m, "accredito", "A", "100", "a1")["esito"] == "applicato"
        prima, giornale = stato(m), (c / "giornale").read_bytes()
        with monkeypatch.context() as mp:
            guasta(mp, m.rete, scrivi=9 if dove == "a-meta-record" else None, sincronizza=1 if dove == "sincronizzando" else 0)
            with pytest.raises(OSError):
                movimento(m, "ordine", "A", "30", "o1")
        # il pezzo di record arrivato sul disco è stato tolto, e la rete è quella di prima: anche le somme dei circuiti
        assert (c / "giornale").read_bytes() == giornale and stato(m) == prima
        assert m.valore("fido", "A")["valore"] == "100" and m.verifica_circuiti()["esito"] == "coerente" and m.ricevuta_di("o1") is None
        dopo = movimento(m, "ordine", "A", "30", "o2")  # la stessa rete continua, con i numeri giusti
        assert dopo["esito"] == "applicato" and evento(dopo) == 3 and m.valore("fido", "A")["valore"] == "70"
        m.rete.istantanea()
    with aperta(Memoria(c)) as riaperta:
        assert riaperta.ricevuta_di("o1") is None and riaperta.ricevuta_di("o2") == dopo
        assert riaperta.valore("fido", "A")["valore"] == "70" and [riaperta.verifica(e)["esito"] for e in range(4)] == ["coerente"] * 4


def test_se_il_record_a_meta_non_si_riesce_a_togliere_la_rete_si_ferma_fino_alla_riapertura(tmp_path, monkeypatch):
    c = tmp_path / "rete"
    with aperta(Memoria(c, scritture=True)) as m:
        nota(m, "A")
        prima = stato(m)
        with monkeypatch.context() as mp:
            guasta(mp, m.rete, scrivi=9, tronca=True)
            with pytest.raises(OSError):
                nota(m, "B")
        assert stato(m) == prima  # la memoria è tornata indietro; è la coda del giornale che non si sa com'è
        for scrive in (lambda: nota(m, "C"), m.rete.istantanea):
            with pytest.raises(RuntimeError, match="riapert"):
                scrive()
        assert stato(m) == prima and not (c / "istantanea").exists()
        assert nota(m, "C", prova=True)["esito"] == "prova" and m.verifica(0)["esito"] == "coerente"  # leggere e provare si può
    with aperta(Memoria(c, scritture=True)) as riaperta:  # chi riapre taglia la coda che non si rilegge
        assert riaperta.conteggi()["eventi"] == 1 and riaperta.ricevuta_di("B") is None
        assert evento(nota(riaperta, "C")) == 1


def test_un_no_che_non_arriva_sul_disco_non_resta_nemmeno_in_memoria(tmp_path, monkeypatch):
    c = tmp_path / "rete"
    with aperta(Memoria(c, scritture=True)) as m:
        fido(m)
        prima, giornale = stato(m), (c / "giornale").read_bytes()
        with monkeypatch.context() as mp:
            guasta(mp, m.rete, scrivi=0)
            with pytest.raises(OSError):
                movimento(m, "ordine", "B", "60", "o1")
        assert stato(m) == prima and (c / "giornale").read_bytes() == giornale and m.ricevuta_di("o1") is None
        no = movimento(m, "ordine", "B", "60", "o1")
        assert no["esito"] == "violazione_regola" and evento(no) == 2 and m.verifica(2)["esito"] == "coerente"


def test_se_chi_prepara_la_nota_fallisce_il_fatto_non_resta(tmp_path):
    c = tmp_path / "rete"
    with aperta(Rete(str(c), decisioni=True)) as r:
        n = r.n
        r.racconta("Prima", "test", [(n("nota"), [(n("cosa"), n("A"))])])
        prima, giornale, segno = (len(r.nomi), len(r.f_tipo), len(r.e_frase)), (c / "giornale").read_bytes(), len(r.nomi)

        def rotta(decisione):
            raise KeyError("chi serializza la ricevuta si è rotto")

        with pytest.raises(KeyError):
            r.racconta("Seconda", "test", [(n("nota"), [(n("cosa"), n("B"))])], neuroni_da=segno, nota_della_scrittura=rotta)
        for inservibile in (lambda d: {"ricevuta": {"decisione": d, "oggetto": object()}},):  # una nota che non si può scrivere nel giornale
            with pytest.raises(TypeError):
                r.racconta("Terza", "test", [(n("nota"), [(n("cosa"), n("C"))])], neuroni_da=segno, nota_della_scrittura=inservibile)
        assert (len(r.nomi), len(r.f_tipo), len(r.e_frase)) == prima and (c / "giornale").read_bytes() == giornale
        assert r.racconta("Quarta", "test", [(n("nota"), [(n("cosa"), n("D"))])]) == 1


def test_il_modo_nella_decisione_e_del_motore_anche_se_la_nota_ne_scrive_un_altro(tmp_path):
    c = tmp_path / "rete"
    with aperta(Memoria(c, scritture=True)) as m:
        r, n = m.rete, m.rete.n
        fido(m)
        falsa = lambda d: {"ricevuta": {"evento": "evento:2", "esito": "applicato", "decisione": {**d, "calcolo": 2}}}
        assert r.racconta("Accredito", "test", [(n("accredito"), [(n("cliente"), n("A")), (n("importo"), n("70"))])], nota_della_scrittura=falsa) == 2
        with pytest.raises(Vietato):  # anche in un no
            r.racconta("Ordine", "test", [(n("ordine"), [(n("cliente"), n("B")), (n("importo"), n("60"))])],
                       nota_del_rifiuto=lambda regole, d: {"ricevuta": {"evento": "evento:3", "esito": "violazione_regola", "regole": regole,
                                                                        "decisione": {k: v for k, v in d.items() if k != "calcolo"}}})
    assert [x[7]["ricevuta"]["decisione"]["calcolo"] for x in _record(c)] == [4, 4, 4, 4]
    with aperta(Memoria(c)) as riaperta:
        assert [(v["esito"], v["calcolo"], v["calcolo_da"]) for v in map(riaperta.verifica, (2, 3))] == [("coerente", 4, "ricevuta")] * 2


# --- il segno ----------------------------------------------------------------------------------------------------

def test_un_segno_sparito_dopo_la_prima_scrittura_non_viene_riscritto(tmp_path):
    c = tmp_path / "rete"
    with aperta(Memoria(c, scritture=True)) as m:
        nota(m, "A")
        prima = (m.conteggi(), (c / "giornale").read_bytes())
        (c / "formato").unlink()
        with pytest.raises(SegnoGuasto, match="non c'è più"):
            nota(m, "B")
        assert (m.conteggi(), (c / "giornale").read_bytes()) == prima and not (c / "formato").exists()


def test_chi_riprende_a_scrivere_dopo_una_riga_scritta_a_mano_lo_dice(tmp_path):
    """Una riga di ``calcoli`` scritta a mano resta nel segno come indizio. Chi riprende a scrivere dichiara
    la propria frontiera; né la riga né la frontiera identificano l'autore di una ricevuta senza modo."""
    c = shutil.copytree(MISTA, tmp_path / "rete")
    (c / "formato").write_text(json.dumps({"formato": 2, "dal": 0, "calcoli": [[4, 0], [2, 1], [4, 6]], "calcolo_dal": 0}))
    with aperta(Memoria(c, scritture=True)) as m:
        assert nota(m, "terza sessione")["evento"] == "evento:7"
        assert m.segno() == {"formato": 2, "dal": 0, "calcoli": [[4, 0], [2, 1], [4, 6]], "calcolo_dal": 0, "dichiarano_da": [7]}
        nota(m, "ancora")
        assert m.segno()["dichiarano_da"] == [7]  # una volta sola: da lì il segno dice già quello che serve
    record = _record(c)
    del record[8][7]["ricevuta"]["decisione"]["calcolo"]  # come se l'evento 8 lo avesse scritto un programma che non dichiara
    _riscrivi(c, record)
    with aperta(Memoria(c)) as m:
        v = m.verifica(4)
        assert v["esito"] == "coerente" and "calcolo" not in v and v["calcoli_compatibili"] == [4, 3, 2, 1]
        x = m.riesamina(4, [5])
        assert x["cambia"] is None and x["ipotetico"]["esito"] == "indeterminato"
        v = m.verifica(8)
        assert v["esito"] == "coerente" and v["calcoli_compatibili"] == [4, 3, 2, 1] and "calcolo" not in v and "Può averla scritta" in v["spiegazione"]


def test_il_segno_si_confronta_con_il_giornale_vero_non_con_chi_ha_letto_tempo_fa(tmp_path):
    c = tmp_path / "rete"
    with aperta(Memoria(c, scritture=True)) as scrive:
        nota(scrive, "A")
        with aperta(Memoria(c)) as legge:  # ha letto un evento solo
            for x in "BCD":
                nota(scrive, x)
            segno = json.loads((c / "formato").read_text())
            (c / "formato").write_text(json.dumps({**segno, "calcoli": [[4, 0], [4, 3]]}))  # parla dell'evento 3, che c'è
            assert legge.conteggi()["eventi"] == 1 and legge.rete.eventi_nel_giornale() == 4
            assert legge.verifica(0)["esito"] == "coerente" and "guasto" not in legge.segno()
            (c / "formato").write_text(json.dumps({**segno, "calcoli": [[4, 0], [4, 5]]}))  # e dell'evento 5, che non c'è
            assert legge.verifica(0)["esito"] == "non_verificabile" and "guasto" in legge.segno()


# --- attestare un tratto, legandolo al giornale ---------------------------------------------------------------------

def test_un_tratto_attestato_vale_per_quel_giornale_e_non_per_un_altro(tmp_path):
    c = shutil.copytree(MISTA, tmp_path / "rete")
    giornale = (c / "giornale").read_bytes()
    tratto = Rete.attesta(str(c), 2, 1, 6, "registro degli   aggiornamenti: tra le 10 e le 11 era installata la 0.9.3")
    assert (c / "giornale").read_bytes() == giornale
    assert (tratto["modo"], tratto["da"], tratto["a"], tratto["fonte"]) == (2, 1, 6, "registro degli aggiornamenti: tra le 10 e le 11 era installata la 0.9.3")
    assert (tratto["byte"], tratto["sha256"]) == Rete.prefisso(str(c), 6) and tratto["byte"] < len(giornale)
    with aperta(Memoria(c, scritture=True)) as m:
        v = m.verifica(4)
        assert (v["esito"], v["calcolo"], v["calcolo_da"]) == ("coerente", 2, "attestazione") and "legato al suo contenuto" in v["spiegazione"]
        x = m.riesamina(4, [5])
        assert (x["calcolo"], x["calcolo_da"], x["ipotetico"]["esito"], x["cambia"]) == (2, "attestazione", "violazione_regola", False)
        assert [m.verifica(e)["calcolo_da"] for e in (0, 6)] == ["ricevuta", "ricevuta"]
        nota(m, "dopo")  # chi scrive dopo non perde l'attestazione
        assert m.segno()["attestati"] == [tratto] and m.verifica(4)["calcolo_da"] == "attestazione"
    # lo stesso segno accanto a un giornale lungo uguale ma con un altro contenuto: non è il suo
    altro = shutil.copytree(c, tmp_path / "altro")
    record = _record(altro)
    record[2][0] = "Un'altra storia"
    _riscrivi(altro, record)
    with aperta(Memoria(altro)) as m:
        v = m.verifica(4)
        assert v["esito"] == "non_verificabile" and "altro contenuto" in v["spiegazione"]
        with pytest.raises(SegnoGuasto):
            m.riesamina(4, [5])
        assert m.verifica(0)["esito"] == "coerente"  # quello che dichiara da sé il suo modo non ha bisogno del segno


def test_che_cosa_non_si_puo_attestare(tmp_path):
    c = shutil.copytree(MISTA, tmp_path / "rete")
    prima = [(c / f).read_bytes() for f in ("giornale", "formato")]
    for modo, da, a, fonte, perche in ((2, 0, 3, "x", "dichiara già"), (2, 4, 7, "x", "dichiara già"), (9, 1, 6, "x", "non è tra quelli noti"),
                                       (2, 1, 9, "x", "solo 7 eventi"), (2, 3, 3, "x", "viene dopo"), (2, 1, 6, "  ", "fonte"), (True, 1, 6, "x", "noti")):
        with pytest.raises(ValueError, match=perche):
            Rete.attesta(str(c), modo, da, a, fonte)
    assert [(c / f).read_bytes() for f in ("giornale", "formato")] == prima
    Rete.attesta(str(c), 2, 1, 4, "x")
    for da, a in ((1, 4), (3, 6), (2, 3)):
        with pytest.raises(ValueError, match="sovrappone"):
            Rete.attesta(str(c), 2, da, a, "x")
    Rete.attesta(str(c), 3, 4, 6, "x")  # accanto sì
    assert [(x["modo"], x["da"], x["a"]) for x in Rete.segno(str(c))["attestati"]] == [(2, 1, 4), (3, 4, 6)]
    (c / "formato").unlink()
    with pytest.raises(ValueError, match="non ha il segno"):
        Rete.attesta(str(c), 2, 1, 6, "x")
    # un segno con due tratti che si sovrappongono non si legge: ogni evento ne ha al più uno
    (c / "formato").write_text(json.dumps({"formato": 2, "dal": 0, "attestati": [
        {"modo": 2, "da": 1, "a": 4, "byte": 1, "sha256": "x"}, {"modo": 3, "da": 3, "a": 6, "byte": 1, "sha256": "x"}]}))
    with pytest.raises(SegnoGuasto):
        Rete.segno(str(c))


def test_il_comando_per_attestare(tmp_path, capsys):
    c = shutil.copytree(MISTA, tmp_path / "rete")
    assert attesta.main([str(c)]) == 0
    visto = json.loads(capsys.readouterr().out)
    assert [(t["da"], t["a"], t["stato"], t.get("da_dove")) for t in visto["tratti"]] == [
        (0, 1, "dichiarato", "ricevuta"), (1, 6, "non_dichiarato", None), (6, 7, "dichiarato", "ricevuta")]
    assert attesta.main([str(c), "2", "1", "6", "--fonte", "registro degli aggiornamenti"]) == 0
    assert json.loads(capsys.readouterr().out)["sha256"] == Rete.prefisso(str(c), 6)[1]
    assert attesta.main([str(c)]) == 0
    assert [(t["da"], t["a"], t.get("da_dove")) for t in json.loads(capsys.readouterr().out)["tratti"]] == [
        (0, 1, "ricevuta"), (1, 6, "attestazione"), (6, 7, "ricevuta")]
    assert attesta.main([str(c), "2", "1", "6", "--fonte", "di nuovo"]) == 1 and "sovrappone" in capsys.readouterr().err


# --- le domande preparate ------------------------------------------------------------------------------------------

def test_una_domanda_preparata_per_qualcuno_che_non_c_era_lo_trova_quando_nasce(tmp_path):
    with aperta(Rete(str(tmp_path / "rete"))) as r:
        n = r.n
        vendita = lambda chi, totale: [(n("vendita"), [(n("cliente"), n(chi)), (n("totale"), n(totale))])]
        r.racconta("Vendita", "test", vendita("Ada", "5"))
        frase = "accendi cliente = Zed in vendita leggi totale"
        p = lingua.prepara(r, frase)
        assert p.esegui() == []
        r.racconta("Vendita a Zed", "test", vendita("Zed", "9"))  # nessun ruolo nuovo: la rete non cambia forma
        assert p.esegui() == lingua.prepara(r, frase).esegui() == [("9",)]
        r.racconta("Un'omonima", "test", [(n("vendita"), [(n("cliente"), r.nuovo("Zed")), (n("totale"), n("1"))])])
        assert p.esegui() == [("9",)]  # una volta trovato, resta lui


def test_una_scrittura_fallita_non_lascia_il_mese_né_la_definizione_che_portava(tmp_path, monkeypatch):
    """Il ritorno indietro con le cose che nascono dentro la scrittura: il mese di un circuito raggruppato, e una
    definizione nuova. Dopo, la memoria deve essere quella che si rilegge dal giornale."""
    from test_revisione_circuiti_090 import circuito, vendita

    c = tmp_path / "rete"
    with aperta(Memoria(c, scritture=True)) as m:
        r = m.rete
        circuito(r, "totali")  # per mese
        vendita(m, "2027-03-01", "10")
        foto = lambda: (list(r.nomi), len(r.f_tipo), len(r.e_frase), json.dumps(r.valori, sort_keys=True, default=str),
                        json.dumps(r._presenze, sort_keys=True, default=str), [(k["id"], k["versione"]) for k in r.circuiti], len(r.regole))
        prima, giornale = foto(), (c / "giornale").read_bytes()
        with monkeypatch.context() as mp:
            guasta(mp, r, scrivi=20)
            with pytest.raises(OSError):
                vendita(m, "2027-04-02", "5")  # farebbe nascere il mese 2027-04
            with pytest.raises(OSError):
                circuito(r, "per cliente", per=None, lega="cliente")  # una definizione
        assert not r.trova("2027-04") and (c / "giornale").read_bytes() == giornale
        # i nomi che chi chiamava aveva già nominato restano in attesa del prossimo evento, come dopo un rifiuto
        assert foto()[1:] == prima[1:] and foto()[0][:len(prima[0])] == prima[0]
        assert m.verifica_circuiti()["esito"] == "coerente"
        x = vendita(m, "2027-04-02", "5")
        assert x["esito"] == "applicato" and evento(x) == 2
        valori = m.valori("totali")
        m.rete.istantanea()
    riletta, _ = Rete.com_era(str(c), 3)
    with aperta(Memoria(c)) as riaperta:
        assert riaperta.valori("totali") == valori == [{"di": "2027-03", "di_id": valori[0]["di_id"], "valore": "10"},
                                                      {"di": "2027-04", "di_id": valori[1]["di_id"], "valore": "5"}]
        assert riaperta.rete.nomi == riletta.nomi and riaperta.verifica_circuiti()["esito"] == "coerente"
        assert [riaperta.verifica(e)["esito"] for e in range(3)] == ["coerente"] * 3
