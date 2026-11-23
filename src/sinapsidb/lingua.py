"""La lingua di SinapsiDB: come si scrive una domanda alla rete, senza Python e senza un modello linguistico.

Una domanda dice quali neuroni accendere, in quali fatti, e che cosa leggere dai fatti che scattano:

    accendi cliente = C00202 in fattura emessa leggi fattura, totale

Più indizi insieme alzano la soglia: scattano solo i fatti che li ricevono tutti.

    accendi prodotto = P00007 e magazzino = "Magazzino Verona" in giacenza leggi quantità

Con ``poi`` la domanda prosegue: i neuroni letti da un passo vengono accesi nel passo dopo.

    accendi cliente = C00202 in fattura emessa leggi fattura
    poi in riga di fattura leggi prodotto
    poi in scheda prodotto leggi distinti fornitore

Si può contare, sommare e raggruppare, e mettere condizioni sui valori:

    accendi cliente = C00202 in fattura emessa somma totale
    in fattura emessa somma totale per mese(quando)
    in fattura emessa dove totale > 3000 leggi fattura

Senza ``in`` gli indizi valgono per i fatti di qualsiasi tipo:

    accendi cliente = C00202 e quando = 2025-03-04 leggi tipo, frase

Di ogni fatto si possono sempre leggere ``tipo``, ``frase`` (quella con cui è stato raccontato), ``id_fatto``
e ``id_evento``. Al posto di un nome si può scrivere l'identificativo di un neurone, ``neurone:17``: serve
quando due cose hanno lo stesso nome.

Le parole della lingua sono: accendi, e, in, dove, leggi, distinti, conta, somma, per, poi, da, come.
Un nome che ne contiene una, o un segno, si scrive tra virgolette: ``stato = "in lavorazione"``.
Al posto di un valore si può scrivere ``?``: la domanda si prepara una volta e si esegue con valori diversi.

Due frasi aiutano a scrivere le domande: ``vocabolario`` elenca i tipi di fatto con i loro ruoli, e
``percorsi da <ruolo> a <ruolo>`` propone le strade che portano da una cosa all'altra. La strada la
sceglie chi chiede: spesso ce n'è più di una, e sono domande diverse.

La lingua legge soltanto. Per scrivere c'è ``Rete.racconta``.
"""

from __future__ import annotations

import difflib
import re
import time
from decimal import MAX_EMAX, MIN_EMIN, ROUND_HALF_EVEN, Context, Decimal, Inexact, InvalidOperation, localcontext
from typing import Any

from sinapsidb import nucleo
from sinapsidb.motore import BIT, Rete, da_unita, piana

PAROLE = frozenset({"accendi", "e", "in", "dove", "leggi", "distinti", "conta", "somma", "per", "poi", "da", "come"})
PARAMETRO = "?"  # segnaposto: il valore arriva quando la domanda viene eseguita
CONFRONTI = {
    ">": lambda a, b: a > b, ">=": lambda a, b: a >= b, "<": lambda a, b: a < b, "<=": lambda a, b: a <= b,
}
MASCHERA = (1 << BIT) - 1
# Quello che si può leggere di ogni fatto oltre ai suoi ruoli: il tipo, la frase con cui è stato raccontato,
# il suo identificativo e quello dell'evento che lo ha scritto. Se un fatto ha un ruolo con uno di questi nomi, vince il ruolo.
SPECIALI = ("tipo", "frase", "id_fatto", "id_evento")
_GETTONE = re.compile(r'\s*(?:"((?:[^"\\]|\\.)*)"|(>=|<=|[=,()<>?])|([^\s=,()<>"?]+))')


class ErroreDomanda(ValueError):
    """La domanda non si capisce o nomina qualcosa che nella rete non c'è. ``args[0]`` lo spiega in parole."""


class Ambigua(ErroreDomanda):
    """La domanda nomina una cosa che nella rete è più d'una: non è sbagliata, va precisata. Chi guarda il passato non
    la deve scambiare per «allora non c'era ancora niente»."""


# --- dalla frase alla struttura ---------------------------------------------------


def _gettoni(testo: str) -> list[tuple[str, str, int]]:
    """La frase a pezzi: (genere, testo, posizione). I generi sono parola, nome, testo e segno."""
    gettoni = []
    pos = 0
    testo = testo.rstrip()
    while pos < len(testo):
        m = _GETTONE.match(testo, pos)
        if m is None:
            raise ErroreDomanda(f"non capisco da qui in poi: «{testo[pos:pos + 30].strip()}»")
        if m.group(1) is not None:
            gettoni.append(("testo", re.sub(r"\\(.)", r"\1", m.group(1)), m.start(1)))
        elif m.group(2) is not None:
            gettoni.append(("segno", m.group(2), m.start(2)))
        else:
            parola = m.group(3)
            gettoni.append(("parola" if parola.casefold() in PAROLE else "nome", parola, m.start(3)))
        pos = m.end()
    return gettoni


class _Passo:
    __slots__ = ("indizi", "tipo", "filtri", "azione", "da", "come")

    def __init__(self) -> None:
        self.indizi: list[tuple[str, str]] = []  # (ruolo, valore o PARAMETRO)
        self.tipo: str | None = None
        self.filtri: list[tuple[str, str, str]] = []  # (ruolo, confronto, valore o PARAMETRO)
        self.azione: tuple = ()
        self.da: str | None = None
        self.come: str | None = None


class _Analisi:
    def __init__(self, testo: str) -> None:
        self.testo = testo
        self.g = _gettoni(testo)
        self.i = 0

    def _ora(self) -> tuple[str, str, int]:
        return self.g[self.i] if self.i < len(self.g) else ("fine", "", len(self.testo))

    def _e(self, genere: str, testo: str) -> bool:
        g = self._ora()
        return g[0] == genere and g[1].casefold() == testo

    def _prendi(self, genere: str, testo: str) -> bool:
        if self._e(genere, testo):
            self.i += 1
            return True
        return False

    def _errore(self, serve: str) -> ErroreDomanda:
        genere, testo, _ = self._ora()
        if genere == "fine":
            return ErroreDomanda(f"la domanda finisce troppo presto: qui serve {serve}")
        if genere == "parola":
            return ErroreDomanda(f"qui serve {serve}, ma c'è «{testo}», che è una parola della lingua: se fa parte di un nome, "
                                 "scrivi il nome tra virgolette")
        return ErroreDomanda(f"qui serve {serve}, ma c'è «{testo}»")

    def _nome(self, serve: str, parametro: bool = False) -> str:
        genere, testo, _ = self._ora()
        if genere == "testo":
            self.i += 1
            return testo
        if parametro and genere == "segno" and testo == "?":
            self.i += 1
            return PARAMETRO
        pezzi = []
        while self._ora()[0] == "nome":
            pezzi.append(self._ora()[1])
            self.i += 1
        if not pezzi:
            raise self._errore(serve)
        return " ".join(pezzi)

    def domanda(self) -> list[_Passo]:
        passi = [self._passo(True)]
        while self._prendi("parola", "poi"):
            passi.append(self._passo(False))
        if self._ora()[0] != "fine":
            raise self._errore("la fine della domanda, oppure «poi»")
        for p in passi[:-1]:
            if p.azione[0] != "leggi":
                raise ErroreDomanda("«conta» e «somma» chiudono la domanda: dopo non si può proseguire con «poi»")
        return passi

    def _passo(self, primo: bool) -> _Passo:
        p = _Passo()
        if primo:
            if self._prendi("parola", "accendi"):
                p.indizi = self._condizioni(("=",), "un indizio, cioè un ruolo")
            if self._prendi("parola", "in"):
                p.tipo = self._nome("il tipo di fatto")
            if not p.indizi and p.tipo is None:
                raise ErroreDomanda("una domanda comincia con «accendi <ruolo> = <nome>» oppure con «in <tipo di fatto>»")
        else:
            if self._prendi("parola", "da"):
                p.da = self._nome("il ruolo da cui proseguire")
            if not self._prendi("parola", "in"):
                raise self._errore("«in <tipo di fatto>»")
            p.tipo = self._nome("il tipo di fatto")
            if self._prendi("parola", "come"):
                p.come = self._nome("il ruolo con cui entrare nei fatti")
        if self._prendi("parola", "dove"):
            p.filtri = self._condizioni(("=", ">", ">=", "<", "<="), "una condizione, cioè un ruolo")
        p.azione = self._azione()
        return p

    def _condizioni(self, ammessi: tuple[str, ...], serve: str) -> list[tuple]:
        condizioni = []
        while True:
            ruolo = self._nome(serve)
            genere, segno, _ = self._ora()
            if genere != "segno" or segno not in ammessi:
                raise self._errore(" oppure ".join(f"«{s}»" for s in ammessi) + f" dopo «{ruolo}»")
            self.i += 1
            valore = self._nome("un nome o un numero", parametro=True)
            condizioni.append((ruolo, valore) if ammessi == ("=",) else (ruolo, segno, valore))
            if not self._prendi("parola", "e"):
                return condizioni

    def _gruppo(self) -> tuple | None:
        if not self._prendi("parola", "per"):
            return None
        nome = self._nome("il ruolo per cui raggruppare")
        if nome.casefold() in ("mese", "anno") and self._prendi("segno", "("):
            ruolo = self._nome("il ruolo che contiene la data")
            if not self._prendi("segno", ")"):
                raise self._errore("«)»")
            return (nome.casefold(), ruolo)
        return ("", nome)

    def _azione(self) -> tuple:
        if self._prendi("parola", "leggi"):
            distinti = self._prendi("parola", "distinti")
            ruoli = [self._nome("che cosa leggere")]
            while self._prendi("segno", ","):
                ruoli.append(self._nome("che cosa leggere"))
            return ("leggi", ruoli, distinti)
        if self._prendi("parola", "conta"):
            return ("conta", None, self._gruppo())
        if self._prendi("parola", "somma"):
            return ("somma", self._nome("il ruolo da sommare"), self._gruppo())
        raise self._errore("«leggi», «conta» oppure «somma»")


def analizza(testo: str) -> list[_Passo]:
    """La domanda scritta, divisa nei suoi passi. Non guarda la rete: controlla solo che la frase stia in piedi."""
    if not testo or not testo.strip():
        raise ErroreDomanda("la domanda è vuota")
    return _Analisi(testo).domanda()


# --- che cosa c'è nella rete --------------------------------------------------------


class _Schema:
    """I tipi di fatto con i loro ruoli, letti dalle linee della rete."""

    def __init__(self, rete: Rete) -> None:
        self.rete = rete
        self.ruoli: dict[int, list[int]] = {}  # tipo -> ruoli
        self.tipi: dict[int, list[int]] = {}  # ruolo -> tipi
        # Un ruolo è di un tipo se almeno un fatto, in vigore o ritirato, lo ha avuto: una linea solo preparata non conta.
        linee = [linea for linea, d in rete.vivi.items() if d]
        linee += [linea for linea, d in rete.morti.items() if d and not rete.vivi.get(linea)]
        for linea in linee:
            tipo, ruolo = linea & MASCHERA, linea >> BIT
            if tipo in rete.forma:
                self.ruoli.setdefault(tipo, []).append(ruolo)
                self.tipi.setdefault(ruolo, []).append(tipo)
        for tipo, ruoli in self.ruoli.items():
            forma = rete.forma.get(tipo)
            if forma:  # l'ordine in cui i ruoli compaiono nei fatti, se è sempre lo stesso
                ruoli.sort(key=lambda r: forma.index(r) if r in forma else len(forma))
            else:  # altrimenti l'ordine in cui i ruoli sono nati: non dipende da come è tenuta la rete
                ruoli.sort()

    def linea(self, tipo: int, ruolo: int) -> dict:
        d = self.rete.vivi.get((ruolo << BIT) | tipo)
        return {} if d is None else d  # se la linea c'è si restituisce lei, anche vuota: è quella che poi si riempie

    def quanti(self, tipo: int) -> int:
        """I fatti in vigore di quel tipo, contati sulla linea con meno neuroni."""
        linee = [self.linea(tipo, r) for r in self.ruoli.get(tipo, ())]
        if not linee:
            return 0
        forma = self.rete.forma.get(tipo)
        if forma and any(forma.count(r) == 1 for r in self.ruoli[tipo]):  # una linea su cui ogni fatto sta una volta sola
            d = min((self.linea(tipo, r) for r in self.ruoli[tipo] if forma.count(r) == 1), key=len)
            return sum(1 if type(x) is int else len(x) for x in d.values())
        return len({f for d in linee for x in d.values() for f in _fatti(x)})  # un ruolo può essere ripetuto: ogni fatto una volta


IDENTIFICATIVO = re.compile(r"neurone:(\d+)")


def _trova(rete: Rete, nome: str) -> int:
    """Il neurone con quel nome, -1 se non c'è. Se il nome indica più neuroni la domanda va precisata.

    Al posto del nome si può scrivere il suo identificativo, ``neurone:17``: è il modo di indicare uno solo
    tra più neuroni con lo stesso nome. Se un neurone si chiama proprio così, vince il nome.
    """
    trovati = rete.per_nome.get(piana(nome))
    if not trovati:
        m = IDENTIFICATIVO.fullmatch(nome.strip())
        if m is None:
            return -1
        if int(m.group(1)) >= len(rete.nomi):
            raise ErroreDomanda(f"«{nome}» non esiste: la rete ha {len(rete.nomi)} neuroni")
        return int(m.group(1))
    if len(trovati) > 1:
        raise ErroreDomanda(f"«{nome}» indica {len(trovati)} cose diverse: " + "; ".join(
            f"{rete.nomi[i]} ({', '.join(rete.intorno(i, 2)) or 'senza fatti'})" for i in trovati[:5]))
    return trovati[0]


def _simili(nome: str, tra: list[str]) -> str:
    vicini = difflib.get_close_matches(piana(nome), [piana(x) for x in tra], n=3, cutoff=0.5)
    scelti = [x for x in tra if piana(x) in vicini]
    return f" Forse: {', '.join(f'«{x}»' for x in scelti)}." if scelti else ""


def vocabolario(rete: Rete) -> list[dict[str, Any]]:
    """I tipi di fatto che ci sono nella rete, con i ruoli e quanti fatti in vigore hanno."""
    schema = _Schema(rete)
    nomi = rete.nomi
    return sorted(({"tipo": nomi[t], "ruoli": [nomi[r] for r in ruoli], "fatti": schema.quanti(t)} for t, ruoli in schema.ruoli.items()),
                  key=lambda x: x["tipo"])


def percorsi(rete: Rete, da: str, a: str, massimo: int = 4, quanti: int = 6) -> list[dict[str, Any]]:
    """Le strade che portano da un ruolo a un altro, dalla più corta, scritte come domande.

    Da un tipo di fatto al successivo si passa attraverso un ruolo solo se lì fa da riferimento: in uno
    dei due tipi è la cosa di cui il fatto parla (il suo primo ruolo, con un solo fatto per neurone: la
    fattura nelle fatture, il prodotto nella sua scheda) e i neuroni dell'altro sono quasi tutti tra
    quelli. Così non si passa da una data, da un prezzo o da una città, che collegherebbero cose che non
    c'entrano. Sono proposte: la strada giusta dipende da che cosa si vuole sapere.
    """
    schema = _Schema(rete)
    nomi = rete.nomi
    ruoli = {piana(nomi[r]): r for r in schema.tipi}
    for nome in (da, a):
        if piana(nome) not in ruoli:
            raise ErroreDomanda(f"«{nome}» non è un ruolo che compare nei fatti." + _simili(nome, [nomi[r] for r in schema.tipi]))
    partenza, arrivo = ruoli[piana(da)], ruoli[piana(a)]
    if partenza == arrivo:
        raise ErroreDomanda("partenza e arrivo sono lo stesso ruolo")
    conta = {t: schema.quanti(t) for t in schema.ruoli}
    ponti: dict[tuple[int, int, int], bool] = {}

    def identifica(tipo: int, ruolo: int) -> bool:
        forma = rete.forma.get(tipo)
        return bool(forma) and forma[0] == ruolo and 0 < conta[tipo] <= 1.05 * len(schema.linea(tipo, ruolo))

    def contenuti(tipo: int, in_tipo: int, ruolo: int) -> bool:
        """I neuroni che hanno quel ruolo nel primo tipo ci sono quasi tutti anche nel secondo (su un campione)."""
        di, dentro = schema.linea(tipo, ruolo), schema.linea(in_tipo, ruolo)
        campione = [n for n, _ in zip(di, range(200))]
        return bool(campione) and sum(n in dentro for n in campione) >= 0.9 * len(campione)

    def ponte(t1: int, t2: int, ruolo: int) -> bool:
        chiave = (min(t1, t2), max(t1, t2), ruolo)
        if chiave not in ponti:
            ponti[chiave] = (identifica(t1, ruolo) and contenuti(t2, t1, ruolo)) or (identifica(t2, ruolo) and contenuti(t1, t2, ruolo))
        return ponti[chiave]

    trovati: list[list[tuple[int, int, int]]] = []
    frontiera: list[list[tuple[int, int, int]]] = [[]]  # strade fatte finora: passi (tipo, ruolo d'ingresso, ruolo d'uscita)
    for _ in range(massimo):
        prossima = []
        for strada in frontiera:
            ruolo = strada[-1][2] if strada else partenza
            for tipo in schema.tipi.get(ruolo, ()):
                if any(t == tipo for t, _, _ in strada) or (strada and not ponte(strada[-1][0], tipo, ruolo)):
                    continue
                for uscita in schema.ruoli[tipo]:
                    if uscita == ruolo or uscita == partenza or any(uscita in (i, u) for _, i, u in strada):
                        continue
                    nuova = strada + [(tipo, ruolo, uscita)]
                    if uscita == arrivo:
                        trovati.append(nuova)
                    else:
                        prossima.append(nuova)
        if len(trovati) >= quanti:
            break
        frontiera = prossima
    trovati.sort(key=lambda s: (len(s), [nomi[t] for t, _, _ in s]))

    def scrivi(strada: list[tuple[int, int, int]]) -> str:
        pezzi = []
        for i, (tipo, ingresso, uscita) in enumerate(strada):
            ultimo = i == len(strada) - 1
            leggi = f"leggi {'distinti ' if ultimo and len(strada) > 1 else ''}{_virgolette(nomi[uscita])}"
            pezzi.append((f"accendi {_virgolette(nomi[ingresso])} = ? in {_virgolette(nomi[tipo])} " if i == 0 else f"poi in {_virgolette(nomi[tipo])} ") + leggi)
        return " ".join(pezzi)

    return [{"passi": len(s), "per": [nomi[t] for t, _, _ in s], "domanda": scrivi(s)} for s in trovati[:quanti]]


def _virgolette(nome: str) -> str:
    """Il nome come va scritto in una domanda: tra virgolette solo se serve."""
    pezzi = nome.split()
    semplice = pezzi and all(_GETTONE.fullmatch(p) and _GETTONE.fullmatch(p).group(3) and p.casefold() not in PAROLE for p in pezzi)
    return nome if semplice and " ".join(pezzi) == nome else '"' + nome.replace("\\", "\\\\").replace('"', '\\"') + '"'


# --- dalla struttura all'esecuzione ---------------------------------------------------


def _fatti(x) -> Any:
    return () if x is None else (x,) if type(x) is int else x


class Domanda:
    """Una domanda preparata: si prepara una volta e si esegue quante volte si vuole, anche con valori diversi."""

    def __init__(self, rete: Rete, testo: str) -> None:
        self.rete = rete
        self.testo = " ".join(testo.split())
        self.passi = analizza(testo)
        self.parametri = sum(v == PARAMETRO for p in self.passi for _, *_, v in (*p.indizi, *p.filtri))
        self.colonne: list[str] = []
        self.piano: list[str] = []
        self.conteggi: list[int] = []  # quanti fatti sono scattati a ogni passo, nell'ultima esecuzione
        self.troncata = False
        self.nucleo = False  # l'ultima esecuzione l'ha fatta il nucleo compilato
        self._numeri: dict[int, float | None] = {}
        self._decimali: dict[int, Decimal | None] = {}
        # I neuroni che la frase nomina, una volta trovati: la domanda è preparata per loro, non per i loro nomi. Per
        # ognuno il numero e la presa che dice se è ancora lui (vedi ``Rete.presa``). Restano gli stessi anche quando
        # la domanda si prepara di nuovo perché la rete ha cambiato forma: cercarli di nuovo per nome vorrebbe dire
        # rispondere per un omonimo nato dopo, o non rispondere più.
        self._legati: dict[str, tuple[int, int | None]] = {}
        self._scelte: list[tuple[list, dict]] = []
        self._prepara()

    # preparazione

    def _prepara(self) -> None:
        rete = self.rete
        schema = _Schema(rete)
        nomi = rete.nomi
        tipi_noti = {piana(nomi[t]): t for t in schema.ruoli}
        self._valida = (len(rete.vivi), [])  # se la rete cambia forma la domanda si prepara di nuovo
        self._ignoti = False  # la frase nomina qualcuno che la rete non conosce ancora
        self._neuroni = len(nomi)
        self._dimenticati = getattr(rete, "dimenticati", None)  # None: una rete che non dimentica, come quella sul disco
        presa = getattr(rete, "presa", None)  # una rete che non dimentica, come quella sul disco, non ne ha bisogno
        # I neuroni che la frase nomina si legano in una copia: restano legati alla domanda solo se questa preparazione
        # arriva in fondo. E quelli legati prima, se erano provvisori, devono esserci ancora: una preparazione fallita a
        # metà non deve lasciare alla prossima un numero che nel frattempo può essere toccato a un altro nome.
        legati = dict(self._legati)
        tiene = getattr(rete, "tiene", None)
        if tiene is not None:
            for nome, (i, sua) in legati.items():
                if sua is not None and not tiene(i, sua):
                    raise ErroreDomanda(f"«{nome}», per cui questa domanda era stata preparata, non c'è più: la domanda va preparata di nuovo.")
        self.piano = []
        self._codice = []
        k = 0  # prossimo parametro
        uscita_prima: list[str] = []
        for numero, p in enumerate(self.passi):
            tipo = None
            if p.tipo is not None:
                tipo = tipi_noti.get(piana(p.tipo))
                if tipo is None:
                    raise ErroreDomanda(f"«{p.tipo}» non è un tipo di fatto della rete." + _simili(p.tipo, [nomi[t] for t in schema.ruoli])
                                        + " La frase «vocabolario» li elenca tutti.")
                self._valida[1].append((tipo, rete.forma.get(tipo)))

            def ruolo(nome: str, tipo=tipo) -> int:
                ammessi = schema.ruoli[tipo] if tipo is not None else list(schema.tipi)
                for r in ammessi:
                    if piana(nomi[r]) == piana(nome):
                        return r
                dove = f"nei fatti «{nomi[tipo]}»" if tipo is not None else "in nessun tipo di fatto"
                elenco = f" Ci sono: {', '.join(nomi[r] for r in ammessi)}." if tipo is not None else _simili(nome, [nomi[r] for r in ammessi])
                raise ErroreDomanda(f"il ruolo «{nome}» non c'è {dove}.{elenco}")

            def valore(v: str) -> tuple[str, int]:
                nonlocal k
                if v == PARAMETRO:
                    k += 1
                    return ("parametro", k - 1)
                if v in legati:
                    return ("neurone", legati[v][0])
                trovato = _trova(rete, v)
                if trovato >= 0:
                    legati[v] = (trovato, presa(trovato) if presa else None)
                else:
                    self._ignoti = True
                return ("neurone", trovato)

            indizi = [(ruolo(r), *valore(v), v) for r, v in p.indizi]
            filtri = []
            for r, segno, v in p.filtri:
                if segno == "=":
                    filtri.append((ruolo(r), "=", *valore(v), v))
                else:
                    if v == PARAMETRO:
                        filtri.append((ruolo(r), segno, "parametro", k, v))
                        k += 1
                    else:
                        try:
                            soglia = float(v)
                        except ValueError:
                            soglia = float("nan")
                        if soglia != soglia:
                            raise ErroreDomanda(f"dopo «{r} {segno}» serve un numero, non «{v}»")
                        filtri.append((ruolo(r), segno, "numero", soglia, v))

            # da dove si entra in questo passo
            ingresso = None
            if numero > 0:
                precedente = self.passi[numero - 1]
                letti = precedente.azione[1]
                da = p.da if p.da is not None else letti[0] if len(letti) == 1 else None
                if da is None:
                    raise ErroreDomanda(f"il passo prima legge più cose ({', '.join(letti)}): scrivi «poi da <ruolo> in …» per dire da quale proseguire")
                if piana(da) not in [piana(x) for x in letti]:
                    raise ErroreDomanda(f"«{da}» non è tra le cose lette dal passo prima ({', '.join(letti)})")
                ingresso = (da, ruolo(p.come if p.come is not None else da))

            # che cosa si legge
            genere = p.azione[0]
            letture: list[tuple[str, int]] = []
            if genere == "leggi":
                for nome in p.azione[1]:
                    speciale = piana(nome) in SPECIALI
                    presente = any(piana(nomi[r]) == piana(nome) for r in (schema.ruoli[tipo] if tipo is not None else schema.tipi))
                    letture.append((piana(nome), -1) if speciale and not presente else ("ruolo", ruolo(nome)))
            somma = ruolo(p.azione[1]) if genere == "somma" else -1
            gruppo = None
            if genere in ("conta", "somma") and p.azione[2] is not None:
                gruppo = (p.azione[2][0], ruolo(p.azione[2][1]))

            self._codice.append({"tipo": tipo, "indizi": indizi, "filtri": filtri, "ingresso": ingresso, "genere": genere,
                                 "letture": letture, "distinti": genere == "leggi" and p.azione[2], "somma": somma, "gruppo": gruppo,
                                 "schema": schema})
            self.piano.append(self._in_parole(numero, p, tipo, indizi, filtri, ingresso, schema))
            uscita_prima = p.azione[1] if genere == "leggi" else []
        ultimo = self.passi[-1]
        if ultimo.azione[0] == "leggi":
            self.colonne = list(ultimo.azione[1])
        else:
            gruppo = ultimo.azione[2]
            cosa = "quanti" if ultimo.azione[0] == "conta" else f"somma di {ultimo.azione[1]}"
            self.colonne = ([f"{gruppo[0]}({gruppo[1]})" if gruppo[0] else gruppo[1]] if gruppo else []) + [cosa]
        del uscita_prima
        compilato = self._compila()
        # La preparazione è riuscita: da qui la domanda è legata a questi neuroni. Quelli tra i nominati che non erano
        # ancora nel giornale sono i soli che possono essere dimenticati.
        self._legati = legati
        self._provvisori = [(nome, i, presa) for nome, (i, presa) in legati.items() if presa is not None]
        self._esegui = compilato

    def _in_parole(self, numero, p, tipo, indizi, filtri, ingresso, schema) -> str:
        nomi = self.rete.nomi
        dove = f"nei fatti «{nomi[tipo]}»" if tipo is not None else "nei fatti di qualsiasi tipo"
        if ingresso is not None:
            come = "" if piana(ingresso[0]) == piana(nomi[ingresso[1]]) else f", come «{nomi[ingresso[1]]}»"
            inizio = f"Accendo i neuroni «{ingresso[0]}» letti prima {dove}{come}"
        elif indizi:
            cosa = " e ".join(f"{nomi[r]} = {testo}" for r, _, _, testo in indizi)
            inizio = f"Accendo {cosa} {dove}"
            if len(indizi) > 1:
                inizio += ": parto dall'indizio che accende meno fatti e tengo quelli che ricevono anche gli altri"
        else:
            numerico = next((f for f in filtri if f[1] != "="), None)
            if numerico is not None and not any(f[1] == "=" for f in filtri) and hasattr(self.rete, "ordinata"):
                inizio = (f"Nessun neurone da cui partire: i valori di «{nomi[numerico[0]]}» {dove} sono tenuti in ordine di grandezza, "
                          "parto dal primo che passa la condizione e prendo i loro fatti")
            elif numerico is not None and len(schema.linea(tipo, numerico[0])) < schema.quanti(tipo):
                inizio = f"Nessun neurone da cui partire: guardo i valori di «{nomi[numerico[0]]}» {dove} e prendo i fatti di quelli che passano la condizione"
            else:
                inizio = f"Nessun neurone da cui partire: passo tutti i fatti «{nomi[tipo]}»"
        if filtri:
            inizio += "; tengo quelli dove " + " e ".join(f"{nomi[f[0]]} {f[1]} {f[-1]}" for f in filtri)
        azione = p.azione
        if azione[0] == "leggi":
            fine = f"leggo {'una volta sola ' if azione[2] else ''}{', '.join(azione[1])}"
        else:
            gruppo = f" per {azione[2][0]}({azione[2][1]})" if azione[2] and azione[2][0] else f" per {azione[2][1]}" if azione[2] else ""
            fine = ("conto i fatti" if azione[0] == "conta" else f"sommo {azione[1]}") + gruppo
        return f"{inizio}; {fine}."

    # numeri

    def _numero(self, n: int) -> float | None:
        if n < 0:
            return None  # il fatto non ha quel ruolo: non c'è nessun numero da leggere
        try:
            return self._numeri[n]
        except KeyError:
            try:
                x = float(self.rete.nomi[n])
            except ValueError:
                x = None
            self._numeri[n] = x
            return x

    def _decimale(self, n: int) -> Decimal | None:
        if n < 0:
            return None  # il fatto non ha quel ruolo: non c'è niente da sommare
        try:
            return self._decimali[n]
        except KeyError:
            try:
                x = Decimal(self.rete.nomi[n])
                if not x.is_finite():
                    x = None
            except InvalidOperation:
                x = None
            self._decimali[n] = x
            return x

    # esecuzione

    def _lettore(self, tipo: int | None, ruolo: int):
        """Dal fatto al neurone che lo raggiunge in quel ruolo; -1 se non c'è."""
        rete = self.rete
        forma = rete.forma.get(tipo) if tipo is not None else None
        if forma and ruolo in forma:  # tutti i fatti del tipo hanno la stessa forma: il ruolo sta sempre allo stesso posto
            s_a, inizio, pos = rete.s_a, rete.f_inizio, forma.index(ruolo)
            return lambda f: s_a[inizio[f] + pos]
        leggi = rete.leggi
        return lambda f: leggi(f, ruolo)

    def _compila(self):
        rete = self.rete
        nomi, per_nome = rete.nomi, rete.per_nome
        passi = []
        # Dove si passano tutti i fatti di un tipo si sceglie la linea più corta, e le righe escono nel suo ordine. La
        # scelta vale per la rete com'è adesso: qui restano le linee tra cui si è scelto e quella presa, e ``esegui``
        # prepara di nuovo se la più corta è diventata un'altra. Così una domanda tenuta da parte dà le righe nello
        # stesso ordine di una preparata adesso.
        scelte: list[tuple[list, dict]] = []
        aggrega = nucleo.aggrega

        def neurone(genere: str, x: int, parametri) -> int:
            if genere == "neurone":
                return x
            trovati = per_nome.get(piana(parametri[x]))
            if not trovati or len(trovati) > 1:
                return _trova(rete, parametri[x])  # un identificativo, un nome che non c'è, oppure più candidati (e allora solleva)
            return trovati[0]

        riceve, legami_di = getattr(rete, "riceve", None), getattr(rete, "legami", None)
        for c in self._codice:
            tipo, schema = c["tipo"], c["schema"]
            lettore = lambda r, tipo=tipo: self._lettore(tipo, r)  # noqa: E731
            forma_del_tipo = rete.forma.get(tipo) if tipo is not None else None

            # Un fatto può avere lo stesso ruolo due volte (una riunione con due presenti). Sulla linea di quel ruolo
            # sta sotto ognuno dei suoi nomi: chi passa la linea neurone per neurone lo incontra due volte, e chi ne
            # legge il ruolo fatto per fatto vede solo il primo nome. Dove può succedere, cioè dove la forma del tipo
            # ripete il ruolo o non è una sola, i fatti si prendono una volta e la condizione guarda tutti i nomi.
            def ripete(r, forma=forma_del_tipo) -> bool:
                return forma is None or forma.count(r) > 1

            # 1. quali fatti scattano
            indizi, ingresso = c["indizi"], c["ingresso"]
            filtri = list(c["filtri"])
            if ingresso is not None:
                d = schema.linea(tipo, ingresso[1])

                def scelti(parametri, accesi, d=d, una_volta=ripete(ingresso[1])):
                    fatti = []
                    for n in accesi:
                        x = d.get(n)
                        if x is not None:
                            if type(x) is int:
                                fatti.append(x)
                            else:
                                fatti.extend(x)
                    return list(dict.fromkeys(fatti)) if una_volta else fatti
            elif tipo is not None and len(indizi) == 1:
                r, genere, x, _ = indizi[0]
                d = schema.linea(tipo, r)
                if genere == "parametro":
                    def scelti(parametri, accesi, d=d, x=x):
                        trovati = per_nome.get(piana(parametri[x]))
                        y = d.get(trovati[0] if trovati and len(trovati) == 1 else _trova(rete, parametri[x]))
                        return () if y is None else (y,) if type(y) is int else y
                else:
                    def scelti(parametri, accesi, d=d, x=x):
                        y = d.get(x)
                        return () if y is None else (y,) if type(y) is int else y
            elif tipo is not None and indizi:
                scatta = rete.scatta

                def scelti(parametri, accesi, indizi=indizi, tipo=tipo):
                    return scatta(tipo, [(r, neurone(genere, x, parametri)) for r, genere, x, _ in indizi])
            elif indizi:  # fatti di qualsiasi tipo
                coppia = next((i for i in indizi if i[0] in rete.ruoli_coppia), None) if len(indizi) > 1 else None
                linee = [schema.linea(t, indizi[0][0]) for t in schema.tipi.get(indizi[0][0], ())]
                riceve, morto, coppie = rete.riceve, rete.f_morto, rete.coppie

                def scelti(parametri, accesi, indizi=indizi, coppia=coppia, linee=linee):
                    accendo = [(r, neurone(genere, x, parametri)) for r, genere, x, _ in indizi]
                    if any(n < 0 for _, n in accendo):
                        return ()
                    if coppia is not None:  # il rilevatore di coppia: dal giorno e da un altro neurone ai fatti che li ricevono insieme
                        giorno = next(n for r, n in accendo if r == coppia[0])
                        altro = next(n for r, n in accendo if r != coppia[0])
                        candidati = _fatti((coppie.get(giorno) or {}).get(altro))
                    else:
                        candidati = [f for d in linee for f in _fatti(d.get(accendo[0][1]))]
                    return [f for f in candidati if morto[f] < 0 and all(riceve(f, r, n) for r, n in accendo)]
            else:  # nessun neurone da cui partire: si passano i fatti del tipo
                uguale = next((f for f in filtri if f[1] == "="), None)
                numerico = next((f for f in filtri if f[1] != "="), None)
                if uguale is not None:  # una condizione di uguaglianza è un indizio: si parte da lì
                    filtri.remove(uguale)
                    d = schema.linea(tipo, uguale[0])

                    def scelti(parametri, accesi, d=d, uguale=uguale):
                        return _fatti(d.get(neurone(uguale[2], uguale[3], parametri)))
                elif numerico is not None:  # si guardano i valori, una volta ciascuno, e si prendono i fatti di quelli che passano
                    filtri.remove(numerico)
                    d = schema.linea(tipo, numerico[0])
                    confronta, numero = CONFRONTI[numerico[1]], self._numero
                    # il motore in memoria tiene i valori di una linea in ordine di grandezza; la rete letta dal disco no
                    in_ordine = getattr(rete, "ordinata", None)

                    def scelti(parametri, accesi, d=d, numerico=numerico, confronta=confronta, numero=numero, in_ordine=in_ordine, tipo=tipo,
                               una_volta=ripete(numerico[0])):
                        soglia = numerico[3] if numerico[2] == "numero" else _soglia(parametri[numerico[3]])
                        fatti = []
                        if in_ordine is not None:  # si parte dal primo valore che passa la condizione
                            prendi = d.get
                            for n in in_ordine(tipo, numerico[0]).scelti(numerico[1], soglia):
                                x = prendi(n)
                                if x is None:
                                    continue  # il neurone è uscito dalla linea: non ha più fatti in vigore lì
                                if type(x) is int:
                                    fatti.append(x)
                                else:
                                    fatti.extend(x)
                            return list(dict.fromkeys(fatti)) if una_volta else fatti
                        for n, x in d.items():
                            v = numero(n)
                            if v is not None and confronta(v, soglia):
                                if type(x) is int:
                                    fatti.append(x)
                                else:
                                    fatti.extend(x)
                        return list(dict.fromkeys(fatti)) if una_volta else fatti
                else:
                    gruppo = c["gruppo"]
                    linee = [schema.linea(tipo, r) for r in schema.ruoli[tipo]]
                    forma = rete.forma.get(tipo)
                    if forma:
                        # Ogni fatto ha tutti i ruoli: basta una linea, quella del gruppo o quella con meno neuroni. Ma
                        # una linea su cui ogni fatto sta una volta sola: non quella di un ruolo che il tipo ripete.
                        una_sola = [schema.linea(tipo, r) for r in schema.ruoli[tipo] if forma.count(r) == 1]
                        if gruppo is not None and forma.count(gruppo[1]) == 1:
                            linee = [schema.linea(tipo, gruppo[1])]
                        elif una_sola:
                            linee = [min(una_sola, key=len)]
                            scelte.append((una_sola, linee[0]))
                    # una linea sola basta se su quella ogni fatto sta una volta: non se è quella di un ruolo ripetuto
                    diretta = len(linee) == 1 and bool(forma) and any(
                        linee[0] is schema.linea(tipo, r) for r in schema.ruoli[tipo] if forma.count(r) == 1)

                    def scelti(parametri, accesi, linee=linee, diretta=diretta):
                        if diretta:
                            fatti = []
                            for x in linee[0].values():
                                if type(x) is int:
                                    fatti.append(x)
                                else:
                                    fatti.extend(x)
                            return fatti
                        return sorted({f for d in linee for x in d.values() for f in _fatti(x)})  # in ordine di nascita

            # 2. le condizioni che restano, guardate fatto per fatto
            prove = []
            for f in filtri:
                leggi = lettore(f[0])
                # Dove il ruolo può essere ripetuto (nel tipo, o in un tipo qualsiasi se la domanda non lo dice) la
                # condizione è vera se uno qualunque dei suoi nomi la passa: così non dipende da quale condizione
                # viene guardata per prima, né da come è scritta la domanda.
                tutti = f[0] if riceve is not None and legami_di is not None and ripete(f[0]) else None
                if f[1] == "=":
                    prove.append(("=", leggi, f[2], f[3], tutti))
                else:
                    prove.append((CONFRONTI[f[1]], leggi, f[2], f[3], tutti))

            # 3. che cosa si legge dai fatti scattati
            genere = c["genere"]
            lettori = []
            for cosa, r in c["letture"]:
                if cosa == "ruolo":
                    leggi = lettore(r)
                    lettori.append(lambda f, leggi=leggi: nomi[n] if (n := leggi(f)) >= 0 else "")
                elif cosa == "tipo":
                    lettori.append(lambda f, t=rete.f_tipo: nomi[t[f]])
                elif cosa == "frase":
                    lettori.append(lambda f, nato=rete.f_nato, frasi=rete.e_frase: frasi[nato[f]])
                elif cosa == "id_fatto":
                    lettori.append(lambda f: f"fatto:{f}")
                else:
                    lettori.append(lambda f, nato=rete.f_nato: f"evento:{nato[f]}")
            forma = rete.forma.get(tipo) if tipo is not None else None
            posizioni = None
            if forma and all(cosa == "ruolo" and r in forma for cosa, r in c["letture"]):
                posizioni = [forma.index(r) for _, r in c["letture"]]
            # Contare o sommare tutti i fatti di un tipo, senza un neurone da cui partire e senza condizioni: è il
            # percorso che passa più fatti, e se c'è lo fa il nucleo compilato. Dà le stesse righe nello stesso
            # ordine, oppure si fa da parte e il conto si fa qui sotto come sempre.
            veloce = None
            if (genere in ("conta", "somma") and tipo is not None and ingresso is None and not indizi and not c["filtri"]
                    and isinstance(rete, Rete) and forma and len(set(forma)) == len(forma)):  # il nucleo non conta due volte un ruolo ripetuto: non ci passa
                veloce = (tipo, c["gruppo"][1] if c["gruppo"] else None, c["somma"] if genere == "somma" else None,
                          {"mese": 7, "anno": 4}.get(c["gruppo"][0]) if c["gruppo"] else None)
            passi.append({"scelti": scelti, "prove": prove, "genere": genere, "lettori": lettori, "posizioni": posizioni,
                          "distinti": c["distinti"], "somma": lettore(c["somma"]) if genere == "somma" else None,
                          "gruppo": (c["gruppo"][0], lettore(c["gruppo"][1])) if c["gruppo"] else None,
                          "uscita": None, "nucleo": veloce})
        # da un passo al successivo: quale ruolo dei fatti scattati si accende dopo
        for i in range(len(passi) - 1):
            da = self._codice[i + 1]["ingresso"][0]
            cosa, r = next(x for x, nome in zip(self._codice[i]["letture"], self.passi[i].azione[1]) if piana(nome) == piana(da))
            if cosa != "ruolo":
                raise ErroreDomanda(f"da «{da}» non si può proseguire: non è un neurone")
            passi[i]["uscita"] = self._lettore(self._codice[i]["tipo"], r)

        self._scelte = scelte
        s_a, inizio = rete.s_a, rete.f_inizio
        numero, decimale = self._numero, self._decimale

        def esegui(parametri, limite):
            accesi = None
            conteggi = []
            self.troncata = False
            self.nucleo = False
            for p in passi:
                if p["nucleo"] is not None:
                    fatto = aggrega(rete, *p["nucleo"])
                    if fatto is not None:
                        righe, visti = fatto
                        conteggi.append(visti)
                        self.conteggi, self.nucleo = conteggi, True
                        return righe
                fatti = p["scelti"](parametri, accesi)
                for prova, leggi, genere, x, tutti in p["prove"]:
                    if prova == "=":
                        n = neurone(genere, x, parametri)
                        # Un nome che non indica nessun neurone non è uguale a niente: nemmeno a un ruolo che manca,
                        # che si legge allo stesso modo (-1). Come per i numeri: un ruolo che manca non è un valore.
                        if n < 0:
                            fatti = []
                        elif tutti is not None:
                            fatti = [f for f in fatti if riceve(f, tutti, n)]
                        else:
                            fatti = [f for f in fatti if leggi(f) == n]
                    else:
                        soglia = x if genere == "numero" else _soglia(parametri[x])
                        if tutti is not None:
                            fatti = [f for f in fatti
                                     if any((v := numero(n)) is not None and prova(v, soglia) for r, n in legami_di(f) if r == tutti)]
                        else:
                            fatti = [f for f in fatti if (v := numero(leggi(f))) is not None and prova(v, soglia)]
                conteggi.append(len(fatti))
                uscita = p["uscita"]
                if uscita is not None:  # si prosegue: i neuroni letti, ognuno una volta sola
                    accesi = {uscita(f) for f in fatti}
                    accesi.discard(-1)
                    continue
                self.conteggi = conteggi
                genere = p["genere"]
                if genere == "leggi":
                    if limite is not None and len(fatti) > limite and not p["distinti"]:
                        fatti, self.troncata = fatti[:limite], True
                    pos = p["posizioni"]
                    if pos is None:
                        lettori = p["lettori"]
                        righe = [tuple([leggi(f) for leggi in lettori]) for f in fatti]
                    elif len(pos) == 1:
                        a = pos[0]
                        righe = [(nomi[s_a[inizio[f] + a]],) for f in fatti]
                    elif len(pos) == 2:
                        a, b = pos
                        righe = [(nomi[s_a[(i := inizio[f]) + a]], nomi[s_a[i + b]]) for f in fatti]
                    elif len(pos) == 3:
                        a, b, c = pos
                        righe = [(nomi[s_a[(i := inizio[f]) + a]], nomi[s_a[i + b]], nomi[s_a[i + c]]) for f in fatti]
                    else:
                        righe = [tuple([nomi[s_a[inizio[f] + a]] for a in pos]) for f in fatti]
                    if p["distinti"]:
                        righe = list(dict.fromkeys(righe))
                        if limite is not None and len(righe) > limite:
                            righe, self.troncata = righe[:limite], True
                    return righe
                gruppo = p["gruppo"]
                chiavi: dict[int, str] = {}
                if gruppo is not None:
                    taglio = {"mese": 7, "anno": 4}.get(gruppo[0])
                    leggi_gruppo = gruppo[1]
                if genere == "conta":
                    if gruppo is None:
                        return [(str(len(fatti)),)]
                    conta: dict[str, int] = {}
                    for f in fatti:
                        n = leggi_gruppo(f)
                        chiave = chiavi.get(n)
                        if chiave is None:
                            chiave = chiavi[n] = (nomi[n][:taglio] if taglio else nomi[n]) if n >= 0 else ""
                        conta[chiave] = conta.get(chiave, 0) + 1
                    return [(chiave, str(quanti)) for chiave, quanti in conta.items()]
                leggi_valore = p["somma"]
                # Le somme si fanno con un contesto proprio, largo e che non arrotonda in silenzio: il risultato non
                # deve dipendere dalla precisione decimale di chi chiama.
                with localcontext(ESATTO):
                    if gruppo is None:
                        totale = Decimal(0)
                        for f in fatti:
                            v = decimale(leggi_valore(f))
                            if v is not None:
                                totale += v
                        return [(format(totale, "f"),)]
                    somme: dict[str, Decimal] = {}
                    for f in fatti:
                        v = decimale(leggi_valore(f))
                        if v is None:
                            continue
                        n = leggi_gruppo(f)
                        chiave = chiavi.get(n)
                        if chiave is None:
                            chiave = chiavi[n] = (nomi[n][:taglio] if taglio else nomi[n]) if n >= 0 else ""
                        somme[chiave] = somme.get(chiave, 0) + v
                    return [(chiave, format(somma, "f")) for chiave, somma in somme.items()]
            return []

        return esegui

    def esegui(self, *parametri: str, limite: int | None = None) -> list[tuple[str, ...]]:
        """Le righe della risposta, come testi. ``limite`` ferma la lettura a quel numero di righe."""
        if len(parametri) != self.parametri:
            raise ErroreDomanda(f"la domanda ha {self.parametri} segnaposto «?» ma sono arrivati {len(parametri)} valori")
        rete = self.rete
        # Prima di tutto chi è nominato nella frase: deve essere ancora lui. Un neurone già nel giornale lo è sempre. Uno
        # nato in una prova può essere stato dimenticato, e allora non conta che il suo numero o il suo nome siano
        # tornati: è un altro. Non lo si cerca di nuovo per nome: si risponderebbe per un altro senza dirlo.
        if self._provvisori:
            for nome, i, presa in self._provvisori:
                if not rete.tiene(i, presa):
                    raise ErroreDomanda(f"«{nome}», per cui questa domanda era stata preparata, non c'è più: la domanda va preparata di nuovo.")
        cambiati = self._dimenticati is not None and rete.dimenticati != self._dimenticati
        if cambiati:
            # Dei neuroni sono stati dimenticati: il loro numero può essere di un altro nome, e quello che la domanda si
            # era tenuta da parte leggendo i nomi (il numero che scrivono) non vale più.
            self._numeri.clear()
            self._decimali.clear()
            self._dimenticati = rete.dimenticati
        quante_linee, forme = self._valida
        if (len(rete.vivi) != quante_linee or any(rete.forma.get(t) is not forma for t, forma in forme)
                # chi la frase nominava e non c'era può essere nato, anche al posto di un nome dimenticato
                or (self._ignoti and (cambiati or len(rete.nomi) != self._neuroni))
                # la linea più corta, da cui passare i fatti di un tipo, è diventata un'altra
                or any(min(tra, key=len) is not presa for tra, presa in self._scelte)):
            try:
                self._prepara()  # nella rete è comparso un ruolo o un tipo nuovo: i posti dei ruoli vanno riletti
            except BaseException:
                # La preparazione non è riuscita (un nome che adesso indica due cose, per dire): il piano di prima non
                # vale più. Alla prossima si riprova, e si ridice l'errore: non si risponde con quello vecchio.
                self._valida = (-1, [])
                raise
        return self._esegui(parametri, limite)


def _soglia(testo: str) -> float:
    try:
        soglia = float(testo)
    except ValueError:
        raise ErroreDomanda(f"qui serve un numero, non «{testo}»") from None
    if soglia != soglia:  # «nan» si lascia leggere come numero, ma non ha niente sopra né sotto
        raise ErroreDomanda(f"qui serve un numero, non «{testo}»")
    return soglia


CIRCUITO = re.compile(r"circuito\s+(.+)", re.IGNORECASE)


class LetturaCircuito:
    """La frase che legge un circuito: una somma che la rete tiene pronta e aggiorna a ogni scrittura.

    ``circuito fatturato per mese`` dà una riga per ogni entità che il circuito ha visto, con il suo valore.
    ``circuito fido residuo di Rossi`` dà il valore di una sola. Si usa come una domanda preparata: ``esegui``,
    ``colonne``, ``piano``.

    Un circuito e un'entità si indicano per nome, oppure con l'identificativo: ``neurone:17``, scritto così, è sempre
    il neurone 17, anche se qualcosa si chiama proprio in quel modo; tra virgolette, ``"neurone:17"``, è sempre un
    nome. È la stessa regola di ``Memoria.valore``: la forma che si propone a chi deve precisare deve indicare
    davvero quello che dice.
    """

    parametri = 0

    def __init__(self, rete: Rete, testo: str) -> None:
        self.rete = rete
        self.testo = " ".join(testo.split())
        self.conteggi: list[int] = []
        self.troncata = False
        self._prepara()

    @staticmethod
    def _spezza(detto: str) -> list[tuple[str, str | None]]:
        """I modi di leggere quello che viene dopo «circuito»: il circuito, e l'entità dopo «di» se c'è. Un nome tra
        virgolette è un pezzo solo, anche se contiene «di»; un identificativo all'inizio è il circuito, e quello che
        segue «di» è l'entità. Solo un nome senza virgolette può essere letto in più modi: prima tutto intero, poi
        spezzato a ogni «di» che non sta tra virgolette, dall'ultimo."""
        def dopo(resto: str, circuito: str) -> list[tuple[str, str | None]] | None:
            resto = resto.strip()
            if not resto:
                return [(circuito, None)]
            return [(circuito, resto[3:].strip())] if resto.startswith("di ") and resto[3:].strip() else None

        if detto.startswith('"'):
            fine = detto.find('"', 1)
            if fine > 0 and (letto := dopo(detto[fine + 1:], detto[:fine + 1])):
                return letto
        m = re.match(r"neurone:\d+(?=\s|$)", detto)
        if m and (letto := dopo(detto[m.end():], m.group())):
            return letto
        candidati: list[tuple[str, str | None]] = [(detto, None)]
        tagli, tra_virgolette = [], False
        for k, c in enumerate(detto):
            if c == '"':
                tra_virgolette = not tra_virgolette
            elif not tra_virgolette and detto.startswith(" di ", k):
                tagli.append(k)
        candidati += [(detto[:k].strip(), detto[k + 4:].strip()) for k in reversed(tagli) if detto[:k].strip() and detto[k + 4:].strip()]
        return candidati

    def _prepara(self, lo_stesso: int | None = None) -> None:
        """Capisce la frase. Con ``lo_stesso`` il circuito non si cerca di nuovo per nome: è quello, e se non c'è più
        la frase non vale più. Serve a una lettura preparata prima che il circuito venisse ridefinito. Nemmeno
        l'entità si cerca di nuovo: resta quella per cui la lettura è stata preparata, e chi esegue ha già controllato
        che ci sia ancora. Cercarla di nuovo vorrebbe dire rispondere per un'altra che ne ha preso il nome o il numero."""
        rete = self.rete
        circuiti = getattr(rete, "circuiti", None)
        if circuiti is None:
            raise ErroreDomanda("questa rete non tiene somme pronte: i circuiti ci sono solo nella rete tutta in memoria")
        detto = CIRCUITO.fullmatch(self.testo).group(1).strip()
        per_nome: dict[str, list[dict]] = {}
        for c in circuiti:
            per_nome.setdefault(piana(c["nome"]), []).append(c)

        def tra_virgolette(x: str) -> bool:
            return len(x) > 1 and x.startswith('"') and x.endswith('"')

        def cerca(nome: str) -> dict | None:
            """Il circuito con quell'identificativo, oppure con quel nome. Due circuiti con lo stesso nome non si
            scelgono a caso: la frase va precisata."""
            m = None if tra_virgolette(nome) else IDENTIFICATIVO.fullmatch(nome)
            if m:
                scelto = next((c for c in circuiti if c["id"] == int(m.group(1))), None)
                if scelto is None:  # un identificativo indica quel neurone e nient'altro: non si ripiega su un nome uguale
                    raise ErroreDomanda(f"«{nome}» non è un circuito. Se un circuito si chiama proprio così, scrivilo tra virgolette.")
                return scelto
            trovati = per_nome.get(piana(nome.strip('"')), [])
            if len(trovati) > 1:
                raise Ambigua(f"«{nome}» indica {len(trovati)} circuiti diversi: scrivi quale, con il suo identificativo ("
                              + ", ".join(f"«circuito neurone:{c['id']}»" for c in trovati) + ")")
            return trovati[0] if trovati else None

        def entita(nome: str) -> int:
            m = None if tra_virgolette(nome) else IDENTIFICATIVO.fullmatch(nome)
            if m:
                if int(m.group(1)) >= len(rete.nomi):
                    raise ErroreDomanda(f"«{nome}» non esiste: la rete ha {len(rete.nomi)} neuroni")
                return int(m.group(1))
            trovati = rete.per_nome.get(piana(nome.strip('"'))) or []
            if not trovati:
                raise ErroreDomanda(f"non conosco «{nome}»")
            if len(trovati) > 1:
                raise Ambigua(f"«{nome}» indica {len(trovati)} cose diverse: scrivi quale, con il suo identificativo ("
                              + ", ".join(f"neurone:{i}" for i in trovati[:5]) + ")")
            return trovati[0]

        scelto, di = None, None
        if lo_stesso is not None:
            scelto = next((c for c in circuiti if c["id"] == lo_stesso), None)
            if scelto is None:
                raise ErroreDomanda(f"il circuito «{self._nome}» per cui questa lettura era stata preparata non c'è più: è stato ritirato. "
                                    "Se un altro ne ha preso il nome, la domanda va preparata di nuovo.")
            di = self._di
        else:
            for circuito, chi in self._spezza(detto):
                scelto = cerca(circuito)
                if scelto is not None:
                    if chi is not None:
                        di = entita(chi)
                    break
        if scelto is None:
            raise ErroreDomanda(f"«{detto}» non è un circuito della rete." + _simili(detto, [c["nome"] for c in circuiti]))
        self._circuito, self._di, self._nome = scelto["id"], di, scelto["nome"]
        self._versione = scelto["versione"]  # il circuito può cambiare, o sparire: chi esegue controlla che sia ancora questo
        self._nome_di = None if di is None else rete.nomi[di]  # e che l'entità sia ancora lei: un numero dimenticato può toccare a un altro nome
        if lo_stesso is None:  # la presa si prende una volta sola, quando la frase viene capita: dopo è quella
            self._presa_di = rete.presa(di) if di is not None and hasattr(rete, "presa") else None
        tagli = {t["taglio"] for t in scelto["termini"]}
        ruoli = {rete.nomi[t["lega"]] for t in scelto["termini"]}
        # il nome della prima colonna: il mese o l'anno se il circuito raggruppa così, altrimenti il ruolo che lega i fatti all'entità
        self.colonne = [{7: "mese", 4: "anno"}.get(next(iter(tagli)), "di") if len(tagli) == 1 and None not in tagli
                        else ruoli.pop() if len(ruoli) == 1 else "di", scelto["nome"]]
        self.piano = [self._in_parole()]

    def _in_parole(self) -> str:
        lettura = getattr(self.rete, "lettura", None)
        if lettura is not None and lettura[0] == "registrato":
            return (f"Leggo il circuito «{self._nome}» su tutto quello che è registrato, senza guardare se vale: questa somma non è tenuta pronta, "
                    "la rifaccio dai fatti.")
        return f"Leggo il circuito «{self._nome}»: è una somma tenuta pronta, aggiornata dalla stessa scrittura che registra i fatti. Non calcolo niente."

    def esegui(self, *parametri: str, limite: int | None = None) -> list[tuple[str, ...]]:
        if parametri:
            raise ErroreDomanda("la lettura di un circuito non ha segnaposto «?»")
        rete = self.rete
        # Una lettura preparata non è una fotografia del circuito di allora. Se nel frattempo il circuito è stato
        # ridefinito (da mese ad anno, un termine in più) si prepara di nuovo, così le colonne dicono quello che
        # contengono; se è stato ritirato non c'è più niente da leggere, e lo si dice invece di rispondere zero.
        # Prima di tutto il destinatario: deve essere ancora lui, con il suo numero e il suo nome. Un neurone nato in
        # una prova può essere dimenticato, e il suo numero toccare a un altro nome, o il suo nome a un altro numero.
        if self._di is not None and (self._di >= len(rete.nomi) or rete.nomi[self._di] != self._nome_di
                                     or (self._presa_di is not None and not rete.tiene(self._di, self._presa_di))):
            raise ErroreDomanda(f"«{self._nome_di}», per cui questa lettura era stata preparata, non c'è più: la domanda va preparata di nuovo.")
        adesso = next((c for c in rete.circuiti if c["id"] == self._circuito), None)
        if adesso is None or adesso["versione"] != self._versione:
            self._prepara(lo_stesso=self._circuito)  # lo stesso circuito, non un altro che ne ha preso il nome
        # Il circuito tiene le somme dei fatti che valgono a un istante: quello va detto ogni volta. Adesso, se la rete
        # è quella viva: una decorrenza o una scadenza passata nel frattempo cambia la somma anche senza scritture.
        # L'istante della vista, se è una vista del passato. E se si chiede quello che è registrato senza guardare la
        # validità, le somme pronte non sono la risposta: si rifanno dai fatti.
        lettura = getattr(rete, "lettura", None)
        if lettura is None:
            somme = rete.somme_pronte(self._circuito)
        elif lettura[0] == "valido":
            somme = rete.somme_pronte(self._circuito, lettura[1])
        else:
            somme = rete.somme_registrate(self._circuito)
        self.piano = [self._in_parole()]
        nomi = rete.nomi
        if self._di is not None:
            righe = [(nomi[self._di], da_unita(somme.get(self._di, 0)))]
        else:
            # In ordine di nascita delle entità: non nell'ordine in cui le somme sono tenute, che dipende da quando
            # sono state fatte (una dopo l'altra mentre si scrive, o tutte insieme da chi riapre). Così la stessa
            # lettura dà le stesse righe nello stesso ordine alla rete viva e a quella riaperta.
            righe = [(nomi[x], da_unita(somme[x])) for x in sorted(somme)]
        self.conteggi = [0]  # nessun fatto è stato letto
        self.troncata = limite is not None and len(righe) > limite
        return righe[:limite] if self.troncata else righe


def prepara(rete: Rete, testo: str) -> Domanda | LetturaCircuito:
    """Prepara una frase della lingua: una domanda, oppure la lettura di un circuito."""
    if CIRCUITO.fullmatch(" ".join(testo.split())):
        return LetturaCircuito(rete, testo)
    return Domanda(rete, testo)


# Per sommare senza perdere cifre. L'arrotondamento è detto qui: una somma che si annulla deve scriversi allo stesso modo
# qualunque cosa il programma che usa la libreria abbia scelto come arrotondamento di serie.
ESATTO = Context(prec=2000, rounding=ROUND_HALF_EVEN, Emin=MIN_EMIN, Emax=MAX_EMAX, capitals=1, clamp=0,
                 traps=[Inexact, InvalidOperation])

TEMPO = re.compile(r"\s+come\s+(?:(risultava|valeva)\s+(?:il\s+(\d{4}-\d{2}-\d{2}(?:T[0-9:.+-]+)?)|all'evento\s+(\d+)|(adesso))|(è\s+registrato))\s*$",
                   re.IGNORECASE)


def tempo(testo: str) -> tuple[str, dict[str, Any] | None]:
    """Separa dalla domanda la clausola del tempo, se c'è, e la restituisce a parte.

    ``… come risultava il 2026-11-16``: solo quello che a quell'istante era già registrato, e che valeva allora.
    ``… come risultava all'evento 41``: quello che era registrato dopo quell'evento, senza guardare la validità.
    ``… come valeva il 2026-11-16``: quello che valeva a quell'istante, sapendo tutto quello che è registrato oggi.
    ``… come valeva adesso``: quello che vale in questo momento. È quello che una memoria risponde senza clausola.
    ``… come è registrato``: tutto quello che è registrato e non ritirato, anche scaduto o non ancora cominciato.
    """
    trovata = TEMPO.search(testo)
    if not trovata:
        return testo, None
    modo, data, evento, adesso, registrato = trovata.groups()
    if registrato is not None:
        return testo[:trovata.start()], {"modo": "registrato"}
    modo = modo.lower()
    clausola: dict[str, Any] = {"modo": modo}
    if evento is not None:
        if modo != "risultava":
            raise ErroreDomanda("«all'evento» si usa con «come risultava»: «come valeva» vuole una data, o «adesso»")
        clausola["evento"] = int(evento)
    elif adesso is not None:
        if modo != "valeva":
            raise ErroreDomanda("«adesso» si usa con «come valeva»: senza clausola la domanda dice già quello che risulta adesso")
        clausola["adesso"] = True
    else:
        from sinapsidb.motore import istante

        clausola["istante"] = istante(data)
        if clausola["istante"] is None:
            raise ErroreDomanda(f"«{data}» non è una data intera: scrivila come 2026-11-16 oppure 2026-11-16T10:30")
    return testo[:trovata.start()], clausola


def chiedi(rete: Rete, testo: str, limite: int = 200) -> dict[str, Any]:
    """Esegue una frase della lingua e restituisce colonne, righe, piano e tempi.

    Oltre alle domande capisce ``vocabolario``, ``percorsi da <ruolo> a <ruolo>`` e ``circuito <nome>``. Il tempo riportato è
    quello della sola esecuzione, in microsecondi: la domanda viene ripetuta e si prende il valore mediano,
    perché una misura sola oscilla troppo.
    """
    pulita = " ".join(testo.split())
    if piana(pulita) == "vocabolario":
        righe = [(v["tipo"], ", ".join(v["ruoli"]), str(v["fatti"])) for v in vocabolario(rete)]
        return {"domanda": pulita, "colonne": ["tipo di fatto", "ruoli", "fatti"], "righe": righe, "quante": len(righe), "troncata": False,
                "piano": ["Elenco i tipi di fatto della rete, con i loro ruoli."], "preparazione_us": None, "esecuzione_us": None, "fatti_scattati": []}
    m = re.fullmatch(r"percorsi\s+da\s+(.+?)\s+a\s+(.+)", pulita, flags=re.IGNORECASE)
    if m:
        da, a = (x.strip().strip('"') for x in m.groups())
        strade = percorsi(rete, da, a)
        return {"domanda": pulita, "colonne": ["passi", "passando per", "domanda"],
                "righe": [(str(s["passi"]), ", ".join(s["per"]), s["domanda"]) for s in strade], "quante": len(strade), "troncata": False,
                "piano": [f"Cerco le strade da «{da}» a «{a}» attraverso i tipi di fatto. La strada la sceglie chi chiede."],
                "preparazione_us": None, "esecuzione_us": None, "fatti_scattati": []}
    t0 = time.perf_counter_ns()
    domanda = prepara(rete, testo)
    t1 = time.perf_counter_ns()
    if domanda.parametri:
        raise ErroreDomanda("qui i segnaposto «?» non si possono usare: scrivi i valori")
    righe = domanda.esegui(limite=limite)
    t2 = time.perf_counter_ns()
    tempi = [t2 - t1]
    conteggi, troncata = list(domanda.conteggi), domanda.troncata
    # La prima esecuzione paga la memoria fredda e i numeri ancora da leggere: se la domanda è breve si ripete.
    for _ in range(20 if tempi[0] < 2_000_000 else 4 if tempi[0] < 200_000_000 else 0):
        t = time.perf_counter_ns()
        domanda.esegui(limite=limite)
        tempi.append(time.perf_counter_ns() - t)
    tempi.sort()
    return {"domanda": domanda.testo, "colonne": domanda.colonne, "righe": righe, "quante": len(righe), "troncata": troncata, "piano": domanda.piano,
            "preparazione_us": round((t1 - t0) / 1000, 1), "esecuzione_us": round(tempi[len(tempi) // 2] / 1000, 2), "fatti_scattati": conteggi}
