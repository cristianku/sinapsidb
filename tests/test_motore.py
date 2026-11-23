"""Prove del motore a soglia: risposte giuste, domande a più indizi, regole, annullamento, disco, arresto brusco."""

import os
import random
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from banco_motore import Banco, Modello, confronta, domande, genera  # noqa: E402

from sinapsidb.motore import Ambiguo, Rete, Vietato  # noqa: E402

# Un processo che scrive fatti con il disco sincronizzato e stampa quanti ne ha confermati: serve a ucciderlo a metà.
SCRITTORE = f"""
import sys
sys.path.insert(0, {str(Path(__file__).parent)!r})
from banco_motore import Banco, genera
from sinapsidb.motore import Rete
e, fatti = genera(200000)
rete = Rete(sys.argv[1], sync="frase")
banco = Banco(rete, e)
for i, f in enumerate(fatti):
    banco.scrivi(i, f)
    print(i + 1, flush=True)
"""


def stato(r: Rete):
    """Tutto quello che conta della rete, in una forma confrontabile."""
    def norma(d):
        return {k: {n: sorted(v) if isinstance(v, list) else [v] for n, v in sub.items()} for k, sub in d.items() if sub}
    return (list(r.nomi), r.f_tipo.tolist(), r.f_n.tolist(), r.f_nato.tolist(), r.f_morto.tolist(), r.s_ruolo.tolist(), r.s_a.tolist(),
            norma(r.vivi), norma(r.morti), norma(r.coppie), list(r.e_frase), list(r.e_tipo), list(r.e_origine))


def test_banco_uguale_al_modello():
    for n in (2000, 10000):
        e, fatti = genera(n)
        rete = Rete()
        banco = Banco(rete, e)
        for i, f in enumerate(fatti):
            banco.scrivi(i, f)
        q = domande(fatti, 3000)
        assert confronta(banco, Modello(fatti), q) == 0
        # la forma generale (lista di indizi) deve dare le stesse risposte dei percorsi preparati
        for s, r in q["A"]:
            assert banco.A_generale(s, r) == banco.A(s, r)


def test_domande_con_piu_indizi():
    """Fatti con un numero variabile di partecipanti, domande con 1, 2 o 3 indizi, contro la scansione di tutti i fatti."""
    rng = random.Random(3)
    rete = Rete()
    tipi = [rete.n(f"tipo{i}") for i in range(5)]
    ruoli = [rete.n(f"ruolo{i}") for i in range(6)]
    cose = [rete.n(f"cosa{i}") for i in range(120)]
    vivi = {}
    for i in range(4000):
        tipo = rng.choice(tipi)
        legami = [(r, rng.choice(cose)) for r in rng.sample(ruoli, rng.randint(1, 4))]
        if rng.random() < 0.1:  # a volte lo stesso ruolo due volte, verso due cose diverse
            legami.append((legami[0][0], rng.choice(cose)))
        via = [rng.choice(list(vivi))] if vivi and rng.random() < 0.25 else []
        rete.racconta(f"fatto {i}", "prova", [(tipo, legami)], via)
        for f in via:
            del vivi[f]
        vivi[len(rete.f_tipo) - 1] = (tipo, set(legami))
    domande_fatte = 0
    for _ in range(6000):
        tipo = rng.choice(tipi)
        if rng.random() < 0.7 and vivi:  # indizi presi da un fatto vero, così molte domande hanno risposta
            t, lg = vivi[rng.choice(list(vivi))]
            tipo, indizi = t, rng.sample(sorted(lg), min(len(lg), rng.randint(1, 3)))
        else:
            indizi = [(rng.choice(ruoli), rng.choice(cose)) for _ in range(rng.randint(1, 3))]
        attesi = sorted(f for f, (t, lg) in vivi.items() if t == tipo and all(x in lg for x in indizi))
        assert sorted(rete.scatta(tipo, indizi)) == attesi, (tipo, indizi)
        domande_fatte += 1


def ordini(rete):
    n = rete.n
    v = {k: n(k) for k in ("è un", "cosa", "tipo", "ordine", "contiene", "nell'ordine", "articolo", "quantità", "stato", "di", "valore", "confermato")}
    rete.proponi_regola("Un ordine confermato deve contenere almeno un articolo", "Nora", "ordine non vuoto", 2, "vieta", [
        {"nome": "ordine confermato", "modo": "presenza", "osserva": v["stato"], "lega": v["di"], "peso": 1, "con_ruolo": v["valore"], "con_valore": v["confermato"]},
        {"nome": "ordine vuoto", "modo": "assenza", "osserva": v["contiene"], "lega": v["nell'ordine"], "peso": 1},
    ])
    return v


def test_regola_dell_ordine_non_vuoto():
    rete = Rete()
    v = ordini(rete)
    n = rete.n
    o417, o500, pickup = n("ordine 417"), n("ordine 500"), n("pickup")
    riga = lambda o, q: (v["contiene"], [(v["nell'ordine"], o), (v["articolo"], pickup), (v["quantità"], n(str(q)))])
    rete.racconta("Rossi ha ordinato due pickup", "ChatGPT", [(v["è un"], [(v["cosa"], o417), (v["tipo"], v["ordine"])]), riga(o417, 2)])
    f_riga = rete.scatta(v["contiene"], [(v["nell'ordine"], o417)])[0]
    rete.racconta("Porta a tre i pickup dell'ordine 417", "ChatGPT", [riga(o417, 3)], [f_riga])
    storia = rete.storia(v["contiene"], v["nell'ordine"], o417, v["quantità"])
    assert [rete.nomi[x[0]] for x in storia] == ["2", "3"] and storia[0][2] == 2 and storia[1][2] == -1
    rete.racconta("Il sito conferma l'ordine 417", "sito", [(v["stato"], [(v["di"], o417), (v["valore"], v["confermato"])])])
    f_riga = rete.scatta(v["contiene"], [(v["nell'ordine"], o417)])[0]
    prima = stato(rete)
    esito = rete.racconta("E se spostassi il pickup nell'ordine 500?", "magazzino", [riga(o500, 3)], [f_riga], prova=True)
    assert esito["regole_scattate"] and esito["regole_scattate"][0]["su"] == "ordine 417" and stato(rete) == prima
    try:
        rete.racconta("Il magazzino sposta il pickup nell'ordine 500", "magazzino", [riga(o500, 3)], [f_riga])
        raise AssertionError("doveva essere vietato")
    except Vietato as e:
        spiegazione = e.args[0][0]
        assert spiegazione["attivazione"] == 2 and spiegazione["soglia"] == 2 and [s[2] for s in spiegazione["segnali"]] == [1, 1]
    assert rete.e_tipo[-1] == "rifiuto" and rete.scatta(v["contiene"], [(v["nell'ordine"], o417)]) == (f_riga,)
    rete.racconta("Aggiunge una riga e sposta la prima, insieme", "magazzino", [riga(o417, 1), riga(o500, 3)], [f_riga])
    assert len(rete.scatta(v["contiene"], [(v["nell'ordine"], o417)])) == 1
    try:  # confermare un ordine senza righe note
        rete.racconta("Conferma l'ordine 900", "sito", [(v["stato"], [(v["di"], n("ordine 900")), (v["valore"], v["confermato"])])])
        raise AssertionError("doveva essere vietato")
    except Vietato:
        o900 = rete.trova("ordine 900")
        assert len(o900) == 1 and rete.intorno(o900[0]) == []  # il nome resta come neurone, senza nessun fatto


def test_annullamento_e_riapertura_dal_disco(tmp_path):
    cartella = str(tmp_path / "annulla")
    rng = random.Random(11)
    rete = Rete(cartella, sync="os")
    v = ordini(rete)
    n = rete.n
    articoli = [n(f"articolo {i}") for i in range(30)]
    nomi_ordini = [f"ordine {i}" for i in range(300)]
    conta = {"scritte": 0, "prove": 0, "rifiuti": 0}
    for passo in range(3000):
        o = n(rng.choice(nomi_ordini))
        righe = rete.scatta(v["contiene"], [(v["nell'ordine"], o)])
        righe = list(righe)
        mossa = rng.random()
        fatti, via = [], []
        if mossa < 0.45:
            fatti = [(v["contiene"], [(v["nell'ordine"], o), (v["articolo"], rng.choice(articoli)), (v["quantità"], n(str(rng.randint(1, 9))))])]
        elif mossa < 0.75 and righe:
            via = [rng.choice(righe)]
            if rng.random() < 0.5:  # trasferimento a un altro ordine
                altro = n(rng.choice(nomi_ordini))
                fatti = [(v["contiene"], [(v["nell'ordine"], altro)] + [x for x in rete.legami(via[0]) if x[0] != v["nell'ordine"]])]
        elif not rete.scatta(v["stato"], [(v["di"], o)]):
            fatti = [(v["stato"], [(v["di"], o), (v["valore"], v["confermato"])])]
        else:
            continue
        prova = rng.random() < 0.15
        prima = stato(rete) if passo % 40 == 0 else None
        try:
            esito = rete.racconta(f"passo {passo}", "prova", fatti, via, prova=prova)
            if prova:
                conta["prove"] += 1
                if prima is not None:
                    assert stato(rete) == prima
            else:
                conta["scritte"] += 1
        except Vietato:
            conta["rifiuti"] += 1
            if prima is not None:  # resta solo l'evento di rifiuto
                dopo = stato(rete)
                assert dopo[:10] == prima[:10] and len(dopo[10]) == len(prima[10]) + 1
    for o in set(sum((r.per_nome.get(x, []) for r in [rete] for x in map(str.casefold, nomi_ordini)), [])):
        confermato = bool(rete.scatta(v["stato"], [(v["di"], o), (v["valore"], v["confermato"])]))
        assert not (confermato and not rete.scatta(v["contiene"], [(v["nell'ordine"], o)]))
    in_memoria = stato(rete)
    rete.istantanea()
    for i in range(200):  # altre scritture dopo l'istantanea: all'avvio va riletta solo la coda del giornale
        rete.racconta(f"dopo {i}", "prova", [(v["contiene"], [(v["nell'ordine"], n(rng.choice(nomi_ordini))), (v["articolo"], articoli[0]), (v["quantità"], n("1"))])])
    finale = stato(rete)
    rete.chiudi()
    assert finale != in_memoria
    con_istantanea = Rete(cartella)
    assert stato(con_istantanea) == finale and len(con_istantanea.regole) == 1
    con_istantanea.chiudi()
    os.remove(os.path.join(cartella, "istantanea"))
    dal_giornale = Rete(cartella)
    assert stato(dal_giornale) == finale and len(dal_giornale.regole) == 1
    dal_giornale.chiudi()


def test_arresto_brusco_durante_le_scritture(tmp_path):
    cartella = str(tmp_path / "arresto")
    for giro in range(2):
        shutil.rmtree(cartella, ignore_errors=True)
        figlio = subprocess.Popen([sys.executable, "-c", SCRITTORE, cartella], stdout=subprocess.PIPE, text=True)
        confermati = 0
        fine = time.time() + 0.25 + 0.15 * giro
        while time.time() < fine:
            riga = figlio.stdout.readline()
            if riga:
                confermati = int(riga)
        figlio.send_signal(signal.SIGKILL)
        figlio.wait()
        rete = Rete(cartella)
        letti = len(rete.e_frase)
        assert letti >= confermati > 0, (letti, confermati)
        e = max(1000, 200000 // 10)
        _, fatti = genera(200000)
        assert confronta(Banco(rete, e), Modello(fatti[:letti]), domande(fatti[:letti], 500)) == 0
        rete.chiudi()
        # una coda scritta a metà: un pezzo di record in fondo al giornale deve essere tagliato
        with open(os.path.join(cartella, "giornale"), "ab") as f:
            f.write(b"\x40\x00\x00\x00\x12\x34\x56\x78{\"mezzo record")
        rete = Rete(cartella)
        assert len(rete.e_frase) == letti
        rete.racconta("dopo la riparazione", "prova", [])
        rete.chiudi()
        assert len(Rete(cartella).e_frase) == letti + 1


def test_nomi_e_ambiguita():
    rete = Rete()
    n = rete.n
    assert n("Greta") == n(" GRETA ") == n("grèta")
    a, b = rete.nuovo("Rossi"), rete.nuovo("Rossi")
    cliente, citta = n("cliente di"), n("città")
    rete.racconta("Mario Rossi è cliente a Verona", "prova", [(cliente, [(n("chi"), a), (citta, n("Verona"))])])
    rete.racconta("Lucia Rossi è cliente a Lugano", "prova", [(cliente, [(n("chi"), b), (citta, n("Lugano"))])])
    try:
        n("rossi")
        raise AssertionError("doveva essere ambiguo")
    except Ambiguo as e:
        candidati = e.args[0]
        assert len(candidati) == 2 and "Verona" in candidati[0][2][0] and "Lugano" in candidati[1][2][0]
