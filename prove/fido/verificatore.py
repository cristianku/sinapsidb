"""Giudice offline: legge un JSONL osservato dal richiedente, senza aprire socket o database."""

from __future__ import annotations

import argparse
import bisect
import json
import math
import sys
from collections import Counter, defaultdict
from datetime import datetime

from .riferimento import Incompleto, SCRITTURE, Storia, unita


class Divergenza(ValueError):
    """La prova non è passata. Non trasformare una risposta mancante in uno zero."""


def serve(condizione, messaggio):
    if not condizione:
        raise Divergenza(messaggio)


def uguale(visto, atteso, cosa):
    if visto != atteso:
        raise Divergenza(f"{cosa}: osservato {visto!r}, atteso {atteso!r}")


def evento(n):
    if isinstance(n, str) and n.startswith("evento:") and n[7:].isdigit():
        return int(n[7:])
    if type(n) is not int:
        raise Divergenza(f"identificativo di evento non valido: {n!r}")
    return n


CAMPI = ("evento", "base", "arrivo", "scritto", "esito")


def ricevuta(r):
    serve(all(type(r[k]) is int for k in ("evento", "base")), "evento/base non interi")
    serve(all(type(r[k]) in (float, int) and math.isfinite(r[k]) for k in ("arrivo", "scritto")), "istanti non finiti")
    uguale(r["base"], r["evento"] - 1, "base")
    serve(r["arrivo"] <= r["scritto"], "commit precedente all'arrivo")
    return {k: r[k] for k in CAMPI}


def contenuto(q):
    return {k: v for k, v in q.items() if k != "id_tentativo"}


def controlla_esame(risposta, esame):
    uguale(risposta["esito"], esame.esito, "esito della decisione")
    esiti = {(g, c): "vieta" if v < 0 else "passa" for g, c, con, _, v in esame.letture if con}
    viste = []
    for y in risposta["letture"]:
        serve(type(y["con_proposta"]) is bool, "con_proposta non booleano")
        uguale((y["regola"], y["versione"], y["segnale"], y["cosa"], y["verdetto"]),
               ("fido non superato", 1, "residuo negativo", "fido residuo", esiti.get((y["quando"], y["su"]))),
               "regola e lettura del fido")
        viste.append((y["quando"], y["su"], y["con_proposta"], y["valido_al"], unita(y["valore"])))
    uguale(Counter(viste), Counter(esame.letture), "letture prima/dopo")


class Giudice:
    def __init__(self, righe, piano=None):
        self.righe = sorted(righe, key=lambda x: x["invio_ns"])
        self.piano = piano
        self.storia = Storia()
        self.ricevute = {}  # una sola, immutabile, per chiave
        self.richieste = {}
        self.finestre = defaultdict(list)
        self.persi = defaultdict(list)
        self.osservati = []  # (ritorno_ns, frontiera minima che deve essere visibile dopo)
        self.deduzioni = {}  # primo invio che può aver creato l'evento
        self.corrente = "tracciato"

    def confronta_piano(self):
        """Il richiedente non può omettere operazioni o sostituire una richiesta con una più facile."""
        if self.piano is None:
            return
        osservate = {l["richiesta"]["id_tentativo"]: l for l in self.righe}
        gruppo, fine_gruppo, barriera = None, -1, -1
        for p in self.piano:
            q = p["richiesta"]
            self.corrente = q["id_tentativo"]
            l = osservate.pop(self.corrente)
            for k in ("seme", "richiedente", "gruppo", "fase"):
                uguale(l.get(k), p.get(k), k)
            if gruppo != p["gruppo"]:
                gruppo, barriera = p["gruppo"], fine_gruppo
            serve(l["invio_ns"] >= barriera, "invio prima della barriera del gruppo precedente")
            fine_gruppo = max(fine_gruppo, l["ritorno_ns"])
            if p.get("perdita") != "in_volo":
                uguale(l["osservazione"] == "persa", "perdita" in p, "guasto programmato")
            # Nei due punti d'iniezione controllati il verdetto atteso non è
            # ambiguo. Per una caduta reale in volo è la sonda a risolverlo.
            if p.get("perdita") in ("dopo_commit", "prima_della_consegna"):
                sonde = [s for s in self.righe if s["invio_ns"] >= l["ritorno_ns"] and s["richiesta"]["op"] == "stato"
                         and s["richiesta"].get("chiave") == q["chiave"]]
                serve(sonde, "nessuna sonda della chiave dopo il guasto")
                uguale(sonde[0]["risposta"]["presente"], p["perdita"] == "dopo_commit", "durabilità al punto di guasto")
            uguale(set(l["richiesta"]), set(q), "campi della richiesta")
            for k, v in q.items():
                x = l["richiesta"][k]
                if k in ("evento", "rettifica", "fino_a"):
                    uguale(evento(x), self.ricevute[v.removeprefix("@")]["evento"], k)
                elif k == "rettifiche":
                    uguale(x, [self.ricevute[i.removeprefix("@")]["evento"] for i in v], k)
                elif k in ("vale_da", "vale_fino_a") and isinstance(v, dict):
                    # Il testo ISO porta microsecondi; gli istanti sul filo sono secondi Unix.
                    serve(abs(datetime.fromisoformat(x).timestamp() - self.ricevute[v["scritto_di"]]["scritto"]) <= 1e-6,
                          "confine diverso dal piano")
                else:
                    uguale(x, v, k)
        serve(not osservate, "tentativi non presenti nel piano")

    def annota(self, k, r):
        x = ricevuta(r)
        uguale(self.ricevute.setdefault(k, x), x, f"ricevuta durevole della chiave {k}")

    def raccogli(self):
        visti = set()
        for l in self.righe:
            q, r = l["richiesta"], l["risposta"]
            i, f = l["invio_ns"], l["ritorno_ns"]
            self.corrente = t = q["id_tentativo"]
            serve(t and t not in visti, "id_tentativo assente o duplicato")
            visti.add(t)
            serve(type(i) is int and type(f) is int and 0 <= i <= f, "intervallo del client non valido")
            serve(l["invio_utc_ns"] <= l["ritorno_utc_ns"], "orologio UTC arretrato durante il tentativo")
            op, k = q["op"], q.get("chiave")
            if op in SCRITTURE:
                serve(isinstance(k, str) and k, "scrittura senza chiave")
                # Il generatore usa un solo contenuto per chiave. Un conflitto deliberato
                # va descritto separatamente, non reinterpretato come una nuova decisione.
                uguale(self.richieste.setdefault(k, contenuto(q)), contenuto(q), "payload al retry")
            if r is None:
                serve(op in SCRITTURE and l["osservazione"] == "persa", "risposta mancante")
                self.persi[k].append((i, f))
                continue
            uguale(l["osservazione"], "risposta", "tipo di osservazione")
            if r["ok"]:
                uguale(r["id_tentativo"], t, "correlazione del tentativo")
            if op in SCRITTURE:
                serve(r["ok"] is True, f"scrittura respinta dal servizio: {r}")
                self.annota(k, r)
                serve(type(r["ripetuta"]) is bool, "manca il bit di ripetizione")
                self.finestre[k].append((i, f, r["ripetuta"]))
                self.osservati.append((f, r["evento"]))
            elif op == "stato" and k is not None:
                serve(r["ok"] and r["chiave"] == k and type(r["presente"]) is bool, "stato(chiave) non valido")
                if r["presente"]:
                    self.annota(k, r)
                    self.osservati.append((f, r["evento"]))
            elif op == "stato" and r["ok"]:
                self.osservati.append((f, evento(r["ultimo_evento"])))
            elif op == "fido" and r["ok"]:
                self.osservati.append((f, evento(r["aggiornato_a"])))

        orari = defaultdict(list)
        prima_prova = {}
        for l in self.righe:
            q, r = l["richiesta"], l["risposta"]
            if r and "evento" in r and "scritto" in r and q.get("chiave"):
                k = q["chiave"]
                prima_prova[k] = min(prima_prova.get(k, math.inf), l["ritorno_utc_ns"])
            if q["op"] in SCRITTURE and (r is None or not r["ripetuta"]):
                orari[q["chiave"]].append((l["invio_utc_ns"], l["ritorno_utc_ns"], r is None, l.get("perdita")))
        for k, x in self.ricevute.items():
            self.corrente = k
            serve(k in self.richieste, "evento mai richiesto")
            prime = [(i, f) for i, f, ripetuta in self.finestre[k] if not ripetuta]
            serve(len(prime) <= 1, "due commit originari per la stessa chiave")
            possibili = prime or self.persi[k]
            serve(possibili, "ricevuta senza tentativo originario")
            # Un commit in volo può finire DOPO che il socket è caduto; deve comunque
            # precedere la prima osservazione che testimonia la sua presenza.
            serve(any(a / 1e9 - 1e-6 <= x["arrivo"] <= x["scritto"] <=
                      (prima_prova[k] if perso and motivo == "in_volo" else b) / 1e9 + 1e-6
                      for a, b, perso, motivo in orari[k] if not prime or not perso),
                  "istanti della decisione fuori dall'invocazione originale")
            n = x["evento"]
            serve(n not in self.deduzioni, "due chiavi per lo stesso evento")
            self.deduzioni[n] = min(i for i, _ in possibili)
            for i, f, ripetuta in self.finestre[k]:
                if ripetuta:
                    serve(self.deduzioni[n] <= f, "ripetizione prima del primo tentativo")

        # Ogni ACK perso deve essere risolto osservando lo stato *prima* di ritentare.
        # Ciò distingue l'assenza dopo un crash da un commit presente ma non confermato.
        sonde = defaultdict(list)
        invii = defaultdict(list)
        for l in self.righe:
            q = l["richiesta"]
            if q["op"] == "stato" and "chiave" in q:
                sonde[q["chiave"]].append(l)
            elif q["op"] in SCRITTURE:
                invii[q["chiave"]].append(l["invio_ns"])
        for k, intervalli in self.persi.items():
            self.corrente = k
            for _, fine in intervalli:
                riprova = min((i for i in invii[k] if i >= fine), default=math.inf)
                serve(any(fine <= l["invio_ns"] <= l["ritorno_ns"] <= riprova for l in sonde[k]),
                      "manca stato(chiave) dopo la conferma persa e prima del retry")

    def ricostruisci(self):
        self.corrente = "frontiera"
        prima, fine = self.righe[0], max(self.righe, key=lambda l: l["ritorno_ns"])
        for l, fase in ((prima, "inizio"), (fine, "fine")):
            serve(l.get("fase") == fase and l["richiesta"]["op"] == "stato" and "chiave" not in l["richiesta"],
                  "servono stato iniziale e stato finale del carico")
            serve(l["risposta"]["ok"] is True, "stato non disponibile")
        self.inizio = evento(prima["risposta"]["ultimo_evento"])
        self.segno = prima["risposta"].get("segno")
        self.fine = evento(fine["risposta"]["ultimo_evento"])
        uguale(self.fine - self.inizio, len(self.deduzioni), "numero di commit (buchi o extra)")
        serve(all(self.inizio < e <= self.fine for e in self.deduzioni), "evento fuori dal prefisso della prova")
        self.osservati.sort()
        self.tempi = [f for f, _ in self.osservati]
        self.prefissi = [self.inizio]
        for _, e in self.osservati:
            self.prefissi.append(max(self.prefissi[-1], e))
        # L'ordine testimoniato dagli eventi deve essere UNA serializzazione lecita.
        # Non ordiniamo per ACK. Se B termina prima che A inizi, B non può trovarsi dopo A.
        self.nati = [0]
        ultimo_t = -math.inf
        for k, x in sorted(self.ricevute.items(), key=lambda kv: kv[1]["evento"]):
            self.corrente = k
            e = x["evento"]
            serve(self.prima_di(self.deduzioni[e]) < e, "ordine di commit contrario al tempo reale")
            serve(x["scritto"] >= ultimo_t, "istante di commit arretra")
            ultimo_t = x["scritto"]
            self.nati.append(max(self.nati[-1], self.deduzioni[e]))
            esame = self.storia.registra(self.richieste[k], e, x["arrivo"], x["scritto"])
            uguale(x["esito"], esame.esito, "decisione del modello indipendente")

    def prima_di(self, t):
        return self.prefissi[bisect.bisect_left(self.tempi, t)]

    def lettura_prefisso(self, n, l):
        serve(self.prima_di(l["invio_ns"]) <= n <= self.fine, "frontiera stantia o inesistente")
        serve(self.nati[n - self.inizio] <= l["ritorno_ns"], "frontiera contiene una scrittura non ancora inviata")

    def controlla(self, l):
        q, r = l["richiesta"], l["risposta"]
        self.corrente = q["id_tentativo"]
        op = q["op"]
        if r is None:
            return
        if op in SCRITTURE:
            serve(self.nati[r["evento"] - self.inizio] <= l["ritorno_ns"], "ACK di un prefisso impossibile")
            return
        if op == "riesamina":
            e = evento(q["evento"])
            scelte = sorted({evento(x) for x in q.get("rettifiche", [])})
            if "fino_a" in q:
                ultimo = evento(q["fino_a"])
                scelte = [i for i in range(e + 1, ultimo + 1) if self.storia.allora[i].esito == "applicato"]
            try:
                ipotesi = self.storia.riesamina(e, scelte)
            except Incompleto:
                serve(r["ok"] is False and r["tipo"] == "scenario_incompleto", "scenario incompleto presentato come ordinario")
                return
            serve(r["ok"] is True, f"riesame fallito: {r}")
            uguale(r["modo"], "per decisione", "modo di riesame")
            uguale(evento(r["evento"]), e, "evento riesaminato")
            uguale(evento(r["base"]), e - 1, "base riesaminata")
            d = self.storia.decisioni[e]
            uguale((r["istante"], r["scritto"]), (d.arrivo, d.scritto), "istanti del riesame")
            uguale([evento(x) for x in r["eventi_usati"]], scelte, "rettifiche realmente usate")
            controlla_esame(r["allora"], self.storia.allora[e])
            controlla_esame(r["ipotetico"], ipotesi)
            uguale(r["cambia"], ipotesi.esito != self.storia.allora[e].esito, "cambio di esito")
            return
        if op == "candidate" and self.storia.allora[evento(q["rettifica"])].esito == "violazione_regola":
            serve(r["ok"] is False and r["tipo"] == "richiesta", "rifiuto presentato come rettifica applicata")
            return
        serve(r["ok"] is True, f"{op} fallita: {r}")
        if op == "fido":
            base = evento(r["aggiornato_a"])
            self.lettura_prefisso(base, l)
            t = r["valido_al"]
            if t is None:
                serve(not any(f.nato <= base for f in self.storia.per_cliente.get(q["cliente"], ())),
                      "istante di validità mancante per cliente noto")
                atteso = 0
            else:
                serve(math.isfinite(t) and l["invio_utc_ns"] / 1e9 - 1e-6 <= t <= l["ritorno_utc_ns"] / 1e9 + 1e-6,
                      "istante di lettura fuori dall'invocazione")
                atteso = self.storia.saldo(q["cliente"], base, t)
            uguale(unita(r["valore"]), atteso, "fido letto")
        elif op == "stato":
            if "chiave" not in q:
                self.lettura_prefisso(evento(r["ultimo_evento"]), l)
                uguale(r["circuiti"], "coerente", "controllo alla riapertura")
                uguale(r.get("segno"), self.segno, "segno durevole cambiato")
                return
            k = q["chiave"]
            if r["presente"]:
                serve(self.deduzioni[r["evento"]] <= l["ritorno_ns"], "ricevuta vista prima della richiesta")
            elif k in self.ricevute:
                e = self.ricevute[k]["evento"]
                serve(e > self.prima_di(l["invio_ns"]), "ricevuta durevole scomparsa")
                if not any(i > l["invio_ns"] for i, _, rep in self.finestre[k] if not rep):
                    serve(not any(f <= l["invio_ns"] for _, f in self.persi[k]),
                          "chiave detta assente dopo la perdita dell'ACK ma poi solo ripetuta")
        elif op == "verifica":
            uguale((evento(r["evento"]), r["esito"], r["differenze"]), (evento(q["evento"]), "coerente", []), "verifica")
        elif op == "candidate":
            self.candidate(q, r)
        else:
            raise Divergenza(f"operazione fuori dal protocollo: {op}")

    def candidate(self, q, r):
        n = evento(q["rettifica"])
        uguale(evento(r["rettifica"]), n, "evento della rettifica")
        osservate = [evento(x) for x in r["candidate"]]
        uguale(osservate, sorted(set(osservate)), "ordine/unicità candidate")
        uguale(r["quante"], len(osservate), "conteggio candidate")
        serve(all(e in self.storia.decisioni and e < n for e in osservate), "candidata senza decisione precedente")
        uguale(r["copertura"], {"completa": True, "eventi_senza_letture": 0}, "copertura del carico nuovo")
        trovate = set(osservate)
        cambiate, incomplete = [], set()
        for e, allora in self.storia.allora.items():
            if e >= n:
                break
            try:
                dopo = self.storia.riesamina(e, [n])
            except Incompleto:
                # Il trasferimento resta intero. Occorre trovare entrambi i clienti
                # anche quando il fatto da ritirare non era nella base.
                d = self.storia.decisioni[n]
                if any(f and f.cliente == c and f.vale(t) for _, c, _, t, _ in allora.letture for f in (d.ritira, d.aggiunge)):
                    serve(e in trovate, f"candidata incompleta {e} persa")
                if e in trovate:
                    incomplete.add(e)
                continue
            if dopo != allora:
                serve(e in trovate, f"decisione toccata {e} non trovata")
            if dopo.esito != allora.esito:
                cambiate.append((e, allora.esito, dopo.esito))
        if q.get("riesamina"):
            uguale([(evento(x["evento"]), x["allora"], x["ipotetico"]) for x in r["esiti_cambiati"]], cambiate, "esiti cambiati")
            uguale(sorted(evento(x["evento"]) for x in r["scenari_incompleti"]), sorted(incomplete), "scenari incompleti")
            uguale(r["non_riesaminabili"], [], "ricevute non riesaminabili")
            uguale(r["riesaminate"], len(trovate) - len(incomplete), "conteggio riesaminate")

    def controlla_registro(self, righe):
        """Confronta anche i delta durevoli (e la proposta di un no), non solo le risposte del socket."""
        righe = iter(righe)
        uguale(next(righe, None), {"inizio": self.inizio, "segno": self.segno}, "intestazione del registro")
        for k, x in sorted(self.ricevute.items(), key=lambda kv: kv[1]["evento"]):
            self.corrente = f"registro, chiave {k}"
            r = next(righe, None)
            serve(r is not None, "evento confermato assente dal registro")
            uguale(r["chiave"], k, "chiave scritta")
            uguale(ricevuta(r), x, "ricevuta su disco")
            uguale((evento(r["evento_ricevuta"]), r["istante"]), (x["evento"], x["arrivo"]), "coordinate registrate")
            si = x["esito"] == "applicato"
            uguale(r["tipo"], "scrittura" if si else "rifiuto", "tipo di evento")
            e = x["evento"]
            d = self.storia.decisioni[e]
            q = self.richieste[k]
            controlla_esame(r, self.storia.allora[e])
            attesi = []
            if d.aggiunge is not None:
                f = d.aggiunge
                specie = "pagamento" if q["op"] == "sposta" else q["op"]
                rif = q[{"ordine": "numero", "accorda": "atto", "pagamento": "causale"}[specie]]
                attesi.append((specie, f.cliente, rif, -f.valore if specie == "ordine" else f.valore, f.da,
                               f.fine if math.isfinite(f.fine) else None))
            tolto = [d.ritira.nato] if d.ritira else []
            for campo, atteso in (("proposti", attesi), ("scritti", attesi if si else [])):
                uguale([(f["specie"], f["cliente"], f["riferimento"], unita(f["importo"]), f["da"], f["fine"])
                        for f in r[campo]], atteso, campo)
            uguale(r["da_togliere"], tolto, "ritiri proposti")
            uguale(r["ritirati"], tolto if si else [], "ritiri effettivi")
        serve(next(righe, None) is None, "evento mai richiesto nel registro")

    def verifica(self, registro=None):
        try:
            serve(len(self.righe) >= 2, "tracciato vuoto o troncato")
            self.raccogli()
            self.confronta_piano()
            self.ricostruisci()
            for l in self.righe:
                self.controlla(l)
            if registro is not None:
                self.controlla_registro(registro)
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise Divergenza(f"{self.corrente}: {exc}") from exc
        return {"esito": "coerente", "tentativi": len(self.righe), "decisioni": len(self.ricevute),
                "conferme_perse": sum(map(len, self.persi.values()))}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--piano", required=True, type=argparse.FileType("r", encoding="utf-8"))
    p.add_argument("--registro", type=argparse.FileType("r", encoding="utf-8"), help="esportazione durevole dopo il recupero")
    p.add_argument("osservazioni", type=argparse.FileType("r", encoding="utf-8"))
    args = p.parse_args()
    try:
        with args.osservazioni as f, args.piano as piano:
            giudice = Giudice([json.loads(line) for line in f], (json.loads(line) for line in piano))
            if args.registro:
                with args.registro as registro:
                    risultato = giudice.verifica(json.loads(line) for line in registro)
            else:
                risultato = giudice.verifica()
            print(json.dumps({**risultato, "registro_controllato": bool(args.registro)}, ensure_ascii=False))
    except (ValueError, KeyError, TypeError) as exc:
        sys.exit(f"Prova non superata: {exc}")


if __name__ == "__main__":
    main()
