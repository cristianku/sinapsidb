"""Genera il piano JSONL o registra le osservazioni sul socket comune. Non calcola esiti attesi."""

from __future__ import annotations

import argparse
import itertools
import json
import random
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path

from .cliente import Cliente


class Piano:
    def __init__(self, seme):
        self.seme, self.n, self.gruppo = seme, 0, 0

    def riga(self, op, *, attore=0, insieme=False, fase=None, perdi=False, **campi):
        self.n += 1
        if not insieme:
            self.gruppo += 1
        return {"seme": self.seme, "gruppo": self.gruppo, "richiedente": attore,
                **({"fase": fase} if fase else {}), **({"perdita": perdi} if perdi else {}),
                "richiesta": {"op": op, "id_tentativo": f"{self.seme}:{self.n}", **campi}}

    def scrivi(self, op, chiave, cliente, importo, **campi):
        rif = {"accorda": "atto", "ordine": "numero", "pagamento": "causale"}[op]
        return self.riga(op, chiave=chiave, cliente=cliente, importo=importo, **{rif: chiave}, **campi)


def collaudo(seme=417):
    """Casi bitemporali 1–11, concorrenza, lettura prima del retry. Parte da un archivio nuovo."""
    p = Piano(seme)
    ieri = "2000-01-01"  # esplicitamente anteriore a tutte le decisioni del collaudo
    yield p.riga("stato", fase="inizio")
    yield p.scrivi("accorda", "FC", "Rossi", "800")
    yield p.scrivi("accorda", "FD", "Bianchi", "100")
    yield p.scrivi("ordine", "O1", "Rossi", "1000")
    yield p.scrivi("ordine", "O2", "Rossi", "1000")
    yield p.scrivi("ordine", "OD", "Bianchi", "300")
    yield p.scrivi("pagamento", "A", "Rossi", "400", vale_da=ieri)
    yield p.riga("riesamina", evento="@O1", rettifiche=["@A"])
    yield p.riga("riesamina", evento="@O2", rettifiche=["@A"])
    yield p.riga("candidate", rettifica="@A", riesamina=True)
    yield p.scrivi("pagamento", "B", "Rossi", "-500", vale_da=ieri)
    yield p.riga("riesamina", evento="@O1", rettifiche=["@A", "@B"])
    yield p.riga("riesamina", evento="@O1", fino_a="@B")
    yield p.riga("riesamina", evento="@O1", rettifiche=["@A"])
    yield p.riga("fido", cliente="Rossi")

    yield p.scrivi("accorda", "FE", "Verdi", "1200")
    yield p.scrivi("ordine", "SI", "Verdi", "1000")
    yield p.scrivi("pagamento", "CONFINE", "Verdi", "0.000001")
    yield p.scrivi("pagamento", "ADDEBITO", "Verdi", "-300", vale_da=ieri,
                   vale_fino_a={"scritto_di": "CONFINE"})
    yield p.riga("riesamina", evento="@SI", rettifiche=[])
    yield p.riga("riesamina", evento="@SI", rettifiche=["@ADDEBITO"])
    yield p.riga("fido", cliente="Verdi")
    yield p.riga("candidate", rettifica="@ADDEBITO", riesamina=True)

    # Stessa disponibilità oggi, due decisioni a cavallo della decorrenza del reso.
    yield p.scrivi("ordine", "O3", "Rossi", "1000")
    yield p.scrivi("pagamento", "ENTRA", "Rossi", "400", vale_da=ieri)
    yield p.scrivi("pagamento", "ESCE", "Rossi", "-400", vale_da={"scritto_di": "CONFINE"})
    for e in ("ENTRA", "ESCE"):
        yield p.riga("candidate", rettifica="@"+e, riesamina=True)
    for e in ("O1", "O3"):
        yield p.riga("riesamina", evento="@"+e, rettifiche=["@ENTRA", "@ESCE"])

    yield p.riga("sposta", chiave="S", causale="A", cliente="Bianchi", importo="400", vale_da=ieri)
    yield p.riga("candidate", rettifica="@S", riesamina=True)
    yield p.riga("riesamina", evento="@OD", rettifiche=["@S"])  # manca il pagamento originario
    yield p.riga("riesamina", evento="@OD", rettifiche=["@A", "@S"])
    yield p.riga("storno", chiave="ST", causale="A")
    yield p.riga("riesamina", evento="@OD", rettifiche=["@ST"])
    yield p.riga("riesamina", evento="@OD", rettifiche=["@A", "@S", "@ST"])
    yield p.riga("verifica", evento="@O1")

    yield p.scrivi("accorda", "FG", "Conteso", "1000")
    for i in range(8):
        yield p.scrivi("ordine", f"G{i}", "Conteso", "160", attore=i, insieme=i > 0)
    yield p.riga("fido", cliente="Conteso")
    yield p.scrivi("ordine", "ACK", "Rossi", "5000", perdi="dopo_commit")
    yield p.scrivi("pagamento", "PERSA", "Bianchi", "1.123456", perdi="prima_della_consegna")
    yield p.riga("stato", fase="ripresa")
    yield p.riga("stato", chiave="ACK")
    yield p.riga("stato", chiave="PERSA")
    yield p.riga("stato", chiave="mai-inviata")
    yield p.scrivi("ordine", "ACK", "Rossi", "5000")
    yield p.scrivi("pagamento", "PERSA", "Bianchi", "1.123456")
    # Nel collaudo (non nel percorso cronometrato) si rilegge OGNI chiave,
    # anche i no. Un evento mancante non è nascosto dall'ultimo saldo corretto.
    for k in ("FC FD O1 O2 OD A B FE SI CONFINE ADDEBITO O3 ENTRA ESCE S ST FG ACK PERSA".split()
              + [f"G{i}" for i in range(8)]):
        yield p.riga("stato", chiave=k)
        yield p.riga("riesamina", evento="@"+k, rettifiche=[])
    yield p.riga("fido", cliente="Rossi")
    yield p.riga("stato", fase="fine")


def carico(seme, clienti, operazioni, quota_letture, richiedenti, distribuzione):
    """Piano ripetibile, inizializzazione e controlli distinguibili dal carico.

    Le causali da stornare o da spostare vengono da pagamenti di gruppi precedenti.
    Le candidate si interrogano fuori dal gruppo.
    """
    if clienti < 1 or operazioni < 1 or not 0 <= quota_letture <= 100 or richiedenti < 1:
        raise ValueError("dimensioni del carico non valide")
    rng, p = random.Random(seme), Piano(seme)
    yield p.riga("stato", fase="inizio")
    for c in range(clienti):
        yield p.scrivi("accorda", f"F{c}", f"C{c}", "1000", fase="preparazione")
    disponibili = []
    for gruppo in range(0, operazioni, richiedenti):
        rettifiche, nuovi = [], []
        for i in range(min(richiedenti, operazioni - gruppo)):
            limite = max(1, clienti // 100) if distribuzione == "contesa" and rng.randrange(2) else clienti
            c, k = f"C{rng.randrange(limite)}", f"T{gruppo+i}"
            comuni = {"attore": i, "insieme": i != 0, "fase": "carico"}
            if rng.randrange(100) < quota_letture:
                yield p.riga("fido", cliente=c, **comuni)
                continue
            mossa = rng.randrange(10)
            v = rng.randrange(1, 100_000)
            importo = f"{v // 100}.{v % 100:02}"
            if mossa < 6:
                yield p.scrivi("ordine", k, c, importo, **comuni)
            elif mossa < 9:
                yield p.scrivi("pagamento", k, c, importo, **comuni)
                if distribuzione != "rettifiche" or c == "C0":
                    nuovi.append((k, c, importo))
            else:
                modo = rng.randrange(4)
                if modo >= 2 and disponibili and not (modo == 3 and distribuzione == "rettifiche"):
                    # Estrazione senza reinserimento, O(1), anche se poi il motore rifiuta la rettifica.
                    j = rng.randrange(len(disponibili))
                    causale, di, somma = disponibili[j]
                    disponibili[j] = disponibili[-1]
                    disponibili.pop()
                    if modo == 2 or clienti == 1:
                        yield p.riga("storno", chiave=k, causale=causale, **comuni)
                    else:
                        if c == di:
                            c = f"C{(int(di[1:])+1) % clienti}"
                        yield p.riga("sposta", chiave=k, causale=causale, cliente=c, importo=somma, vale_da="2000-01-01", **comuni)
                else:
                    yield p.scrivi("pagamento", k, "C0" if distribuzione == "rettifiche" else c,
                                   ("-" if modo == 1 else "") + importo, vale_da="2000-01-01", **comuni)
                rettifiche.append(k)
        disponibili.extend(nuovi)
        for k in rettifiche:
            yield p.riga("candidate", rettifica="@"+k, riesamina=True, fase="ricerca")
    yield p.riga("stato", fase="fine")


def selettivita(seme, altri, decisioni_cliente):
    """`altri` può crescere 10× a candidate fisse; `decisioni_cliente` cresce a zero esiti cambiati."""
    if altri < 0 or decisioni_cliente < 1:
        raise ValueError("numero di clienti o di decisioni non valido")
    p = Piano(seme)
    yield p.riga("stato", fase="inizio")
    yield p.scrivi("accorda", "F", "Bersaglio", str(decisioni_cliente + 1))
    for i in range(altri):
        yield p.scrivi("accorda", f"AF{i}", f"Altro{i}", "10", fase="preparazione")
        yield p.scrivi("ordine", f"AO{i}", f"Altro{i}", "1", fase="preparazione")
    for i in range(decisioni_cliente):
        yield p.scrivi("ordine", f"O{i}", "Bersaglio", "1")
    yield p.scrivi("pagamento", "R", "Bersaglio", "0.000001", vale_da="2000-01-01")
    yield p.riga("candidate", rettifica="@R", riesamina=True, fase="ricerca")
    yield p.riga("stato", fase="fine")


def risolvi(q, eventi):
    """Il piano è comune: identifica gli eventi con le chiavi, non con ID di uno schieramento."""
    q = q.copy()
    for campo in ("evento", "rettifica", "fino_a"):
        if campo in q:
            q[campo] = eventi[q[campo].removeprefix("@")]["evento"]
    if "rettifiche" in q:
        q["rettifiche"] = [eventi[x.removeprefix("@")]["evento"] for x in q["rettifiche"]]
    for campo in ("vale_da", "vale_fino_a"):
        if isinstance(q.get(campo), dict):
            t = eventi[q[campo]["scritto_di"]]["scritto"]
            q[campo] = datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="microseconds")
    return q


def registra(piano, socket, *, dopo_ripresa=None, precedenti=()):
    """Registra anche invio e ritorno in caso di perdita della risposta.

    `dopo_ripresa`, solo nel collaudo sotto il controllo del chiamante, può
    riavviare il processo sullo stesso socket. Il client si riconnette da zero.
    """
    eventi, connessioni = {}, {}
    def ricorda(l):
        r, q = l["risposta"], l["richiesta"]
        if r and "evento" in r and "scritto" in r and "chiave" in q:
            eventi[q["chiave"]] = r
    for l in precedenti:
        ricorda(l)
    with ExitStack() as stack:
        pool = stack.enter_context(ThreadPoolExecutor(max_workers=16))
        for _, righe in itertools.groupby(piano, key=lambda r: r["gruppo"]):
            gruppo = list(righe)
            if any(l.get("fase") == "ripresa" for l in gruppo):
                for c in connessioni.values():
                    c.chiudi()
                connessioni.clear()
                if dopo_ripresa:
                    dopo_ripresa()
            if len({l["richiedente"] for l in gruppo}) != len(gruppo):
                raise ValueError("due richieste contemporanee sulla stessa connessione")
            lavori = []
            for l in gruppo:
                a = l["richiedente"]
                if a not in connessioni:
                    c = connessioni[a] = Cliente(socket)
                    stack.callback(c.chiudi)
                q = risolvi(l["richiesta"], eventi)
                lavori.append(pool.submit(_tentativo, connessioni[a], l, q))
            for lavoro in lavori:
                l = lavoro.result()
                ricorda(l)
                yield l


def _tentativo(c, l, q):
    utc = time.time_ns()
    inizio = time.monotonic_ns()
    perdita = l.get("perdita")
    try:
        r = None if perdita == "prima_della_consegna" else c.chiedi(**q)
    except OSError:
        if perdita != "in_volo":
            raise
        r = None
    fine, fine_utc = time.monotonic_ns(), time.time_ns()
    # Punto di guasto deterministico: si sopprime la consegna al chiamante.
    # Il contenuto scartato non si usa neppure per risolvere @chiave.
    persa = r is None or perdita == "dopo_commit"
    return {**l, "richiesta": q, "invio_ns": inizio, "ritorno_ns": fine, "invio_utc_ns": utc, "ritorno_utc_ns": fine_utc,
            "osservazione": "persa" if persa else "risposta", "risposta": None if persa else r}


def main():
    a = argparse.ArgumentParser(description=__doc__)
    sub = a.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("genera")
    p.add_argument("--tipo", choices=("collaudo", "carico", "selettivita"), default="collaudo")
    p.add_argument("--seme", type=int, default=417)
    p.add_argument("--clienti", type=int, default=1000)
    p.add_argument("--operazioni", type=int, default=10000)
    p.add_argument("--letture", type=int, choices=(50, 95), default=95)
    p.add_argument("--richiedenti", type=int, choices=(1, 4, 16), default=1)
    p.add_argument("--distribuzione", choices=("uniforme", "contesa", "rettifiche"), default="uniforme")
    p.add_argument("uscita", type=Path)
    p = sub.add_parser("registra")
    p.add_argument("--socket", required=True)
    p.add_argument("piano", type=Path)
    parte = p.add_mutually_exclusive_group()
    parte.add_argument("--prima-del-riavvio", action="store_true")
    parte.add_argument("--riprendi-da", type=Path, help="osservazioni del primo segmento sullo stesso archivio")
    p.add_argument("uscita", type=Path)
    args = a.parse_args()
    if args.cmd == "genera":
        if args.tipo == "collaudo":
            righe = collaudo(args.seme)
        elif args.tipo == "selettivita":
            righe = selettivita(args.seme, args.clienti, args.operazioni)
        else:
            righe = carico(args.seme, args.clienti, args.operazioni, args.letture, args.richiedenti, args.distribuzione)
    else:
        def segmento():
            with args.piano.open(encoding="utf-8") as f:
                piano = (json.loads(line) for line in f)
                if not args.prima_del_riavvio and not args.riprendi_da:
                    yield from registra(piano, args.socket)
                    return
                prima = []
                for l in piano:
                    if l.get("fase") == "ripresa":
                        break
                    prima.append(l)
                else:
                    raise ValueError("il piano non contiene la barriera di ripresa")
                if args.prima_del_riavvio:
                    yield from registra(prima, args.socket)
                else:
                    with args.riprendi_da.open(encoding="utf-8") as obs:
                        precedenti = [json.loads(x) for x in obs]
                    attesi = [p["richiesta"]["id_tentativo"] for p in prima]
                    if [p["richiesta"]["id_tentativo"] for p in precedenti] != attesi:
                        raise ValueError("il primo segmento non corrisponde ai tentativi del piano")
                    yield from registra(itertools.chain([l], piano), args.socket, precedenti=precedenti)
        righe = segmento()
    with args.uscita.open("x", encoding="utf-8") as f:
        for l in righe:
            f.write(json.dumps(l, ensure_ascii=False, separators=(",", ":")) + "\n")
            f.flush()


if __name__ == "__main__":
    main()
