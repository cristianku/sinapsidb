"""Il motore di SinapsiDB: una rete di neuroni a soglia, senza SQL.

Tutto è neurone. Una cosa nominata, un ruolo, un tipo di fatto, un valore, una regola: neuroni.
Un FATTO è un neurone a soglia: riceve una sinapsi da ciascuno dei suoi partecipanti, ognuna con
un ruolo. Una DOMANDA accende alcuni neuroni (gli indizi) e fissa la soglia al numero di indizi:
scattano solo i fatti che li ricevono tutti. Dai fatti scattati l'attivazione prosegue lungo il
ruolo chiesto e arriva alla risposta. Una REGOLA è lo stesso meccanismo valutato a ogni scrittura.

Niente si cancella: un fatto superato viene ritirato dall'evento che lo supera e resta leggibile.

Su disco c'è un solo giornale in sola aggiunta: un record per evento, con lunghezza e CRC32.
È la verità: la rete in memoria si ricostruisce rileggendolo. Un'istantanea facoltativa evita di
rileggerlo tutto all'avvio. Solo libreria standard di Python.

Un solo processo scrive. ``sync="frase"`` sincronizza il disco a ogni evento; ``sync="os"`` lascia
fare al sistema operativo: più rapido, ma un'interruzione di corrente può perdere gli ultimi eventi.
``sync="lotti"`` (di solito per il tempo di un'importazione, con ``Rete.a_lotti``) tiene gli eventi in
attesa e li scrive a gruppi: più rapido ancora, ma li perde anche se si ferma il processo.

Le misure sono in ``docs/confronto-postgresql.md``.
"""

from __future__ import annotations

import bisect
import errno
import fcntl
import hashlib
import heapq
import json
import marshal
import math
import operator
import os
import re
import struct
import sys
import time
import unicodedata
import zlib
from contextlib import contextmanager
from array import array
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

BIT = 21  # bit riservati all'id di un neurone dentro una chiave: fino a 2 milioni di neuroni
MASSIMO_LEGAMI = 127  # quanti legami può avere un fatto: il loro numero sta in un byte
# Una linea è «questo ruolo, in fatti di questo tipo», e la sua chiave tiene il tipo nei BIT più bassi: un tipo di fatto
# deve essere uno dei primi 2.097.152 neuroni della rete. Oltre, la sua linea si confonderebbe con quella di un altro
# tipo: non si scrive, e lo si dice. I ruoli e gli altri neuroni non hanno questo limite.
MASSIMO_TIPO = 1 << BIT
PEZZO_DI_GIORNALE = 64 << 20  # il giornale si rilegge a pezzi di questa misura, non tutto in memoria in una volta
MASSIMO_RECORD = 32 << 20  # quanto può essere lungo il record di un evento: oltre non si scrive, e non si crede a chi lo dichiara
# Quanto si guarda oltre un record che non si rilegge, per sapere se dopo ce ne sono altri. Più di un record e di un
# gruppo scritto a lotti messi insieme: una scrittura sola rimasta a metà ci sta sempre dentro, e non passa per un guasto.
FINESTRA_DI_CODA = 64 << 20


def record_buono(lunghezza: int, crc: int, corpo) -> bool:
    """Se quello che si è letto è un record del giornale: lungo quanto dichiara, con il controllo che torna, e fatto
    come un record, cioè una lista. Otto byte a zero hanno il controllo giusto (quello di un testo vuoto) e non sono
    un record: sono spazio mai scritto. Lo usano tutti quelli che camminano sul giornale, perché si fermino nello
    stesso punto."""
    return 2 <= lunghezza == len(corpo) and corpo[:1] == b"[" and zlib.crc32(corpo) == crc
# In coda al file dell'istantanea: l'impronta di tutto quello che viene prima. Un'istantanea che non la porta, o che
# non le corrisponde, non prova di essere quella che è stata salvata: non si usa, si rilegge il giornale.
SIGILLO = b"\nSINAPSIDB-ISTANTANEA-SHA256:"
TAGLI = {"mese": 7, "anno": 4}  # quanti caratteri di una data fanno il suo mese e il suo anno: 2026-11, 2026

# Di chi è un fatto, per un circuito ordinario, quando nomina più volte il ruolo che lega. Il modo è cambiato tre volte
# in un giorno, e con ognuno sono state scritte ricevute vere: per verificarle bisogna rifare il conto come allora.
# Per ogni modo: come si rifanno le somme dai fatti, e che cosa aggiunge una scrittura.
#   «tutte»      il fatto vale per ogni entità che nomina, una volta ciascuna
#   «prima»      vale solo per la prima che nomina
#   «occorrenze» vale una volta per ogni volta che la nomina: la stessa entità due volte conta doppio
# Il modo di oggi è il 4: ogni entità nominata, una volta sola, a ogni scrittura come rifacendo le somme.
#
# Con quale modo è stata presa una decisione lo dice chi l'ha scritta, nel giornale:
#   - dalla 0.9.6 ogni decisione porta il suo modo (``calcolo``) nello stesso record dell'evento. È del motore fino al
#     giornale: chi prepara la nota non lo può togliere né cambiare (``Rete._con_la_decisione``);
#   - la 0.9.5 lo scriveva solo nel segno del giornale (``calcoli``: coppie di modo ed evento da cui vale), e lo
#     scriveva aprendo una memoria, anche solo per leggere. Chi scriveva senza una memoria non toccava il segno;
#   - i programmi di prima non dichiaravano niente.
# Per una decisione che non dichiara il suo modo vale un'attestazione, se c'è: il segno dice chi ha scritto quel
# tratto. Se non c'è, tutti i modi noti sono possibili, e si può dire solo con quali la ricevuta è compatibile: non
# quale programma l'ha scritta. I numeri non si riusano: un modo nuovo prende un numero nuovo.
CALCOLO = 4
CALCOLI = {1: ("tutte", "prima"),  # fino alla 0.9.2: i due percorsi non coincidevano
           2: ("prima", "prima"),  # la 0.9.3
           3: ("tutte", "occorrenze"),  # la 0.9.4
           4: ("tutte", "tutte")}
TUTTI_I_CALCOLI = tuple(sorted(CALCOLI, reverse=True))


# Come un record del giornale diventa testo. Un codificatore solo, tenuto da parte: ``json.dumps`` ne farebbe uno nuovo
# a ogni evento. Scrive esattamente quello che scriverebbe ``json.dumps`` con le stesse scelte.
_codifica = json.JSONEncoder(ensure_ascii=False, separators=(",", ":")).encode
# Come lo stesso codificatore scrive un testo: serve a chi compone a mano il record di un fatto semplice.
_testo_nel_giornale = json.encoder.encode_basestring
_SOLO_INTERI = {int}
# I nomi dei tipi di fatto che definiscono regole e circuiti.
_NOMI_DELLE_DEFINIZIONI = frozenset(("regola", "segnale", "circuito", "termine"))
# I nomi che hanno un compito nel motore: quando nasce un neurone che si chiama così il motore se lo segna.
_NOMI_SPECIALI = _NOMI_DELLE_DEFINIZIONI | {"quando", "vale da", "vale fino a"}
LOTTO = 1 << 18  # quanti byte di giornale si tengono in attesa quando si scrive a lotti


def _un_testo(x):
    """Lo stesso testo per ogni valore uguale, se è un testo: l'origine e il tipo degli eventi sono pochi valori ripetuti."""
    return sys.intern(x) if type(x) is str else x


def testo_semplice(x):
    """Il testo così com'è, senza la classe che lo porta. Un'enumerazione di testo vale il suo valore: è quello che
    finisce nel giornale, ed è quello che deve restare in memoria e nell'impronta di una richiesta. ``str(x)`` non
    basta: per ``class Canale(str, Enum)`` dà il nome del membro."""
    return str.__str__(x) if isinstance(x, str) and type(x) is not str else x


def _grandezza(nome: str) -> float | None:
    """Il numero che un neurone porta nel nome, per metterlo in ordine di grandezza; None se non è un numero."""
    try:
        v = float(nome)
    except ValueError:
        return None
    return None if v != v else v  # un «non numero» non sta in nessun ordine


class Ordinata:
    """I neuroni che una linea ha ricevuto, in ordine di grandezza del numero che portano nel nome.

    Serve alle domande «sopra», «sotto», «almeno», «al massimo» senza un neurone da cui partire: invece di guardare
    tutti i valori della linea si parte dal primo che passa la condizione. È quello che un indice ordinato fa in un
    database a tabelle.

    Si costruisce la prima volta che una domanda la chiede, dai neuroni che la linea ha in quel momento, e da lì in
    poi il motore le fa vedere ogni neurone che entra nella linea. I nuovi aspettano in una lista corta, che le
    domande guardano per intero, e vengono fusi con gli altri quando sono tanti: così una scrittura non paga mai il
    riordino di tutta la linea. Un neurone che dalla linea è uscito può restare qui: chi legge prende i fatti dalla
    linea, e lì non lo trova. Non va sul disco: è un'altra forma di quello che c'è già, e si rifà.

    Quello che non può restare è un neurone dimenticato: il suo numero può toccare a un altro nome, con un altro
    valore. Per questo si ricorda il neurone più alto che ha visto, e il motore butta via l'ordine quando dimentica
    neuroni da lì in giù (``Rete.dimentica``): si rifà alla prossima domanda.
    """

    __slots__ = ("valori", "neuroni", "attesa", "massimo")

    def __init__(self, nomi, neuroni) -> None:
        self.valori, self.neuroni = array("d"), array("I")
        self.attesa: dict[int, float] = {}
        self.massimo = -1
        self._metti(sorted((v, n) for n in neuroni if (v := _grandezza(nomi[n])) is not None))

    def _metti(self, coppie) -> None:
        self.valori, self.neuroni = array("d", [v for v, _ in coppie]), array("I", [n for _, n in coppie])
        if coppie:
            self.massimo = max(self.massimo, max(self.neuroni))

    def vede(self, neurone: int, nome: str) -> None:
        """Un neurone è entrato nella linea. Se c'è già, non cambia niente."""
        v = _grandezza(nome)
        if v is None or neurone in self.attesa:
            return
        valori, neuroni = self.valori, self.neuroni
        i = bisect.bisect_left(valori, v)
        while i < len(valori) and valori[i] == v:
            if neuroni[i] == neurone:
                return
            i += 1
        self.attesa[neurone] = v
        if neurone > self.massimo:
            self.massimo = neurone
        if len(self.attesa) > max(1024, len(valori) >> 6):
            self._metti(sorted([*zip(valori, neuroni), *((x, n) for n, x in self.attesa.items())]))
            self.attesa = {}

    def scelti(self, confronto: str, soglia: float):
        """I neuroni il cui numero sta, rispetto alla soglia, come dice il confronto: «>», «>=», «<» oppure «<=»."""
        if soglia != soglia:
            return ()  # una soglia che non è un numero non ha niente sopra né sotto, come nel confronto fatto uno per uno
        valori, neuroni = self.valori, self.neuroni
        if confronto == ">":
            da, a = bisect.bisect_right(valori, soglia), len(valori)
        elif confronto == ">=":
            da, a = bisect.bisect_left(valori, soglia), len(valori)
        elif confronto == "<":
            da, a = 0, bisect.bisect_left(valori, soglia)
        else:
            da, a = 0, bisect.bisect_right(valori, soglia)
        trovati = neuroni[da:a]
        if self.attesa:
            passa = {">": float.__gt__, ">=": float.__ge__, "<": float.__lt__, "<=": float.__le__}[confronto]
            soglia = float(soglia)
            altri = sorted((v, n) for n, v in self.attesa.items() if passa(v, soglia))
            if altri:
                # Anche quelli che aspettano escono in ordine di grandezza, in mezzo agli altri: l'ordine delle righe
                # di una domanda non deve dipendere da quando questo ordine è stato costruito, o rifatto. Chi riapre
                # dal giornale lo costruisce in un altro momento, e deve leggere le stesse righe.
                trovati = [n for _, n in heapq.merge(zip(valori[da:a], trovati), altri)]
        return trovati


def piana(nome: str) -> str:
    """Il nome senza maiuscole, accenti e spazi doppi: è la chiave con cui si cerca un neurone."""
    if nome.isascii():  # il caso più comune: niente accenti da togliere
        return " ".join(nome.casefold().split())
    s = unicodedata.normalize("NFD", nome)
    return " ".join("".join(c for c in s if unicodedata.category(c) != "Mn").casefold().split())


class Vietato(Exception):
    """Una regola è scattata: la scrittura non è avvenuta. ``args[0]`` è la spiegazione."""


class NonNumerico(ValueError):
    """Un circuito deve sommare un valore che non è un numero, o che è fuori dal suo dominio: la scrittura non è avvenuta."""


class GiornaleGuasto(ValueError):
    """Nel giornale c'è un record che non si rilegge e che non è una coda scritta a metà: è intero, oppure dopo di lui
    ce ne sono altri. Una coda a metà è una scrittura mai confermata, e chi apre per scrivere la taglia. Questo no:
    tagliare da lì toglierebbe scritture confermate, e leggere solo fin lì le nasconderebbe senza dirlo. Il giornale
    non si apre, né per scrivere né per leggere, finché non viene ripristinato da una copia."""


class SegnoGuasto(ValueError):
    """Il segno durevole del giornale (il file «formato») c'è ma non si legge, o non ha la forma che deve avere, o
    parla di eventi che il giornale non ha. Non è un giornale senza segno. Finché non viene ripristinato non si scrive
    e non si verifica, e non lo si sostituisce con uno nuovo: la frontiera che conteneva non si può dedurre da quanto
    è lungo il giornale oggi."""


class ScenarioIncompleto(ValueError):
    """Una rettifica indicata per un riesame ha bisogno di un evento che non è né nella base né tra quelli indicati."""


class DefinizioneNonValida(ValueError):
    """Una regola o un circuito scritti nella rete non si possono leggere: la scrittura non è avvenuta."""


# I circuiti sommano in interi, in milionesimi: niente arrotondamenti, e il risultato non dipende dal contesto
# decimale di chi chiama. Un numero entra in un circuito se è finito, ha al più sei decimali e trenta cifre intere.
# Il formato delle ricevute delle decisioni. Una ricevuta scritta oggi lo dichiara: è il 2, in cui la versione di una
# regola o di un circuito è l'ultimo evento che ne ha cambiato la definizione. Le ricevute scritte prima non lo
# dichiarano, e hanno avuto più di una forma: il motore rifà la decisione una volta sola e chi verifica la confronta
# con ognuna delle forme storiche, per intero. Le ricevute vecchie non si riscrivono.
FORMATO = 2

SCALA = 6
_UNITA = 10 ** SCALA
_MASSIMO = 10 ** (30 + SCALA)


def in_unita(testo: str) -> int | None:
    """Il numero scritto in ``testo``, in milionesimi, come intero esatto. None se non è un numero che un circuito
    sa sommare senza perdere niente."""
    try:
        segno, cifre, esponente = Decimal(testo).as_tuple()
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not isinstance(esponente, int) or len(cifre) > 40 or esponente > 40:
        return None  # non è finito, oppure è fuori misura
    n = int("".join(map(str, cifre)) or "0")
    e = esponente + SCALA
    if e < -len(cifre):  # più decimali che cifre: senza fare la potenza si sa già che non ci sta
        return 0 if n == 0 else None
    if e >= 0:
        n *= 10 ** e
    else:
        n, resto = divmod(n, 10 ** -e)
        if resto:
            return None  # più di sei decimali: sommarlo vorrebbe dire arrotondarlo
    if n >= _MASSIMO:
        return None
    return -n if segno else n


def istante(testo: str) -> float | None:
    """L'istante scritto in ``testo`` come data intera (2026-11-22) o data e ora (2026-11-22T10:30), in secondi.
    Senza fuso vale l'ora universale. None se non è una data intera: una data senza anno non si completa."""
    try:
        quando = datetime.fromisoformat(testo.strip())
    except (ValueError, AttributeError):
        return None
    if quando.tzinfo is None:
        quando = quando.replace(tzinfo=timezone.utc)
    return quando.timestamp()


_DATA_PER_ESTESO = re.compile(r"\d{4}-\d{2}-\d{2}(?:[T ].+)?")


def data_di_calendario(nome: str) -> bool:
    """Se il nome è una data scritta per esteso, 2026-11-22, anche con l'ora dopo. È la sola forma di cui il mese e
    l'anno sono l'inizio del nome: 20261122 e 2020-W53-5 sono date anche loro, ma il loro inizio non è un mese."""
    return _DATA_PER_ESTESO.fullmatch(nome) is not None and istante(nome) is not None


def da_unita(v: int) -> str:
    """Il numero in milionesimi scritto in cifre, senza zeri inutili."""
    intero, resto = divmod(abs(v), _UNITA)
    return ("-" if v < 0 else "") + str(intero) + (("." + f"{resto:0{SCALA}d}".rstrip("0")) if resto else "")


class Ambiguo(Exception):
    """Un nome indica più neuroni: serve una domanda. ``args[0]`` è l'elenco dei candidati."""


class Rete:
    def __init__(self, cartella: str | None = None, sync: str = "frase", decisioni: bool = False, sola_lettura: bool = False) -> None:
        # neuroni
        self.nomi: list[str] = []
        self.per_nome: dict[str, list[int]] = {}
        self._confermati = 0  # neuroni già scritti nel giornale
        # fatti: neuroni a soglia, una riga di colonne ciascuno
        self.f_tipo = array("i")
        self.f_inizio = array("i")  # prima sinapsi del fatto
        self.f_n = array("b")  # quante sinapsi ha
        self.f_nato = array("i")  # evento che lo ha creato
        self.f_morto = array("i")  # evento che lo ha ritirato, -1 se è in vigore
        # sinapsi: dal fatto al partecipante, con un ruolo
        self.s_ruolo = array("i")
        self.s_a = array("i")
        # dendriti. Una LINEA è «questo ruolo, in fatti di questo tipo». Per ogni linea, da ogni neurone
        # si arriva ai fatti in vigore che lo ricevono lì (un intero se è uno solo, altrimenti una lista)
        # e, a parte, a quelli ritirati.
        self.vivi: dict[int, dict[int, object]] = {}
        self.morti: dict[int, dict[int, list[int]]] = {}
        # rilevatori di coppia: per un valore (un giorno) e un partecipante, tutti i fatti, anche ritirati
        self.coppie: dict[int, dict[int, list[int]]] = {}
        # forma di un tipo di fatto: i ruoli nell'ordine in cui compaiono, se tutti i fatti di quel tipo
        # li hanno uguali; None se almeno uno è diverso. Serve a leggere un ruolo senza cercarlo.
        self.forma: dict[int, tuple | None] = {}
        self.ruoli_coppia: set[int] = set()
        # eventi
        self.e_frase: list[str] = []
        self.e_origine: list[str] = []
        self.e_quando = array("d")  # l'istante in cui la richiesta è arrivata: è l'istante della decisione
        self.e_scritto = array("d")  # l'istante in cui l'evento è stato scritto: non torna mai indietro
        self.e_tipo: list[str] = []
        # una nota facoltativa per evento, scritta nello stesso record del giornale: chi racconta ci mette, per
        # esempio, la chiave contro i doppioni e la ricevuta, così stanno o non stanno insieme ai fatti
        self.e_nota: dict[int, object] = {}
        self.regole: list[dict] = []
        self._t_regola = -1  # neuroni «regola» e «segnale», quando esistono
        self._t_segnale = -1
        # Circuiti mantenuti: somme tenute pronte, per entità. Sono stato derivato: non vanno nel giornale e si
        # rifanno dai fatti in vigore. ``_termini`` dice, per tipo di fatto, a quali circuiti contribuisce.
        self.circuiti: list[dict] = []
        self.valori: dict[int, dict[int, int]] = {}  # in milionesimi, interi: vedi in_unita
        # Quanti fatti contribuiscono a ogni entità di ogni circuito. Un'entità sta nel circuito finché almeno un fatto
        # le dà qualcosa, anche se la somma fa zero; quando l'ultimo se ne va esce, come uscirebbe rifacendo le somme
        # dai fatti. Senza, un mese rimasto senza fatture resterebbe a zero in memoria e sparirebbe alla rilettura.
        self._presenze: dict[int, dict[int, int]] = {}
        # Come va letta questa rete da chi legge un circuito: None com'è adesso; ("valido", istante) è una vista del
        # passato; ("registrato",) vuol dire tutto quello che è registrato, senza guardare la validità.
        self.lettura: tuple | None = None
        self._termini: dict[int, list[tuple]] = {}
        self._ordinate: dict[int, Ordinata] = {}  # linea -> i suoi neuroni in ordine di grandezza, per le linee a cui è stato chiesto
        self._delta: dict[tuple[int, int], int] | None = None  # quanto la scrittura in corso sposta ogni valore
        # Il tempo di validità. Un fatto vale da «vale da» a «vale fino a», fine esclusa; se non lo dice, vale da
        # quando è registrato, senza scadenza. Le somme dei circuiti sono quelle dei fatti che valgono all'istante
        # ``_valido_al`` e restano giuste finché non si passa una decorrenza o una scadenza: tra ``_confine_prima``
        # compreso e ``_confine_dopo`` escluso. Fuori di lì si rifanno dai fatti.
        self._r_da = -1  # neuroni dei ruoli «vale da» e «vale fino a», quando esistono
        self._r_a = -1
        self._valido_al = 0.0
        self._confine_prima = -math.inf
        self._confine_dopo = math.inf
        self._in_corso: float | None = None  # l'istante dell'evento che si sta scrivendo
        self._riesame_di = -1  # solo nello scenario di un riesame: l'evento della decisione che si riesamina
        self._da_parte: set[int] = set()  # solo nello scenario: i fatti degli eventi lasciati fuori, che non contano
        self._con_nascita = False  # rifacendo una decisione: accanto a ogni versione si dice anche l'evento di nascita
        # I controlli di ammissione valgono per le scritture nuove: una data che non è di calendario, un ruolo che
        # manca o che è ripetuto in un circuito raggruppato. Rifacendo una decisione già presa non si applicano: si
        # rilegge con la semantica di quando è stata presa, altrimenti una ricevuta integra non si verificherebbe più.
        self._ammissione = True
        self._calcolo = CALCOLO  # con quale modo si dice di chi è un fatto: quello di oggi, salvo quando si rilegge una decisione di prima
        # La frontiera di questo programma nel segno del giornale: la mette chi scrive, prima del primo evento che
        # scrive, e non chi apre. ``_da_dichiarare`` è il segno da scrivere, preparato quando la scrittura comincia.
        self._dichiarato = False
        self._da_dichiarare: dict | None = None
        # Se una scrittura è rimasta a metà sul disco e non si è riusciti a toglierla, la coda del giornale non si sa
        # com'è: questa rete non scrive più e non salva istantanee finché non viene riaperta. Qui c'è il perché.
        self._rotta: str | None = None
        # Chi tiene da parte il numero di un neurone non ancora scritto nel giornale (una domanda preparata) prende una
        # presa su di lui. Dimenticarlo toglie la presa: se il numero rinasce, anche con lo stesso nome, è un altro.
        self._prese: dict[int, int] = {}
        self._serie = 0
        # Quante volte dei neuroni sono stati dimenticati. Chi tiene da parte qualcosa ricavato dal nome di un neurone
        # (il numero che scrive, per esempio) lo butta quando questo conto cambia: il numero può toccare a un altro nome.
        self.dimenticati = 0
        # Le tabelle che il nucleo compilato tiene accanto alla rete (``nucleo.Tabelle``), se è stato usato: sono
        # ricavate dai nomi dei neuroni, e chi dimentica un neurone le deve avvisare.
        self._tabelle_del_nucleo = None
        # Se la rete ha regole o circuiti, e quali tipi di fatto li definiscono: lo guarda ogni scrittura, e cercarli
        # per nome ogni volta costa. Si tiene da parte e si butta quando nasce o si dimentica un neurone con uno di
        # quei nomi. None: da ricalcolare.
        self._definizioni: tuple[bool, frozenset] | None = None
        # Gli eventi scritti a lotti che non sono ancora nel giornale (vedi ``a_lotti``).
        self._in_attesa = bytearray()
        # Una rete aperta in sola lettura non scrive niente sul disco, nemmeno per mettere ordine: non taglia la coda
        # di un giornale che non si rilegge fino in fondo, perché potrebbe essere un record che chi scrive sta ancora
        # scrivendo. Quel taglio lo fa solo chi apre per scrivere, che è uno solo.
        self._sola_lettura = sola_lettura
        # Mentre una scrittura viene decisa i fatti proposti sono già nella rete, e possono ancora tornare indietro: in
        # quel momento non si salva un'istantanea e non si comincia un'altra scrittura.
        self._decidendo = False
        # La rete è fuori uso (``_rotta``) e in più quello che ha in memoria non corrisponde al giornale: un evento è
        # nel giornale e non qui, o è qui e non nel giornale. Allora non va nemmeno letta: va riaperta.
        self._memoria_guasta = False
        # Le tracce di un evento mentre si fa, per poterlo togliere senza lasciare segni. Un posto rimasto vuoto in una
        # linea o in una somma non si toglie subito ma alla chiusura dell'evento: se nello stesso evento torna a
        # riempirsi (una sostituzione, o una scrittura che torna indietro) l'entità resta dov'era, e le righe di una
        # domanda escono nello stesso ordine. Vale uguale per chi scrive e per chi rilegge il giornale.
        self._spenti: list[tuple[dict, int]] = []
        self._azzerati: list[tuple[int, int]] = []
        self._evento_aperto = False  # un evento si sta facendo o rileggendo: i posti vuoti li toglie chi lo chiude
        # Solo mentre una scrittura viene decisa: le linee nate vuote e le forme cambiate, da rimettere com'erano se
        # la scrittura non avviene. Fuori da una scrittura sono None e nessuno le segna.
        self._linee_nate: list[tuple[dict, int]] | None = None
        self._forme_cambiate: list[tuple[int, object]] | None = None
        # Una rete chiusa non ha più il suo giornale: non scrive e non salva istantanee. Si può ancora leggere.
        self._chiusa = False
        # Il segno è stato messo al suo posto ma la cartella non si è sincronizzata: il suo nome può non essere ancora
        # sul disco. Prima della prossima scrittura la sincronizzazione si riprova.
        self._nome_del_segno_incerto = False
        self.istantanea_scartata = False  # l'istantanea trovata non provava di contenere solo neuroni scritti: non è stata usata
        self._istanti: dict[int, float | None] = {}
        self._non_numerici: list[str] = []
        # con ``decisioni`` ogni evento scritto da racconta porta la sua decisione nel giornale, anche se chi chiama
        # non mette una nota: serve a una memoria in cui ogni decisione deve essere verificabile
        self.decisioni = decisioni
        self._sync = "frase"
        self._fd = -1
        self._scritti = 0  # byte del giornale già riletti o scritti
        # L'impronta di quei byte, come questa rete li ha riletti o scritti, e non come il file è adesso: è quella che
        # l'istantanea certifica. Un byte del giornale che si rovina dopo essere stato letto non deve finire certificato.
        self._impronta = hashlib.sha256()
        self.cartella = cartella
        if cartella and not decisioni and os.path.lexists(os.path.join(cartella, "formato")):
            # Il contratto è del giornale: se porta il segno, da lì in poi ogni evento deve avere la sua decisione,
            # anche quando chi lo apre questa volta non lo dice. Altrimenti resterebbe un evento senza ricevuta.
            self.decisioni = True
        self.sync = sync  # dopo la cartella: scrivere a lotti dipende anche da che giornale è
        if cartella:
            self._apri()

    # --- neuroni -------------------------------------------------------------

    def nuovo(self, nome: str) -> int:
        """Fa nascere un neurone con quel nome, anche se uno con lo stesso nome c'è già."""
        if type(nome) is not str:  # un'enumerazione di testo: in memoria resta il testo che finisce nel giornale
            nome = testo_semplice(nome)
        return self._nasce(nome, piana(nome))

    def _nasce(self, nome: str, chiave: str) -> int:
        """Il neurone nasce. ``chiave`` deve essere ``piana(nome)``: la passa chi l'ha appena calcolata, e nessun altro.
        Con una chiave sbagliata il neurone si troverebbe sotto un altro nome, e il motore non saprebbe che cos'è."""
        nome.encode()  # un nome che il giornale non sa scrivere (un surrogato spaiato) non nasce: bloccherebbe ogni scrittura dopo
        i = len(self.nomi)
        self.nomi.append(nome)
        omonimi = self.per_nome.get(chiave)
        if omonimi is None:
            self.per_nome[chiave] = [i]
        else:
            omonimi.append(i)
        if chiave in _NOMI_SPECIALI:  # quasi nessun nome lo è: una sola domanda, invece di sei
            self._speciale(chiave, i)
        return i

    def _speciale(self, chiave: str, i: int) -> None:
        if chiave in _NOMI_DELLE_DEFINIZIONI:
            self._definizioni = None
        # Tre nomi hanno un compito nel motore. «quando»: i fatti che lo usano come ruolo vengono
        # collegati anche ai rilevatori di coppia (partecipante, giorno).
        if chiave == "quando":
            self.ruoli_coppia.add(i)
        elif chiave == "regola" and self._t_regola < 0:
            self._t_regola = i
        elif chiave == "segnale" and self._t_segnale < 0:
            self._t_segnale = i
        elif chiave == "vale da" and self._r_da < 0:
            self._r_da = i
        elif chiave == "vale fino a" and self._r_a < 0:
            self._r_a = i

    def dimentica(self, da: int) -> None:
        """Toglie i neuroni nati da quel numero in poi, come se non fossero mai nati.

        Si può fare solo finché nessun evento li ha scritti nel giornale e nessun fatto li usa: serve a
        chi prova una scrittura, o se la vede rifiutare, e non vuole lasciare nomi nella rete.
        """
        if da < self._confermati:
            raise ValueError("questi neuroni sono già nel giornale: non si possono dimenticare")
        if self._tabelle_del_nucleo is not None:
            self._tabelle_del_nucleo.dimentica(da)
        self._definizioni = None
        if da < len(self.nomi):
            self.dimenticati += 1
        if self._prese:  # chi teneva da parte uno di questi numeri non lo ritrova più: nemmeno se rinasce con lo stesso nome
            for i in [i for i in self._prese if i >= da]:
                del self._prese[i]
        for i in range(len(self.nomi) - 1, da - 1, -1):
            chiave = piana(self.nomi[i])
            omonimi = self.per_nome[chiave]
            omonimi.remove(i)
            if not omonimi:
                del self.per_nome[chiave]
            self.ruoli_coppia.discard(i)
            if self._t_regola == i:
                self._t_regola = -1
            if self._t_segnale == i:
                self._t_segnale = -1
            if self._r_da == i:
                self._r_da = -1
            if self._r_a == i:
                self._r_a = -1
            self._istanti.pop(i, None)
        del self.nomi[da:]
        # le tracce lasciate da fatti provati e poi annullati: la forma di un tipo nuovo, le linee vuote
        for tipo in [t for t in self.forma if t >= da]:
            del self.forma[tipo]
        for linee in (self.vivi, self.morti):
            for linea in [x for x in linee if (x >> BIT) >= da or (x & ((1 << BIT) - 1)) >= da]:
                if linee[linea]:
                    raise ValueError("un fatto usa ancora questi neuroni")
                del linee[linea]
        if self._ordinate:  # un numero dimenticato può toccare a un altro nome: l'ordine che lo aveva visto non vale più
            self._ordinate = {linea: o for linea, o in self._ordinate.items() if o.massimo < da}

    @property
    def sync(self) -> str:
        """Come gli eventi arrivano sul disco: «frase», «os» oppure «lotti» (vedi in testa al modulo)."""
        return self._sync

    @sync.setter
    def sync(self, modo: str) -> None:
        if modo not in ("frase", "os", "lotti"):
            raise ValueError(f"«{modo}» non è un modo di sincronizzare: frase, os oppure lotti")
        if modo == "lotti":
            self._ammette_i_lotti()
        elif self._sync == "lotti":
            # Si esce dai lotti: quello che è in attesa arriva nel giornale adesso, prima di qualunque evento scritto
            # nel modo nuovo. Altrimenti un evento successivo lo precederebbe sul disco, e il giornale racconterebbe
            # un'altra storia. Se non ci arriva, il modo non cambia e la rete si ferma.
            self.scarica()
            if modo == "frase":
                self._conferma_sul_disco()
        self._sync = modo

    def _conferma_sul_disco(self) -> None:
        """Uscendo dai lotti verso un modo in cui ogni evento è confermato: quello che è stato scritto a gruppi, anche
        prima e senza niente in attesa adesso, deve essere sul disco. Se non si riesce a saperlo la rete si ferma."""
        if self._fd < 0 or self._rotta:
            return
        try:
            os.fdatasync(self._fd)
        except OSError as e:
            self._rotta = f"gli eventi scritti a lotti non si sono potuti confermare sul disco ({e.strerror or e})"
            raise

    def _ammette_i_lotti(self) -> None:
        """Scrivere a lotti lascia la memoria avanti rispetto al disco: non si fa dove ogni scrittura va confermata.
        Vale per la rete che registra le decisioni e per il giornale che le registra, anche se questa volta è stato
        aperto senza dirlo: il contratto è del giornale, e chi lo riapre lo trova nel suo segno."""
        if self.decisioni or (self.cartella and os.path.lexists(os.path.join(self.cartella, "formato"))):
            raise ValueError("un giornale che registra le decisioni non si scrive a lotti: ogni evento va confermato sul disco")

    @property
    def fuori_uso(self) -> str | None:
        """Perché questa rete non scrive più finché non viene riaperta, oppure None. Quando succede, quello che ha in
        memoria può non essere quello che c'è nel giornale: chi la tiene aperta la deve riaprire."""
        return self._rotta

    @property
    def memoria_guasta(self) -> bool:
        """La rete è fuori uso e quello che ha in memoria non è quello che c'è nel giornale: le sue risposte non valgono."""
        return self._memoria_guasta

    def presa(self, i: int) -> int | None:
        """Quello che serve a chi tiene da parte il numero di un neurone per sapere, più tardi, se è ancora lui. None
        se è già nel giornale: il suo numero non toccherà mai a un altro. Altrimenti un contrassegno che vale finché il
        neurone non viene dimenticato."""
        if i < self._confermati:
            return None
        presa = self._prese.get(i)
        if presa is None:
            self._serie += 1
            presa = self._prese[i] = self._serie
        return presa

    def tiene(self, i: int, presa: int | None) -> bool:
        """Il neurone ``i`` è ancora quello su cui era stata presa ``presa``?"""
        return presa is None or self._prese.get(i) == presa

    def n(self, nome: str) -> int:
        """Il neurone con questo nome; lo crea se non c'è. Se il nome è ambiguo solleva Ambiguo."""
        chiave = piana(nome)
        trovati = self.per_nome.get(chiave)
        if not trovati:
            return self._nasce(testo_semplice(nome), chiave)
        if len(trovati) > 1:
            raise Ambiguo([(i, self.nomi[i], self.intorno(i)) for i in trovati])
        return trovati[0]

    def trova(self, nome: str) -> list[int]:
        return list(self.per_nome.get(piana(nome), ()))

    # --- fatti ---------------------------------------------------------------

    def leggi(self, f: int, ruolo: int) -> int:
        """Il partecipante del fatto con quel ruolo, -1 se non c'è."""
        i = self.f_inizio[f]
        s_ruolo = self.s_ruolo
        # i primi due posti senza ciclo: quasi sempre il ruolo cercato è lì
        if s_ruolo[i] == ruolo:
            return self.s_a[i]
        n = self.f_n[f]
        if n > 1:
            if s_ruolo[i + 1] == ruolo:
                return self.s_a[i + 1]
            for j in range(i + 2, i + n):
                if s_ruolo[j] == ruolo:
                    return self.s_a[j]
        return -1

    def legami(self, f: int) -> list[tuple[int, int]]:
        i = self.f_inizio[f]
        return [(self.s_ruolo[j], self.s_a[j]) for j in range(i, i + self.f_n[f])]

    def _fatto(self, tipo: int, legami, evento: int) -> int:
        f = len(self.f_tipo)
        self.f_tipo.append(tipo)
        self.f_inizio.append(len(self.s_a))
        self.f_n.append(len(legami))
        self.f_nato.append(evento)
        self.f_morto.append(-1)
        ruoli = tuple([r for r, _ in legami])
        nota = self.forma.get(tipo, 0)
        if nota == 0:
            self.forma[tipo] = ruoli
            if self._forme_cambiate is not None:
                self._forme_cambiate.append((tipo, 0))
        elif nota is not None and nota != ruoli:
            self.forma[tipo] = None
            if self._forme_cambiate is not None:
                self._forme_cambiate.append((tipo, nota))
        vivi = self.vivi
        valore = -1
        ruolo_di, verso, coppia = self.s_ruolo.append, self.s_a.append, self.ruoli_coppia
        for r, n in legami:
            ruolo_di(r)
            verso(n)
            # il passo che ogni fatto ripete per ogni suo legame: accendere la linea
            linea = (r << BIT) | tipo
            d = vivi.get(linea)
            if d is None:
                d = vivi[linea] = {}
                if self._linee_nate is not None:
                    self._linee_nate.append((vivi, linea))
            v = d.get(n)
            if v is None:
                d[n] = f
            elif type(v) is int:
                if v != f:
                    d[n] = [v, f]
            elif not v:  # il posto lasciato vuoto da un fatto ritirato in questo stesso evento: lo riprende, dov'era
                d[n] = f
            elif v[-1] != f:
                v.append(f)
            if r in coppia:
                valore = n
        if valore >= 0:
            c = self.coppie.get(valore)
            if c is None:
                c = self.coppie[valore] = {}
                if self._linee_nate is not None:
                    self._linee_nate.append((self.coppie, valore))
            visti = set()
            for r, n in legami:
                if n != valore and n not in visti:
                    visti.add(n)
                    lista = c.get(n)
                    if lista is None:
                        c[n] = f
                    elif type(lista) is int:
                        c[n] = [lista, f]
                    else:
                        lista.append(f)
        if self._ordinate:  # le linee tenute in ordine di grandezza vedono i neuroni che ricevono
            for r, n in legami:
                ordinata = self._ordinate.get((r << BIT) | tipo)
                if ordinata is not None:
                    ordinata.vede(n, self.nomi[n])
        if self._termini and tipo in self._termini:
            self._contributo(f, 1, nuovo=True)
        return f

    def ordinata(self, tipo: int, ruolo: int) -> Ordinata:
        """I neuroni della linea (tipo, ruolo) in ordine di grandezza. La prima volta si costruisce, poi resta
        aggiornata a ogni scrittura."""
        linea = (ruolo << BIT) | tipo
        ordinata = self._ordinate.get(linea)
        if ordinata is None:
            # anche i neuroni che in questo momento hanno solo fatti ritirati: un fatto messo da parte per il tempo di
            # una lettura torna in vigore senza passare da una scrittura, e l'ordine non deve averlo perso
            ordinata = self._ordinate[linea] = Ordinata(self.nomi, self.vivi.get(linea, {}).keys() | self.morti.get(linea, {}).keys())
        return ordinata

    def validita(self, f: int) -> tuple[float, float] | None:
        """Da quando a quando vale il fatto, fine esclusa. Se non lo dichiara vale da quando è stato scritto nel
        giornale, senza scadenza; un fatto ancora in valutazione vale dall'istante che si sta valutando. None se la
        dichiarazione non è una data intera, o se finisce prima di cominciare."""
        da = a = None
        if self._r_da >= 0 or self._r_a >= 0:
            for ruolo in (self._r_da, self._r_a):
                n = self.leggi(f, ruolo) if ruolo >= 0 else -1
                if n < 0:
                    continue
                try:
                    x = self._istanti[n]
                except KeyError:
                    x = self._istanti[n] = istante(self.nomi[n])
                if x is None:
                    return None
                if ruolo == self._r_da:
                    da = x
                else:
                    a = x
        if da is None:
            e = self.f_nato[f]
            da = self.e_scritto[e] if e < len(self.e_scritto) else self._in_corso
            if 0 <= self._riesame_di < e < len(self.e_scritto) and da <= self.e_scritto[self._riesame_di]:
                # Nel riesame di una decisione, un fatto senza periodo scritto da un evento successivo vale da dopo
                # di lei anche se l'orologio segnava lo stesso istante: a parità di istante decide l'ordine degli eventi.
                da = math.nextafter(self.e_scritto[self._riesame_di], math.inf)
        if a is None:
            a = math.inf
        return (da, a) if da < a else None

    def _contributi(self, f: int, nuovo: bool = False) -> list[tuple[int, int, int]]:
        """Quanto il fatto porta a ogni circuito che lo osserva, all'istante delle somme: (circuito, entità, valore).
        Il numero e il periodo si controllano sempre, anche per un fatto che adesso non vale: il periodo decide se
        sommare, non se il dato è buono. Quello che non va finisce in ``_non_numerici``."""
        periodo = self.validita(f)
        if periodo is None:
            self._non_numerici.append(f"non è una data intera, o finisce prima di cominciare: il periodo di validità in {self.in_parole(f)}")
        else:
            al = self._valido_al
            for confine in periodo:  # una decorrenza o una scadenza: oltre quell'istante le somme vanno rifatte
                if confine <= al:
                    if confine > self._confine_prima:
                        self._confine_prima = confine
                elif confine < self._confine_dopo:
                    self._confine_dopo = confine
        contributi = []
        ammissione = nuovo and self._ammissione  # un fatto che nasce adesso, in una scrittura vera
        inizio, s_ruolo, s_a = self.f_inizio[f], self.s_ruolo, self.s_a
        for c, lega, somma, peso, taglio in self._termini[self.f_tipo[f]]:
            # Di chi è il fatto. Un circuito ordinario lo dà a ogni entità che il fatto nomina nel ruolo che lega, una
            # volta ciascuna: un accredito intestato a due clienti vale per tutti e due, e uno che nomina due volte lo
            # stesso cliente vale una volta. Un circuito raggruppato lo dà a un mese solo, il primo. Rileggendo una
            # decisione di prima si usa il modo di allora (``_calcolo``).
            entita = [s_a[j] for j in range(inizio, inizio + self.f_n[f]) if s_ruolo[j] == lega]
            if not taglio and len(entita) > 1:
                modo = CALCOLI[self._calcolo][1]
                entita = entita[:1] if modo == "prima" else entita if modo == "occorrenze" else list(dict.fromkeys(entita))
            if taglio:
                if ammissione:
                    # Un circuito raggruppato per mese deve sapere a quale mese dare ogni fatto che osserva: uno, e di
                    # calendario. Se non lo sa la scrittura non entra, invece di lasciare il fatto fuori dalla somma.
                    perche = self._non_raggruppabile(f, lega, entita[0] if entita else -1)
                    if perche:
                        # Si segna, e si prosegue come per un fatto di prima: quello che il fatto aggiunge adesso è
                        # quello che l'annullamento toglierà tra un momento. Saltarlo qui e toglierlo là lascerebbe un
                        # mese a meno.
                        self._non_numerici.append(f"{perche}: il circuito «{self.nomi[c]}» raggruppa per {'mese' if taglio == 7 else 'anno'}, in {self.in_parole(f)}")
                entita = [self._periodo(x, taglio) for x in entita[:1]]
            if not entita:
                continue  # il fatto non dice di chi è: non contribuisce a nessuna somma
            n = self.leggi(f, somma)
            v = in_unita(self.nomi[n]) if n >= 0 else None
            if v is None:
                self._non_numerici.append(f"non è un numero: «{self.nomi[n] if n >= 0 else ''}» in {self.in_parole(f)}")
            elif periodo is not None and periodo[0] <= self._valido_al < periodo[1]:
                contributi.extend((c, x, peso * v) for x in entita)
        return contributi

    def _non_raggruppabile(self, f: int, lega: int, x: int) -> str | None:
        """Perché un circuito raggruppato non sa a quale mese dare il fatto, oppure None se lo sa."""
        if x < 0:
            return f"manca «{self.nomi[lega]}»"
        if sum(1 for r, _ in self.legami(f) if r == lega) > 1:
            return f"«{self.nomi[lega]}» c'è più di una volta"
        if not data_di_calendario(self.nomi[x]):
            return f"«{self.nomi[x]}» non è una data scritta come 2026-11-22"
        return None

    def _periodo(self, x: int, taglio: int) -> int:
        """Il neurone del mese, o dell'anno, di una data: quello che ha per nome l'inizio del nome della data. È lo
        stesso taglio che fa la lingua con ``per mese(quando)``, così la somma tenuta pronta e quella calcolata
        parlano degli stessi mesi. Se quel neurone non c'è ancora nasce qui, dentro la scrittura che lo usa per
        prima, e finisce nel giornale con lei: chi rilegge il giornale lo ritrova con lo stesso numero."""
        nome = self.nomi[x][:taglio]
        trovati = self.per_nome.get(piana(nome))
        return trovati[0] if trovati else self.nuovo(nome)

    def somme_pronte(self, circuito: int, al: float | None = None) -> dict[int, int]:
        """Tutte le somme che il circuito tiene pronte, per entità, in milionesimi, per i fatti che valgono all'istante
        ``al`` (adesso, se non è detto). Chi le legge non ricalcola niente."""
        self._porta_a(time.time() if al is None else al)
        return self.valori.get(circuito, {})

    def _contributo(self, f: int, segno: int, nuovo: bool = False) -> None:
        """Aggiunge o toglie il fatto dalle somme dei circuiti che lo osservano, se vale all'istante delle somme."""
        for c, x, v in self._contributi(f, nuovo):
            v = segno * v
            per_entita, presenti = self.valori[c], self._presenze[c]
            quanti = presenti.get(x, 0) + segno
            if quanti:
                presenti[x] = quanti
                per_entita[x] = per_entita.get(x, 0) + v
            else:
                # Era l'ultimo fatto di quell'entità: esce dal circuito. Ma non subito: il suo posto resta, a zero,
                # fino alla chiusura dell'evento (``_chiudi_evento``). Se nello stesso evento un altro fatto la riporta
                # dentro, o se la scrittura torna indietro, è ancora dov'era.
                presenti[x] = 0
                per_entita[x] = 0
                self._azzerati.append((c, x))
            if self._delta is not None:
                self._delta[c, x] = self._delta.get((c, x), 0) + v

    def vale(self, f: int, al: float) -> bool:
        """Se il fatto vale a quell'istante."""
        periodo = self.validita(f)
        return periodo is not None and periodo[0] <= al < periodo[1]

    def fuori_validita(self, al: float) -> list[int]:
        """I fatti in vigore che a quell'istante non valgono: non ancora cominciati, o scaduti. Quelli che dichiarano un
        periodo li trova dalle linee dei due ruoli, data per data; quelli che non lo dichiarano valgono da quando sono
        stati scritti, e sono in fondo: gli eventi scritti dopo quell'istante. Non passa tutti i fatti."""
        fuori = set()
        dopo = bisect.bisect_right(self.e_scritto, al)  # il primo evento scritto dopo l'istante: di solito non ce n'è
        if dopo < len(self.e_scritto):
            for f in range(bisect.bisect_left(self.f_nato, dopo), len(self.f_tipo)):
                if self.f_morto[f] < 0 and not self.vale(f, al):
                    fuori.add(f)
        for ruolo in (self._r_da, self._r_a):
            if ruolo < 0:
                continue
            for linea, per_neurone in self.vivi.items():
                if linea >> BIT != ruolo:
                    continue
                for n, v in per_neurone.items():
                    try:
                        x = self._istanti[n]
                    except KeyError:
                        x = self._istanti[n] = istante(self.nomi[n])
                    if x is None or (x > al if ruolo == self._r_da else x <= al):
                        fuori.update((v,) if type(v) is int else v)
        return sorted(fuori)

    def _riaccendi(self, linea: int, n: int, f: int) -> None:
        """Rimette in vigore un fatto ritirato che torna indietro: nel posto che la sua entità ha tenuto sulla linea, e
        tra i suoi fatti dov'era, cioè in ordine di nascita."""
        d = self.vivi[linea]
        v = d.get(n)
        if v is None or (type(v) is not int and not v):
            d[n] = f
        elif type(v) is int:
            if v != f:
                d[n] = [f, v] if f < v else [v, f]
        elif f not in v:
            bisect.insort(v, f)

    def _spegni(self, linea: int, n: int, f: int) -> None:
        d = self.vivi[linea]
        v = d.get(n)
        if v is None:
            return
        if type(v) is int:
            if v == f:
                # L'entità non ha più fatti in vigore su questa linea. Il suo posto non si toglie subito: resta vuoto
                # fino alla chiusura dell'evento. Chi legge una linea trova un insieme vuoto di fatti, che è la verità.
                d[n] = []
                self._spenti.append((d, n))
        elif v:
            if v[-1] == f:
                v.pop()
            elif f in v:
                v.remove(f)
            if len(v) == 1:
                d[n] = v[0]

    def _chiudi_evento(self) -> None:
        """Toglie i posti rimasti vuoti nelle linee e nelle somme. Si chiama alla fine di ogni evento: quando è scritto,
        quando è riletto dal giornale, e quando una scrittura è tornata indietro."""
        if self._spenti:
            for d, n in self._spenti:
                v = d.get(n)
                if v is not None and type(v) is not int and not v:
                    del d[n]
            self._spenti = []
        if self._azzerati:
            for c, x in self._azzerati:
                presenti = self._presenze.get(c)
                if presenti is not None and presenti.get(x) == 0:
                    del presenti[x]
                    self.valori[c].pop(x, None)
            self._azzerati = []

    def _ritira(self, f: int, evento: int) -> None:
        self.f_morto[f] = evento
        tipo = self.f_tipo[f]
        if self._termini and tipo in self._termini:
            self._contributo(f, -1)
        i = self.f_inizio[f]
        for j in range(i, i + self.f_n[f]):
            linea, n = (self.s_ruolo[j] << BIT) | tipo, self.s_a[j]
            self._spegni(linea, n, f)
            m = self.morti.get(linea)
            if m is None:
                m = self.morti[linea] = {}
                if self._linee_nate is not None:
                    self._linee_nate.append((self.morti, linea))
            lista = m.get(n)
            if lista is None:
                m[n] = [f]
            else:
                lista.append(f)
        if not self._evento_aperto:  # un ritiro da solo, fuori da un evento: si chiude qui
            self._chiudi_evento()

    def _ordine_delle_linee(self, fatti) -> list[tuple[dict, list[int]]]:
        """L'ordine in cui stanno adesso le entità sulle linee da cui, tolti quei fatti, qualcuna sparirebbe. Serve a
        chi mette da parte dei fatti per il tempo di una lettura e poi li rimette: con ``_riordina`` ogni entità torna
        al suo posto. Costa quanto la linea, e solo per le linee toccate."""
        via = set(fatti)
        ordini: dict[int, tuple[dict, list[int]]] = {}
        for f in via:
            tipo = self.f_tipo[f]
            for r, n in self.legami(f):
                d = self.vivi.get((r << BIT) | tipo)
                if d is None or id(d) in ordini:
                    continue
                v = d.get(n)
                if v is not None and (type(v) is int or all(x in via for x in v)):
                    ordini[id(d)] = (d, list(d))
        return list(ordini.values())

    def _metti_da_parte(self, fatti) -> tuple:
        """Toglie dei fatti dalla rete per il tempo di una lettura: quelli che adesso non valgono. Restituisce quello
        che serve a ``_rimetti`` per riportare la rete esattamente com'era: una lettura non lascia tracce."""
        da_parte = (list(fatti), self._ordine_delle_linee(fatti), (self._linee_nate, self._forme_cambiate))
        self._linee_nate, self._forme_cambiate = [], []  # le linee che nascono vuote tra i ritirati spariscono poi
        evento = len(self.e_frase)
        for f in da_parte[0]:
            self._ritira(f, evento)
        return da_parte

    def _rimetti(self, da_parte: tuple) -> None:
        fatti, ordini, tracce = da_parte
        try:
            self._annulla([], fatti)
            self._riordina(ordini)
        finally:
            self._linee_nate, self._forme_cambiate = tracce
            self._non_numerici = []  # quello che i circuiti hanno annotato mentre i fatti uscivano e rientravano non è di nessuna scrittura

    @staticmethod
    def _riordina(ordini) -> None:
        """Rimette le linee nell'ordine preso con ``_ordine_delle_linee``. La linea resta lo stesso oggetto: chi la
        tiene da parte, come una domanda preparata, continua a vedere quella."""
        for d, ordine in ordini:
            if list(d) != ordine and len(d) == len(ordine) and all(n in d for n in ordine):
                coppie = [(n, d[n]) for n in ordine]
                d.clear()
                d.update(coppie)

    def _annulla(self, creati: list[int], ritirati: list[int]) -> None:
        """Riporta la rete a prima della scrittura: toglie i fatti creati, rimette in vigore i ritirati, e non lascia
        tracce: ogni entità torna al suo posto sulle linee e nelle somme, le linee nate vuote spariscono, la forma dei
        tipi torna quella di prima. Chi poi rilegge il giornale trova la stessa rete, nello stesso ordine."""
        for f in reversed(creati):
            tipo = self.f_tipo[f]
            if self._termini and tipo in self._termini:
                self._contributo(f, -1)
            valore = -1
            lg = self.legami(f)
            for r, n in lg:
                self._spegni((r << BIT) | tipo, n, f)
                if r in self.ruoli_coppia:
                    valore = n
            if valore >= 0:
                c = self.coppie[valore]
                for n in {n for _, n in lg if n != valore}:
                    x = c[n]
                    if type(x) is int:
                        del c[n]
                    else:
                        x.pop()
                        if len(x) == 1:
                            c[n] = x[0]
            del self.s_ruolo[self.f_inizio[f]:]
            del self.s_a[self.f_inizio[f]:]
            for colonna in (self.f_tipo, self.f_inizio, self.f_n, self.f_nato, self.f_morto):
                colonna.pop()
        for f in reversed(ritirati):
            self.f_morto[f] = -1
            tipo = self.f_tipo[f]
            if self._termini and tipo in self._termini:
                self._contributo(f, 1)
            for r, n in self.legami(f):
                linea = (r << BIT) | tipo
                m = self.morti[linea]
                m[n].pop()
                if not m[n]:
                    del m[n]
                self._riaccendi(linea, n, f)
                if self._ordinate:
                    ordinata = self._ordinate.get(linea)
                    if ordinata is not None:
                        ordinata.vede(n, self.nomi[n])
        self._chiudi_evento()
        if self._forme_cambiate:
            for tipo, prima in reversed(self._forme_cambiate):
                if prima == 0 and type(prima) is int:
                    self.forma.pop(tipo, None)
                else:
                    self.forma[tipo] = prima
            self._forme_cambiate = []
        if self._linee_nate:
            for linee, chiave in reversed(self._linee_nate):
                if chiave in linee and not linee[chiave]:
                    del linee[chiave]
            self._linee_nate = []

    # --- domande: attivazione e soglia -----------------------------------------

    def scatta(self, tipo: int, indizi):
        """I fatti in vigore di quel tipo che ricevono tutti gli indizi (ruolo, neurone).

        La soglia è il numero di indizi. Si parte dall'indizio che accende meno fatti e per ognuno si
        guarda sui suoi dendriti se riceve anche gli altri: è la somma degli ingressi confrontata con la
        soglia, calcolata solo dove può riuscire.
        """
        vivi = self.vivi
        if len(indizi) == 1:
            r, n = indizi[0]
            d = vivi.get((r << BIT) | tipo)
            v = d.get(n) if d is not None else None
            if v is None:
                return ()
            return (v,) if type(v) is int else v
        linee = []
        for r, n in indizi:
            d = vivi.get((r << BIT) | tipo)
            v = d.get(n) if d is not None else None
            if v is None:
                return ()
            linee.append((1 if type(v) is int else len(v), r, n, v))
        linee.sort(key=lambda x: x[0])
        primo = linee[0][3]
        resto = [(r, n) for _, r, n, _ in linee[1:]]
        riceve = self.riceve
        out = []
        for f in (primo,) if type(primo) is int else primo:
            for r, n in resto:
                if not riceve(f, r, n):
                    break
            else:
                out.append(f)
        return out

    def riceve(self, f: int, ruolo: int, n: int) -> bool:
        """Vero se il fatto ha una sinapsi con quel ruolo verso quel neurone."""
        i = self.f_inizio[f]
        s_ruolo, s_a = self.s_ruolo, self.s_a
        for j in range(i, i + self.f_n[f]):
            if s_a[j] == n and s_ruolo[j] == ruolo:
                return True
        return False

    def rispondi(self, tipo: int, indizi, chiesto: int) -> list[int]:
        """Accende gli indizi, fa scattare i fatti e segue il ruolo chiesto."""
        leggi = self.leggi
        return [leggi(f, chiesto) for f in self.scatta(tipo, indizi)]

    def percorso(self, tipo: int, indizio: int, chiesto: int) -> list:
        """Un percorso preparato: «in fatti di questo tipo, entra dal ruolo dell'indizio ed esci dal ruolo
        chiesto». Come una domanda preparata: si prende una volta e si riusa."""
        linea = (indizio << BIT) | tipo
        vivi = self.vivi.get(linea)
        if vivi is None:
            vivi = self.vivi[linea] = {}
        morti = self.morti.get(linea)
        if morti is None:
            morti = self.morti[linea] = {}
        return [vivi, chiesto, tipo, -1, morti]

    def segui(self, p: list, n: int) -> list[int]:
        """Domanda con un solo indizio: accende n all'ingresso del percorso e legge l'uscita dei fatti
        che scattano. È ``rispondi`` con soglia 1, senza costruire la lista degli indizi."""
        v = p[0].get(n)
        if v is None:
            return []
        if type(v) is int:
            return [self.leggi(v, p[1])]
        forma = self.forma.get(p[2])
        if forma is not None:  # tutti i fatti del tipo hanno la stessa forma: l'uscita è sempre allo stesso posto
            pos = p[3]
            if pos < 0 and p[1] in forma:
                pos = p[3] = forma.index(p[1])
            if pos >= 0:
                s_a, inizio = self.s_a, self.f_inizio
                return [s_a[inizio[f] + pos] for f in v]
        leggi, chiesto = self.leggi, p[1]
        return [leggi(f, chiesto) for f in v]

    def storia_di(self, p: list, n: int) -> list[tuple[int, int, int]]:
        """Come ``storia``, su un percorso preparato."""
        m = p[4].get(n)
        v = p[0].get(n)
        leggi, nato, morto, chiesto = self.leggi, self.f_nato, self.f_morto, p[1]
        if m is None:
            if v is None:
                return []
            if type(v) is int:
                return [(leggi(v, chiesto), nato[v], -1)]
            fatti = sorted(v)
        else:
            fatti = list(m)
            if v is not None:
                if type(v) is int:
                    fatti.append(v)
                    if v < m[-1]:
                        fatti.sort()
                else:
                    fatti.extend(v)
                    fatti.sort()
        return [(leggi(f, chiesto), nato[f], morto[f]) for f in fatti]

    def storia(self, tipo: int, ruolo: int, n: int, chiesto: int) -> list[tuple[int, int, int]]:
        """Tutti i valori avuti, dal più vecchio: (valore, evento che l'ha scritto, evento che l'ha ritirato)."""
        linea = (ruolo << BIT) | tipo
        d = self.morti.get(linea)
        m = d.get(n) if d is not None else None
        d = self.vivi.get(linea)
        v = d.get(n) if d is not None else None
        leggi, nato, morto = self.leggi, self.f_nato, self.f_morto
        if m is None:
            if v is None:
                return []
            if type(v) is int:
                return [(leggi(v, chiesto), nato[v], -1)]
            fatti = sorted(v)
        else:
            fatti = list(m)
            if v is not None:
                if type(v) is int:
                    fatti.append(v)
                    if v < m[-1]:
                        fatti.sort()
                else:
                    fatti.extend(v)
                    fatti.sort()
        return [(leggi(f, chiesto), nato[f], morto[f]) for f in fatti]

    def insieme(self, n: int, valore: int) -> list[tuple[int, str]]:
        """Gli eventi in cui n compare insieme a quel valore (per esempio un giorno), con la frase."""
        nato = self.f_nato
        frase = self.e_frase
        c = self.coppie.get(valore)
        x = c.get(n) if c is not None else None
        if x is None:
            return []
        if type(x) is int:
            return [(nato[x], frase[nato[x]])]
        return [(nato[f], frase[nato[f]]) for f in x]

    def intorno(self, n: int, limite: int = 6) -> list[str]:
        """I primi fatti in vigore in cui il neurone compare, in parole: serve a distinguere gli omonimi."""
        out = []
        for d in self.vivi.values():
            v = d.get(n)
            if v is None:
                continue
            for f in (v,) if type(v) is int else v:
                out.append(self.in_parole(f))
                if len(out) >= limite:
                    return out
        return out

    def in_parole(self, f: int) -> str:
        parti = ", ".join(f"{self.nomi[r]}: {self.nomi[n]}" for r, n in self.legami(f))
        return f"{self.nomi[self.f_tipo[f]]} ({parti})"

    # --- scritture -------------------------------------------------------------

    def racconta(self, frase: str, origine: str, fatti=(), ritira=(), prova: bool = False, quando: float | None = None,
                 nota: object = None, neuroni_da: int | None = None, nota_del_rifiuto=None, nota_della_scrittura=None,
                 scritto: float | None = None):
        """Una frase, una transazione: o entra tutta o non entra niente.

        ``fatti`` è una lista di (tipo, [(ruolo, neurone), ...]); ``ritira`` una lista di fatti.
        Con ``prova`` non scrive nessun fatto e dice che cosa sarebbe successo.
        Se una regola scatta solleva Vietato e registra il tentativo come evento di rifiuto.
        ``nota`` è un piccolo oggetto che finisce nel giornale nello stesso record dell'evento.
        ``nota_del_rifiuto``, se c'è, è una funzione che riceve le regole scattate e la decisione e restituisce
        la nota da scrivere nel record del rifiuto: così anche un rifiuto porta con sé chiave e ricevuta.
        ``nota_della_scrittura`` fa lo stesso per una scrittura accettata: riceve la decisione e restituisce la
        nota, al posto di ``nota``.

        ``quando`` è l'istante in cui la richiesta è arrivata; ``scritto`` quello in cui viene scritta, che di solito
        fissa il motore e non torna mai indietro. Se sono diversi le regole si valutano due volte: sull'istante di
        arrivo, e sull'istante di scrittura con tutto quello che nel frattempo è stato registrato. La scrittura
        passa solo se passano tutte e due: così una richiesta rimasta in coda non spende una disponibilità che
        un'altra, arrivata dopo e servita prima, ha già preso.

        La **decisione** dice su che cosa si è deciso: la base (l'ultimo evento già nel giornale), l'istante,
        fissato qui una volta sola, e ogni regola consultata, anche quelle che non sono scattate, con le letture
        che ha fatto. In un rifiuto porta anche la proposta, che nel giornale altrimenti non resterebbe.
        ``neuroni_da``: se la scrittura non avviene (prova o rifiuto), i neuroni nati da quel numero in
        poi vengono dimenticati; senza, restano, perché chi ha chiamato ne ha già gli identificativi.
        """
        e = len(self.e_frase)
        if self._decidendo:
            raise RuntimeError("questa rete sta già decidendo una scrittura: un'altra non può cominciare adesso")
        # Prima di toccare qualsiasi cosa: se non si può scrivere, o la richiesta non sta in piedi, non si scrive niente
        # e lo si dice. Comunque finisca qui, i neuroni nati per questa richiesta non restano.
        try:
            if self._sola_lettura and not prova:
                raise RuntimeError("questa rete è aperta in sola lettura: non scrive")
            if self._chiusa and not prova:
                raise RuntimeError("questa rete è stata chiusa: il suo giornale non riceve più niente, quindi non scrive")
            if self._rotta and (not prova or self._memoria_guasta):
                # Se la memoria non corrisponde al giornale nemmeno una prova: deciderebbe su uno stato che non è il suo.
                raise RuntimeError(f"questa rete non scrive{' e non decide' if self._memoria_guasta else ''} più niente "
                                   f"finché non viene riaperta: {self._rotta}")
            if not prova and self._fd >= 0 and self.decisioni:
                # Il segno si rilegge a ogni scrittura, non solo alla prima: può essersi guastato dopo. Se questa
                # richiesta arriva a scrivere un evento, anche un rifiuto, prima dell'evento il segno deve portare
                # la frontiera di questo programma: qui si prepara quello che manca, se manca.
                segno = self.segno(self.cartella)
                if segno is None and self._dichiarato:
                    raise SegnoGuasto("il segno del giornale (il file «formato») non c'è più: questo programma lo aveva scritto. "
                                      "Va ripristinato da una copia; non ne scrivo uno nuovo")
                self._da_dichiarare = self._frontiera(segno, e)
            # L'orologio si guarda una volta sola, e solo se serve: chi passa i due istanti li ha già presi lui, e una
            # seconda lettura potrebbe cadere dall'altra parte di una decorrenza o di una scadenza.
            if quando is None or scritto is None:
                adesso = time.time()
                if quando is None:
                    quando = adesso
                if scritto is None:  # mai prima dell'arrivo, mai prima dell'ultimo evento scritto: l'orologio può anche arretrare
                    scritto = max(adesso, quando, self.e_scritto[-1] if e else quando)
            ritirati = list(ritira)
            fatti = list(fatti)
            if self._ammissione:
                for che, x in (("di arrivo", quando), ("di scrittura", scritto)):
                    if type(x) not in (int, float) or not -1e300 < x < 1e300:  # né infinito né NaN, né un intero che non sta in un numero
                        # un istante che non è un numero finito non si confronta con niente: i fatti scritti lì non
                        # avrebbero un periodo, e il giornale non si rileggerebbe
                        raise ValueError(f"l'istante {che} deve essere un numero finito di secondi: {x!r} non lo è")
                if not prova and e and scritto < self.e_scritto[-1]:
                    # L'istante di scrittura non torna mai indietro: chi rilegge il giornale lo ricava anche dall'evento
                    # di prima, e chi cerca i fatti scritti dopo un istante conta su quell'ordine. Una prova non scrive
                    # niente, e rifare una decisione di allora è una prova agli istanti di allora.
                    raise ValueError("l'istante di scrittura non può venire prima di quello dell'ultimo evento scritto")
                if type(testo_semplice(frase)) is not str or type(testo_semplice(origine)) is not str:
                    raise ValueError("la frase e l'origine di un evento sono testi")
                # Un fatto senza partecipanti non sta su nessuna linea, e chi lo cercasse dopo non saprebbe da dove
                # arrivarci. Uno che non entra intero nelle colonne (troppi legami, o un legame che non è il numero di
                # un neurone) si fermerebbe a metà. Vale per le scritture nuove: un fatto scritto prima si rilegge.
                quanti_neuroni = len(self.nomi)
                for quale, fatto in enumerate(fatti):
                    try:
                        tipo, legami = fatto
                        # I legami si prendono una volta sola e si tengono come coppie: quello che viene controllato qui
                        # è quello che entra nella rete e nel giornale, qualunque cosa passi chi chiama. Un legame è
                        # una coppia se, preso una volta, dà due pezzi: né uno né tre.
                        legami = [(ruolo, neurone) for ruolo, neurone in legami]
                    except (TypeError, ValueError):
                        raise ValueError("un fatto è un tipo con i suoi legami, e ogni legame è una coppia: il ruolo e il neurone") from None
                    if not legami:
                        raise ValueError("un fatto deve avere almeno un legame: così com'è non si può scrivere")
                    if isinstance(tipo, int) and tipo >= MASSIMO_TIPO:
                        raise ValueError(f"un tipo di fatto deve essere tra i primi {MASSIMO_TIPO} neuroni della rete: «{self.nomi[tipo]}» "
                                         f"è il numero {tipo}, e la sua linea si confonderebbe con quella di un altro tipo" if tipo < quanti_neuroni
                                         else f"{tipo!r} non è il numero di un neurone")
                    if len(legami) > MASSIMO_LEGAMI:
                        raise ValueError(f"un fatto può avere al massimo {MASSIMO_LEGAMI} legami: questo ne ha {len(legami)}")
                    for x in (tipo, *[x for coppia in legami for x in coppia]):
                        if not isinstance(x, int) or not 0 <= x < quanti_neuroni:
                            raise ValueError(f"un fatto lega neuroni che ci sono, per numero: {x!r} non è il numero di un neurone")
                    # Nella rete entrano numeri e basta: un numero di un'altra classe (un'enumerazione) vale il suo
                    # valore, come nel giornale. Tenuto com'è finirebbe tra le chiavi delle linee, e l'istantanea non
                    # si salverebbe più.
                    indice = operator.index
                    fatti[quale] = (indice(tipo), [(indice(ruolo), indice(neurone)) for ruolo, neurone in legami])
            # Un fatto si ritira una volta sola. Ritirare di nuovo uno già ritirato non è una scrittura da fare: e se poi
            # la scrittura tornasse indietro, il ritiro di allora verrebbe annullato insieme a quello di adesso.
            gia_ritirati = [f for f in ritirati if not 0 <= f < len(self.f_morto) or self.f_morto[f] >= 0]
            if self._ammissione and (gia_ritirati or len(set(ritirati)) != len(ritirati)):
                raise ValueError("si può ritirare solo un fatto in vigore, e una volta sola: " + (
                    ", ".join(f"fatto:{f}" for f in gia_ritirati) + " non lo è" if gia_ritirati else "lo stesso fatto è indicato due volte"))
        except BaseException:
            if neuroni_da is not None and neuroni_da >= self._confermati:
                self.dimentica(neuroni_da)
            raise
        # Rileggendo una decisione già presa non si rifiuta niente; ma quello che questa chiamata non ha ritirato non è
        # suo da rimettere in vigore.
        da_ritirare = [f for f in dict.fromkeys(ritirati) if f not in gia_ritirati]
        # un testo di una sottoclasse, come un'enumerazione: nel giornale va il suo valore, e in memoria lo stesso
        frase, origine = testo_semplice(frase), testo_semplice(origine)
        non_scritti = self.nomi[self._confermati:]  # neuroni che la base non conosce ancora
        # Da qui in poi i neuroni li fa nascere solo il motore: il mese di un circuito raggruppato. Se la scrittura non
        # avviene non devono restare, anche quando chi chiama tiene i suoi: nessuno ne ha l'identificativo, e un nome
        # rimasto in memoria senza evento sposterebbe i numeri di tutti quelli che nascono dopo.
        da_dimenticare = len(self.nomi) if neuroni_da is None else neuroni_da
        # Da qui la rete viene toccata. Qualunque cosa vada storta prima che l'evento sia nel giornale, anche una che
        # nessuno si aspettava, la rete torna com'era: c'è una strada sola per tornare indietro, ed è qui sotto.
        creati: list[int] = []
        tolti: list[int] = []
        indietro = False  # la rete è già tornata com'era, oppure l'evento è già scritto: non c'è più niente da annullare
        a_meta = False  # un fatto sta nascendo o venendo ritirato: se si ferma lì, non si sa più che cosa annullare
        tracce_di_prima = (self._linee_nate, self._forme_cambiate)
        self._decidendo = self._evento_aperto = True
        try:
            self._linee_nate, self._forme_cambiate = [], []
            self._in_corso = quando
            self._porta_a(quando)  # le somme devono essere quelle dell'istante della decisione, non dell'ultima lettura
            self._delta = {} if self._termini else None
            self._non_numerici = []
            for f in da_ritirare:
                a_meta = True
                self._ritira(f, e)
                tolti.append(f)
                a_meta = False
            for tipo, legami in fatti:
                a_meta = True
                creati.append(self._fatto(tipo, legami, e))
                a_meta = False
            if self._non_numerici:  # un circuito non può sommare quello che non è un numero: non si scrive niente
                raise NonNumerico("un circuito non può sommare questo fatto: " + "; ".join(dict.fromkeys(self._non_numerici)))
            if self._r_da >= 0 or self._r_a >= 0:
                # Un fatto che non dice da quando vale, una volta scritto vale dall'istante di scrittura. Se dichiara una
                # fine che viene prima, il suo periodo sarebbe vuoto e il giornale non si potrebbe più rileggere: si
                # rifiuta adesso, prima di scrivere. Lo stesso per un periodo che non sta in piedi in un fatto qualsiasi.
                self._in_corso = scritto
                vuoti = [self.in_parole(f) for f in creati if self.validita(f) is None]
                self._in_corso = quando
                if vuoti:
                    raise NonNumerico("il periodo di validità non è una data intera, o finisce prima che il fatto venga scritto: " + "; ".join(vuoti))
            nuove = None
            ci_sono, definizioni = self._definizioni or self._tipi_delle_definizioni()
            if self._ammissione and ci_sono:
                if any(self.f_tipo[f] in definizioni for f in creati) or any(self.f_tipo[f] in definizioni for f in ritirati):
                    # La scrittura cambia una regola o un circuito: si controlla che si possano leggere, e che i fatti che
                    # ci sono già abbiano quello che serve ai circuiti nuovi, prima di decidere e prima di scrivere. Vale
                    # anche per una prova: una definizione che non sta in piedi non «si potrebbe scrivere».
                    regole, circuiti, termini = self._leggi_definizioni()
                    di_prima = {(c["id"], c["versione"]) for c in self.circuiti}
                    self._somme(circuiti, self._valido_al, ammissione={c["id"] for c in circuiti if (c["id"], c["versione"]) not in di_prima})
                    nuove = (regole, circuiti, termini)
            consultate = self._valuta(creati, ritirati, e - 1, quando) if self.regole else []
            scattate = [c for c in consultate if c["verdetto"] == "vieta"]
            # ``calcolo``: con quale modo si è detto di chi è ogni fatto. Lo dichiara chi decide, nello stesso record
            # dell'evento: chi rilegge non lo deve ricavare da quello che la decisione ha dato.
            decisione = {"formato": FORMATO, "calcolo": self._calcolo, "base": e - 1, "istante": quando, "regole": consultate}
            if scritto != quando and self.regole and not scattate:
                # La richiesta è arrivata prima di essere servita. Sull'istante di arrivo passa; si rivaluta sull'istante
                # di scrittura, dove vale tutto quello che è stato registrato nel frattempo.
                self._porta_a(scritto)
                if self._termini:
                    self._delta = {}
                    for segno, gruppo in ((1, creati), (-1, ritirati)):
                        for f in gruppo:
                            if self.f_tipo[f] in self._termini:
                                for c, x, v in self._contributi(f):
                                    self._delta[c, x] = self._delta.get((c, x), 0) + segno * v
                alla_scrittura = self._valuta(creati, ritirati, e - 1, scritto)
                senza_istante = lambda regole: [{**r, "letture": [{**y, "valido_al": None} for y in r["letture"]]} for r in regole]
                if senza_istante(alla_scrittura) == senza_istante(consultate):
                    decisione["alla_scrittura"] = {"istante": scritto, "come_all_arrivo": True}
                else:
                    decisione["alla_scrittura"] = {"istante": scritto, "regole": alla_scrittura}
                scattate = [c for c in alla_scrittura if c["verdetto"] == "vieta"]
            if scattate or prova:
                descrizione = [self.in_parole(f) for f in creati]
                self._annulla(creati, tolti)
                self._delta = None
                # I neuroni nominati restano: chi ha chiamato ne ha già gli identificativi. Non hanno fatti,
                # e finiscono nel giornale con il prossimo evento, in ordine, così la rilettura dà gli stessi numeri.
                # Chi invece non li vuole lasciare lo dice con ``neuroni_da``.
                self.dimentica(da_dimenticare)
                indietro = True
                if prova:
                    return {"esito": "prova", "regole_scattate": scattate, "avrebbe_creato": descrizione, "decisione": decisione}
                decisione["proposta"] = {"nuovi": non_scritti, "fatti": [[t, [x for coppia in lg for x in coppia]] for t, lg in fatti],
                                         "ritira": ritirati}
                if nota_del_rifiuto:  # riceve una copia: la decisione è del motore, e chi la riceve non la può cambiare
                    nota = nota_del_rifiuto(json.loads(json.dumps(scattate, ensure_ascii=False)), json.loads(json.dumps(decisione, ensure_ascii=False)))
                else:
                    nota = {"ricevuta": {"esito": "violazione_regola", "evento": f"evento:{e}", "regole": scattate,
                                         "decisione": decisione}} if self.decisioni else None
                # la rete è già tornata com'era: se il rifiuto non arriva sul disco non resta nemmeno in memoria
                self._evento(f"rifiutato: {frase}", origine, "rifiuto", quando, [], [], self._nota_scritta(self._con_la_decisione(nota, decisione)), scritto)
                raise Vietato(scattate)
            if nota_della_scrittura:  # riceve una copia: la decisione è del motore, e chi la riceve non la può cambiare
                nota = nota_della_scrittura(json.loads(json.dumps(decisione, ensure_ascii=False)))
            elif nota is None and self.decisioni:
                nota = {"ricevuta": {"esito": "applicato", "evento": f"evento:{e}", "decisione": decisione}}
            nota = self._nota_scritta(self._con_la_decisione(nota, decisione))
            # Prima il giornale, poi la memoria. Se l'evento non arriva intero sul disco la rete torna com'era prima
            # della richiesta: un fatto che il giornale non ha non deve restare qui, né finire in un'istantanea, né
            # dare il suo numero d'evento a quello che viene scritto dopo.
            self._sul_disco(frase, origine, "scrittura", quando, fatti, ritirati, nota, scritto)
            indietro = True  # l'evento è nel giornale: da qui non si torna più indietro
        except BaseException as errore:
            if not indietro:
                if a_meta:
                    # Si è fermata mentre un fatto nasceva o veniva ritirato: le colonne e le linee possono essere
                    # rimaste a metà, e annullare a occhio le sfalserebbe. Nel giornale non c'è niente: va riaperta.
                    self._rotta = self._rotta or f"una scrittura si è fermata a metà di un fatto, in memoria ({errore!r})"
                    self._memoria_guasta = True
                else:
                    try:
                        self._annulla(creati, tolti)
                        self._delta, self._non_numerici = None, []
                        self.dimentica(da_dimenticare)
                    except BaseException as guasto:  # non si è riusciti nemmeno a tornare indietro: la memoria non corrisponde più al giornale
                        self._rotta = self._rotta or f"una scrittura fallita non si è potuta annullare in memoria ({guasto})"
                        self._memoria_guasta = True
            raise
        finally:
            self._decidendo = self._evento_aperto = False
            self._linee_nate, self._forme_cambiate = tracce_di_prima
        # L'evento è nel giornale. Quello che segue allinea la memoria, e non si può annullare: se una di queste cose non
        # riesce, la memoria resta indietro rispetto al giornale. Non si fa finta di niente e non si toglie il record:
        # la rete si ferma, non conferma e non decide più niente finché non viene riaperta. Chi riapre legge il giornale.
        try:
            self._delta = None
            self._chiudi_evento()  # i posti rimasti vuoti si tolgono adesso, come fa chi rilegge il giornale
            if scritto != quando and self.circuiti:
                # Da scritti, i fatti nuovi che non dichiarano un periodo valgono dall'istante di scrittura: le somme si
                # portano lì, e quell'istante diventa un confine. Chi legge un istante precedente le rifà, senza di loro.
                self._porta_a(scritto)
                if scritto > self._confine_prima:
                    self._confine_prima = scritto
            self._in_memoria(frase, origine, "scrittura", quando, nota, scritto)
            if nuove is not None:
                # Le somme delle definizioni nuove si rifanno adesso che l'evento è scritto. Prima, i fatti di questa
                # scrittura valevano dall'istante di arrivo, come serve per decidere; da scritti valgono dall'istante di
                # scrittura. Tenere le somme di prima vorrebbe dire che un accredito arrivato alle 9 e scritto alle 11
                # copre un ordine arrivato alle 10: chi rilegge il giornale non lo ritroverebbe.
                self.regole, self.circuiti, self._termini = nuove
                self._valido_al = scritto
                self.valori, self._confine_prima, self._confine_dopo, self._presenze = self._somme(self.circuiti, scritto)
        except BaseException as guasto:
            self._rotta = self._rotta or f"un evento è nel giornale ma la memoria non è riuscita ad allinearsi ({guasto})"
            self._memoria_guasta = True
            raise
        return e

    def _tipi_delle_definizioni(self) -> tuple[bool, frozenset]:
        """Se la rete può avere regole o circuiti (c'è un neurone «regola» o «circuito»), e i tipi di fatto che li
        definiscono. Si ricalcola solo quando nasce o si dimentica un neurone con uno di quei nomi."""
        tipi = {nome: self._id(nome) for nome in _NOMI_DELLE_DEFINIZIONI}
        self._definizioni = (self._t_regola >= 0 or tipi["circuito"] >= 0, frozenset(tipi.values()) - {-1})
        return self._definizioni

    def _con_la_decisione(self, nota, decisione: dict):
        """La nota di un evento, con dentro la decisione che il motore ha preso. La nota la prepara chi chiama, ma la
        decisione è del motore fino al giornale.

        In una rete che registra le decisioni la ricevuta porta la decisione del motore, tutta: se chi prepara la nota
        l'ha lasciata fuori, o ne ha scritta un'altra (un'altra base, un altro modo di calcolo), ci va quella vera,
        nella forma in cui si rilegge dal giornale. Una nota senza ricevuta non si scrive: quell'evento nessuno lo
        potrebbe verificare. Dove le decisioni non si registrano la nota è di chi chiama; se porta una decisione, il
        motore ci scrive almeno il modo di calcolo che ha usato."""
        ricevuta = nota.get("ricevuta") if isinstance(nota, dict) else None
        if self.decisioni:
            if not isinstance(ricevuta, dict):
                raise ValueError("questa rete registra le decisioni: la nota di un evento deve portare la sua ricevuta")
            ricevuta["decisione"] = json.loads(json.dumps(decisione, ensure_ascii=False))
        elif isinstance(ricevuta, dict) and isinstance(ricevuta.get("decisione"), dict):
            ricevuta["decisione"]["calcolo"] = self._calcolo
        return nota

    @staticmethod
    def _nota_scritta(nota):
        """La nota come si rilegge dal giornale: una copia, fatta dei soli tipi che il giornale conosce. In memoria (e
        poi nell'istantanea) resta questa e non l'oggetto di chi ha chiamato: quello può essere riusato o cambiato dopo,
        e allora la memoria direbbe una cosa e il giornale un'altra."""
        return None if nota is None else json.loads(_codifica(nota))

    def _evento(self, frase, origine, tipo, quando, fatti, ritirati, nota=None, scritto=None) -> None:
        """Un evento: prima sul disco, tutto intero, e solo dopo in memoria. Se è nel giornale e la memoria non riesce a
        contarlo, la rete si ferma: quello che ha in memoria non è più quello del giornale."""
        self._sul_disco(frase, origine, tipo, quando, fatti, ritirati, nota, scritto)
        try:
            self._in_memoria(frase, origine, tipo, quando, nota, scritto)
        except BaseException as guasto:
            self._rotta = self._rotta or f"un evento è nel giornale ma la memoria non è riuscita ad allinearsi ({guasto})"
            self._memoria_guasta = True
            raise

    def _sul_disco(self, frase, origine, tipo, quando, fatti, ritirati, nota=None, scritto=None) -> None:
        """Mette nel giornale il record dell'evento, tutto o niente, senza toccare la memoria. Se solleva, nel giornale
        l'evento non c'è. Se non riesce nemmeno a togliere un record rimasto a metà, la rete resta fuori uso finché non
        viene riaperta: chi riapre taglia la coda che non si rilegge."""
        if self._fd < 0:
            return
        quando = time.time() if quando is None else quando
        scritto = quando if scritto is None else scritto
        piatti = [[t, [x for coppia in lg for x in coppia]] for t, lg in fatti]
        record = [frase, origine, quando, tipo, self.nomi[self._confermati:], piatti, ritirati]
        if nota is not None or scritto != quando:
            record.append(nota)  # nello stesso record dei fatti: o ci sono tutti e due o nessuno
        if scritto != quando:
            record.append(scritto)  # l'istante di scrittura, quando non coincide con quello di arrivo
        corpo = _codifica(record).encode()
        self._scrivi_record(struct.pack("<II", len(corpo), zlib.crc32(corpo)) + corpo)

    def _scrivi_record(self, dati: bytes) -> None:
        """Mette nel giornale un record già composto, tutto o niente. Vedi ``_sul_disco``."""
        if len(dati) - 8 > MASSIMO_RECORD:
            # In ogni modo di scrivere, anche a lotti: un record più lungo del massimo chi riapre non lo crederebbe, e
            # il giornale non si aprirebbe più. Si rifiuta adesso, prima di confermarlo.
            raise ValueError(f"il record di un evento può essere lungo al massimo {MASSIMO_RECORD >> 20} MB: questo evento va diviso")
        if self._sync == "lotti":  # si mette in fila: arriva nel giornale con gli altri (vedi ``a_lotti``)
            if self.decisioni:  # non ci si arriva dalla porta: ``sync`` lo rifiuta. Ma qui si conferma, e si ricontrolla
                raise ValueError("un giornale che registra le decisioni non si scrive a lotti: ogni evento va confermato sul disco")
            self._in_attesa += dati
            if len(self._in_attesa) >= LOTTO:
                self.scarica()
            return
        self._il_giornale_e_il_mio()  # prima di tutto, anche prima del segno: se il giornale non è più il suo non lascia niente
        if self.decisioni:
            # sul disco prima dell'evento: un evento di oggi non resta mai senza la frontiera che lo dichiara
            if self._da_dichiarare is not None:
                self._scrivi_segno(self._da_dichiarare)
            elif self._nome_del_segno_incerto:  # la volta prima il segno era arrivato, la sincronizzazione della cartella no
                self._sincronizza_la_cartella()
            self._dichiarato, self._da_dichiarare = True, None
        try:
            resto = memoryview(dati)
            while resto:  # una scrittura può fermarsi prima della fine senza essere un errore: si scrive finché c'è da scrivere
                scritti = os.write(self._fd, resto)
                if scritti <= 0:
                    raise OSError(errno.EIO, "il giornale non accetta altri byte")
                resto = resto[scritti:]
            if self._sync == "frase":
                os.fdatasync(self._fd)
        except BaseException:
            try:  # via quello che del record è arrivato nel giornale: non deve restare una coda a metà
                os.ftruncate(self._fd, self._scritti)
                if self._sync == "frase":
                    os.fdatasync(self._fd)
            except OSError as e:
                self._rotta = f"un evento è rimasto scritto a metà nel giornale e non si è riusciti a toglierlo ({e.strerror or e})"
            raise
        self._scritti += len(dati)
        self._impronta.update(dati)

    def _il_giornale_e_il_mio(self) -> None:
        """Prima di aggiungere: il giornale deve finire dove questa rete lo ha lasciato. Se è più lungo o più corto ci
        ha scritto un altro programma (uno di prima, che non prende il giornale per sé), o qualcuno lo ha accorciato:
        quello che questa rete ha in memoria non è più quello del giornale, e un evento scritto adesso avrebbe un
        numero che il giornale non gli dà. Non si scrive: la rete si ferma, e va riaperta."""
        com_e = os.fstat(self._fd)
        if com_e.st_size != self._scritti or com_e.st_nlink == 0:  # più lungo, più corto, oppure cancellato: il file non ha più un nome
            self._rotta = self._rotta or ("il giornale è cambiato sotto questa rete: ci ha scritto un altro programma, è stato accorciato, "
                                          "oppure è stato cancellato")
            self._memoria_guasta = True
            raise RuntimeError(f"questa rete non scrive più finché non viene riaperta: {self._rotta}")

    @contextmanager
    def a_lotti(self):
        """Per caricare molti dati che si possono ricaricare: finché dura, gli eventi arrivano nel giornale a gruppi
        invece che uno alla volta. È più rapido, perché ogni scrittura nel giornale è una chiamata al sistema, ma la
        memoria va avanti rispetto al disco: se il processo si ferma si perdono gli ultimi eventi, e se un gruppo non
        si riesce a scrivere la rete resta fuori uso finché non viene riaperta. Non si usa dove ogni scrittura va
        confermata a chi la chiede: una rete con le decisioni la rifiuta."""
        prima = self._sync
        self.sync = "lotti"  # se questo giornale non si può scrivere a lotti lo dice qui, prima di cominciare
        try:
            yield self
        finally:
            self._sync = prima
            if not self._rotta:  # un gruppo che non è arrivato non si rimanda: la rete è già ferma, e l'errore è già partito
                self.scarica()
                if prima == "frase":
                    self._conferma_sul_disco()

    def scarica(self) -> None:
        """Mette nel giornale gli eventi scritti a lotti che sono ancora in attesa.

        Se il gruppo non arriva intero la rete si ferma: quello che ne è arrivato resta nel giornale fino all'ultimo
        evento intero, il resto è perso, e niente viene mai rimandato. Rimandare il gruppo dall'inizio scriverebbe due
        volte gli eventi già arrivati. Chi riapre trova un prefisso di quello che era stato raccontato: la coda si
        ricarica."""
        if self._rotta:
            raise RuntimeError(f"questa rete non scrive più finché non viene riaperta: {self._rotta}")
        if not self._in_attesa or self._fd < 0:
            return
        try:
            self._ammette_i_lotti()
        except ValueError as vietato:
            # Tra la scelta dei lotti e la pubblicazione il giornale è diventato uno che registra le decisioni (il suo
            # segno è tornato al suo posto). Il gruppo non si pubblica, né adesso né dopo: sono eventi senza decisione.
            # Restano solo in memoria, e la rete si ferma: chi riapre trova il giornale com'era.
            self._rotta = f"un gruppo di eventi scritti a lotti non si può pubblicare: {vietato}"
            self._memoria_guasta = True
            self._in_attesa.clear()
            raise
        self._il_giornale_e_il_mio()
        arrivati = 0
        try:
            resto = memoryview(self._in_attesa)
            try:
                while resto:
                    scritti = os.write(self._fd, resto)
                    if scritti <= 0:
                        raise OSError(errno.EIO, "il giornale non accetta altri byte")
                    arrivati += scritti
                    resto = resto[scritti:]
            finally:
                resto.release()
        except BaseException as e:
            # La memoria ha eventi che il giornale non ha, e non si possono togliere uno per uno: la rete si ferma.
            self._rotta = f"un gruppo di eventi scritti a lotti non è arrivato nel giornale ({e})"
            self._memoria_guasta = True
            # Quanti eventi interi sono arrivati: il giornale si taglia lì, perché non resti un evento a metà.
            interi = 0
            while interi + 8 <= arrivati:
                fine = interi + 8 + struct.unpack_from("<I", self._in_attesa, interi)[0]
                if fine > arrivati:
                    break
                interi = fine
            try:
                os.ftruncate(self._fd, self._scritti + interi)
            except OSError:
                pass  # resta una coda a metà: la taglia chi riapre
            self._scritti += interi
            self._impronta.update(self._in_attesa[:interi])
            self._in_attesa.clear()
            raise
        self._scritti += arrivati
        self._impronta.update(self._in_attesa)
        self._in_attesa.clear()

    def racconta_molti(self, origine: str, eventi) -> int:
        """Racconta molti fatti, un evento ciascuno, e dice quanti. ``eventi`` dà, uno alla volta, la frase, il tipo
        e i legami (ruolo, neurone) di un fatto: è quello che arriva da un'importazione.

        Fa esattamente quello che farebbe ``racconta`` chiamata una volta per fatto, e scrive nel giornale gli stessi
        byte: c'è la prova. Ma per il caso semplice, che in un'importazione è quasi tutti i casi, non ripassa da tutto
        quello che ``racconta`` deve guardare ogni volta. Il caso semplice è: la rete non ha regole né periodi di
        validità, non registra le decisioni, il fatto non è una definizione, e l'orologio non è tornato indietro.
        Appena una di queste cose non è vera, quel fatto lo racconta ``racconta``: qui non si decide niente.
        """
        # Le tracce di un fatto che poi non viene scritto (vedi ``_annulla``): si segnano per tutta la durata, e si
        # svuotano a ogni fatto scritto. ``racconta``, quando un fatto tocca a lei, usa le sue e rimette queste.
        nate, cambiate = [], []
        tracce_di_prima = (self._linee_nate, self._forme_cambiate)
        self._linee_nate, self._forme_cambiate = nate, cambiate
        try:
            return self._racconta_molti(origine, eventi, nate, cambiate)
        finally:
            self._linee_nate, self._forme_cambiate = tracce_di_prima

    def _racconta_molti(self, origine: str, eventi, nate: list, cambiate: list) -> int:
        """Il giro di ``racconta_molti``, con le tracce già predisposte."""
        origine = testo_semplice(origine)
        origine_scritta = _testo_nel_giornale(origine) if type(origine) is str else None
        racconta, fatto_nuovo, nomi = self.racconta, self._fatto, self.nomi
        e_frase, e_origine, e_quando, e_scritto, e_tipo = self.e_frase, self.e_origine, self.e_quando, self.e_scritto, self.e_tipo
        origine_tenuta, scrittura = _un_testo(origine), _un_testo("scrittura")
        quanti = 0
        for frase, tipo, legami in eventi:
            e = len(e_frase)
            definizioni = (self._definizioni or self._tipi_delle_definizioni())[1]
            try:
                # Il caso semplice si riconosce senza toccare niente. Un fatto fatto male (un tipo che non è un numero,
                # un legame che non è una coppia, un numero che non è di un neurone) non lo è: va a ``racconta``, che lo
                # rifiuta come rifiuta ogni fatto fatto male, senza che la rete si fermi.
                semplice = not (
                    self.regole or self.decisioni or self._rotta or self._sola_lettura or self._decidendo or self._chiusa
                    or self._r_da >= 0 or self._r_a >= 0
                    or not self._ammissione or origine_scritta is None or type(frase) is not str
                    or type(tipo) is not int or tipo in definizioni)
                if semplice:
                    # I legami si prendono una volta sola, come fa ``racconta``: le coppie prese qui sono quelle che
                    # vengono controllate, che entrano nella rete e che vanno nel giornale. Chi passa qualcosa che a
                    # ogni passata dà altri valori, o che dichiara una lunghezza e ne ha un'altra, non lo può più
                    # far entrare in un modo nella memoria e in un altro nel giornale.
                    legami = [(ruolo, neurone) for ruolo, neurone in legami]
                    piatti = [x for coppia in legami for x in coppia]
                    semplice = not (
                        not legami or len(legami) > MASSIMO_LEGAMI or set(map(type, piatti)) != _SOLO_INTERI
                        or tipo >= MASSIMO_TIPO or min(piatti) < 0 or max(piatti) >= len(nomi) or not 0 <= tipo < len(nomi))
            except (TypeError, ValueError, OverflowError):  # qualcosa che non si può scorrere, spacchettare o cercare
                semplice = False
            if not semplice:
                # Non è il caso semplice: lo decide ``racconta``, e l'orologio lo guarda lei, al suo solito punto. Qui
                # non lo si è ancora guardato: prima di decidere ``racconta`` può dover leggere il segno dal disco, e
                # l'istante della scrittura è quello di dopo quella lettura, non uno preso prima.
                racconta(frase, origine, [(tipo, legami)])
                quanti += 1
                continue
            quando = time.time()
            if type(quando) is not float or not -1e300 < quando < 1e300 or (e and quando < e_scritto[-1]):
                # L'orologio è tornato indietro, o non dà un numero come si deve: anche questo lo tratta ``racconta``.
                # L'istante è quello già preso qui, e non ne legge un altro: tra le due letture non c'è niente di mezzo,
                # e una seconda potrebbe cadere dall'altra parte di una scadenza.
                racconta(frase, origine, [(tipo, legami)], quando=quando, scritto=max(quando, e_scritto[-1]) if e else quando)
                quanti += 1
                continue
            da_dimenticare = len(nomi)
            self._in_corso = quando
            self._porta_a(quando)
            self._delta = None
            self._non_numerici = []
            try:
                f = fatto_nuovo(tipo, legami, e)
            except BaseException as errore:
                # Si è fermato mentre nasceva: le colonne possono essere rimaste a metà. Nel giornale non c'è niente.
                self._rotta = self._rotta or f"una scrittura si è fermata a metà di un fatto, in memoria ({errore!r})"
                self._memoria_guasta = True
                raise
            if self._non_numerici:  # un circuito non può sommare quello che non è un numero: non si scrive niente
                perche = list(dict.fromkeys(self._non_numerici))
                self._annulla([f], [])
                self._non_numerici = []
                self.dimentica(da_dimenticare)
                raise NonNumerico("un circuito non può sommare questo fatto: " + "; ".join(perche))
            if self._fd >= 0:
                try:  # prima il giornale, poi la memoria, come in ``racconta``: anche comporre il record può fallire
                    # Il record di un fatto semplice, composto a mano: gli stessi byte che darebbe il codificatore.
                    testo = (f'[{_testo_nel_giornale(frase)},{origine_scritta},{quando!r},"scrittura",'
                             f'[{",".join(map(_testo_nel_giornale, nomi[self._confermati:]))}],'
                             f'[[{tipo},[{",".join(map(str, piatti))}]]],[]]')
                    corpo = testo.encode()
                    self._scrivi_record(struct.pack("<II", len(corpo), zlib.crc32(corpo)) + corpo)
                except BaseException:
                    try:
                        self._annulla([f], [])
                        self.dimentica(da_dimenticare)
                    except BaseException as guasto:
                        self._rotta = self._rotta or f"una scrittura fallita non si è potuta annullare in memoria ({guasto})"
                        self._memoria_guasta = True
                    raise
            try:  # l'evento è nel giornale: se la memoria non riesce a contarlo la rete si ferma, come in ``racconta``
                e_scritto.append(quando)
                e_frase.append(frase)
                e_origine.append(origine_tenuta)
                e_quando.append(quando)
                e_tipo.append(scrittura)
                self._confermati = len(nomi)
            except BaseException as guasto:
                self._rotta = self._rotta or f"un evento è nel giornale ma la memoria non è riuscita ad allinearsi ({guasto})"
                self._memoria_guasta = True
                raise
            # Il fatto è scritto: le sue tracce non sono più da togliere. Si svuotano adesso, non al fatto dopo: se nel
            # frattempo qualcuno torna indietro per conto suo, non deve portarsi via la forma di un fatto che è nel giornale.
            if nate:
                nate.clear()
            if cambiate:
                cambiate.clear()
            quanti += 1
        return quanti

    def _in_memoria(self, frase, origine, tipo, quando, nota=None, scritto=None) -> None:
        """Conta in memoria un evento che è già nel giornale. Non può fallire a metà."""
        quando = time.time() if quando is None else quando
        scritto = quando if scritto is None else scritto
        self.e_scritto.append(scritto)
        if nota is not None:
            self.e_nota[len(self.e_frase)] = nota
        self.e_frase.append(frase)
        self.e_origine.append(_un_testo(origine))  # un testo solo per ogni origine: vedi _rileggi
        self.e_quando.append(quando)
        self.e_tipo.append(_un_testo(tipo))
        self._confermati = len(self.nomi)

    # --- regole: neuroni a soglia scritti nella rete ---------------------------

    def _id(self, nome: str) -> int:
        trovati = self.per_nome.get(piana(nome))
        return trovati[0] if trovati else -1

    def proponi_regola(self, frase: str, origine: str, nome: str, soglia: int, azione: str, segnali) -> int:
        """Scrive la regola nella rete, come fatti. ``segnali``: lista di dizionari con
        nome, modo ("presenza" o "assenza"), osserva (tipo di fatto), lega (ruolo dell'entità),
        peso e, se servono, con_ruolo e con_valore."""
        segno = len(self.nomi)  # se la regola viene rifiutata, i neuroni nati per lei si dimenticano
        n = self.n
        r = self.nuovo(nome)
        fatti = [(n("regola"), [(n("regola di"), r), (n("soglia"), n(str(soglia))), (n("azione"), n(azione))])]
        for s in segnali:
            if "circuito" in s:  # il segnale legge un circuito: si accende se il valore è sotto il limite
                lg = [(n("segnale di"), r), (n("nome"), n(s["nome"])), (n("modo"), n("sotto")), (n("legge il circuito"), s["circuito"]),
                      (n("limite"), n(str(s["limite"]))), (n("peso"), n(str(s["peso"])))]
            else:
                lg = [(n("segnale di"), r), (n("nome"), n(s["nome"])), (n("modo"), n(s["modo"])), (n("osserva"), s["osserva"]),
                      (n("lega"), s["lega"]), (n("peso"), n(str(s["peso"])))]
                if "con_ruolo" in s:
                    lg += [(n("con ruolo"), s["con_ruolo"]), (n("con valore"), s["con_valore"])]
            fatti.append((n("segnale"), lg))
        return self.racconta(frase, origine, fatti, neuroni_da=segno)

    def proponi_circuito(self, frase: str, origine: str, nome: str, termini) -> int:
        """Scrive nella rete un circuito mantenuto: una somma tenuta pronta per ogni entità. ``termini``: lista di
        dizionari con osserva (tipo di fatto), lega (ruolo dell'entità), somma (ruolo con il numero) e peso (un
        intero, di solito +1 o -1). Da quel momento ogni scrittura aggiorna la somma nello stesso commit. Se la
        definizione non sta in piedi, o i fatti che ci sono già non hanno un numero in quel ruolo, non scrive niente.

        Un termine può avere anche ``per``: «mese» oppure «anno». Allora l'entità non è il neurone che il fatto ha
        nel ruolo ``lega``, che deve essere una data, ma il suo mese (o il suo anno): la somma è tenuta pronta per
        ogni mese. Il mese è un neurone come gli altri, che ha per nome l'inizio del nome della data."""
        segno = len(self.nomi)  # se la definizione viene rifiutata, i neuroni nati per lei si dimenticano
        n = self.n
        c = self.nuovo(nome)
        fatti = [(n("circuito"), [(n("circuito di"), c)])]
        for t in termini:
            fatti.append((n("termine"), [(n("termine di"), c), (n("osserva"), t["osserva"]), (n("lega"), t["lega"]),
                                         (n("somma"), t["somma"]), (n("peso"), n(str(t["peso"]))),
                                         *([(n("raggruppa per"), n(t["per"]))] if t.get("per") else [])]))
        return self.racconta(frase, origine, fatti, neuroni_da=segno)

    def _porta_a(self, al: float) -> None:
        """Porta le somme dei circuiti all'istante ``al``. Se tra l'istante di prima e questo c'è una decorrenza o
        una scadenza le rifà dai fatti; altrimenti sono già giuste."""
        if self.circuiti and not self._confine_prima <= al < self._confine_dopo:
            self.valori, self._confine_prima, self._confine_dopo, self._presenze = self._somme(self.circuiti, al)
        self._valido_al = al

    def valore(self, circuito: int, di: int, al: float | None = None) -> Decimal:
        """Il valore che il circuito tiene pronto per quell'entità, per i fatti che valgono all'istante ``al`` (adesso,
        se non è detto). Zero se non ha ricevuto niente. Una decorrenza o una scadenza passata nel frattempo cambia
        il valore anche se nessuno ha scritto."""
        self._porta_a(time.time() if al is None else al)
        return Decimal(da_unita(self.valori[circuito].get(di, 0)))

    def _somme(self, circuiti: list[dict], al: float, senza_validita: bool = False,
               ammissione=()) -> tuple[dict[int, dict[int, int]], float, float, dict[int, dict[int, int]]]:
        """Le somme dei circuiti rifatte dai fatti in vigore che valgono all'istante ``al``; i due confini entro cui
        restano giuste, cioè l'ultima decorrenza o scadenza non dopo ``al`` e la prima dopo; e quanti fatti
        contribuiscono a ogni entità. Con ``senza_validita`` si sommano tutti i fatti in vigore, anche scaduti o non
        ancora cominciati: è la lettura di quello che è registrato.

        Un circuito ordinario dà il fatto a ogni entità che nomina nel ruolo che lega; uno raggruppato a un mese solo,
        quello del primo. È lo stesso criterio dell'aggiornamento fatto a ogni scrittura, ed è quello con cui le
        versioni di prima rifacevano le somme dei circuiti ordinari: una ricevuta scritta allora si verifica ancora.
        ``ammissione`` sono i circuiti che una scrittura sta definendo adesso: per loro i fatti che ci sono già devono
        dire a quale mese vanno, come lo dovranno dire quelli che arriveranno. Per gli altri si rilegge quello che è
        stato scritto, anche con le regole di prima."""
        somme: dict[int, dict[int, int]] = {}
        presenze: dict[int, dict[int, int]] = {}
        prima, dopo = -math.inf, math.inf
        # Per i circuiti che nascono adesso serve sapere quanti fatti non ritirati ha ogni tipo osservato: uno senza
        # il ruolo che lega non sta sulla linea, e non lo si vedrebbe. Si contano una volta sola, in una passata, per
        # tutti i termini.
        da_contare = {t["osserva"] for c in circuiti if c["id"] in ammissione for t in c["termini"] if t["taglio"]}
        non_ritirati = dict.fromkeys(da_contare, 0)
        if da_contare:
            for tipo, morto in zip(self.f_tipo, self.f_morto):
                if morto < 0 and tipo in non_ritirati:
                    non_ritirati[tipo] += 1
        for c in circuiti:
            per_entita, presenti = somme[c["id"]], presenze[c["id"]] = {}, {}
            rigoroso = c["id"] in ammissione
            for t in c["termini"]:
                lega, taglio = t["lega"], t["taglio"]
                solo_la_prima = bool(taglio) or CALCOLI[self._calcolo][0] == "prima"
                visti = 0
                for x, v in list(self.vivi.get((lega << BIT) | t["osserva"], {}).items()):
                    entita = x
                    for f in (v,) if type(v) is int else v:
                        if solo_la_prima and self.leggi(f, lega) != x:
                            continue  # il ruolo c'è due volte nel fatto: conta per la prima entità
                        visti += 1
                        if taglio:
                            if rigoroso:
                                perche = self._non_raggruppabile(f, lega, x)
                                if perche:
                                    raise NonNumerico(f"il circuito «{c['nome']}» raggruppa per {'mese' if taglio == 7 else 'anno'}, e {perche} in {self.in_parole(f)}")
                            if entita == x:
                                entita = self._periodo(x, taglio)
                        periodo = self.validita(f)
                        if periodo is None and not senza_validita:
                            raise NonNumerico(f"il circuito «{c['nome']}» non può sommare un fatto senza una data intera: {self.in_parole(f)}")
                        if not senza_validita:
                            for confine in periodo:
                                if confine <= al:
                                    prima = max(prima, confine)
                                else:
                                    dopo = min(dopo, confine)
                        n = self.leggi(f, t["somma"])
                        u = in_unita(self.nomi[n]) if n >= 0 else None
                        if u is None:
                            raise NonNumerico(f"il circuito «{c['nome']}» dovrebbe sommare un valore che non è un numero in {self.in_parole(f)}")
                        if senza_validita or periodo[0] <= al < periodo[1]:
                            per_entita[entita] = per_entita.get(entita, 0) + t["peso"] * u
                            presenti[entita] = presenti.get(entita, 0) + 1
                if rigoroso and taglio:
                    tipo = t["osserva"]
                    in_vigore = non_ritirati[tipo]
                    if in_vigore > visti:
                        raise NonNumerico(f"il circuito «{c['nome']}» raggruppa per {'mese' if taglio == 7 else 'anno'}, e {in_vigore - visti} fatti "
                                          f"«{self.nomi[tipo]}» non hanno «{self.nomi[lega]}»")
        return somme, prima, dopo, presenze

    def somme_registrate(self, circuito: int) -> dict[int, int]:
        """Le somme del circuito su tutti i fatti registrati e non ritirati, senza guardare se valgono: quello che il
        circuito varrebbe se la validità non esistesse. Non sono tenute pronte: si rifanno dai fatti ogni volta."""
        definito = next((c for c in self.circuiti if c["id"] == circuito), None)
        return {} if definito is None else self._somme([definito], math.inf, senza_validita=True)[0][circuito]

    def somme_dai_fatti(self) -> dict[int, dict[int, int]]:
        """Le somme di tutti i circuiti, rifatte da capo dai fatti in vigore che valgono all'istante a cui i circuiti
        sono portati: quello che i circuiti devono valere."""
        return self._somme(self.circuiti, self._valido_al)[0]

    def verifica_circuiti(self) -> list[dict]:
        """Confronta quello che i circuiti tengono pronto con le somme rifatte dai fatti. Restituisce le differenze."""
        differenze = []
        rifatte = self.somme_dai_fatti()
        for c in self.circuiti:
            tenute, giuste = self.valori.get(c["id"], {}), rifatte[c["id"]]
            for x in sorted(tenute.keys() | giuste.keys()):
                a, b = tenute.get(x, 0), giuste.get(x, 0)
                if a != b:
                    differenze.append({"circuito": c["nome"], "di": self.nomi[x], "di_id": x, "tenuto": da_unita(a), "dai_fatti": da_unita(b)})
                elif (x in tenute) != (x in giuste):  # stessa somma, zero, ma solo da una parte l'entità sta nel circuito
                    differenze.append({"circuito": c["nome"], "di": self.nomi[x], "di_id": x, "tenuto": da_unita(a), "dai_fatti": da_unita(b),
                                       "presenza": "solo tenuta" if x in tenute else "solo dai fatti"})
        return differenze

    def _leggi_definizioni(self) -> tuple[list[dict], list[dict], dict[int, list[tuple]]]:
        """Legge dalla rete le regole e i circuiti che ci sono scritti, senza cambiare niente: è la rete a dire quali
        ci sono. Solleva DefinizioneNonValida se una non si può leggere."""
        g = self._id

        def numero(f: int, ruolo: str, che: str, come=float):
            n = self.leggi(f, g(ruolo))
            try:
                x = come(self.nomi[n]) if n >= 0 else None
            except (ValueError, InvalidOperation):
                x = None
            if x is None or (come is float and not math.isfinite(x)):
                raise DefinizioneNonValida(f"{che}: «{ruolo}» deve essere un numero, e c'è «{self.nomi[n] if n >= 0 else ''}»")
            return x

        def intero(testo: str) -> int:
            u = in_unita(testo)
            if u is None or u % _UNITA:
                raise ValueError
            return u // _UNITA

        def versione(f: int, pezzo: str, di: str, chi: int) -> int:
            """L'ultimo evento che ha cambiato la definizione: quello che l'ha scritta, o l'ultimo che le ha aggiunto
            o tolto un pezzo (un termine a un circuito, un segnale a una regola)."""
            ultimo = self.f_nato[f]
            if g(pezzo) >= 0 and g(di) >= 0:
                linea = (g(di) << BIT) | g(pezzo)
                v = self.vivi.get(linea, {}).get(chi)
                for x in () if v is None else (v,) if type(v) is int else v:
                    ultimo = max(ultimo, self.f_nato[x])
                for x in self.morti.get(linea, {}).get(chi, ()):
                    if x not in self._da_parte:  # in uno scenario, i pezzi degli eventi lasciati fuori non ci sono mai stati
                        ultimo = max(ultimo, self.f_nato[x], self.f_morto[x])
            return ultimo

        circuiti, termini_per_tipo = [], {}
        if g("circuito") >= 0:
            for v in list(self.vivi.get((g("circuito di") << BIT) | g("circuito"), {}).values()):
                for f in (v,) if type(v) is int else sorted(v):
                    c = self.leggi(f, g("circuito di"))
                    termini = []
                    for s in sorted(self.scatta(g("termine"), [(g("termine di"), c)])) if g("termine") >= 0 else ():
                        termine = {k: self.leggi(s, g(k)) for k in ("osserva", "lega", "somma")}
                        if min(termine.values()) < 0:
                            raise DefinizioneNonValida(f"il circuito «{self.nomi[c]}» ha un termine senza osserva, lega o somma")
                        termine["peso"] = numero(s, "peso", f"il circuito «{self.nomi[c]}»", intero)
                        per = self.leggi(s, g("raggruppa per")) if g("raggruppa per") >= 0 else -1
                        termine["taglio"] = TAGLI.get(piana(self.nomi[per])) if per >= 0 else None
                        if per >= 0 and termine["taglio"] is None:
                            raise DefinizioneNonValida(f"il circuito «{self.nomi[c]}» raggruppa per «{self.nomi[per]}»: si può per mese o per anno")
                        termini.append(termine)
                    if not termini:
                        raise DefinizioneNonValida(f"il circuito «{self.nomi[c]}» non ha termini")
                    circuiti.append({"id": c, "nome": self.nomi[c], "versione": versione(f, "termine", "termine di", c),
                                     "nata": self.f_nato[f], "termini": termini})
                    for t in termini:
                        termini_per_tipo.setdefault(t["osserva"], []).append((c, t["lega"], t["somma"], t["peso"], t["taglio"]))
        if len({c["id"] for c in circuiti}) != len(circuiti):
            raise DefinizioneNonValida("un circuito è definito due volte")
        noti = {c["id"] for c in circuiti}
        raggruppati = {c["id"] for c in circuiti if any(t["taglio"] for t in c["termini"])}
        regole = []
        if g("regola") >= 0:
            for v in list(self.vivi.get((g("regola di") << BIT) | g("regola"), {}).values()):
                for f in (v,) if type(v) is int else sorted(v):
                    r = self.leggi(f, g("regola di"))
                    che = f"la regola «{self.nomi[r]}»"
                    segnali = []
                    for s in sorted(self.scatta(g("segnale"), [(g("segnale di"), r)])) if g("segnale") >= 0 else ():
                        if self.nomi[self.leggi(s, g("modo"))] == "sotto":
                            limite = in_unita(self.nomi[self.leggi(s, g("limite"))]) if self.leggi(s, g("limite")) >= 0 else None
                            circuito = self.leggi(s, g("legge il circuito"))
                            if limite is None or circuito not in noti:
                                raise DefinizioneNonValida(f"{che} legge un circuito che non c'è, oppure il suo limite non è un numero")
                            if circuito in raggruppati:
                                # una regola lega il circuito all'entità del fatto proposto: con un mese al posto dell'entità
                                # leggerebbe la somma sbagliata. Non c'è ancora: si rifiuta, invece di decidere su un numero che non è il suo
                                raise DefinizioneNonValida(f"{che} legge un circuito raggruppato per mese o per anno: una regola non lo sa ancora leggere")
                            segnali.append({"nome": self.nomi[self.leggi(s, g("nome"))], "circuito": circuito, "limite": limite,
                                            "osserva": -1, "lega": -1, "peso": numero(s, "peso", che)})
                            continue
                        segnali.append({
                            "circuito": -1,
                            "nome": self.nomi[self.leggi(s, g("nome"))],
                            "assenza": self.nomi[self.leggi(s, g("modo"))] == "assenza",
                            "osserva": self.leggi(s, g("osserva")),
                            "lega": self.leggi(s, g("lega")),
                            "peso": numero(s, "peso", che),
                            "con": (self.leggi(s, g("con ruolo")), self.leggi(s, g("con valore"))),
                        })
                    regole.append({
                        "nome": self.nomi[r], "id": r, "versione": versione(f, "segnale", "segnale di", r), "nata": self.f_nato[f],
                        "soglia": numero(f, "soglia", che),
                        "azione": self.nomi[self.leggi(f, g("azione"))],
                        "segnali": segnali, "tipi": {s["osserva"] for s in segnali} - {-1},
                        "circuiti": {s["circuito"] for s in segnali} - {-1},
                    })
        return regole, circuiti, termini_per_tipo

    def _compila_regole(self) -> None:
        """Rilegge le regole e i circuiti dalla rete e rifà le somme dei circuiti dai fatti in vigore."""
        self.regole, self.circuiti, self._termini = self._leggi_definizioni()
        self._valido_al = self.e_scritto[-1] if len(self.e_scritto) else 0.0  # l'ultimo istante che la rete conosce
        self.valori, self._confine_prima, self._confine_dopo, self._presenze = self._somme(self.circuiti, self._valido_al)

    def _valuta(self, creati: list[int], ritirati: list[int], base: int = -1, istante: float = 0.0) -> list[dict]:
        """Rivaluta solo le regole che osservano i tipi di fatto toccati, per le sole entità toccate.

        Restituisce ogni regola consultata, con il verdetto («vieta» o «passa») e le letture fatte. Ogni lettura
        dice che cosa ha guardato, di chi, su quale base, per quale momento, se con la proposta applicata, e il
        valore che ha trovato: è la testimonianza che una verifica deve poter ritrovare.
        """
        consultate = []
        toccati = creati + ritirati
        for regola in self.regole:
            entita = set()
            for f in toccati:
                tipo = self.f_tipo[f]
                if tipo in regola["tipi"]:
                    for s in regola["segnali"]:
                        if s["osserva"] == tipo:
                            x = self.leggi(f, s["lega"])
                            if x >= 0:
                                entita.add(x)
            if regola["circuiti"] and self._delta:  # una regola che legge un circuito va consultata per ogni entità
                entita.update(x for c, x in self._delta if c in regola["circuiti"])  # di cui la scrittura sposta il valore
            versioni = {c["id"]: c for c in self.circuiti}
            for x in sorted(entita):
                attivazione = 0.0
                letti, letture = [], []
                for s in regola["segnali"]:
                    if s["circuito"] >= 0:
                        c = s["circuito"]
                        dopo = self.valori[c].get(x, 0)
                        prima = dopo - (self._delta or {}).get((c, x), 0)
                        acceso = dopo < s["limite"]
                        letti.append((s["nome"], s["peso"], 1 if acceso else 0))
                        comune = {"segnale": s["nome"], "cosa": versioni[c]["nome"], "circuito_id": c, "versione": versioni[c]["versione"],
                                  **({"versione_di_nascita": versioni[c]["nata"]} if self._con_nascita else {}),
                                  "modo": "sotto", "limite": da_unita(s["limite"]), "di": x, "base": base, "valido_al": istante}
                        letture.append({**comune, "con_proposta": False, "valore": da_unita(prima)})  # quanto c'era prima della richiesta
                        letture.append({**comune, "con_proposta": True, "valore": da_unita(dopo)})  # e quanto ce ne sarebbe dopo
                        attivazione += s["peso"] * acceso
                        continue
                    indizi = [(s["lega"], x)]
                    if s["con"][0] >= 0:
                        indizi.append(s["con"])
                    acceso = any(self.vale(f, istante) for f in self.scatta(s["osserva"], indizi)) != s["assenza"]
                    letti.append((s["nome"], s["peso"], 1 if acceso else 0))
                    letture.append({
                        "segnale": s["nome"], "cosa": self.nomi[s["osserva"]], "cosa_id": s["osserva"],
                        "modo": "assenza" if s["assenza"] else "presenza",
                        "di": x, "con": list(s["con"]) if s["con"][0] >= 0 else None,
                        "base": base, "valido_al": istante, "con_proposta": True, "valore": 1 if acceso else 0,
                    })
                    attivazione += s["peso"] * acceso
                vieta = attivazione >= regola["soglia"] and regola["azione"] == "vieta"
                consultate.append({"regola": regola["nome"], "regola_id": regola["id"], "versione": regola["versione"],
                                   **({"versione_di_nascita": regola["nata"]} if self._con_nascita else {}),
                                   "su": self.nomi[x], "su_id": x,
                                   "attivazione": attivazione, "soglia": regola["soglia"], "segnali": letti,
                                   "azione": regola["azione"], "verdetto": "vieta" if vieta else "passa", "letture": letture})
        return consultate

    # --- disco: giornale e istantanea -----------------------------------------

    def _apri(self) -> None:
        if not self._sola_lettura:
            os.makedirs(self.cartella, exist_ok=True)
        giornale = os.path.join(self.cartella, "giornale")
        istantanea = os.path.join(self.cartella, "istantanea")
        if not self._sola_lettura:
            # Uno solo scrive. Chi apre per scrivere prende il giornale per sé prima di leggerlo, e lo tiene finché non
            # lo chiude: un secondo programma confermerebbe eventi con un numero che il giornale non gli dà, e dopo una
            # scrittura a metà ognuno taglierebbe i record dell'altro. Chi legge soltanto non prende niente.
            fd = os.open(giornale, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                os.close(fd)
                raise RuntimeError("questo giornale è già aperto per scrivere da un altro programma, o da un'altra parte di questo: "
                                   "uno solo scrive. Si può aprire per leggere") from None
            self._fd = fd
        try:
            lunghezza = os.path.getsize(giornale) if os.path.exists(giornale) else 0
            da = 0
            if os.path.exists(istantanea):
                da = self._carica_istantanea(istantanea, lunghezza)
            fine = self._rileggi_il_giornale(giornale, da) if lunghezza > da else da
            if self._fd >= 0 and fine < lunghezza:
                # coda scritta a metà da un arresto brusco: la taglia chi apre per scrivere, e solo lui
                os.ftruncate(self._fd, fine)
            self._scritti = fine
            self._compila_regole()
        except BaseException:
            if self._fd >= 0:  # non si è aperta: il giornale torna libero
                os.close(self._fd)
                self._fd = -1
            raise

    def _rileggi_il_giornale(self, giornale: str, da: int) -> int:
        """Rilegge il giornale da quel byte, un pezzo alla volta, e dice fin dove arrivano i record buoni. Quello che
        resta dopo può essere solo una coda scritta a metà: lo giudica ``giudica_la_coda``, che altrimenti solleva
        ``GiornaleGuasto``. Il giornale è quello che c'è nel momento in cui lo si apre: se chi scrive aggiunge
        qualcosa mentre lo si legge, non lo si guarda."""
        fine, resto = da, b""
        with open(giornale, "rb") as f:
            fine_file = os.fstat(f.fileno()).st_size
            f.seek(da)
            while fine + len(resto) < fine_file:
                pezzo = f.read(min(PEZZO_DI_GIORNALE, fine_file - fine - len(resto)))
                if not pezzo:  # il file è stato accorciato mentre lo si leggeva: vale quello che c'è
                    fine_file = fine + len(resto)
                    break
                dati = resto + pezzo if resto else pezzo
                letti = self._rileggi(dati, 0)
                with memoryview(dati) as vista:
                    self._impronta.update(vista[:letti])  # solo i record riletti, nell'ordine in cui sono entrati
                fine += letti
                resto = dati[letti:]
                if len(resto) >= 8:
                    # In testa a quello che resta c'è un record che non si è riletto. Se leggere ancora non lo può
                    # rendere buono ci si ferma qui, senza tenere in memoria tutto il resto del giornale: è intero, o
                    # dichiara una lunghezza a cui non si può credere, o che va oltre la fine del file.
                    lunghezza = struct.unpack_from("<I", resto, 0)[0]
                    if 8 + lunghezza <= len(resto) or lunghezza > MASSIMO_RECORD or fine + 8 + lunghezza > fine_file:
                        break
            if fine < fine_file:
                # Se quello che resta è tutto qui, si giudicano questi byte e non una seconda lettura del file: tra
                # l'una e l'altra chi scrive può aver tagliato la sua coda a metà e scritto altro nello stesso punto.
                self.giudica_la_coda(f, fine, fine_file, resto if len(resto) == fine_file - fine else None)
        return fine

    @staticmethod
    def giudica_la_coda(f, fine: int, fine_file: int, gia_letti: bytes | None = None) -> None:
        """Quello che c'è nel giornale tra l'ultimo record buono (``fine``) e la fine del file. Se è una coda scritta
        a metà non fa niente: un record incompleto in fondo, o dello spazio mai scritto, sono una scrittura mai
        confermata. Se non lo è solleva ``GiornaleGuasto``.

        Non è una coda: un record che c'è tutto e non si rilegge; un record a cui non torna la lunghezza ma che ha
        il resto giusto; qualunque cosa sia seguita, più avanti, da un record buono; e un resto troppo lungo per
        essere una scrittura sola. Alla lunghezza che il record guasto dichiara non si crede: può essere proprio lei
        quella rovinata, e allora il record sembrerebbe arrivare oltre la fine del file come una coda a metà."""
        resta = fine_file - fine
        if resta <= 0:
            return
        # La finestra contiene sempre una scrittura sola rimasta a metà: un record lungo quanto il massimo, più un
        # gruppo scritto a lotti. Se le costanti cambiano, la finestra le segue.
        larga = max(FINESTRA_DI_CODA, 16 + MASSIMO_RECORD + LOTTO)
        if gia_letti is not None:
            finestra = bytes(gia_letti[:larga])
        else:
            f.seek(fine)
            finestra = f.read(min(resta, larga))
            if len(finestra) < min(resta, larga):  # il file è stato accorciato nel frattempo: vale quello che c'è
                resta = len(finestra)
        qui = f"nel giornale il record che comincia al byte {fine} non si rilegge, e non è una coda scritta a metà: "
        da_fare = ". Tagliare da lì toglierebbe scritture confermate: il giornale va ripristinato da una copia"
        if not finestra.strip(b"\0"):  # spazio mai scritto: tutto a zero, fino in fondo
            letti = len(finestra)
            while letti < resta:
                pezzo = f.read(min(1 << 20, resta - letti))
                if not pezzo:
                    return
                if pezzo.strip(b"\0"):
                    raise GiornaleGuasto(qui + "dopo dello spazio mai scritto ci sono altri dati" + da_fare)
                letti += len(pezzo)
            return
        # C'è un record buono più avanti? Un record comincia con la sua lunghezza e il suo controllo, e poi con «[».
        vista, dove, lavoro = memoryview(finestra), finestra.find(b"[", 9), 0
        while dove >= 0:
            q = dove - 8
            lunghezza, crc = struct.unpack_from("<II", finestra, q)
            if 2 <= lunghezza <= MASSIMO_RECORD and q + 8 + lunghezza <= resta:
                lavoro += lunghezza
                if lavoro > 8 * len(finestra) + (64 << 20):
                    # Troppi pezzi che sembrano l'inizio di un record: non è quello che lascia una scrittura a metà, e
                    # controllarli tutti costerebbe ore. Dal giornale scritto dal motore non ci si arriva.
                    raise GiornaleGuasto(qui + "quello che resta non si lascia giudicare" + da_fare)
                if q + 8 + lunghezza <= len(finestra):
                    corpo = vista[dove:dove + lunghezza]
                else:
                    f.seek(fine + dove)
                    corpo = f.read(lunghezza)
                if len(corpo) == lunghezza and zlib.crc32(corpo) == crc:
                    raise GiornaleGuasto(qui + "dopo di lui ce ne sono altri" + da_fare)
            dove = finestra.find(b"[", dove + 1)
        if len(finestra) >= 8:
            lunghezza, crc = struct.unpack_from("<II", finestra, 0)
            if 8 + lunghezza <= resta:
                raise GiornaleGuasto(qui + "è intero" + da_fare)
            if resta <= len(finestra) and finestra[8:9] == b"[" and zlib.crc32(vista[8:]) == crc:
                raise GiornaleGuasto(qui + "c'è tutto, ma la lunghezza che dichiara è rovinata" + da_fare)
        if resta > len(finestra):
            raise GiornaleGuasto(qui + "quello che resta è troppo lungo per essere una scrittura sola" + da_fare)

    @staticmethod
    def _fin_dove(segno: dict) -> int:
        """L'evento più avanti di cui il segno parla: un giornale che non ci arriva non è il suo."""
        return max([segno["dal"], segno.get("calcolo_dal", 0), *segno.get("dichiarano_da", ()),
                    *(dal for _, dal in segno.get("calcoli") or []), *(x["a"] for x in segno.get("attestati", ()))])

    @staticmethod
    def _dichiarano(segno: dict | None) -> list[int]:
        """Gli eventi da cui un programma che dichiara il modo in ogni decisione ha cominciato a scrivere, in ordine."""
        if not segno:
            return []
        return sorted({*([segno["calcolo_dal"]] if "calcolo_dal" in segno else []), *segno.get("dichiarano_da", ())})

    @classmethod
    def _frontiera(cls, segno: dict | None, evento: int) -> dict | None:
        """Il segno che il giornale deve avere prima che questo programma ci scriva l'evento ``evento``. None se quello
        che c'è va già bene, come succede dalla seconda scrittura in poi.

        In un giornale senza segno dice da quale evento ogni evento porta la sua decisione (``dal``). Se l'ultimo modo
        scritto in ``calcoli`` non è quello di questo programma aggiunge la sua frontiera: vale anche per un programma
        più vecchio che torna a scrivere dopo uno più nuovo. E dice che da questo evento scrive un programma che
        dichiara il modo in ogni decisione (``calcolo_dal`` la prima volta, poi ``dichiarano_da``): serve quando
        l'ultima cosa che il segno dice è un'attribuzione, della 0.9.5 o scritta a mano. Da lì una decisione che non
        dichiara il suo modo non l'ha scritta questo programma, e quell'attribuzione non la copre più.
        """
        if segno is not None:
            if segno["formato"] != FORMATO:
                raise SegnoGuasto(f"in questo giornale le decisioni sono nel formato {segno['formato']}, che questo programma non scrive: "
                                  "lo ha scritto un programma diverso, e questo non ci aggiunge niente")
            oltre = cls._fin_dove(segno)
            if oltre > evento:
                raise SegnoGuasto(f"il segno del giornale parla dell'evento {oltre}, ma il giornale ne ha {evento}: non è il segno di "
                                  "questo giornale, o il giornale ha perso degli eventi. Non ci aggiungo una frontiera")
        nuovo = {"formato": FORMATO, "dal": evento} if segno is None else dict(segno)
        calcoli = [list(c) for c in nuovo.get("calcoli") or []]
        dichiarano = cls._dichiarano(segno)
        # l'ultima cosa che il segno dice è «da qui scrive chi dichiara»? A parità di evento vale questa
        serve = not dichiarano or bool(calcoli and calcoli[-1][1] > dichiarano[-1])
        if not calcoli or calcoli[-1][0] != CALCOLO:
            calcoli.append([CALCOLO, evento])
            serve = True
        nuovo["calcoli"] = calcoli
        if serve and evento not in dichiarano:
            if "calcolo_dal" not in nuovo:
                nuovo["calcolo_dal"] = evento
            else:
                nuovo["dichiarano_da"] = sorted([*nuovo.get("dichiarano_da", ()), evento])
        return None if nuovo == segno else nuovo

    def _scrivi_segno(self, segno: dict) -> None:
        """Il segno durevole sta fuori dal giornale, perché togliere la traccia a un evento non lo faccia sembrare uno
        di prima. Si scrive intero in un file a parte e si mette al posto del vecchio in un colpo solo."""
        provvisorio = os.path.join(self.cartella, "formato.nuovo")
        with open(provvisorio, "w") as f:
            json.dump(segno, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(provvisorio, os.path.join(self.cartella, "formato"))
        self._nome_del_segno_incerto = True  # il file è al suo posto, ma il suo nome non è ancora sul disco
        self._sincronizza_la_cartella()

    def _sincronizza_la_cartella(self) -> None:
        """Mette sul disco il nome nuovo del segno. Finché non riesce, nessun evento viene confermato."""
        cartella = os.open(self.cartella, os.O_RDONLY)
        try:
            os.fsync(cartella)
        finally:
            os.close(cartella)
        self._nome_del_segno_incerto = False

    def _rileggi(self, dati: bytes, da: int, eventi: int | None = None) -> int:
        """Rilegge i record del giornale da ``da``. Con ``eventi`` si ferma quando la rete ne ha quel numero."""
        pos = da
        fine = len(dati)
        self._evento_aperto = True  # i posti vuoti si tolgono alla fine di ogni evento, come fa chi scrive
        while pos + 8 <= fine and (eventi is None or len(self.e_frase) < eventi):
            lunghezza, crc = struct.unpack_from("<II", dati, pos)
            corpo = dati[pos + 8:pos + 8 + lunghezza]
            if not record_buono(lunghezza, crc, corpo):
                break
            try:
                frase, origine, quando, tipo, nuovi, piatti, ritirati, *resto = json.loads(corpo)
            except ValueError:  # il controllo torna ma dentro non c'è un evento: ci si ferma qui, e lo giudica chi apre
                break
            for nome in nuovi:
                self.nuovo(nome)
            self._confermati = len(self.nomi)
            e = len(self.e_frase)
            if resto and resto[0] is not None:
                self.e_nota[e] = resto[0]
            for f in ritirati:
                self._ritira(f, e)
            for t, lg in piatti:
                self._fatto(t, list(zip(lg[0::2], lg[1::2])), e)
            if self._spenti or self._azzerati:
                self._chiudi_evento()
            self.e_frase.append(frase)
            # L'origine e il tipo sono pochi testi ripetuti per ogni evento. Letti dal giornale sarebbero una copia
            # ciascuno: con dieci milioni di eventi quasi due gigabyte di testi uguali, in memoria e poi nell'istantanea.
            # Si tiene un testo solo per ogni valore, come succede quando gli eventi vengono scritti.
            self.e_origine.append(_un_testo(origine))
            self.e_quando.append(quando)
            # i record che non lo hanno: vale l'arrivo, ma mai prima dell'evento precedente, perché l'ordine resti quello
            prima = self.e_scritto[-1] if len(self.e_scritto) else quando
            self.e_scritto.append(resto[1] if len(resto) > 1 else max(prima, quando))
            self.e_tipo.append(_un_testo(tipo))
            pos += 8 + lunghezza
        self._evento_aperto = False
        return pos

    @classmethod
    def com_era(cls, cartella: str, eventi: int, calcolo: int | None = None) -> tuple["Rete", list | None]:
        """La rete com'era dopo i primi ``eventi`` eventi, ricostruita in memoria dal solo giornale, e il record
        dell'evento successivo, se c'è. Non apre il giornale in scrittura e non guarda l'istantanea. Con ``calcolo``
        le somme dei circuiti si rifanno con il modo di una versione di prima: serve a verificare le sue ricevute."""
        rete = cls()
        if calcolo is not None:
            rete._calcolo = calcolo
        successivo = None
        with open(os.path.join(cartella, "giornale"), "rb") as f:  # si legge un record alla volta, solo fin dove serve
            while True:
                testa = f.read(8)
                if len(testa) < 8:
                    break
                lunghezza, crc = struct.unpack("<II", testa)
                corpo = f.read(lunghezza) if lunghezza <= MASSIMO_RECORD else b""
                if not record_buono(lunghezza, crc, corpo):
                    break
                if len(rete.e_frase) == eventi:
                    successivo = json.loads(corpo)
                    break
                rete._rileggi(testa + corpo, 0)
        if len(rete.e_frase) != eventi:
            raise ValueError(f"il giornale ha solo {len(rete.e_frase)} eventi")
        rete._compila_regole()
        return rete, successivo

    @classmethod
    def vista(cls, cartella: str, eventi: int, valido_al: float | None = None) -> "Rete":
        """La rete da interrogare per una lettura del passato: quello che era registrato dopo i primi ``eventi``
        eventi e, se ``valido_al`` è detto, solo i fatti che valgono a quell'istante. È una copia in memoria rifatta
        dal giornale: non scrive e non va usata per decidere."""
        rete, _ = cls.com_era(cartella, eventi)
        rete.lettura = ("registrato",) if valido_al is None else ("valido", valido_al)
        if valido_al is not None:
            rete._porta_a(valido_al)
            for f in range(len(rete.f_tipo)):
                if rete.f_morto[f] < 0:
                    periodo = rete.validita(f)
                    if periodo is None or not periodo[0] <= valido_al < periodo[1]:
                        rete._ritira(f, eventi)
        return rete

    @staticmethod
    def _record(cartella: str):
        """I record del giornale, uno alla volta, finché sono interi."""
        with open(os.path.join(cartella, "giornale"), "rb") as f:
            while True:
                testa = f.read(8)
                if len(testa) < 8:
                    return
                lunghezza, crc = struct.unpack("<II", testa)
                corpo = f.read(lunghezza) if lunghezza <= MASSIMO_RECORD else b""
                if not record_buono(lunghezza, crc, corpo):
                    return
                yield json.loads(corpo)

    @classmethod
    def scenario(cls, cartella: str, evento: int, rettifiche=(), salta_definizioni: bool = False,
                 calcolo: int | None = None) -> tuple["Rete", list, int, list]:
        """La rete su cui riesaminare la decisione di un evento: la sua base, cioè gli eventi che la precedono, più
        le sole rettifiche indicate, che sono eventi scritti dopo. Gli altri eventi non ci sono: né quello della
        decisione, né quelli di mezzo.

        Il giornale si rilegge fino all'ultima rettifica tenendo gli stessi numeri di neuroni e di fatti della
        storia vera: i fatti degli eventi lasciati fuori nascono e sono subito messi da parte. Se una rettifica
        ritira un fatto scritto da un evento lasciato fuori solleva ScenarioIncompleto: non aggiunge niente di
        nascosto. Una rettifica non può cambiare regole o circuiti, che restano quelli di allora; con
        ``salta_definizioni`` un evento così viene lasciato fuori e riportato tra gli esclusi.

        Restituisce la rete, il record dell'evento, quanti neuroni conosceva la base, e gli eventi esclusi.
        """
        scelte = set(rettifiche)
        if any(e <= evento for e in scelte):
            raise ValueError("una rettifica è un evento scritto dopo la decisione che si riesamina")
        ultimo = max(scelte, default=evento)
        rete = cls()
        if calcolo is not None:
            rete._calcolo = calcolo
        bersaglio, neuroni_base, esclusi, visti = None, 0, [], -1
        for e, (frase, origine, quando, tipo, nuovi, piatti, ritirati, *resto) in enumerate(cls._record(cartella)):
            if e > ultimo:
                break
            visti = e
            if e == evento:
                bersaglio, neuroni_base = [frase, origine, quando, tipo, nuovi, piatti, ritirati, *resto], len(rete.nomi)
            for nome in nuovi:
                rete.nuovo(nome)  # sempre: i numeri dei neuroni restano quelli della storia vera
            rete._confermati = len(rete.nomi)
            dentro = e < evento or e in scelte
            if e in scelte:
                if tipo != "scrittura":
                    raise ValueError(f"l'evento {e} è un rifiuto: non ha cambiato niente, non è una rettifica")
                definizioni = {rete._id(x) for x in ("regola", "segnale", "circuito", "termine")} - {-1}
                if any(t in definizioni for t, _ in piatti) or any(rete.f_tipo[f] in definizioni for f in ritirati):
                    if not salta_definizioni:
                        raise ValueError(f"l'evento {e} cambia una regola o un circuito: una rettifica non può, le regole restano quelle di allora")
                    dentro = False
                    esclusi.append({"evento": e, "perche": "cambia una regola o un circuito"})
            rete._evento_aperto = True
            if dentro:
                for f in ritirati:
                    if rete.f_morto[f] >= 0:
                        raise ScenarioIncompleto(
                            f"scenario incompleto: l'evento {e} ritira un fatto scritto dall'evento {rete.f_nato[f]}, "
                            "che non è né nella base né tra le rettifiche indicate")
                    rete._ritira(f, e)
            for t, lg in piatti:
                f = rete._fatto(t, list(zip(lg[0::2], lg[1::2])), e)
                if not dentro:
                    rete._ritira(f, e)  # il fatto prende il suo numero ma nello scenario non c'è
                    rete._da_parte.add(f)
            rete._chiudi_evento()
            rete._evento_aperto = False
            rete.e_frase.append(frase)
            rete.e_origine.append(_un_testo(origine))
            rete.e_quando.append(quando)
            prima = rete.e_scritto[-1] if len(rete.e_scritto) else quando
            rete.e_scritto.append(resto[1] if len(resto) > 1 else max(prima, quando))
            rete.e_tipo.append(_un_testo(tipo))
        if bersaglio is None or visti < ultimo:
            raise ValueError(f"nel giornale non c'è l'evento {evento if bersaglio is None else ultimo}")
        rete._riesame_di = evento
        rete._compila_regole()
        return rete, bersaglio, neuroni_base, esclusi

    def rifai_decisione(self, record: list, proposta: dict | None = None, neuroni_base: int | None = None, ammissione: bool = False) -> dict:
        """Rifà la decisione di un record del giornale su questa rete, che deve essere la sua base (``com_era``):
        applica la proposta una volta sola in uno stato provvisorio e rivaluta le regole di allora. Per una
        scrittura accettata la proposta è il record stesso; per un rifiuto è quella conservata nella ricevuta.
        Restituisce la decisione ricalcolata e l'esito. La rete, dopo, non va più usata come base.

        Con ``neuroni_base`` la rete è uno ``scenario``: i neuroni della storia vera ci sono già, e quelli che una
        proposta rifiutata aveva fatto nascere, poi dimenticati, rinascono in fondo con un numero nuovo. Se la
        proposta ritira un fatto che nello scenario non è più in vigore, l'esito è «non_applicabile».

        La decisione rifatta porta, accanto a ogni versione, anche l'evento di nascita della definizione
        (``versione_di_nascita``): serve a chi la deve confrontare con una ricevuta scritta in una forma di prima.

        Rifare non è riammettere: di solito la richiesta si rilegge con la semantica di quando è stata decisa. Con
        ``ammissione`` no: è un'ipotesi su una base diversa da quella di allora, e le si chiede quello che si
        chiederebbe a una scrittura di oggi. Se oggi non entrerebbe l'esito è «non_applicabile», con il perché.
        """
        rete = self
        _, _, quando, tipo, nuovi, piatti, ritirati, *resto = record
        confermati_dal_record = len(nuovi)  # i nomi che l'evento ha scritto davvero nel giornale
        scritto = resto[1] if len(resto) > 1 else quando
        if tipo == "rifiuto":
            if proposta is None:
                raise ValueError("un rifiuto si rifà solo con la proposta conservata nella ricevuta")
            nuovi, piatti, ritirati = proposta["nuovi"], proposta["fatti"], proposta["ritira"]
        if neuroni_base is None:
            for nome in nuovi:
                rete.nuovo(nome)
        else:
            if tipo == "rifiuto":
                # I neuroni che il no ha dimenticato rinascono in fondo: i loro numeri di allora sono di altri. Quelli
                # che il record del no ha scritto nel giornale, perché chi chiamava li aveva già nominati, sono nella
                # storia con il loro numero: restano quelli, altrimenti l'ipotesi parlerebbe di un altro cliente.
                primo_dimenticato = neuroni_base + confermati_dal_record
                rinati = [rete.nuovo(nome) for nome in nuovi[confermati_dal_record:]]
                posto = lambda x: rinati[x - primo_dimenticato] if x >= primo_dimenticato else x
                piatti = [[posto(t), [posto(x) for x in lg]] for t, lg in piatti]
            mancano = [f for f in ritirati if rete.f_morto[f] >= 0]
            if mancano:
                return {"decisione": None, "esito": "non_applicabile",
                        "spiegazione": "la richiesta ritira un fatto che in questo scenario non è più in vigore: " + ", ".join(f"fatto:{f}" for f in mancano)}
        fatti = [(t, list(zip(lg[0::2], lg[1::2]))) for t, lg in piatti]
        rete._con_nascita = True
        rete._ammissione = ammissione  # di solito la decisione è già stata presa: si rilegge, non si riammette
        try:
            esito = rete.racconta("verifica", "verifica", fatti, ritirati, prova=True, quando=quando, scritto=scritto)
        except ValueError as e:  # un valore che non si può sommare, una definizione che non sta in piedi, un fatto senza legami
            if not ammissione:
                raise
            return {"decisione": None, "esito": "non_applicabile", "spiegazione": f"su questa base la richiesta oggi non entrerebbe: {e}"}
        finally:
            rete._con_nascita = False
            rete._ammissione = True
        return {"decisione": esito["decisione"], "esito": "rifiuto" if esito["regole_scattate"] else "scrittura"}

    def istantanea(self) -> None:
        """Salva la rete com'è in memoria, per riaprire senza rileggere tutto il giornale."""
        if self._rotta:
            raise RuntimeError(f"questa rete non salva istantanee finché non viene riaperta: {self._rotta}")
        if self._decidendo:
            raise RuntimeError("questa rete sta decidendo una scrittura: i fatti proposti possono ancora tornare indietro, e in un'istantanea non ci vanno")
        if self._sola_lettura:
            raise RuntimeError("questa rete è aperta in sola lettura: non salva istantanee")
        if self._chiusa:
            raise RuntimeError("questa rete è stata chiusa: non salva istantanee")
        self.scarica()  # l'istantanea dice fin dove arriva il giornale: gli eventi in attesa ci devono essere
        sul_disco = self._impronta_del_giornale(self._scritti)
        if sul_disco is None or sul_disco.digest() != self._impronta.digest():
            # Il giornale sul disco non è più quello che questa rete ha riletto e scritto: un byte si è rovinato, o
            # qualcuno lo ha cambiato. La memoria è ancora quella giusta, ma un'istantanea adesso certificherebbe un
            # giornale che da solo non si riapre più, e ogni scrittura in più finirebbe in coda a un giornale guasto.
            self._rotta = self._rotta or ("il giornale sul disco non è più quello che questa rete ha riletto e scritto: "
                                          "va ripristinato da una copia")
            raise GiornaleGuasto(f"non salvo l'istantanea: {self._rotta}")
        # Solo i neuroni che sono già nel giornale. Quelli nominati e non ancora scritti (una prova, un rifiuto) non
        # fanno parte di quello che dura: chi riapre li conterebbe come scritti, e i nomi nati dopo avrebbero nel
        # giornale un numero diverso da quello in memoria.
        confermati = self._confermati
        nomi, per_nome = self.nomi, self.per_nome
        if len(nomi) > confermati:
            nomi = nomi[:confermati]
            per_nome = {chiave: tenuti for chiave, omonimi in per_nome.items() if (tenuti := [i for i in omonimi if i < confermati])}
        stato = {
            # ``confermati`` è la prova che i nomi salvati sono tutti nel giornale: chi riapre si fida solo di un'istantanea che lo dice
            "fino_a": self._scritti, "confermati": len(nomi), "nomi": nomi, "per_nome": per_nome,
            # L'impronta del giornale fin dove l'istantanea arriva, presa dai byte che questa rete ha riletto o scritto:
            # chi riapre usa l'istantanea solo accanto a un giornale che ha proprio quei byte.
            "impronta": self._impronta.hexdigest(),
            "ruoli_coppia": sorted(i for i in self.ruoli_coppia if i < confermati),
            "colonne": {k: getattr(self, k).tobytes() for k in ("f_tipo", "f_inizio", "f_n", "f_nato", "f_morto", "s_ruolo", "s_a", "e_quando", "e_scritto")},
            "vivi": self.vivi, "morti": self.morti, "coppie": self.coppie, "forma": self.forma,
            "e_frase": self.e_frase, "e_origine": self.e_origine, "e_tipo": self.e_tipo, "e_nota": self.e_nota,
        }
        provvisoria = os.path.join(self.cartella, "istantanea.nuova")
        corpo = marshal.dumps(stato)
        with open(provvisoria, "wb") as f:
            f.write(corpo)
            f.write(SIGILLO + hashlib.sha256(corpo).hexdigest().encode())  # la prova che il file è quello salvato, intero
            f.flush()
            os.fsync(f.fileno())
        del corpo
        os.replace(provvisoria, os.path.join(self.cartella, "istantanea"))

    def _impronta_del_giornale(self, fino_a: int):
        """L'impronta dei primi ``fino_a`` byte del giornale come sono adesso sul disco, da continuare (un oggetto di
        ``hashlib``), oppure None se il giornale è più corto. Due giornali con la stessa impronta hanno quel pezzo
        uguale byte per byte."""
        impronta, resta = hashlib.sha256(), fino_a
        try:
            with open(os.path.join(self.cartella, "giornale"), "rb") as f:
                while resta > 0:
                    pezzo = f.read(min(resta, 1 << 20))
                    if not pezzo:
                        break
                    impronta.update(pezzo)
                    resta -= len(pezzo)
        except FileNotFoundError:
            pass
        return impronta if resta == 0 else None

    def _carica_istantanea(self, percorso: str, lunghezza_giornale: int) -> int:
        # L'istantanea è una scorciatoia: se non prova di essere intera e di essere quella salvata (un file troncato,
        # un byte cambiato da un disco o da una copia, una di prima che non porta il sigillo) non si usa e non ferma
        # niente. Si rilegge il giornale, che ha un controllo per ogni record, e chi apre per scrivere può salvarne
        # una nuova.
        try:
            coda = len(SIGILLO) + 64
            with open(percorso, "rb") as f:
                # Prima la coda: un'istantanea di prima, senza sigillo, può essere di qualche gigabyte, e leggerla
                # tutta per poi buttarla costerebbe tempo e memoria proprio alla prima apertura dopo un aggiornamento.
                lunga = f.seek(0, os.SEEK_END)
                if lunga <= coda:
                    raise ValueError("l'istantanea è troppo corta per portare il suo sigillo")
                f.seek(lunga - coda)
                if f.read(len(SIGILLO)) != SIGILLO:
                    raise ValueError("l'istantanea non porta il suo sigillo")
                impronta = f.read(64)
                # Due passate sul file, a pezzi: prima l'impronta, poi il contenuto. Tenerlo tutto in memoria per fare
                # una passata sola costerebbe, a ogni avvio, un'istantanea in più accanto alla rete che ne nasce.
                f.seek(0)
                calcolata, resta = hashlib.sha256(), lunga - coda
                while resta > 0:
                    pezzo = f.read(min(resta, 1 << 20))
                    if not pezzo:
                        raise ValueError("l'istantanea è cambiata mentre veniva letta")
                    calcolata.update(pezzo)
                    resta -= len(pezzo)
                if calcolata.hexdigest().encode() != impronta:
                    raise ValueError("l'istantanea non corrisponde al suo sigillo")
                f.seek(0)
                stato = marshal.load(f)
            if type(stato) is not dict or type(stato.get("fino_a")) is not int:
                raise ValueError("l'istantanea non è fatta come un'istantanea")
        except (OSError, EOFError, ValueError, TypeError, MemoryError):
            self.istantanea_scartata = True
            return 0
        if stato["fino_a"] > lunghezza_giornale:
            # L'istantanea è più avanti del giornale (un giornale ripristinato da una copia di prima, per dire): non
            # ci si fida, si rilegge tutto, e chi apre per scrivere ne salva una nuova.
            self.istantanea_scartata = True
            return 0
        sul_disco = self._impronta_del_giornale(stato["fino_a"])
        if sul_disco is None or stato.get("impronta") != sul_disco.hexdigest():
            # Non è l'istantanea di questo giornale, o non lo dice (quelle di prima non portano l'impronta). Può essere
            # di una copia con lo stesso inizio e un seguito diverso, lunga uguale: mostrerebbe eventi che qui non ci
            # sono. Non si usa: si rilegge il giornale, e chi apre per scrivere può salvarne una nuova.
            self.istantanea_scartata = True
            return 0
        if stato.get("confermati") != len(stato["nomi"]):
            # Un'istantanea di prima: poteva contenere un nome nato in una prova e mai scritto. Contarlo come scritto
            # sposterebbe i numeri dei neuroni che nascono dopo, e il giornale non si rileggerebbe più uguale. Non si può
            # sapere senza rileggere il giornale: si rilegge, e chi apre può salvarne una nuova.
            self.istantanea_scartata = True
            return 0
        self.nomi = stato["nomi"]
        self.per_nome = stato["per_nome"]
        self._confermati = len(self.nomi)
        self.ruoli_coppia = set(stato["ruoli_coppia"])
        self._t_regola, self._t_segnale = self._id("regola"), self._id("segnale")
        self._r_da, self._r_a = self._id("vale da"), self._id("vale fino a")
        for k, v in stato["colonne"].items():
            getattr(self, k).frombytes(v)
        if "e_scritto" not in stato["colonne"]:  # le istantanee di prima: vale l'istante di arrivo, tenuto in ordine
            self.e_scritto = array("d")
            for quando in self.e_quando:
                self.e_scritto.append(max(self.e_scritto[-1], quando) if len(self.e_scritto) else quando)
        self.vivi, self.morti, self.coppie, self.forma = stato["vivi"], stato["morti"], stato["coppie"], stato["forma"]
        # un'istantanea salvata dopo una rilettura del giornale fatta da una versione di prima ha una copia dell'origine
        # e del tipo per ogni evento: qui tornano un testo solo per valore, e le copie si liberano
        self.e_frase = stato["e_frase"]
        self.e_origine = [_un_testo(x) for x in stato["e_origine"]]
        self.e_tipo = [_un_testo(x) for x in stato["e_tipo"]]
        self.e_nota = stato.get("e_nota", {})  # le istantanee più vecchie non l'hanno
        # Il giornale ha proprio i byte che chi ha salvato aveva riletto o scritto: l'impronta continua da lì.
        self._impronta = sul_disco
        return stato["fino_a"]

    @staticmethod
    def segno(cartella: str) -> dict | None:
        """Il segno durevole del giornale, il file «formato» accanto al giornale. Per esempio
        ``{"formato": 2, "dal": N, "calcoli": [[4, M]], "calcolo_dal": D}``.

        - ``formato``, ``dal``: dall'evento N ogni evento porta la sua decisione, in quel formato.
        - ``calcoli``: coppie di modo ed evento da cui vale. Le scrive chi scrive nel giornale quando il suo modo non
          è l'ultimo dichiarato; la 0.9.5 le scriveva aprendo; chi conosce la storia di un tratto le può scrivere a mano.
        - ``calcolo_dal`` e ``dichiarano_da``: gli eventi da cui ha cominciato a scrivere un programma che dichiara il
          modo in ogni decisione. Quello che ``calcoli`` diceva prima non copre le decisioni scritte dopo senza modo.
        - ``attestati``: tratti di cui qualcuno ha attestato il modo con ``Rete.attesta``, legati al contenuto del
          giornale: ``{"modo", "da", "a", "byte", "sha256", "fonte", "quando"}``.

        Lo scrive chi scrive nel giornale, prima del primo evento che scrive: chi apre per leggere non lo tocca.
        None se il giornale non ce l'ha: finora ci ha scritto solo un programma di prima. Se il file c'è ma non si
        legge, o non ha questa forma, solleva SegnoGuasto: un segno guasto non è un segno assente.
        """
        try:
            with open(os.path.join(cartella, "formato"), "rb") as f:
                dati = f.read()
        except (FileNotFoundError, NotADirectoryError):
            return None
        except OSError as e:
            raise SegnoGuasto(f"il segno del giornale (il file «formato») c'è ma non si riesce a leggere: {e.strerror or e}") from e
        try:
            segno = json.loads(dati)
        except ValueError:
            segno = None
        numero = lambda x: type(x) is int and x >= 0
        in_ordine = lambda fila: all(a <= b for a, b in zip(fila, fila[1:]))
        buono = isinstance(segno, dict) and type(segno.get("formato")) is int and numero(segno.get("dal"))
        if buono:
            calcoli, da, attestati = segno.get("calcoli", []), segno.get("dichiarano_da", []), segno.get("attestati", [])
            buono = (isinstance(calcoli, list) and all(isinstance(c, list) and len(c) == 2 and type(c[0]) is int and numero(c[1]) for c in calcoli)
                     and in_ordine([c[1] for c in calcoli])
                     and ("calcolo_dal" not in segno or numero(segno["calcolo_dal"]))
                     and isinstance(da, list) and all(numero(x) for x in da) and in_ordine(da)
                     and isinstance(attestati, list) and all(
                         isinstance(x, dict) and type(x.get("modo")) is int and numero(x.get("da")) and numero(x.get("a")) and x["da"] < x["a"]
                         and numero(x.get("byte")) and isinstance(x.get("sha256"), str)
                         # senza la fonte restano un tratto e un'impronta: dicono quali byte, non chi lo sa e da dove
                         and isinstance(x.get("fonte"), str) and x["fonte"].strip() for x in attestati)
                     # i tratti attestati stanno in fila e non si sovrappongono: ogni evento ne ha al più uno
                     and all(x["a"] <= y["da"] for x, y in zip(attestati, attestati[1:])))
        if not buono:
            raise SegnoGuasto("il segno del giornale (il file «formato») c'è ma non si legge, o non ha la forma che deve avere: va ripristinato "
                              "da una copia. Non lo sostituisco con uno nuovo, perché la frontiera che conteneva non si può dedurre dal giornale")
        return segno

    @classmethod
    def calcolo_di(cls, segno: dict | None, evento: int, decisione: dict | None = None,
                   cartella: str | None = None) -> tuple[str, tuple[int, ...], str | None, str | None]:
        """Con quale modo è stata presa la decisione di un evento, per quello che il giornale dichiara. Restituisce lo
        stato, i modi con cui rifarla, il perché se serve, e da dove viene il modo.

        - «dichiarato»: è un modo solo, e non se ne provano altri, nemmeno per salvare una ricevuta che con un altro
          tornerebbe. Lo dicono due cose soltanto: la decisione stessa (``ricevuta``), che è la parola di chi l'ha
          scritta, oppure un tratto attestato (``attestazione``), che ha una fonte ed è legato al contenuto del
          giornale.
        - «non_supportato»: è dichiarato, in uno di quei due modi, un modo che questo programma non conosce. Non è
          un modo assente: non si ripiega su quelli noti.
        - «non_dichiarato»: nessuno dei due lo dice. Tutti i modi noti sono possibili; chi verifica dice con quali la
          ricevuta coincide, senza attribuirla a uno.

        Una riga di ``calcoli`` nel segno non dichiara niente: la lasciava la 0.9.5 anche solo aprendo un giornale, e
        scritta a mano non porta né una fonte né un legame con il giornale. Resta nel segno come indizio per chi
        legge, e non restringe i modi possibili.

        Con ``cartella`` un tratto attestato si controlla contro il giornale: se il giornale non è quello per cui è
        stato attestato solleva SegnoGuasto.
        """
        conosciuto = lambda modo: type(modo) is int and modo in CALCOLI
        ignoto = lambda modo, dove: ("non_supportato", (), f"{dove} dichiara il modo di calcolo {modo!r}, che questo programma non conosce: "
                                                           "per rifare questa decisione serve un programma che lo conosca", None)
        if isinstance(decisione, dict) and "calcolo" in decisione:
            modo = decisione["calcolo"]
            return ("dichiarato", (modo,), None, "ricevuta") if conosciuto(modo) else ignoto(modo, "la decisione")
        for tratto in (segno or {}).get("attestati", ()):
            if tratto["da"] <= evento < tratto["a"]:
                if cartella is not None and cls.prefisso(cartella, tratto["a"]) != (tratto["byte"], tratto["sha256"]):
                    raise SegnoGuasto(f"il segno attesta il modo di calcolo degli eventi da {tratto['da']} a {tratto['a'] - 1}, ma di un giornale "
                                      "con un altro contenuto: non è il segno di questo giornale")
                modo = tratto["modo"]
                return ("dichiarato", (modo,), None, "attestazione") if conosciuto(modo) else ignoto(modo, "l'attestazione nel segno")
        return "non_dichiarato", TUTTI_I_CALCOLI, ("né la decisione né un'attestazione dicono con quale modo di calcolo è stata presa. "
                                                   "Può averla scritta un programma più vecchio, che non lo dichiarava: il giornale non dice quale"), None

    @staticmethod
    def prefisso(cartella: str, eventi: int) -> tuple[int, str]:
        """L'identità dei primi ``eventi`` eventi del giornale: quanti byte occupano, e la loro impronta. Due giornali
        con la stessa identità hanno quegli eventi uguali byte per byte. Non dice chi li ha scritti."""
        impronta, letti, contati = hashlib.sha256(), 0, 0
        with open(os.path.join(cartella, "giornale"), "rb") as f:
            while contati < eventi:
                testa = f.read(8)
                if len(testa) < 8:
                    break
                lunghezza, crc = struct.unpack("<II", testa)
                corpo = f.read(lunghezza) if lunghezza <= MASSIMO_RECORD else b""
                if not record_buono(lunghezza, crc, corpo):
                    break
                impronta.update(testa)
                impronta.update(corpo)
                letti += 8 + lunghezza
                contati += 1
        if contati < eventi:
            raise ValueError(f"il giornale ha solo {contati} eventi")
        return letti, impronta.hexdigest()

    def eventi_nel_giornale(self) -> int:
        """Quanti eventi interi ha il giornale sul disco adesso: quelli che questa rete ha letto o scritto, più quelli
        che un altro programma ha aggiunto dopo. È la lunghezza vera, non la testa di chi ha letto tempo fa."""
        quanti = len(self.e_frase)
        if not self.cartella:
            return quanti
        if self._rotta:  # quello che questa rete ha in memoria non fa più testo: si conta il giornale
            return sum(1 for _ in self._record(self.cartella))
        self.scarica()
        with open(os.path.join(self.cartella, "giornale"), "rb") as f:
            f.seek(self._scritti)
            while True:
                testa = f.read(8)
                if len(testa) < 8:
                    return quanti
                lunghezza, crc = struct.unpack("<II", testa)
                corpo = f.read(lunghezza) if lunghezza <= MASSIMO_RECORD else b""
                if not record_buono(lunghezza, crc, corpo):
                    return quanti
                quanti += 1

    @classmethod
    def attesta(cls, cartella: str, modo: int, da: int, a: int, fonte: str) -> dict:
        """Scrive nel segno del giornale che gli eventi da ``da`` ad ``a`` escluso sono stati decisi con il modo
        ``modo``, per chi lo sa da una fonte che non è il giornale: il registro degli aggiornamenti, per esempio. Il
        giornale da solo non può dirlo, e questa funzione non lo deduce: registra la parola di chi attesta, con la
        fonte, e la lega al contenuto del giornale fino ad ``a``, così non vale per un altro giornale.

        Va chiamata a giornale fermo. Rifiuta un tratto che contiene una decisione che dichiara già il suo modo, uno
        che si sovrappone a un tratto già attestato, e un giornale senza segno o con il segno guasto. Non tocca il
        giornale e non riscrive nessuna ricevuta. Restituisce il tratto scritto.
        """
        if type(modo) is not int or modo not in CALCOLI:
            raise ValueError(f"il modo {modo!r} non è tra quelli noti: {', '.join(str(m) for m in sorted(CALCOLI))}")
        if not (type(da) is int and type(a) is int and 0 <= da < a):
            raise ValueError("il tratto va da un evento a un altro che viene dopo, escluso")
        if not isinstance(fonte, str) or not fonte.strip():
            raise ValueError("serve la fonte: da dove si sa chi ha scritto questo tratto")
        # A giornale fermo, da prima di leggere il segno a dopo averlo riscritto: se un programma lo ha aperto per
        # scrivere, l'attestazione legherebbe il tratto a un giornale che sta cambiando, e il segno letto qui potrebbe
        # non essere più quello che c'è quando lo si sostituisce.
        try:
            fermo = os.open(os.path.join(cartella, "giornale"), os.O_RDONLY)
        except OSError as e:
            raise ValueError(f"il giornale non si apre: {e.strerror or e}") from None
        try:
            try:
                fcntl.flock(fermo, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise ValueError("il giornale è aperto da un programma che scrive: un tratto si attesta a giornale fermo") from None
            return cls._attesta_a_giornale_fermo(cartella, modo, da, a, fonte)
        finally:
            os.close(fermo)

    @classmethod
    def _attesta_a_giornale_fermo(cls, cartella: str, modo: int, da: int, a: int, fonte: str) -> dict:
        segno = cls.segno(cartella)
        if segno is None:
            raise ValueError("questo giornale non ha il segno: finora ci ha scritto solo un programma di prima, e non c'è dove attestare")
        quanti = sum(1 for _ in cls._record(cartella))
        if cls._fin_dove(segno) > quanti:
            raise SegnoGuasto(f"il segno del giornale parla dell'evento {cls._fin_dove(segno)}, ma il giornale ne ha {quanti}: non è il "
                              "segno di questo giornale, o il giornale ha perso degli eventi. Non ci aggiungo un'attestazione")
        byte, impronta = cls.prefisso(cartella, a)  # solleva se il giornale non arriva fin lì
        for e, record in enumerate(cls._record(cartella)):
            if e >= a:
                break
            nota = record[7] if len(record) > 7 else None
            ricevuta = nota.get("ricevuta") if isinstance(nota, dict) else None
            decisione = ricevuta.get("decisione") if isinstance(ricevuta, dict) else None
            if e >= da and isinstance(decisione, dict) and "calcolo" in decisione:
                raise ValueError(f"l'evento {e} dichiara già il suo modo di calcolo ({decisione['calcolo']!r}): non c'è niente da attestare")
        tratto = {"modo": modo, "da": da, "a": a, "byte": byte, "sha256": impronta, "fonte": " ".join(fonte.split()),
                  "quando": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        attestati = sorted([*segno.get("attestati", ()), tratto], key=lambda x: x["da"])
        if any(x["a"] > y["da"] for x, y in zip(attestati, attestati[1:])):
            raise ValueError("il tratto si sovrappone a uno già attestato")
        nuovo = {**segno, "attestati": attestati}
        provvisorio = os.path.join(cartella, "formato.nuovo")
        with open(provvisorio, "w") as f:
            json.dump(nuovo, f)  # in ASCII, come lo scrive il motore: si rilegge uguale su qualunque macchina
            f.flush()
            os.fsync(f.fileno())
        os.replace(provvisorio, os.path.join(cartella, "formato"))
        return tratto

    def chiudi(self) -> None:
        """Chiude il giornale. Da qui la rete non scrive più e non salva istantanee: senza giornale confermerebbe
        scritture che non restano da nessuna parte. Quello che ha in memoria si può ancora leggere."""
        if self.cartella:
            self._chiusa = True
        if self._fd >= 0:
            try:
                if not self._rotta:
                    self.scarica()
                os.fsync(self._fd)
            finally:  # il giornale si chiude anche se l'ultimo gruppo non è arrivato sul disco
                os.close(self._fd)
                self._fd = -1

    def conteggi(self) -> dict:
        in_vigore = sum(1 for m in self.f_morto if m < 0)
        return {"neuroni": len(self.nomi), "fatti": len(self.f_tipo), "fatti_in_vigore": in_vigore,
                "sinapsi": len(self.s_a), "eventi": len(self.e_frase)}
