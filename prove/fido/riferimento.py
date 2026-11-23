"""Modello esterno del problema del fido. Non importa adattatori, driver o motori.

Un fatto è identificato dall'evento che lo ha aggiunto (in questo carico al massimo
uno per scrittura). Anche un no conserva la proposta, ma non crea né ritira fatti.
Il modello è per il collaudo: non è l'implementazione del concorrente PostgreSQL.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone

SCRITTURE = {"accorda", "pagamento", "ordine", "storno", "sposta"}
SI, NO = "applicato", "violazione_regola"


def unita(testo: str) -> int:
    """Testo sul filo → milionesimi, anche per i totali. Nessun arrotondamento."""
    if not isinstance(testo, str) or not re.fullmatch(r"-?[0-9]+(?:\.[0-9]{1,6})?", testo):
        raise ValueError(f"importo decimale non esatto: {testo!r}")
    intera, _, decimali = testo.lstrip("-").partition(".")
    return (-1 if testo.startswith("-") else 1) * (int(intera) * 1_000_000 + int(decimali.ljust(6, "0")))


def istante(s: str) -> float:
    d = datetime.fromisoformat(s)
    return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).timestamp()


class Incompleto(ValueError):
    pass


@dataclass(frozen=True)
class Fatto:
    nato: int
    cliente: str
    valore: int
    da: float | None
    fine: float
    scritto: float
    causale: str | None

    def vale(self, t, *, proposta=False, decisione=None):
        if self.da is not None:
            return self.da <= t < self.fine
        if proposta:  # il fatto ipotizzato viene provato all'istante in esame
            return t < self.fine
        if decisione is not None and self.nato > decisione and self.scritto == t:
            return False
        return self.scritto <= t < self.fine


@dataclass(frozen=True)
class Decisione:
    evento: int
    arrivo: float
    scritto: float
    aggiunge: Fatto | None
    ritira: Fatto | None


@dataclass(frozen=True)
class Esame:
    esito: str
    # (giro, cliente, con proposta, istante, importo in milionesimi)
    letture: tuple


class Storia:
    def __init__(self):
        self.decisioni: dict[int, Decisione] = {}
        self.allora: dict[int, Esame] = {}
        self.per_cliente: dict[str, list[Fatto]] = defaultdict(list)
        self.ritirato: dict[int, int] = {}
        self.pagamenti: dict[str, Fatto] = {}

    def saldo(self, cliente, base, t):
        return sum(f.valore for f in self.per_cliente.get(cliente, ())
                   if f.nato <= base < self.ritirato.get(f.nato, math.inf) and f.vale(t))

    def registra(self, richiesta, e, arrivo, scritto):
        """Decide dal solo input e dalla frontiera già ricostruita, mai dall'esito osservato."""
        op = richiesta["op"]
        vecchio = self.pagamenti.get(richiesta.get("causale")) if op in ("storno", "sposta") else None
        if op in ("storno", "sposta") and vecchio is None:
            raise ValueError("il tracciato di collaudo rettifica una causale inesistente")
        nuovo = None
        if op != "storno":
            v = unita(richiesta["importo"])
            da = istante(richiesta["vale_da"]) if "vale_da" in richiesta else None
            fine = istante(richiesta["vale_fino_a"]) if "vale_fino_a" in richiesta else math.inf
            if (scritto if da is None else da) >= fine:
                raise ValueError("è stato registrato un intervallo vuoto")
            nuovo = Fatto(e, richiesta["cliente"], -v if op == "ordine" else v, da, fine, scritto,
                          richiesta.get("causale"))
        d = Decisione(e, arrivo, scritto, nuovo, vecchio)
        esame = self._decidi(d, {}, set())
        self.decisioni[e], self.allora[e] = d, esame
        if esame.esito == SI:
            if vecchio:
                self.ritirato[vecchio.nato] = e
                del self.pagamenti[vecchio.causale]
            if nuovo:
                self.per_cliente[nuovo.cliente].append(nuovo)
                if nuovo.causale is not None:
                    if nuovo.causale in self.pagamenti:
                        raise ValueError("il tracciato richiede causali univoche fra i pagamenti vivi")
                    self.pagamenti[nuovo.causale] = nuovo
        return esame

    def _decidi(self, d, aggiunti, tolti):
        letture = []
        for giro, t in (("arrivo", d.arrivo), ("scrittura", d.scritto)):
            if giro == "scrittura" and t == d.arrivo:
                break
            delta = defaultdict(int)
            if d.ritira and d.ritira.vale(t, decisione=d.evento):
                delta[d.ritira.cliente] -= d.ritira.valore
            if d.aggiunge and d.aggiunge.vale(t, proposta=True):
                delta[d.aggiunge.cliente] += d.aggiunge.valore
            vietato = False
            for c, v in sorted(delta.items()):
                prima = sum(f.valore for f in self.per_cliente.get(c, ())
                            if f.nato < d.evento <= self.ritirato.get(f.nato, math.inf)
                            and f.nato not in tolti and f.vale(t))
                prima += sum(f.valore for f in aggiunti.values() if f.cliente == c and f.vale(t, decisione=d.evento))
                dopo = prima + v
                letture.extend(((giro, c, False, t, prima), (giro, c, True, t, dopo)))
                vietato |= dopo < 0
            if vietato:
                return Esame(NO, tuple(letture))
        return Esame(SI, tuple(letture))

    def riesamina(self, e, rettifiche):
        """Sovrappone *interi eventi* al prefisso, senza alterare la storia reale."""
        aggiunti, tolti = {}, set()
        for n in sorted(set(rettifiche)):
            if n <= e or self.allora[n].esito != SI:
                raise ValueError("rettifica non applicabile alla base")
            d = self.decisioni[n]
            if d.ritira:
                origine = d.ritira.nato
                if origine in aggiunti:
                    del aggiunti[origine]
                elif origine >= e:
                    raise Incompleto(f"manca l'evento {origine} per il ritiro {n}")
                else:
                    tolti.add(origine)
            if d.aggiunge:
                aggiunti[n] = d.aggiunge
        d = self.decisioni[e]
        if d.ritira and d.ritira.nato in tolti:
            return Esame("non_applicabile", ())
        return self._decidi(d, aggiunti, tolti)
