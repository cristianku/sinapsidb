"""Banco di prova comune: gli stessi fatti e le stesse cinque domande usati per gli altri prototipi."""
import random

from sinapsidb.motore import Rete


def genera(n):
    rng = random.Random(42)
    e = max(1000, n // 10)
    fatti = []
    for _ in range(n):
        s = int(e * rng.random() ** 2)
        r = rng.randrange(20)
        o = int(e * rng.random() ** 2)
        d = rng.randrange(365)
        fatti.append((s, r, o, d))
    return e, fatti


def frase(i, f):
    return f"frase {i}: ent{f[0]} rel{f[1]} ent{f[2]} giorno {f[3]}"


class Banco:
    """Collega il banco alla rete: ogni fatto del banco è un neurone a soglia con tre sinapsi."""

    def __init__(self, rete: Rete, e: int):
        n = rete.n
        self.rete = rete
        self.SOGG, self.OGG, self.QUANDO = n("soggetto"), n("oggetto"), n("quando")
        self.rel = [n(f"rel{j}") for j in range(20)]
        self.giorno = [n(f"giorno {d}") for d in range(365)]
        self.ent = [n(f"ent{k}") for k in range(e)]
        self.base = self.ent[0]
        # percorsi preparati, uno per relazione: dal soggetto all'oggetto e dall'oggetto al soggetto
        self.p_avanti = [rete.percorso(t, self.SOGG, self.OGG) for t in self.rel]
        self.p_indietro = [rete.percorso(t, self.OGG, self.SOGG) for t in self.rel]

    def scrivi(self, i, f, origine="banco"):
        s, r, o, d = f
        tipo, sogg = self.rel[r], self.ent[s]
        vecchio = self.p_avanti[r][0].get(sogg)
        return self.rete.racconta(frase(i, f), origine,
                                  [(tipo, [(self.SOGG, sogg), (self.OGG, self.ent[o]), (self.QUANDO, self.giorno[d])])],
                                  () if vecchio is None else (vecchio,))

    # Le cinque domande, tutte attraverso l'attivazione a soglia della rete.
    def A(self, s, r):
        return self.rete.segui(self.p_avanti[r], self.ent[s])

    def B(self, r, o):
        return self.rete.segui(self.p_indietro[r], self.ent[o])

    def A_generale(self, s, r):
        return self.rete.rispondi(self.rel[r], [(self.SOGG, self.ent[s])], self.OGG)

    def C(self, e, d):
        return self.rete.insieme(self.ent[e], self.giorno[d])

    def D(self, r1, o, r2):
        segui, avanti = self.rete.segui, self.p_avanti[r2]
        out = []
        for s in segui(self.p_indietro[r1], self.ent[o]):
            for o2 in segui(avanti, s):
                out.append((s, o2))
        return out

    def E(self, s, r):
        return self.rete.storia_di(self.p_avanti[r], self.ent[s])

    def A_per_nome(self, s, r):
        n = self.rete.n
        return self.rete.rispondi(n(f"rel{r}"), [(self.SOGG, n(f"ent{s}"))], self.OGG)


class Modello:
    """Le risposte giuste, calcolate con dizionari semplici direttamente dai fatti."""

    def __init__(self, fatti):
        self.cur, self.storia, self.giorni = {}, {}, {}
        for i, (s, r, o, d) in enumerate(fatti):
            st = self.storia.setdefault((s, r), [])
            if st:
                st[-1] = (st[-1][0], st[-1][1], i)
            st.append((o, i, -1))
            self.cur[(s, r)] = o
            self.giorni.setdefault((s, d), []).append(i)
            if o != s:
                self.giorni.setdefault((o, d), []).append(i)
        self.inverso = {}
        for (s, r), o in self.cur.items():
            self.inverso.setdefault((r, o), set()).add(s)
        self.fatti = fatti

    def A(self, s, r):
        return [self.cur[(s, r)]] if (s, r) in self.cur else []

    def B(self, r, o):
        return self.inverso.get((r, o), set())

    def C(self, e, d):
        return [(i, frase(i, self.fatti[i])) for i in self.giorni.get((e, d), [])]

    def D(self, r1, o, r2):
        return {(s, self.cur[(s, r2)]) for s in self.inverso.get((r1, o), ()) if (s, r2) in self.cur}

    def E(self, s, r):
        return self.storia.get((s, r), [])


def domande(fatti, quante=10000):
    """Parametri delle domande, presi da valori che esistono nei fatti."""
    rng = random.Random(7)
    n = len(fatti)
    q = {"A": [], "B": [], "C": [], "D": [], "E": []}
    for k in range(quante):
        s, r, o, d = fatti[rng.randrange(n)]
        q["A"].append((s, r))
        s, r, o, d = fatti[rng.randrange(n)]
        q["B"].append((r, o))
        s, r, o, d = fatti[rng.randrange(n)]
        q["C"].append((s if k % 2 == 0 else o, d))
        s, r, o, d = fatti[rng.randrange(n)]
        q["D"].append((r, o, rng.randrange(20)))
        s, r, o, d = fatti[rng.randrange(n)]
        q["E"].append((s, r))
    return q


def confronta(banco, modello, q):
    """Numero di risposte diverse tra la rete e il modello, su tutte le domande."""
    b = banco.base
    diverse = 0
    for s, r in q["A"]:
        diverse += [x - b for x in banco.A(s, r)] != modello.A(s, r)
    for r, o in q["B"]:
        diverse += {x - b for x in banco.B(r, o)} != modello.B(r, o)
    for e, d in q["C"]:
        diverse += banco.C(e, d) != modello.C(e, d)
    for r1, o, r2 in q["D"]:
        diverse += {(s - b, o2 - b) for s, o2 in banco.D(r1, o, r2)} != modello.D(r1, o, r2)
    for s, r in q["E"]:
        diverse += [(v - b, nato, morto) for v, nato, morto in banco.E(s, r)] != modello.E(s, r)
    return diverse
