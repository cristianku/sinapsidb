"""Le scorciatoie prese per importare più in fretta non devono cambiare niente di quello che la rete scrive e decide.

L'importazione da PostgreSQL era lenta. Tre cose tolte dal percorso di ogni record: i tipi delle
definizioni cercati per nome a ogni scrittura, un codificatore del giornale nuovo a ogni evento, una chiamata al
sistema per ogni evento. Ognuna ha qui la prova che il risultato è lo stesso di prima.
"""

import errno
import json
import random
from types import SimpleNamespace

import pytest

from sinapsidb import motore
from sinapsidb.memoria import Memoria
from sinapsidb.motore import DefinizioneNonValida, Rete
from test_revisione_circuiti_090 import aperta


def vendita(r, chi, totale, giorno="2026-01-05"):
    n = r.n
    return r.racconta(f"Vendita a {chi}", "test", [(n("vendita"), [(n("cliente"), n(chi)), (n("quando"), n(giorno)), (n("totale"), n(totale))])])


# --- i tipi delle definizioni tenuti da parte ----------------------------------------------------------------------

def a_mano(r):
    """Come si calcolava a ogni scrittura, prima di tenerlo da parte."""
    return (r._t_regola >= 0 or r._id("circuito") >= 0, {r._id(x) for x in ("regola", "segnale", "circuito", "termine")} - {-1})


def test_quello_che_si_tiene_da_parte_e_quello_che_si_calcolerebbe(tmp_path):
    rng = random.Random(5)
    nomi = ["regola", "Regola", " SEGNALE ", "circuito", "termine", "cliente", "quando", "vale da", "altro", "Circuito"]
    with aperta(Rete(str(tmp_path / "rete"))) as r:
        for passo in range(300):
            mossa = rng.random()
            if mossa < 0.5:
                try:
                    r.n(rng.choice(nomi))
                except motore.Ambiguo:
                    pass  # ci sono già due omonimi: non nasce niente
            elif mossa < 0.65:
                r.nuovo(rng.choice(nomi))  # un omonimo
            elif mossa < 0.85 and len(r.nomi) > r._confermati:
                r.dimentica(rng.randrange(r._confermati, len(r.nomi)))
            else:
                r.racconta(f"Nota {passo}", "test", [(r.n("nota"), [(r.n("cosa"), r.n(f"X{passo}"))])])
            assert (r._definizioni or r._tipi_delle_definizioni()) == a_mano(r), passo
    with aperta(Rete(str(tmp_path / "rete"))) as riletta:
        assert (riletta._definizioni or riletta._tipi_delle_definizioni()) == a_mano(riletta)


def test_una_definizione_si_controlla_ancora_dalla_prima_scrittura_che_la_porta(tmp_path):
    with aperta(Rete(str(tmp_path / "rete"))) as r:
        n = r.n
        for i in range(3):  # scritture ordinarie: la rete non ha né regole né circuiti
            vendita(r, f"C{i}", "10")
        assert r._definizioni == (False, frozenset())
        # un fatto che si chiama «termine» in una rete senza circuiti è un fatto qualsiasi, come prima
        r.racconta("Il termine del contratto", "test", [(n("termine"), [(n("contratto"), n("K1")), (n("quando"), n("2027-01-01"))])])
        r.proponi_circuito("Ricavi", "test", "ricavi", [{"osserva": n("vendita"), "lega": n("cliente"), "somma": n("totale"), "peso": 1}])
        assert r._definizioni[0] and r.valore(n("ricavi"), n("C1")) == 10
        vendita(r, "C1", "5")
        assert r.valore(n("ricavi"), n("C1")) == 15
        # e adesso una definizione che non sta in piedi viene fermata: una regola che legge un circuito per mese
        r.proponi_circuito("Per mese", "test", "ricavi per mese", [{"osserva": n("vendita"), "lega": n("quando"), "somma": n("totale"), "peso": 1, "per": "mese"}])
        with pytest.raises(DefinizioneNonValida):
            r.proponi_regola("Limite", "test", "limite", 1, "vieta", [{"nome": "negativo", "circuito": n("ricavi per mese"), "limite": 0, "peso": 1}])
        # una regola provata e non scritta: i neuroni nati per lei si dimenticano, e con loro quello che si era tenuto
        segno = len(r.nomi)
        r.racconta("Prova", "test", [(n("regola"), [(n("regola di"), n("fantasma")), (n("soglia"), n("1")), (n("azione"), n("vieta"))])],
                   prova=True, neuroni_da=segno)
        assert len(r.nomi) == segno and (r._definizioni or r._tipi_delle_definizioni()) == a_mano(r)


# --- il giornale ------------------------------------------------------------------------------------------------

def test_il_codificatore_tenuto_da_parte_scrive_quello_che_scriveva_json_dumps():
    rng = random.Random(9)
    pezzi = ["", "Rossi", "è già qui", 'virgolette " e \\ barra', "riga\nnuova\ttab", "  \x00 \x1f", "🙂 emoji", "</script>", "1e5"]
    for _ in range(300):
        record = [rng.choice(pezzi) * rng.randint(0, 3), rng.choice(pezzi), rng.random() * 2e9, "scrittura", [rng.choice(pezzi) for _ in range(rng.randint(0, 4))],
                  [[rng.randrange(10**6), [rng.randrange(10**7) for _ in range(rng.randint(0, 8))]]], [rng.randrange(100) for _ in range(rng.randint(0, 2))]]
        if rng.random() < 0.3:
            record += [{"ricevuta": {"esito": "applicato", "decisione": {"formato": 2, "regole": [], "istante": rng.random()}}, "chiave": rng.choice(pezzi)}]
        assert motore._codifica(record) == json.dumps(record, ensure_ascii=False, separators=(",", ":"))


def storia(r, quanti=60):
    n = r.n
    r.proponi_circuito("Ricavi", "test", "ricavi", [{"osserva": n("vendita"), "lega": n("cliente"), "somma": n("totale"), "peso": 1}])
    for i in range(quanti):
        vendita(r, f"C{i % 7}", f"{i}.50", f"2026-{1 + i % 12:02d}-03")
        if i % 9 == 0:
            r.racconta("Storno", "test", ritira=[len(r.f_tipo) - 1])


def test_a_lotti_il_giornale_e_lo_stesso_byte_per_byte(tmp_path, monkeypatch):
    orologio = SimpleNamespace(time=lambda: 1_790_000_000.0)
    monkeypatch.setattr(motore, "time", orologio)
    with aperta(Rete(str(tmp_path / "uno"), sync="os")) as uno:
        storia(uno)
    with aperta(Rete(str(tmp_path / "lotti"), sync="os")) as r:
        with r.a_lotti():
            storia(r)
            assert r.sync == "lotti" and (tmp_path / "lotti" / "giornale").stat().st_size == 0  # sono ancora in attesa
            assert r.eventi_nel_giornale() == len(r.e_frase)  # chi conta gli eventi sul disco li fa arrivare
        assert r.sync == "os" and not r._in_attesa
        assert (tmp_path / "lotti" / "giornale").read_bytes() == (tmp_path / "uno" / "giornale").read_bytes()
        assert r._scritti == (tmp_path / "lotti" / "giornale").stat().st_size
    with aperta(Rete(str(tmp_path / "lotti"))) as riletta:
        assert riletta.conteggi() == uno.conteggi() and riletta.valori == uno.valori


def test_un_gruppo_grande_arriva_sul_disco_da_solo_e_l_istantanea_lo_aspetta(tmp_path, monkeypatch):
    monkeypatch.setattr(motore, "LOTTO", 2000)
    with aperta(Rete(str(tmp_path / "rete"), sync="os")) as r:
        with r.a_lotti():
            storia(r, quanti=40)
            sul_disco = (tmp_path / "rete" / "giornale").stat().st_size
            assert 0 < sul_disco < r._scritti + len(r._in_attesa) and r._scritti == sul_disco
            r.istantanea()  # dice fin dove arriva il giornale: non può dirlo di eventi che non ci sono
            assert not r._in_attesa and (tmp_path / "rete" / "giornale").stat().st_size == r._scritti
            vendita(r, "Dopo", "1")
        attesi = (r.conteggi(), r.valori)
    with aperta(Rete(str(tmp_path / "rete"))) as riletta:  # dall'istantanea più la coda del giornale
        assert (riletta.conteggi(), riletta.valori) == attesi


def test_se_un_gruppo_non_arriva_sul_disco_la_rete_si_ferma(tmp_path, monkeypatch):
    with aperta(Rete(str(tmp_path / "rete"), sync="os")) as r:
        vendita(r, "Prima", "1")
        scrivi = motore.os.write
        with pytest.raises(OSError):
            with r.a_lotti():
                for i in range(5):
                    vendita(r, f"C{i}", "2")
                monkeypatch.setattr(motore.os, "write", lambda fd, dati: (_ for _ in ()).throw(OSError(errno.ENOSPC, "disco pieno")))
        monkeypatch.setattr(motore.os, "write", scrivi)
        assert r.sync == "os" and r.fuori_uso and not r._in_attesa
        for scrive in (lambda: vendita(r, "Dopo", "3"), r.istantanea, r.scarica):
            with pytest.raises(RuntimeError, match="riapert"):
                scrive()
        assert r.eventi_nel_giornale() == 1 and len(r.e_frase) == 6  # sul disco ce n'è uno: la memoria non fa più testo
    with aperta(Rete(str(tmp_path / "rete"))) as riaperta:  # nel giornale c'è quello che c'era prima del gruppo
        assert riaperta.conteggi()["fatti"] == 1 and vendita(riaperta, "Dopo", "3") == 1


def test_dove_ogni_scrittura_va_confermata_non_si_scrive_a_lotti(tmp_path):
    with aperta(Memoria(tmp_path / "rete", scritture=True)) as m:
        with pytest.raises(ValueError, match="decisioni"):
            with m.rete.a_lotti():
                pass
        assert m.rete.sync == "frase"
    with pytest.raises(ValueError, match="decisioni"):
        Rete(str(tmp_path / "altra"), sync="lotti", decisioni=True)
    r = Rete()  # una rete senza giornale: non c'è niente da mettere in fila
    with r.a_lotti():
        vendita(r, "C", "1")
    assert r.conteggi()["fatti"] == 1 and not r._in_attesa


def test_un_neurone_nasce_uguale_da_n_e_da_nuovo():
    r = Rete()
    a = r.nuovo("  Vale   DA ")
    b = r.n("Città")  # ``n`` ha già ridotto il nome per cercarlo, e non lo riduce una seconda volta
    assert r.per_nome == {"vale da": [a], "citta": [b]} and r._r_da == a and r.n("citta") == b and r.n("VALE da") == a
    assert r.n("Quando") in r.ruoli_coppia and r.nuovo("quando") in r.ruoli_coppia
    with pytest.raises(TypeError):  # la chiave già pronta non è un argomento per chi chiama da fuori
        r.nuovo("CIRCUITO", "CIRCUITO")


# --- molti fatti in una volta ------------------------------------------------------------------------------------

def _fatti_a_caso(seme, quanti=150):
    """Fatti di più tipi, in un ordine a caso: nomi nuovi e ripetuti, numeri, date, un tipo senza forma fissa."""
    rng = random.Random(seme)
    giorni = [f"2026-{mese:02d}-{giorno:02d}" for mese in (1, 2, 12) for giorno in (3, 17)]
    for i in range(quanti):
        scelta = rng.random()
        if scelta < 0.6:
            yield f"Vendita {i} a C{i % 9}", "vendita", [("cliente", f"C{rng.randrange(9)}"), ("quando", rng.choice(giorni)), ("totale", f"{rng.randint(1, 900)}.{rng.randint(0, 99):02d}")]
        elif scelta < 0.8:
            yield f"Nota {i}: «virgolette», \\ e\nuna riga", "nota", [("cosa", f"X{i}")] + ([("su", f"C{rng.randrange(9)}")] if rng.random() < 0.5 else [])
        else:
            yield f"Pagamento {i} 🙂", "pagamento", [("cliente", f"C{rng.randrange(9)}"), ("importo", str(rng.randint(1, 50)))]


def _uno_alla_volta(r, origine, eventi):
    quanti = 0
    for frase, tipo, legami in eventi:
        r.racconta(frase, origine, [(tipo, legami)])
        quanti += 1
    return quanti


def _prepara(r, che):
    """Quello che c'è nella rete prima dei fatti: niente, un circuito, un circuito per mese, una regola, un periodo."""
    n = r.n
    if che in ("circuito", "regola"):
        r.proponi_circuito("Ricavi", "test", "ricavi", [{"osserva": n("vendita"), "lega": n("cliente"), "somma": n("totale"), "peso": 1}])
    if che == "per mese":
        r.proponi_circuito("Per mese", "test", "ricavi per mese", [{"osserva": n("vendita"), "lega": n("quando"), "somma": n("totale"), "peso": 1, "per": "mese"}])
    if che == "regola":
        r.proponi_regola("Limite", "test", "limite", 1, "vieta", [{"nome": "negativo", "circuito": n("ricavi"), "limite": 0, "peso": 1}])
    if che == "periodo":
        r.racconta("Un listino", "test", [(n("listino"), [(n("prodotto"), n("P1")), (n("vale da"), n("2026-01-01"))])])


@pytest.mark.parametrize("che", ["niente", "circuito", "per mese", "regola", "periodo", "decisioni"])
def test_molti_fatti_in_una_volta_sono_gli_stessi_fatti_uno_alla_volta(tmp_path, monkeypatch, che):
    monkeypatch.setattr(motore, "time", SimpleNamespace(time=lambda: 1_790_000_000.25))
    reti = {}
    for come in ("uno", "molti"):
        r = reti[come] = Rete(str(tmp_path / come), sync="os", decisioni=che == "decisioni")
        _prepara(r, che)
        n = r.n
        eventi = ((frase, n(tipo), [(n(ruolo), n(nome)) for ruolo, nome in legami]) for frase, tipo, legami in _fatti_a_caso(3))
        assert (r.racconta_molti if come == "molti" else lambda o, e: _uno_alla_volta(r, o, e))("importazione", eventi) == 150
        r.chiudi()
    assert (tmp_path / "molti" / "giornale").read_bytes() == (tmp_path / "uno" / "giornale").read_bytes()
    a, b = reti["uno"], reti["molti"]
    for campo in ("nomi", "per_nome", "vivi", "morti", "coppie", "forma", "valori", "_presenze", "e_frase", "e_origine", "e_tipo", "e_nota", "_confermati"):
        assert getattr(a, campo) == getattr(b, campo), campo
    for colonna in ("f_tipo", "f_inizio", "f_n", "f_nato", "f_morto", "s_ruolo", "s_a", "e_quando", "e_scritto"):
        assert getattr(a, colonna) == getattr(b, colonna), colonna
    with aperta(Rete(str(tmp_path / "molti"))) as riletta:
        assert riletta.conteggi() == a.conteggi() and riletta.valori == a.valori


def test_in_mezzo_ai_molti_una_definizione_e_un_fatto_senza_legami_li_tratta_racconta(tmp_path, monkeypatch):
    monkeypatch.setattr(motore, "time", SimpleNamespace(time=lambda: 1_790_000_000.25))
    with aperta(Rete(str(tmp_path / "rete"))) as r:
        n = r.n
        vendita = lambda chi, totale: ("Vendita", n("vendita"), [(n("cliente"), n(chi)), (n("totale"), n(totale))])
        r.proponi_circuito("Ricavi", "test", "ricavi", [{"osserva": n("vendita"), "lega": n("cliente"), "somma": n("totale"), "peso": 1}])
        # un termine in più arriva in mezzo ai fatti, come un fatto: è una definizione, e la tratta ``racconta``
        suo, = r.scatta(n("termine"), [(n("termine di"), n("ricavi"))])
        resi = ("I resi tolgono", n("termine"), [(k, n("reso") if k == n("osserva") else n("-1") if k == n("peso") else v) for k, v in r.legami(suo)])
        reso = ("Un reso", n("reso"), [(n("cliente"), n("A")), (n("totale"), n("4"))])
        versione = r.circuiti[0]["versione"]
        assert r.racconta_molti("test", [vendita("A", "10"), resi, reso, vendita("A", "5")]) == 4
        assert r.circuiti[0]["versione"] != versione and r.valore(n("ricavi"), n("A")) == 11
        with pytest.raises(ValueError, match="almeno un legame"):
            r.racconta_molti("test", [vendita("B", "1"), ("Vuoto", n("vendita"), [])])
        assert r.valore(n("ricavi"), n("B")) == 1 and len(r.e_frase) == 6  # il primo è entrato, il secondo no
        # un valore che il circuito non sa sommare: non entra, e la rete resta com'era
        prima = (len(r.nomi), len(r.f_tipo), len(r.e_frase), dict(r.valori[n("ricavi")]))
        segno = len(r.nomi)
        with pytest.raises(motore.NonNumerico):
            r.racconta_molti("test", [("Vendita strana", n("vendita"), [(n("cliente"), n("A")), (n("totale"), n("tanto"))])])
        r.dimentica(segno)  # «tanto» lo aveva nominato chi chiamava: resta suo, come con ``racconta``
        assert (len(r.nomi), len(r.f_tipo), len(r.e_frase), dict(r.valori[n("ricavi")])) == prima


def test_se_il_giornale_non_riceve_un_fatto_dei_molti_quel_fatto_non_resta(tmp_path, monkeypatch):
    with aperta(Rete(str(tmp_path / "rete"), sync="os")) as r:
        n = r.n
        vendita = lambda i: (f"Vendita {i}", n("vendita"), [(n("cliente"), n(f"C{i}")), (n("totale"), n(str(i)))])
        assert r.racconta_molti("test", (vendita(i) for i in range(3))) == 3
        giornale, prima, scrivi = (tmp_path / "rete" / "giornale").read_bytes(), (len(r.f_tipo), len(r.e_frase), r._confermati), motore.os.write
        monkeypatch.setattr(motore.os, "write", lambda fd, dati: (_ for _ in ()).throw(OSError(errno.ENOSPC, "disco pieno")))
        with pytest.raises(OSError):
            r.racconta_molti("test", [vendita(7)])
        monkeypatch.setattr(motore.os, "write", scrivi)
        assert (len(r.f_tipo), len(r.e_frase), r._confermati) == prima and (tmp_path / "rete" / "giornale").read_bytes() == giornale
        assert r.racconta_molti("test", [vendita(8)]) == 1 and len(r.e_frase) == 4
    with aperta(Rete(str(tmp_path / "rete"))) as riletta:
        assert riletta.conteggi()["fatti"] == 4 and not riletta.trova("Vendita 7")


def test_se_l_orologio_torna_indietro_i_molti_passano_da_racconta(tmp_path, monkeypatch):
    ore = iter([1_790_000_100.0, 1_790_000_100.0] + [1_790_000_050.0] * 20)
    monkeypatch.setattr(motore, "time", SimpleNamespace(time=lambda: next(ore)))
    with aperta(Rete(str(tmp_path / "rete"))) as r:
        n = r.n
        nota = lambda i: (f"Nota {i}", n("nota"), [(n("cosa"), n(f"X{i}"))])
        assert r.racconta_molti("test", [nota(0), nota(1), nota(2)]) == 3
        assert list(r.e_quando) == [1_790_000_100.0, 1_790_000_100.0, 1_790_000_050.0]
        assert list(r.e_scritto) == [1_790_000_100.0] * 3  # l'istante di scrittura non torna indietro


def test_di_un_gruppo_interrotto_resta_nel_giornale_solo_quello_che_e_arrivato_intero(tmp_path, monkeypatch):
    """Dalla revisione: dopo un errore a metà gruppo il gruppo non si rimanda, altrimenti quello che era già arrivato
    finisce scritto due volte. Resta il prefisso di eventi interi, e la rete si ferma."""
    c = tmp_path / "rete"
    with aperta(Rete(str(c), sync="os")) as r:
        vendita(r, "Prima", "1")
        vero, chiamate = motore.os.write, []

        def a_meta(fd, dati):  # lascia passare il primo evento del gruppo e mezzo secondo, poi il disco è pieno
            if fd != r._fd:
                return vero(fd, dati)
            chiamate.append(len(dati))
            if len(chiamate) > 1:
                raise OSError(errno.ENOSPC, "disco pieno")
            primo = 8 + int.from_bytes(bytes(dati[:4]), "little")
            return vero(fd, dati[:primo + 11])

        with pytest.raises(OSError):
            with r.a_lotti():
                for i in range(4):
                    vendita(r, f"C{i}", "2")
                monkeypatch.setattr(motore.os, "write", a_meta)
        monkeypatch.setattr(motore.os, "write", vero)
        assert len(chiamate) == 2 and r.fuori_uso and r.eventi_nel_giornale() == 2
        assert r._scritti == (c / "giornale").stat().st_size  # il mezzo evento è stato tagliato
    with aperta(Rete(str(c))) as riaperta:
        assert riaperta.e_frase == ["Vendita a Prima", "Vendita a C0"] and riaperta.conteggi()["fatti"] == 2


def test_il_modo_di_sincronizzare_si_controlla_quando_si_sceglie(tmp_path):
    with aperta(Memoria(tmp_path / "memoria", scritture=True)) as m:
        m.racconta("Nota", [{"tipo": "nota", "legami": {"cosa": "X"}}])
        with pytest.raises(ValueError, match="decisioni"):
            m.rete.sync = "lotti"
        with pytest.raises(ValueError, match="non è un modo"):
            m.rete.sync = "veloce"
        assert m.rete.sync == "frase"
    # lo stesso giornale riaperto senza dire che registra le decisioni: lo dice il suo segno
    for apri in (lambda: Rete(str(tmp_path / "memoria"), sync="lotti"), lambda: Rete(str(tmp_path / "memoria")).a_lotti().__enter__()):
        with pytest.raises(ValueError, match="decisioni"):
            apri()


def test_chi_passa_i_due_istanti_non_fa_guardare_l_orologio(tmp_path, monkeypatch):
    letture = []
    monkeypatch.setattr(motore, "time", SimpleNamespace(time=lambda: letture.append(1) or 500.0))
    r = Rete()
    n = r.n
    r.racconta("Con i due istanti", "test", [(n("nota"), [(n("cosa"), n("A"))])], quando=100.0, scritto=100.0)
    assert not letture
    r.racconta("Con l'arrivo soltanto", "test", [(n("nota"), [(n("cosa"), n("B"))])], quando=120.0)
    r.racconta("Senza niente", "test", [(n("nota"), [(n("cosa"), n("C"))])])
    assert len(letture) == 2 and list(r.e_quando) == [100.0, 120.0, 500.0] and list(r.e_scritto) == [100.0, 500.0, 500.0]
