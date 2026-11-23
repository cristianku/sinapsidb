"""La memoria di SinapsiDB: la rete vera, quella a cui si racconta e a cui si chiede.

È tenuta separata dalla rete di prova del confronto con PostgreSQL. La scrive un solo processo, il
server MCP, attraverso ``racconta``; ``chiedi`` la legge con la lingua di SinapsiDB (vedi ``lingua``).

``racconta`` riceve la frase così com'è stata detta e la sua struttura: uno o più fatti, ognuno con un
tipo e con i partecipanti nei loro ruoli. Chi capisce la frase (un modello linguistico, un programma)
sta fuori: qui si controlla la proposta, si risolvono i nomi e si scrive tutto o niente. La risposta è
una ricevuta che dice che cosa è successo:

- ``applicato``: i fatti sono stati scritti; la ricevuta elenca i fatti creati, quelli ritirati (il
  «prima» di una correzione) e i neuroni, i tipi e i ruoli che sono nati con questo racconto;
- ``nessun_cambiamento``: quei fatti c'erano già, identici;
- ``da_chiarire``: serve una scelta di chi racconta, e non si scrive niente. Succede quando un nome
  indica più cose, quando una correzione nomina un tipo o un ruolo che non c'è, o quando gli indizi di
  una correzione trovano più fatti;
- ``conflitto``: la chiave contro i doppioni è già stata usata per un racconto diverso, oppure i fatti
  da sostituire non sono più quelli attesi;
- ``violazione_regola``: una regola della rete ha vietato la scrittura, con la spiegazione;
- ``prova``: era una prova; dice che cosa sarebbe successo, senza lasciare traccia nella rete.

Niente si cancella: una correzione ritira il fatto di prima e scrive quello nuovo nello stesso evento,
e il fatto ritirato resta nella rete con la sua frase.

Gli identificativi sono gli stessi per raccontare e per chiedere: ``neurone:17`` al posto di un nome,
``fatto:51`` per un fatto, ``evento:105`` per un evento.
"""

from __future__ import annotations

import bisect
import copy
import difflib
import hashlib
import json
import os
import re
import struct
import threading
import time
import math
import zlib
from collections import Counter
from pathlib import Path
from typing import Any

from sinapsidb import lingua
from sinapsidb.motore import (FORMATO, MASSIMO_RECORD, DefinizioneNonValida, NonNumerico, Rete, ScenarioIncompleto, SegnoGuasto, Vietato,
                              da_unita, istante as istante_di, piana, record_buono, testo_semplice)

MASSIMO_FATTI = 20  # per racconto
MASSIMO_LEGAMI = 16  # per fatto
MASSIMO_NOME = 200  # caratteri di un nome, di un tipo o di un ruolo
MASSIMO_FRASE = 2000
ID_NEURONE = lingua.IDENTIFICATIVO
ID_FATTO = re.compile(r"fatto:(\d+)")


class Rifiutato(ValueError):
    """Il racconto non sta in piedi (manca la frase, un fatto è vuoto, un nome è troppo lungo). ``args[0]`` lo spiega."""


class SoloLettura(RuntimeError):
    """Le scritture non sono accese su questa memoria."""


class NomeAssente(ValueError):
    """Nessun neurone ha questo nome. ``nome`` è il nome chiesto, ``che`` dice per che cosa era chiesto («il nome»,
    «il circuito»): chi risponde «zero» a un'entità mai vista lo fa solo per l'entità, non per un circuito che manca."""

    def __init__(self, messaggio: str, nome: str, che: str) -> None:
        super().__init__(messaggio)
        self.nome, self.che = nome, che


class NomeAmbiguo(ValueError):
    """Il nome indica più neuroni: bisogna dire quale, con il suo identificativo. ``candidati`` li elenca
    (``neurone:17``). Non è un nome assente: non vale «zero», e non si sceglie al posto di chi chiede."""

    def __init__(self, messaggio: str, nome: str, candidati: list[str]) -> None:
        super().__init__(messaggio)
        self.nome, self.candidati = nome, candidati


class Memoria:
    """La rete vera e le due cose che ci si possono fare: raccontare e chiedere. Un'operazione alla volta."""

    def __init__(self, cartella: Path | str, scritture: bool = False) -> None:
        self.cartella = Path(cartella)
        self.scritture = scritture
        self._lock = threading.Lock()
        # Chi apre per leggere non tocca il giornale: nemmeno per tagliarne la coda, che può essere un record che chi
        # scrive sta ancora scrivendo.
        self.rete = Rete(str(self.cartella), sync="frase", decisioni=True, sola_lettura=not scritture)
        try:
            # Le chiavi contro i doppioni stanno nel giornale, nello stesso record dell'evento che hanno scritto:
            # al riavvio si ritrovano insieme ai fatti, e una richiesta ripetuta riceve la ricevuta di allora.
            self._chiavi: dict[str, tuple[str, dict]] = {}
            # Le chiavi che nel giornale ci sono, ma con la nota rovinata (senza l'impronta del racconto, o senza la
            # ricevuta): non impediscono di aprire, ma non sono chiavi assenti. Chi le ripete non viene valutato di
            # nuovo, perché scriverebbe una seconda volta: gli si dice che la nota di allora è guasta.
            self._chiavi_guaste: dict[Any, int] = {}
            for evento, nota in self.rete.e_nota.items():
                if isinstance(nota, dict) and nota.get("chiave") is not None:
                    chiave = nota["chiave"]
                    if not isinstance(chiave, (str, int, float)) or isinstance(chiave, bool):
                        continue  # non è una chiave che qualcuno possa ripetere: la verifica di quell'evento dice che è guasto
                    if self._nota_con_chiave_intera(nota):
                        self._chiavi[chiave] = (nota["impronta"], nota["ricevuta"])
                    else:
                        self._chiavi_guaste[chiave] = evento
            # Le dipendenze delle decisioni: per ogni entità di cui una decisione ha letto qualcosa, gli istanti a cui lo ha
            # letto e da quale decisione. Serve a trovare le decisioni che una rettifica può toccare. Si tiene per entità e
            # non per circuito, perché la definizione di un circuito può cambiare dopo: quello che una decisione ha letto di
            # un'entità passa sempre da un fatto in cui quell'entità partecipa. Si ricava dalle ricevute, quando serve.
            self._dipendenze: dict[int, list[tuple[float, int]]] = {}
            self._dipendenze_fino_a = 0
            self._senza_letture: list[int] = []  # eventi che non hanno una ricevuta con le letture: lì la ricerca non può vedere
            # i tratti attestati nel segno già controllati contro il giornale: il giornale si allunga soltanto, quello che
            # un tratto copre non cambia più
            self._tratti_controllati: set[tuple] = set()
            self._eventi_contati: tuple[tuple, int, bool] | None = None  # il giornale com'era, quanti eventi contati, se sono tutti
        except BaseException:
            self.rete.chiudi()  # non si è aperta: il giornale non resta preso da una memoria che nessuno ha
            raise

    @staticmethod
    def _nota_con_chiave_intera(nota: dict) -> bool:
        """Se la nota di un evento scritto con una chiave ha quello che serve a ritrovarla: l'impronta del racconto e
        la ricevuta."""
        return isinstance(nota.get("impronta"), str) and isinstance(nota.get("ricevuta"), dict)

    def _viva(self) -> None:
        """Si chiama prima di rispondere a qualunque cosa che legga la rete in memoria. Se una scrittura è finita nel
        giornale e la memoria non è riuscita ad allinearsi (o viceversa), quello che questa memoria direbbe non è
        quello che c'è nel giornale: un saldo vecchio, una ricevuta che manca. Non risponde: va riaperta. Va chiamata
        tenendo ``_lock``."""
        if self.rete.memoria_guasta:
            raise RuntimeError(f"questa memoria va riaperta prima di rispondere: {self.rete.fuori_uso}")

    # --- chiedere ----------------------------------------------------------------

    def chiedi(self, domanda: str, limite: int = 50) -> dict[str, Any]:
        """Risponde a una frase della lingua di SinapsiDB. Solleva ``lingua.ErroreDomanda`` se non si capisce."""
        if len(domanda) > MASSIMO_FRASE:
            raise lingua.ErroreDomanda("la domanda è troppo lunga")
        limite = max(1, min(int(limite), 200))
        domanda, clausola = lingua.tempo(domanda)
        with self._lock:
            self._viva()
            rete = self.rete
            ultimo = f"evento:{len(rete.e_frase) - 1}" if rete.e_frase else None
            if clausola is not None and clausola["modo"] == "registrato":
                rete.lettura = ("registrato",)  # chi legge un circuito sa che qui la validità non si guarda
                try:
                    risposta = lingua.chiedi(rete, domanda, limite=limite)
                finally:
                    rete.lettura = None
                risposta["lettura"] = {"modo": "registrato", "registrato_fino_a": ultimo, "valido_al": None}
                return risposta
            if clausola is None:
                # Senza clausola si risponde con quello che vale adesso. I fatti che dichiarano un periodo e adesso non
                # valgono si mettono da parte per il tempo della domanda e poi si rimettono: niente va nel giornale.
                adesso = time.time()
                rete._porta_a(adesso)
                fuori = rete.fuori_validita(adesso)
                # Una lettura non lascia tracce: dopo, ogni fatto e ogni entità sono dov'erano, e la stessa domanda
                # dà le stesse righe nello stesso ordine, come a chi riapre dal giornale.
                da_parte = rete._metti_da_parte(fuori)
                # Una domanda ha un istante solo, quello dichiarato nella risposta: chi legge un circuito lo legge a
                # quell'istante, non all'orologio di un momento dopo.
                rete.lettura = ("valido", adesso)
                try:
                    risposta = lingua.chiedi(rete, domanda, limite=limite)
                finally:
                    rete.lettura = None
                    rete._rimetti(da_parte)
                risposta["lettura"] = {"modo": "adesso", "registrato_fino_a": ultimo, "valido_al": adesso}
                return risposta
            # Una lettura del passato si fa su una copia della rete rifatta dal giornale: quello che era registrato
            # fino a un certo evento e, tra quello, i fatti che valgono a un certo istante.
            quanti = len(self.rete.e_frase)
            valido_al = clausola.get("istante")
            if clausola.get("adesso"):
                valido_al = time.time()
            if clausola["modo"] == "risultava":
                if "evento" in clausola:
                    if clausola["evento"] >= quanti:
                        raise lingua.ErroreDomanda(f"l'evento {clausola['evento']} non c'è ancora: l'ultimo è il {quanti - 1}")
                    quanti = clausola["evento"] + 1
                else:  # registrato a quell'istante: gli eventi scritti non dopo. L'istante di scrittura non torna mai indietro
                    quanti = bisect.bisect_right(self.rete.e_scritto, valido_al)
            vista = Rete.vista(str(self.cartella), quanti, valido_al)
            if not vista.forma:
                risposta = {"domanda": " ".join(domanda.split()), "colonne": [], "righe": [], "quante": 0, "troncata": False,
                            "piano": ["A quel punto la rete era vuota."], "preparazione_us": None, "esecuzione_us": None, "fatti_scattati": []}
            else:
                try:
                    risposta = lingua.chiedi(vista, domanda, limite=limite)
                except lingua.Ambigua:
                    raise  # allora quel nome indicava più cose: la domanda va precisata, non è «allora non c'era niente»
                except lingua.ErroreDomanda:
                    # Un tipo o un ruolo che oggi c'è e allora non c'era ancora: non è una domanda sbagliata, allora
                    # non c'era niente da trovare. Se la domanda non sta in piedi nemmeno oggi, l'errore resta.
                    lingua.prepara(self.rete, domanda)
                    risposta = {"domanda": " ".join(domanda.split()), "colonne": [], "righe": [], "quante": 0, "troncata": False,
                                "piano": ["A quel punto nella rete non c'era ancora niente di quello che la domanda cerca."],
                                "preparazione_us": None, "esecuzione_us": None, "fatti_scattati": []}
            risposta["lettura"] = {"modo": clausola["modo"], "registrato_fino_a": f"evento:{quanti - 1}" if quanti else None,
                                   "valido_al": None if valido_al is None else valido_al}
            return risposta

    def conteggi(self) -> dict[str, int]:  # quanti sono in memoria: lo si può chiedere anche a una memoria da riaprire
        with self._lock:
            return self.rete.conteggi()

    def ricevuta_di(self, chiave: str) -> dict[str, Any] | None:
        """La ricevuta durevole di una richiesta fatta con quella chiave, oppure None se non c'è. Non scrive niente:
        serve a chi ha perso una conferma e, dopo una riapertura, vuole sapere se la sua richiesta è entrata prima di
        ripeterla."""
        with self._lock:
            if self.rete.fuori_uso:
                # Una scrittura è rimasta in sospeso sul disco: se è entrata lo dice solo il giornale, dopo la riapertura.
                # Rispondere «assente» adesso potrebbe essere smentito un attimo dopo.
                raise RuntimeError(f"questa memoria va riaperta prima di sapere se una richiesta è entrata: {self.rete.fuori_uso}")
            if chiave in self._chiavi_guaste:
                # Nel giornale la chiave c'è, ma la sua nota è rovinata: «assente» sarebbe smentito dal giornale.
                raise ValueError(f"la chiave è nel giornale, all'evento {self._chiavi_guaste[chiave]}, ma la nota che la porta è rovinata: "
                                 "la ricevuta di allora non si può dare. Che cosa non va lo dice la verifica di quell'evento")
            trovata = self._chiavi.get(chiave)
            return None if trovata is None else copy.deepcopy(trovata[1])  # una copia: quella custodita non si tocca

    # --- verificare ----------------------------------------------------------------

    def verifica(self, evento: int | str) -> dict[str, Any]:
        """Rifà la decisione di un evento e la confronta con la sua ricevuta.

        Ricostruisce dal solo giornale la rete com'era prima dell'evento, applica la richiesta una volta sola in
        uno stato provvisorio, rivaluta le regole di allora e confronta regole consultate, letture, valori ed esito
        con quelli che la ricevuta dichiara. Trova anche quello che nella ricevuta manca. Non scrive niente.

        Le consultazioni si confrontano per identificativo della regola e dell'entità, non per nome, e una per una:
        due entità con lo stesso nome restano due. Si confrontano anche l'evento e l'esito scritti nella ricevuta,
        che sono quelli che riceve chi ripete la richiesta.

        Che cosa non controlla: per una scrittura accettata riapplica i fatti e i ritiri già risolti che stanno nel
        record, non rifà la scelta che la richiesta aveva fatto, per esempio quali fatti trovavano gli indizi di una
        sostituzione. E usa lo stesso valutatore del motore: non è un verificatore indipendente.
        """
        n = int(str(evento).removeprefix("evento:"))
        with self._lock:
            self._viva()
            # Tutto quello che si confronta viene dal giornale letto adesso: il record dell'evento e la rete com'era
            # prima. La copia che questa memoria ha in RAM potrebbe venire da un'istantanea, e non fa testo.
            if not 0 <= n < len(self.rete.e_frase):
                raise ValueError(f"l'evento {n} non c'è")
            rete, record = Rete.com_era(str(self.cartella), n)
            if record is None:
                raise ValueError(f"l'evento {n} non è nel giornale")
            esito = {"evento": f"evento:{n}", "tipo": record[3]}
            nota = record[7] if len(record) > 7 else None
            dichiarata = nota["ricevuta"].get("decisione") if isinstance(nota, dict) and isinstance(nota.get("ricevuta"), dict) else None
            try:
                segno = self._leggi_segno()
            except SegnoGuasto as guasto:  # senza il segno non si sa che cosa il giornale dichiarava: non si indovina
                return {**esito, "esito": "non_verificabile", "differenze": [], "spiegazione": str(guasto)}
            if dichiarata is not None and not isinstance(dichiarata, dict):
                # Al posto della decisione c'è un'altra cosa (un testo, una lista): non è una ricevuta che manca, è una
                # ricevuta rovinata. Ogni differenza è un guasto segnalato, non un errore di chi verifica.
                return {**esito, "esito": "guasto", "differenze": ["nella ricevuta la decisione non è fatta come una decisione"]}
            dal_segno = segno is not None and n >= segno["dal"]
            if dichiarata is None:
                # Dove il giornale porta il segno durevole, da quell'evento in poi ogni evento deve avere la sua decisione.
                # Senza il segno vale la posizione: dalla prima decisione del giornale in poi. Prima, l'evento è storico.
                prima = min((e for e, x in rete.e_nota.items() if isinstance(x, dict) and isinstance(x.get("ricevuta"), dict)
                             and x["ricevuta"].get("decisione") is not None), default=None)
                if dal_segno or (prima is not None and n > prima):
                    da = segno["dal"] if dal_segno else prima
                    return {**esito, "esito": "guasto", "differenze": [
                        f"l'evento non ha la decisione, ma in questo giornale le decisioni si registrano dall'evento {da}"]}
                return {**esito, "esito": "non_verificabile", "differenze": [],
                        "spiegazione": "questo evento è stato scritto prima che le decisioni venissero registrate"}
            if nota.get("chiave") is not None and not (self._nota_con_chiave_intera(nota) and isinstance(nota["chiave"], (str, int, float))
                                                       and not isinstance(nota["chiave"], bool)):
                # L'evento è stato scritto con una chiave, e la nota non ha più quello che serve a ritrovarla: chi
                # ripete quella richiesta non riceverebbe la ricevuta di allora. È un guasto, anche se la decisione torna.
                return {**esito, "esito": "guasto", "differenze": ["la nota porta una chiave ma non l'impronta del racconto, o la chiave non è una chiave"]}
            ricevuta = nota["ricevuta"]
            forme = self._forme_ammesse(dichiarata, segno if dal_segno else None)
            if isinstance(forme, str):
                return {**esito, "esito": "guasto", "differenze": [forme]}
            # La decisione rifatta si confronta con la ricevuta in ognuna delle forme ammesse, per intero. Una ricevuta
            # di oggi ne ammette una sola. Una scritta prima può coincidere con più di una, se non c'è niente che le
            # distingua.
            # Lo stesso vale per il modo di fare le somme dei circuiti. Se è dichiarato si rifà con quello e con
            # nessun altro, e la risposta dice chi lo dichiara: la ricevuta stessa, un tratto attestato e legato al
            # giornale, o una riga del segno. Se è dichiarato un modo che questo programma non conosce non si rifà.
            # Se non lo ha dichiarato nessuno si rifà con tutti i modi noti, e la risposta dice con quali la ricevuta
            # coincide: è una compatibilità, non dice quale programma l'ha scritta.
            try:
                stato, modi, perche, fonte = self._calcolo_di(segno, n, dichiarata)
            except SegnoGuasto as guasto:  # il tratto è attestato per un giornale con un altro contenuto
                return {**esito, "esito": "non_verificabile", "differenze": [], "spiegazione": str(guasto)}
            if stato == "non_supportato":
                return {**esito, "esito": "non_supportato", "differenze": [], "spiegazione": perche}
            try:
                prove = self._rifatte(n, rete, record, ricevuta, dichiarata, forme, modi, esito)
            except SegnoGuasto as guasto:
                return {**esito, "esito": "non_verificabile", "differenze": [], "spiegazione": str(guasto)}
            except (ValueError, KeyError, TypeError, AttributeError, IndexError, ArithmeticError) as rovinata:
                # La ricevuta c'è ma le manca qualcosa senza cui la decisione non si può rifare (la proposta di un no,
                # le regole consultate), o porta un valore che non è del suo tipo. È un guasto del giornale, e si dice.
                return {**esito, "esito": "guasto", "differenze": [
                    f"la ricevuta non basta a rifare la decisione: {rovinata if isinstance(rovinata, ValueError) else 'manca o non è leggibile ' + str(rovinata)}"]}
            buone = [p for p in prove if p["forme"]]
            scelta = buone[0] if buone else min(prove, key=lambda p: min(len(d) for d in p["confronti"].values()))
            calcolata = scelta["rifatta"]["decisione"]
            risposta = {**esito, "esito": "coerente" if buone else "guasto",
                        "differenze": [] if buone else min(scelta["confronti"].values(), key=len),
                        "regole_consultate": len(calcolata["regole"]), "letture": sum(len(r["letture"]) for r in calcolata["regole"])}
            if buone:
                risposta["forma"] = scelta["forme"]
            if stato == "dichiarato":
                risposta["calcolo"], risposta["calcolo_da"] = modi[0], fonte
                if fonte != "ricevuta":  # non è la parola di chi ha scritto la decisione: si dice di chi è
                    risposta["spiegazione"] = (
                        "il modo di calcolo non lo dichiara la decisione: lo attesta il segno del giornale per questo tratto, legato al suo contenuto"
                        if fonte == "attestazione" else
                        "il modo di calcolo non lo dichiara la decisione: lo dice una riga del segno del giornale, che non è legata al suo contenuto")
            else:
                risposta["calcoli_compatibili"] = [p["calcolo"] for p in buone]
                risposta["spiegazione"] = perche + (
                    ": la ricevuta coincide rifacendola con " + self._modi_in_parole(risposta["calcoli_compatibili"])
                    + ". Vuol dire che è compatibile, non quale programma l'ha scritta" if buone else
                    ": non coincide con nessuno dei modi con cui può essere stata scritta (" + self._modi_in_parole(modi) + ")")
            return risposta

    def segno(self) -> dict | None:
        """Il segno durevole del giornale, per chi guarda lo stato: None se non c'è ancora. Se c'è ma non si legge, o
        non è di questo giornale, lo dice invece di sollevare: chi guarda deve poterlo vedere."""
        try:
            return self._leggi_segno()
        except SegnoGuasto as guasto:
            return {"guasto": str(guasto)}

    def _leggi_segno(self) -> dict | None:
        """Il segno del giornale, letto adesso e confrontato con il giornale vero: il file che c'è adesso sul disco,
        non quanti eventi ha letto questa memoria quando si è aperta. Un segno che parla di eventi che il giornale non
        ha non è il suo, e non si usa per rendere certa una risposta."""
        segno = Rete.segno(str(self.cartella))
        if segno is None and self.rete._dichiarato:
            # Questa memoria lo ha scritto, e adesso non c'è più: non è «non c'è ancora». Come per chi scrive: senza
            # il segno non si sa che cosa il giornale dichiarava, e non si verifica facendo finta che non ci fosse.
            raise SegnoGuasto("il segno del giornale (il file «formato») non c'è più: questa memoria lo aveva scritto. Va ripristinato da una copia")
        if segno is not None:
            oltre = Rete._fin_dove(segno)
            quanti = self._eventi_nel_giornale(oltre)
            if oltre > quanti:
                raise SegnoGuasto(f"il segno del giornale parla dell'evento {oltre}, ma il giornale ne ha {quanti}: non è il segno di "
                                  "questo giornale, o il giornale ha perso degli eventi")
        return segno

    def _com_e_il_giornale(self) -> tuple:
        """Quello che distingue il file del giornale com'è adesso: se cambia, quello che se ne sapeva non vale più."""
        try:
            x = os.stat(self.cartella / "giornale")
        except OSError:
            return ()
        return (x.st_dev, x.st_ino, x.st_size, x.st_mtime_ns)

    def _eventi_nel_giornale(self, bastano: int) -> int:
        """Quanti eventi interi ha il giornale sul disco adesso, contati sul file e non sulla memoria; ci si ferma a
        ``bastano``, perché serve sapere solo se ci arriva. Si riconta ogni volta che il file cambia, dall'inizio: un
        giornale sostituito con un altro non eredita il conto di quello di prima."""
        com_e = self._com_e_il_giornale()
        if not com_e:
            return 0
        prima = self._eventi_contati
        if prima is not None and prima[0] == com_e and (prima[1] >= bastano or prima[2]):
            return prima[1]
        quanti, finito = 0, True
        with open(self.cartella / "giornale", "rb") as f:
            while True:
                if quanti >= bastano:
                    finito = False  # ce ne sono abbastanza: non serve leggere il resto
                    break
                testa = f.read(8)
                if len(testa) < 8:
                    break
                lunghezza, crc = struct.unpack("<II", testa)
                corpo = f.read(lunghezza) if lunghezza <= MASSIMO_RECORD else b""
                if not record_buono(lunghezza, crc, corpo):
                    break
                quanti += 1
        self._eventi_contati = (com_e, quanti, finito)
        return quanti

    def _calcolo_di(self, segno: dict | None, n: int, dichiarata: dict) -> tuple:
        """``Rete.calcolo_di``, controllando contro il giornale il tratto attestato da cui il modo viene. Il controllo
        si rifà ogni volta che il file del giornale cambia: un giornale scambiato con un altro, anche lungo uguale,
        non eredita l'attestazione."""
        tratto = next((x for x in (segno or {}).get("attestati", ()) if x["da"] <= n < x["a"]), None)
        chiave = (tratto["a"], tratto["byte"], tratto["sha256"], self._com_e_il_giornale()) if tratto else None
        esito = Rete.calcolo_di(segno, n, dichiarata, None if chiave in self._tratti_controllati else str(self.cartella))
        if esito[3] == "attestazione":
            self._tratti_controllati = {chiave}  # vale per il giornale com'è adesso
        return esito

    @staticmethod
    def _modi_in_parole(modi) -> str:
        return ("il modo " if len(modi) == 1 else "i modi ") + ", ".join(str(m) for m in modi)

    def _rifatte(self, n: int, base: Rete | None, record: list, ricevuta: dict, dichiarata: dict, forme: tuple, modi: tuple,
                 tipo: dict) -> list[dict]:
        """La decisione di un evento rifatta dal giornale con ognuno dei modi indicati e, per ognuno, le forme in cui
        coincide per intero con la ricevuta. ``base`` è la rete com'era prima dell'evento, se chi chiama l'ha già: si
        usa per il primo modo, poi per ogni modo la base si rifà dal giornale con le somme di quel modo."""
        prove = []
        for calcolo in modi:
            if base is None:
                base, _ = Rete.com_era(str(self.cartella), n, calcolo)
            else:
                base._calcolo = calcolo
                base._compila_regole()
            rifatta = base.rifai_decisione(record, dichiarata.get("proposta"))
            base = None  # dopo aver rifatto una decisione la rete non è più una base
            senza_fatti = not record[5] and not record[6]
            confronti = {forma: self._differenze(tipo, ricevuta, dichiarata, rifatta, forma, senza_fatti) for forma in forme}
            prove.append({"calcolo": calcolo, "rifatta": rifatta, "confronti": confronti, "forme": [f for f in forme if not confronti[f]]})
        return prove

    # Le forme in cui una ricevuta può essere scritta. «2» è quella di oggi, dichiarata nella ricevuta. Le altre sono
    # quelle che il motore ha avuto prima, quando la ricevuta non dichiarava niente: la versione di una definizione
    # era il suo evento di nascita («1a», «1b») oppure già l'ultimo che l'aveva cambiata («1c»), e le letture di
    # presenza non portavano ancora l'identificativo del tipo («1a»). Ognuna vale per la ricevuta intera.
    FORME_DI_PRIMA = ("1c", "1b", "1a")

    @classmethod
    def _forme_ammesse(cls, dichiarata: dict, segno: dict | None) -> tuple | str:
        """Le forme in cui questa ricevuta può essere letta, oppure la frase che dice perché il suo formato non va."""
        formato = dichiarata.get("formato")
        if segno is not None and formato != segno["formato"]:
            return f"la ricevuta dichiara il formato {formato or 1}, ma da questo punto del giornale le decisioni sono nel formato {segno['formato']}"
        if formato == FORMATO:
            return ("2",)
        if formato is None:
            return cls.FORME_DI_PRIMA
        return f"la ricevuta dichiara il formato {formato}, che questo motore non conosce"

    @staticmethod
    def _nella_forma(decisione: dict, forma: str) -> dict:
        """La decisione rifatta dal motore, scritta in una delle forme: una convenzione sola per tutta la ricevuta."""
        d = json.loads(json.dumps(decisione, ensure_ascii=False))
        for regole in (d["regole"], (d.get("alla_scrittura") or {}).get("regole", [])):
            for r in regole:
                nascita = r.pop("versione_di_nascita", None)
                if forma in ("1a", "1b"):
                    r["versione"] = nascita
                for y in r["letture"]:
                    nascita = y.pop("versione_di_nascita", None)
                    if forma in ("1a", "1b") and "circuito_id" in y:
                        y["versione"] = nascita
                    if forma == "1a":
                        y.pop("cosa_id", None)
        if forma != "2":
            d.pop("formato", None)
        return d

    @classmethod
    def _differenze(cls, esito: dict, ricevuta: dict, dichiarata: dict, rifatta: dict, forma: str, senza_fatti: bool = False) -> list[str]:
        """Le differenze tra una ricevuta e la decisione rifatta dal giornale, scritta in quella forma."""
        differenze = []
        calcolata = cls._nella_forma(rifatta["decisione"], forma)
        if ricevuta.get("evento") != esito["evento"]:
            differenze.append(f"la ricevuta dice di essere di «{ricevuta.get('evento')}», ma sta nel record di «{esito['evento']}»")
        atteso = rifatta["esito"]
        esiti = {"scrittura": "applicato", "rifiuto": "violazione_regola"}
        # Una richiesta con chiave che non ha cambiato niente lascia un evento scritto e senza fatti: l'esito che
        # dichiara è «nessun_cambiamento», e vale solo lì. Con dei fatti nel record sarebbe una ricevuta falsa.
        dichiarato = ricevuta.get("esito")
        vale_come = "applicato" if dichiarato == "nessun_cambiamento" and senza_fatti else dichiarato
        if vale_come != esiti.get(esito["tipo"]):
            differenze.append(f"la ricevuta dichiara l'esito «{dichiarato}», ma nel giornale l'evento è «{esito['tipo']}»")
        if vale_come != esiti[atteso]:
            differenze.append(f"la ricevuta dichiara l'esito «{dichiarato}», rifacendo la decisione è «{esiti[atteso]}»")
        for campo, che in (("formato", "il formato"), ("base", "la base"), ("istante", "l'istante")):
            if dichiarata.get(campo) != calcolata.get(campo):
                differenze.append(f"{che} nella ricevuta è {dichiarata.get(campo)}, rifacendo la decisione è {calcolata.get(campo)}")
        # il modo di calcolo si confronta solo se la ricevuta lo dichiara: quelle scritte fino alla 0.9.5 non lo portano
        if "calcolo" in dichiarata and dichiarata["calcolo"] != calcolata.get("calcolo"):
            differenze.append(f"il modo di calcolo nella ricevuta è {dichiarata['calcolo']}, la decisione è stata rifatta con il {calcolata.get('calcolo')}")

        def confronta(scritte: list, rifatte: list, dove: str) -> None:
            # Una consultazione è una regola valutata su un'entità. Si riconosce dagli identificativi, che non cambiano,
            # e si contano: due consultazioni uguali per nome ma su due entità diverse sono due.
            scritte = [r for r in scritte if isinstance(r, dict)]
            chi_e = lambda r: (r.get("regola_id"), r.get("versione"), r.get("su_id"))
            detto = lambda k, r: f"{dove}la regola «{r.get('regola')}» (versione {k[1]}) su «{r.get('su')}» (neurone:{k[2]})"
            quante_scritte, quante_rifatte = Counter(chi_e(r) for r in scritte), Counter(chi_e(r) for r in rifatte)
            una = {chi_e(r): r for r in scritte + rifatte}
            for k in sorted(quante_rifatte.keys() | quante_scritte.keys(), key=str):
                a, b = quante_scritte[k], quante_rifatte[k]
                if a < b:
                    differenze.append(f"{detto(k, una[k])} andava consultata e nella ricevuta non c'è" if a == 0 else
                                      f"{detto(k, una[k])} è stata consultata {b} volte e nella ricevuta compare {a} volte")
                elif a > b:
                    differenze.append(f"la ricevuta dichiara {detto(k, una[k])}, che rifacendo la decisione non viene consultata" if b == 0 else
                                      f"la ricevuta dichiara {a} volte {detto(k, una[k])}, che rifacendo la decisione è consultata {b} volte")
                else:
                    mie = sorted((r for r in scritte if chi_e(r) == k), key=lambda r: json.dumps(r, sort_keys=True))
                    sue = sorted((r for r in rifatte if chi_e(r) == k), key=lambda r: json.dumps(r, sort_keys=True))
                    for x, y in zip(mie, sue):
                        diversi = sorted(c for c in x.keys() | y.keys() if x.get(c) != y.get(c))
                        if diversi:
                            differenze.append(f"{detto(k, y)}: nella ricevuta sono diversi da quelli rifatti: {', '.join(diversi)}")

        confronta(dichiarata.get("regole", []), calcolata["regole"], "")
        # la valutazione all'istante di scrittura, se c'è stata, si confronta allo stesso modo
        scritta, rifatta_dopo = dichiarata.get("alla_scrittura"), calcolata.get("alla_scrittura")
        if (scritta is None) != (rifatta_dopo is None):
            differenze.append("la valutazione all'istante di scrittura c'è nella ricevuta e non rifacendo la decisione, o viceversa")
        elif scritta is not None:
            if scritta.get("istante") != rifatta_dopo["istante"] or bool(scritta.get("come_all_arrivo")) != bool(rifatta_dopo.get("come_all_arrivo")):
                differenze.append("la valutazione all'istante di scrittura nella ricevuta non è quella che si ritrova rifacendo la decisione")
            elif "regole" in rifatta_dopo or "regole" in scritta:
                confronta(scritta.get("regole", []), rifatta_dopo.get("regole", []), "alla scrittura, ")
        ultime = rifatta_dopo["regole"] if rifatta_dopo and "regole" in rifatta_dopo else calcolata["regole"]
        if "regole" in ricevuta and ricevuta["regole"] != [r for r in ultime if r["verdetto"] == "vieta"]:
            differenze.append("le regole scattate scritte nella ricevuta non sono quelle che scattano rifacendo la decisione")
        if atteso != esito["tipo"]:
            differenze.append(f"nel giornale l'evento è «{esito['tipo']}», rifacendo la decisione sarebbe «{atteso}»")
        return differenze

    # --- riesaminare -----------------------------------------------------------------

    @staticmethod
    def _letture(decisione: dict) -> list[dict]:
        """Le letture di una decisione, in fila: quelle all'arrivo e, se ci sono, quelle all'istante di scrittura."""
        fila = []
        scrittura = decisione.get("alla_scrittura") or {}
        giri = [("arrivo", decisione["regole"], None)]
        if scrittura.get("come_all_arrivo"):
            giri.append(("scrittura", decisione["regole"], scrittura["istante"]))
        elif "regole" in scrittura:
            giri.append(("scrittura", scrittura["regole"], None))
        for quando, regole, istante in giri:
            for r in regole:
                for y in r["letture"]:
                    fila.append({"quando": quando, "regola": r["regola"], "regola_id": r["regola_id"], "versione": r["versione"], "su": r["su"],
                                 "su_id": r["su_id"], "verdetto": r["verdetto"], "segnale": y["segnale"], "cosa": y["cosa"],
                                 "cosa_id": y.get("circuito_id", y.get("cosa_id")), "di": y["di"], "con_proposta": y["con_proposta"],
                                 "valido_al": y["valido_al"] if istante is None else istante, "valore": y["valore"]})
        return fila

    def riesamina(self, evento: int | str, rettifiche: list | None = None, fino_a: int | str | None = None) -> dict[str, Any]:
        """Vedi ``_riesamina``. Una ricevuta rovinata nel giornale non si riesamina, e lo si dice: non è un errore di
        chi chiede né del programma."""
        try:
            return self._riesamina(evento, rettifiche, fino_a)
        except (KeyError, TypeError, AttributeError, IndexError, OverflowError) as rovinata:
            raise ValueError(f"la ricevuta di «{evento}» non è fatta come una ricevuta ({type(rovinata).__name__}: {rovinata}): "
                             "non si può riesaminare. Che cosa non va lo dice la verifica") from None

    def _riesamina(self, evento: int | str, rettifiche: list | None = None, fino_a: int | str | None = None) -> dict[str, Any]:
        """Riesamina una decisione passata come se fossero già state note alcune rettifiche. Non scrive niente.

        ``rettifiche``: gli eventi da includere, scritti dopo la decisione, indicati uno per uno. Oppure ``fino_a``:
        tutte le scritture registrate dopo la decisione fino a quell'evento; la risposta elenca comunque gli eventi
        usati. Senza rettifiche rifà la decisione com'era.

        Il motore ricostruisce dal giornale la base della decisione, ci mette sopra **solo** le rettifiche indicate,
        applica la richiesta una volta sola e valuta le regole di allora agli istanti di allora. Una rettifica
        conta se vale all'istante della decisione: un pagamento registrato dopo ma valido da prima sì, uno che non
        dichiara un periodo no, perché vale da quando è scritto.

        Il riesame è **per decisione**: ogni richiesta è riesaminata da sola. Un sì ipotetico non consuma niente per le
        altre richieste e non autorizza niente: ricevute, fatti e circuiti restano com'erano.
        """
        numero = lambda x: int(str(x).removeprefix("evento:"))
        n = numero(evento)
        with self._lock:
            self._viva()
            quanti = len(self.rete.e_frase)
            if not 0 <= n < quanti:
                raise ValueError(f"l'evento {n} non c'è")
            if rettifiche is not None and fino_a is not None:
                raise ValueError("si indicano le rettifiche una per una, oppure «fino_a»: non tutte e due")
            if fino_a is not None:
                ultimo = numero(fino_a)
                if not n <= ultimo < quanti:
                    raise ValueError(f"«fino_a» deve essere un evento tra {n} e {quanti - 1}")
                scelte = [e for e in range(n + 1, ultimo + 1) if self.rete.e_tipo[e] == "scrittura"]
            else:
                scelte = sorted({numero(x) for x in rettifiche or []})
                for e in scelte:
                    if not n < e < quanti:
                        raise ValueError(f"l'evento {e} non è una rettifica di questa decisione: deve essere scritto dopo, ed esistere")
            com_era, record = Rete.com_era(str(self.cartella), n)
            if record is None:
                raise ValueError(f"l'evento {n} non è nel giornale")
            nota = record[7] if len(record) > 7 else None
            ricevuta = nota.get("ricevuta") if isinstance(nota, dict) else None
            dichiarata = ricevuta.get("decisione") if isinstance(ricevuta, dict) else None
            if dichiarata is None:
                raise ValueError(f"l'evento {n} non ha una decisione da riesaminare")
            if ricevuta.get("esito") == "nessun_cambiamento":
                # La ricevuta di una richiesta con chiave che ha trovato tutto già scritto: nel giornale non c'è che
                # cosa chiedeva, e non ha deciso niente. Non si può dire che cosa sarebbe successo su un'altra base.
                raise ValueError(f"l'evento {n} è una richiesta che non ha cambiato niente: non c'è una decisione da riesaminare")
            # La ricevuta si legge nella forma in cui è stata scritta. Se non la dichiara, è quella che si riconosce
            # rifacendo la decisione sulla sua base; le letture ipotetiche si scrivono poi nella stessa forma.
            segno = self._leggi_segno()  # se c'è ma non si legge, o non è di questo giornale, non si riesamina: SegnoGuasto è un ValueError
            forme = self._forme_ammesse(dichiarata, segno if segno is not None and n >= segno["dal"] else None)
            # Prima di riesaminare, la ricevuta deve essere coerente con il giornale: lo stesso controllo di «verifica»,
            # in qualunque formato. Riesaminare una ricevuta guasta darebbe una risposta dall'aria ordinaria. Come
            # «verifica», la si rifà con il modo di fare le somme che chi l'ha scritta ha dichiarato. Se non lo ha
            # dichiarato nessuno, con ognuno dei modi con cui la ricevuta coincide: l'ipotesi si fa poi con ognuno di
            # quelli, e se non danno tutti la stessa risposta non se ne sceglie una.
            stato, modi, perche, fonte = self._calcolo_di(segno, n, dichiarata)
            if stato == "non_supportato":
                raise ValueError(f"la decisione dell'evento {n} non si può riesaminare: {perche}")
            tipo = {"evento": f"evento:{n}", "tipo": record[3]}
            prove = [] if isinstance(forme, str) else [p for p in self._rifatte(n, com_era, record, ricevuta, dichiarata, forme, modi, tipo) if p["forme"]]
            if not prove:
                raise ValueError(f"la ricevuta dell'evento {n} non è coerente con il giornale: va verificata prima di riesaminarla")
            allora = {"esito": ricevuta.get("esito"), "letture": self._letture(dichiarata)}
            ipotesi = []
            for p in prove:
                rete, record, neuroni_base, esclusi = Rete.scenario(str(self.cartella), n, scelte, salta_definizioni=fino_a is not None,
                                                                    calcolo=p["calcolo"])
                # Conta quello che è entrato nella base, non quello che si è chiesto di guardare. Con delle rettifiche
                # dentro, la base non è più quella di allora: è un'ipotesi, e le si chiede quello che si chiede a una
                # scrittura di oggi. Senza, è la stessa base: si rilegge com'è stata decisa.
                lasciate_fuori = {x["evento"] for x in esclusi}
                rifatta = rete.rifai_decisione(record, dichiarata.get("proposta"), neuroni_base=neuroni_base,
                                               ammissione=any(e not in lasciate_fuori for e in scelte))
                if rifatta["decisione"] is None:
                    ipotetico = {"esito": "non_applicabile", "letture": [], "spiegazione": rifatta["spiegazione"]}
                else:  # le letture ipotetiche si scrivono nella stessa forma in cui è scritta la ricevuta
                    ipotetica = self._nella_forma(rifatta["decisione"], p["forme"][0])
                    ipotetico = {"esito": "applicato" if rifatta["esito"] == "scrittura" else "violazione_regola", "letture": self._letture(ipotetica)}
                ipotesi.append((p["calcolo"], ipotetico))
        fuori = {x["evento"] for x in esclusi}
        usati = [e for e in scelte if e not in fuori]
        chi_e = lambda y: (y["quando"], y["regola_id"], y["su_id"], y["segnale"], y["con_proposta"])
        di_allora = {chi_e(y) for y in allora["letture"]}
        # Le ipotesi uguali sono una sola, qualunque sia il modo. Se ne restano più di una il giornale non basta a dire
        # quale vale: si riportano tutte, ognuna con i modi che la danno, e non si dice se la decisione cambia.
        diverse: dict[str, dict] = {}
        for calcolo, ipotetico in ipotesi:
            diverse.setdefault(json.dumps(ipotetico, sort_keys=True, ensure_ascii=False), {"calcoli": [], **ipotetico})["calcoli"].append(calcolo)
        tutte_le_letture = allora["letture"] + [y for _, ipotetico in ipotesi for y in ipotetico["letture"]]
        if len(diverse) == 1:
            ipotetico = ipotesi[0][1]
            cambia, in_piu = ipotetico["esito"] != allora["esito"], [y for y in ipotetico["letture"] if chi_e(y) not in di_allora]
        else:
            ipotetico = {"esito": "indeterminato", "letture": [], "alternative": list(diverse.values()),
                         "spiegazione": perche + ". La ricevuta coincide rifacendola con " + self._modi_in_parole([p["calcolo"] for p in prove])
                                        + ", che su questa ipotesi non danno la stessa risposta: il giornale non basta a dire quale vale"}
            cambia, in_piu = None, []
        return {
            "evento": f"evento:{n}", "modo": "per decisione", "forma": prove[0]["forme"][0],
            **({"calcolo": modi[0], "calcolo_da": fonte} if stato == "dichiarato" else {"calcoli_compatibili": [p["calcolo"] for p in prove]}),
            "spiegazione": "ogni richiesta è riesaminata da sola, sulla sua base più le sole rettifiche indicate: un sì ipotetico "
                           "non consuma niente per le altre richieste e non autorizza niente",
            "base": f"evento:{n - 1}" if n else None, "istante": record[2], "scritto": record[8] if len(record) > 8 else record[2],
            "eventi_usati": [f"evento:{e}" for e in usati],
            "eventi_esclusi": [{"evento": f"evento:{x['evento']}", "perche": x["perche"]} for x in esclusi],
            "regole": sorted({(y["regola"], y["versione"]) for y in tutte_le_letture}),
            "allora": allora, "ipotetico": ipotetico,
            "letture_in_piu": in_piu,
            "cambia": cambia,
        }

    def _aggiorna_dipendenze(self) -> None:
        """Porta l'indice delle dipendenze fino all'ultimo evento, leggendo le ricevute di quelli che mancano."""
        rete = self.rete
        for e in range(self._dipendenze_fino_a, len(rete.e_frase)):
            nota = rete.e_nota.get(e)
            decisione = nota["ricevuta"].get("decisione") if isinstance(nota, dict) and isinstance(nota.get("ricevuta"), dict) else None
            try:
                # Una ricevuta rovinata (senza le regole consultate, o con un'altra cosa al posto della decisione) non
                # fa cadere la ricerca per tutta la memoria: quell'evento è uno di quelli in cui non si può vedere.
                letture = self._letture(decisione) if isinstance(decisione, dict) and decisione else None
                coppie = None if letture is None else [(y["di"], y["valido_al"]) for y in letture]
            except (KeyError, TypeError, AttributeError):
                coppie = None
            if coppie is None:
                self._senza_letture.append(e)
            else:
                for di, valido_al in coppie:
                    self._dipendenze.setdefault(di, []).append((valido_al, e))
        self._dipendenze_fino_a = len(rete.e_frase)

    def candidate(self, rettifica: int | str, riesamina: bool = False) -> dict[str, Any]:
        """Le decisioni che una rettifica può toccare, trovate dalle loro dipendenze e non guardando se un valore di
        oggi è cambiato. Una rettifica è un evento che ha scritto o ritirato dei fatti; ogni fatto ha un effetto su
        chi riguarda, per il periodo in cui vale. Sono candidate le decisioni prese prima della rettifica che hanno
        letto qualcosa di una di quelle entità a un istante dentro quel periodo.

        La ricerca è larga apposta: guarda chi partecipa ai fatti toccati, non come sono definiti oggi i circuiti, che
        possono essere cambiati dopo la decisione. Può proporre una decisione che al riesame non cambia; non deve
        perderne una che cambia. Dice anche se la sua copertura è completa: gli eventi scritti prima che le decisioni
        venissero registrate non hanno letture, e lì non può vedere.

        Con ``riesamina`` ogni candidata è riesaminata con quella sola rettifica, e si dice quali esiti cambiano.
        """
        r = int(str(rettifica).removeprefix("evento:"))
        with self._lock:
            self._viva()
            rete = self.rete
            if not 0 <= r < len(rete.e_frase):
                raise ValueError(f"l'evento {r} non c'è")
            if rete.e_tipo[r] != "scrittura":
                raise ValueError(f"l'evento {r} è un rifiuto: non ha cambiato niente, non è una rettifica")
            self._aggiorna_dipendenze()
            # i fatti che la rettifica ha scritto stanno in fila, quelli che ha ritirato portano il suo numero
            toccati = list(range(bisect.bisect_left(rete.f_nato, r), bisect.bisect_right(rete.f_nato, r)))
            toccati += [f for f in range(len(rete.f_morto)) if rete.f_morto[f] == r]
            effetti: dict[int, list[tuple[float, float]]] = {}
            for f in toccati:
                periodo = rete.validita(f)
                if periodo is None:
                    continue
                for _ruolo, x in rete.legami(f):  # ognuno dei partecipanti del fatto, per il periodo in cui il fatto vale
                    effetti.setdefault(x, []).append(periodo)
            trovate = set()
            for entita, periodi in effetti.items():
                for valido_al, e in self._dipendenze.get(entita, ()):
                    if e < r and any(da <= valido_al < a for da, a in periodi):
                        trovate.add(e)
            senza_letture = bisect.bisect_left(self._senza_letture, r)  # tra gli eventi scritti prima della rettifica
        trovate = sorted(trovate)
        esito = {"rettifica": f"evento:{r}", "candidate": [f"evento:{e}" for e in trovate], "quante": len(trovate),
                 "entita_toccate": len(effetti), "copertura": {"completa": senza_letture == 0, "eventi_senza_letture": senza_letture}}
        if riesamina:
            riesami, incompleti, guaste = [], [], []
            for e in trovate:
                try:
                    riesami.append(self.riesamina(e, [r]))
                except ScenarioIncompleto as perche:  # la rettifica da sola non basta: ha bisogno di un altro evento
                    incompleti.append({"evento": f"evento:{e}", "perche": str(perche)})
                except ValueError as perche:  # una ricevuta che non è coerente con il giornale non si riesamina: si dice
                    guaste.append({"evento": f"evento:{e}", "perche": str(perche)})
            esito["riesaminate"] = len(riesami)
            esito["esiti_cambiati"] = [{"evento": x["evento"], "allora": x["allora"]["esito"], "ipotetico": x["ipotetico"]["esito"]} for x in riesami if x["cambia"]]
            # quelle di cui il giornale non dice con quale modo sono state prese, e i modi possibili non concordano
            esito["esiti_indeterminati"] = [{"evento": x["evento"], "allora": x["allora"]["esito"],
                                             "possibili": sorted({a["esito"] for a in x["ipotetico"]["alternative"]})}
                                            for x in riesami if x["cambia"] is None]
            esito["scenari_incompleti"] = incompleti
            esito["non_riesaminabili"] = guaste
        return esito

    # --- circuiti mantenuti ----------------------------------------------------------

    def _neurone(self, nome: str, che: str) -> int:
        """Il neurone con quel nome, oppure ``neurone:17``. Se il nome ne indica più di uno bisogna dire quale."""
        trovato = lingua.IDENTIFICATIVO.fullmatch(nome.strip())
        if trovato:
            n = int(trovato.group(1))
            if n >= len(self.rete.nomi):
                raise ValueError(f"{nome} non c'è")
            return n
        trovati = self.rete.trova(nome)
        if not trovati:
            raise NomeAssente(f"{che} «{nome}» non c'è", nome, che)
        if len(trovati) > 1:
            candidati = [f"neurone:{i}" for i in trovati]
            raise NomeAmbiguo(f"{che} «{nome}» indica più neuroni ({', '.join(candidati)}): indica quale con il suo identificativo",
                              nome, candidati)
        return trovati[0]

    def valore(self, circuito: str, di: str) -> dict[str, Any]:
        """Il valore che un circuito mantenuto tiene pronto per un'entità: una lettura sola, senza passare i fatti.
        Dice anche fino a quale evento è aggiornato: è sempre l'ultimo scritto."""
        with self._lock:
            self._viva()
            c, x = self._neurone(circuito, "il circuito"), self._neurone(di, "il nome")
            definito = next((k for k in self.rete.circuiti if k["id"] == c), None)
            if definito is None:
                raise ValueError(f"«{circuito}» non è un circuito mantenuto")
            adesso = time.time()
            return {"circuito": definito["nome"], "versione": definito["versione"], "di": self.rete.nomi[x], "di_id": f"neurone:{x}",
                    "valore": str(self.rete.valore(c, x, adesso)), "aggiornato_a": f"evento:{len(self.rete.e_frase) - 1}", "valido_al": adesso}

    def valori(self, circuito: str) -> list[dict[str, Any]]:
        """Tutti i valori che un circuito tiene pronti adesso, entità per entità, in ordine di nome."""
        with self._lock:
            self._viva()
            c = self._neurone(circuito, "il circuito")
            if not any(k["id"] == c for k in self.rete.circuiti):
                raise ValueError(f"«{circuito}» non è un circuito mantenuto")
            # lo stesso adesso di ``valore`` e della lingua: l'orologio, anche se è tornato indietro rispetto all'ultima scrittura
            somme = self.rete.somme_pronte(c)
            righe = [{"di": self.rete.nomi[x], "di_id": f"neurone:{x}", "valore": da_unita(v)} for x, v in somme.items()]
        return sorted(righe, key=lambda r: (r["di"].lower(), r["di_id"]))

    def decisioni(self, limite: int = 200) -> list[dict[str, Any]]:
        """Le ultime decisioni registrate, dalla più recente: che cosa era stato chiesto, com'è finita e che cosa ha
        letto ogni regola, prima e dopo la richiesta. È un riassunto per chi guarda: la ricevuta intera sta nel giornale."""
        with self._lock:
            self._viva()
            rete = self.rete
            definizioni = {rete._id(x) for x in ("regola", "segnale", "circuito", "termine")} - {-1}
            righe = []
            for e in range(len(rete.e_frase) - 1, -1, -1):
                nota = rete.e_nota.get(e)
                ricevuta = nota.get("ricevuta") if isinstance(nota, dict) else None
                decisione = ricevuta.get("decisione") if isinstance(ricevuta, dict) else None
                if decisione is None:
                    continue
                letture: dict[tuple, dict] = {}
                guasta = False
                try:
                    for y in self._letture(decisione):
                        if y["quando"] != "arrivo":
                            continue
                        voce = letture.setdefault((y["regola_id"], y["su_id"], y["segnale"]), {"regola": y["regola"], "cosa": y["cosa"], "di": y["su"]})
                        voce["dopo" if y["con_proposta"] else "prima"] = y["valore"]
                except (KeyError, TypeError, AttributeError):
                    # Una ricevuta rovinata non fa sparire l'elenco: quella decisione si vede, senza le sue letture, e
                    # si dice che la ricevuta non è leggibile. Che cosa non va lo dice la verifica.
                    letture, guasta, decisione = {}, True, {}
                scritti = range(bisect.bisect_left(rete.f_nato, e), bisect.bisect_right(rete.f_nato, e))
                righe.append({
                    "evento": e, "frase": rete.e_frase[e].removeprefix("rifiutato: "), "tipo": rete.e_tipo[e], "esito": ricevuta.get("esito"),
                    "arrivo": rete.e_quando[e], "scritto": rete.e_scritto[e], "chi": rete.e_origine[e],
                    "letture": list(letture.values()), **({"ricevuta_guasta": True} if guasta else {}),
                    "alla_scrittura": isinstance(decisione.get("alla_scrittura"), dict) and "regole" in decisione["alla_scrittura"],
                    "definizione": any(rete.f_tipo[f] in definizioni for f in scritti),
                    "fatti": [rete.in_parole(f) for f in scritti][:6],
                })
                if len(righe) >= limite:
                    break
        return righe

    def verifica_circuiti(self) -> dict[str, Any]:
        """Controlla che i circuiti tengano pronto il valore giusto, in due modi: contro le somme rifatte dai fatti che
        questa memoria ha in RAM, e contro una rete ricostruita da capo dal solo giornale. Non scrive niente."""
        with self._lock:
            self._viva()
            rete = self.rete
            differenze = [{**d, "contro": "i fatti in memoria"} for d in rete.verifica_circuiti()]
            fin_qui = len(rete.e_frase)
            dal_giornale, oltre = Rete.com_era(str(self.cartella), fin_qui)
            dal_giornale._porta_a(rete._valido_al)  # le due somme si confrontano allo stesso istante di validità
            giuste = dal_giornale.valori
            for c in rete.circuiti:
                tenute, attese = rete.valori.get(c["id"], {}), giuste.get(c["id"], {})
                for x in sorted(tenute.keys() | attese.keys()):
                    a, b = tenute.get(x, 0), attese.get(x, 0)
                    if a != b:
                        differenze.append({"circuito": c["nome"], "di": rete.nomi[x], "di_id": x, "tenuto": da_unita(a), "dai_fatti": da_unita(b),
                                           "contro": "il giornale"})
                    elif (x in tenute) != (x in attese):  # stessa somma, zero, ma l'entità sta nel circuito solo da una parte
                        differenze.append({"circuito": c["nome"], "di": rete.nomi[x], "di_id": x, "tenuto": da_unita(a), "dai_fatti": da_unita(b),
                                           "contro": "il giornale", "presenza": "solo in memoria" if x in tenute else "solo nel giornale"})
            if [(c["id"], c["versione"]) for c in rete.circuiti] != [(c["id"], c["versione"]) for c in dal_giornale.circuiti]:
                differenze.append({"circuito": None, "contro": "il giornale", "spiegazione": "i circuiti in memoria non sono quelli scritti nel giornale"})
            # Il controllo vale fino all'ultimo evento che questa memoria conosce. Se il giornale è già più avanti,
            # una somma giusta fin lì non è la somma di adesso: lo si dice, distinto da una somma sbagliata.
            esito = "guasto" if differenze else "indietro" if oltre is not None else "coerente"
            return {"esito": esito, "differenze": differenze, "circuiti": len(rete.circuiti),
                    "valori": sum(len(v) for v in rete.valori.values()), "verificato_fino_a": f"evento:{fin_qui - 1}",
                    "giornale_oltre": oltre is not None}

    # --- raccontare --------------------------------------------------------------

    def racconta(self, frase: str, fatti: list[dict] | None = None, sostituisce: list[dict] | None = None, chiave: str | None = None,
                 prova: bool = False, chi: str = "connettore", nuovi: list[str] | None = None, accetta_nuovi: bool = False) -> dict[str, Any]:
        """Scrive una frase con la sua struttura, tutta o niente, e restituisce la ricevuta.

        ``fatti``: lista di ``{"tipo": ..., "legami": {ruolo: nome, ...}, "soggetto": ruolo}``. Il soggetto è il
        ruolo della cosa di cui il fatto parla; se manca vale il primo legame. Con ``"nuova_occorrenza": true``
        il fatto si scrive anche se ce n'è già uno identico: è un altro accadimento, non lo stesso raccontato
        di nuovo.

        ``sostituisce``: lista di ``{"tipo": ..., "indizi": {ruolo: nome, ...}}``. I fatti in vigore che ricevono
        tutti quegli indizi vengono ritirati nello stesso evento. Se sono più di uno bisogna dire quali:
        ``"attesi": ["fatto:51"]`` (e se nel frattempo sono cambiati è un conflitto) oppure ``"tutti": true``.

        ``nuovi``: nomi per cui far nascere un neurone nuovo anche se uno con quel nome c'è già (un omonimo).
        ``chiave``: la stessa chiave con lo stesso racconto restituisce la ricevuta di prima, senza scrivere
        due volte. ``accetta_nuovi``: in una correzione, conferma che un tipo o un ruolo nuovo è voluto anche
        se somiglia a uno che c'è già.
        """
        if not self.scritture and not prova:
            raise SoloLettura("le scritture non sono accese su questo server: per ora si può solo chiedere, o provare con prova=true")
        istante = time.time()  # l'istante della decisione è quello in cui la richiesta arriva, non quello in cui ottiene il turno
        chi = testo_semplice(chi)  # un'enumerazione di testo vale il suo valore: lo stesso nell'impronta e nel giornale
        richiesta = self._controlla(frase, fatti or [], sostituisce or [], nuovi or [], chiave, chi)
        impronta = self._impronta(richiesta, chi)
        if isinstance(chiave, float) and not math.isfinite(chiave):
            raise Rifiutato("la chiave deve essere un testo, o un numero che resta uguale a se stesso")
        with self._lock:
            # Anche qui, prima di tutto: una memoria che non corrisponde più al giornale non dice «già scritto», non
            # dice «niente da cambiare» e non prova niente. Risponderebbe per uno stato che il giornale non ha.
            self._viva()
            if chiave is not None and chiave in self._chiavi_guaste:
                # La chiave è già stata usata, ma la nota che la porta nel giornale è rovinata: non si sa per quale
                # racconto. Non si valuta di nuovo, perché potrebbe scrivere una seconda volta la stessa richiesta.
                return {"esito": "conflitto", "scritture_applicate": 0,
                        "spiegazione": f"questa chiave è già nel giornale, all'evento {self._chiavi_guaste[chiave]}, ma la nota che la porta è rovinata: "
                                       "non si può dire se è lo stesso racconto. Per un racconto nuovo serve una chiave nuova"}
            if chiave is not None and chiave in self._chiavi:
                vecchia, ricevuta = self._chiavi[chiave]
                if vecchia == impronta:
                    return {**copy.deepcopy(ricevuta), "ripetuta": True}
                return {"esito": "conflitto", "scritture_applicate": 0,
                        "spiegazione": "questa chiave è già stata usata per un racconto diverso: per un racconto nuovo serve una chiave nuova"}
            ricevuta = self._racconta(richiesta, prova, chi, accetta_nuovi, chiave, impronta, istante)
            # Anche un rifiuto è un esito definitivo di quella richiesta: ritentata con la stessa chiave deve tornare lo
            # stesso rifiuto, senza rivalutarla, anche se nel frattempo lo stato è cambiato. Per riprovare serve una chiave nuova.
            # Una prova non lascia niente, nemmeno la sua chiave: la richiesta vera con la stessa chiave va valutata.
            if chiave is not None and not prova and ricevuta["esito"] in ("applicato", "nessun_cambiamento", "violazione_regola"):
                self._chiavi[chiave] = (impronta, ricevuta)
            # A chi chiede va una copia. La ricevuta custodita è quella del giornale: se chi la riceve la arricchisce o
            # la cambia, non deve cambiare quello che una richiesta ripetuta e un'istantanea diranno dopo.
            return copy.deepcopy(ricevuta)

    # la forma del racconto

    def _controlla(self, frase, fatti, sostituisce, nuovi, chiave, chi) -> dict[str, Any]:
        """Controlla la forma del racconto e la riporta a una struttura semplice, con il soggetto di ogni fatto al primo posto."""

        def testo(x: Any, che: str, massimo: int = MASSIMO_NOME) -> str:
            if isinstance(x, (int, float)) and not isinstance(x, bool):
                x = str(x)
            if not isinstance(x, str) or not x.strip():
                raise Rifiutato(f"{che} deve essere un testo non vuoto")
            x = " ".join(x.split())
            if len(x) > massimo:
                raise Rifiutato(f"{che} è troppo lungo: al massimo {massimo} caratteri")
            return x

        def coppie(voce: Any, campo: str, che: str) -> list[list[str]]:
            if not isinstance(voce, dict) or not isinstance(voce.get(campo), dict):
                raise Rifiutato(f"{che} deve avere «tipo» e «{campo}», e «{campo}» deve dire ruolo per ruolo chi partecipa")
            legami = [[testo(r, f"un ruolo di {che}"), testo(v, f"il nome nel ruolo «{r}»")] for r, v in voce[campo].items()]
            if not legami:
                raise Rifiutato(f"{che} non ha nessun partecipante")
            if len(legami) > MASSIMO_LEGAMI:
                raise Rifiutato(f"{che} ha troppi partecipanti: al massimo {MASSIMO_LEGAMI}")
            if len({piana(r) for r, _ in legami}) != len(legami):
                raise Rifiutato(f"in {che} lo stesso ruolo compare due volte")
            # il periodo di validità, se è dichiarato, deve essere fatto di date intere: una data senza anno non si completa
            periodo = {piana(r): istante_di(v) for r, v in legami if piana(r) in ("vale da", "vale fino a")}
            for ruolo, quando in periodo.items():
                if quando is None:
                    raise Rifiutato(f"«{ruolo}» in {che} deve essere una data intera, come 2026-11-16 oppure 2026-11-16T10:30")
            if len(periodo) == 2 and not periodo["vale da"] < periodo["vale fino a"]:
                raise Rifiutato(f"in {che} «vale fino a» deve venire dopo «vale da»")
            return legami

        frase = testo(frase, "la frase", MASSIMO_FRASE)
        testo(chi, "chi racconta")
        if chiave is not None:
            testo(chiave, "la chiave")
        if not fatti and not sostituisce:
            raise Rifiutato("il racconto non contiene fatti: serve almeno un fatto, con il suo tipo e i suoi legami")
        if not isinstance(fatti, list) or not isinstance(sostituisce, list) or len(fatti) + len(sostituisce) > MASSIMO_FATTI:
            raise Rifiutato(f"troppi fatti in un racconto solo: al massimo {MASSIMO_FATTI}")
        puliti = []
        for f in fatti:
            legami = coppie(f, "legami", "ogni fatto")
            soggetto = f.get("soggetto")
            if soggetto is not None:
                soggetto = testo(soggetto, "il soggetto")
                primo = [x for x in legami if piana(x[0]) == piana(soggetto)]
                if not primo:
                    raise Rifiutato(f"il soggetto «{soggetto}» non è tra i ruoli del fatto ({', '.join(r for r, _ in legami)})")
                legami = primo + [x for x in legami if x is not primo[0]]  # il soggetto al primo posto
            puliti.append({"tipo": testo(f.get("tipo"), "il tipo di ogni fatto"), "legami": legami, "soggetto": soggetto,
                           "nuova_occorrenza": f.get("nuova_occorrenza") is True})
        da_sostituire = []
        for s in sostituisce:
            attesi = s.get("attesi") if isinstance(s, dict) else None
            if attesi is not None:
                if not isinstance(attesi, list) or not all(isinstance(a, str) and ID_FATTO.fullmatch(a.strip()) for a in attesi):
                    raise Rifiutato("«attesi» deve essere un elenco di identificativi di fatti, come fatto:51")
                attesi = sorted({int(ID_FATTO.fullmatch(a.strip()).group(1)) for a in attesi})
            da_sostituire.append({"tipo": testo(s.get("tipo") if isinstance(s, dict) else None, "il tipo di ogni fatto da sostituire"),
                                  "indizi": coppie(s, "indizi", "ogni fatto da sostituire"), "attesi": attesi, "tutti": s.get("tutti") is True})
        if not isinstance(nuovi, list):
            raise Rifiutato("«nuovi» deve essere un elenco di nomi")
        return {"frase": frase, "fatti": puliti, "sostituisce": da_sostituire, "nuovi": [testo(n, "un nome tra i nuovi") for n in nuovi]}

    @staticmethod
    def _impronta(richiesta: dict, chi: str) -> str:
        """L'impronta del racconto: non cambia se cambia solo l'ordine in cui sono scritti i legami."""
        forma = [richiesta["frase"], chi, sorted(piana(n) for n in richiesta["nuovi"]),
                 [[f["tipo"], f["soggetto"], sorted(f["legami"]), f["nuova_occorrenza"]] for f in richiesta["fatti"]],
                 [[s["tipo"], sorted(s["indizi"]), s["attesi"], s["tutti"]] for s in richiesta["sostituisce"]]]
        return hashlib.sha256(json.dumps(forma, ensure_ascii=False).encode()).hexdigest()

    # la scrittura

    def _racconta(self, richiesta: dict, prova: bool, chi: str, accetta_nuovi: bool, chiave: str | None, impronta: str,
                  istante: float | None = None) -> dict[str, Any]:
        rete = self.rete
        nomi = rete.nomi
        da_creare: dict[str, str] = {}  # nome piano -> nome com'è stato scritto: i neuroni che nasceranno
        omonimi = {piana(n) for n in richiesta["nuovi"]}
        ambigui: dict[str, dict] = {}

        def trova(nome: str, campo: str) -> int | str:
            """Il neurone con quel nome; se non c'è, la chiave sotto cui nascerà."""
            chiave_nome = piana(nome)
            if chiave_nome in omonimi:
                da_creare.setdefault(chiave_nome, nome)
                return chiave_nome
            trovati = rete.per_nome.get(chiave_nome)
            if not trovati:
                m = ID_NEURONE.fullmatch(nome)
                if m:
                    if int(m.group(1)) >= len(nomi):
                        raise Rifiutato(f"{nome} non esiste: la rete ha {len(nomi)} neuroni")
                    return int(m.group(1))
                da_creare.setdefault(chiave_nome, nome)
                return chiave_nome
            if len(trovati) > 1:
                ambigui.setdefault(chiave_nome, {"nome": nome, "campi": [], "candidati": [
                    {"id": f"neurone:{i}", "nome": nomi[i], "fatti": rete.intorno(i, 3)} for i in trovati]})["campi"].append(campo)
            return trovati[0]

        # 1. i nomi: tipi, ruoli e partecipanti. Qui non nasce ancora niente.
        proposti = []
        for i, f in enumerate(richiesta["fatti"]):
            proposti.append((trova(f["tipo"], f"fatti[{i}].tipo"),
                             [(trova(r, f"fatti[{i}].legami.{r} (il ruolo)"), trova(v, f"fatti[{i}].legami.{r}")) for r, v in f["legami"]], f))
        ricerche = []
        for i, s in enumerate(richiesta["sostituisce"]):
            ricerche.append((trova(s["tipo"], f"sostituisce[{i}].tipo"),
                             [(trova(r, f"sostituisce[{i}].indizi.{r} (il ruolo)"), trova(v, f"sostituisce[{i}].indizi.{r}")) for r, v in s["indizi"]], s))

        # 2. il vocabolario: che cosa è nuovo, e che cosa in una correzione non può andare
        schema = lingua._Schema(rete)
        tipi_noti, ruoli_noti = set(schema.ruoli), set(schema.tipi)
        nome_di = lambda x: da_creare.get(x, x) if isinstance(x, str) else nomi[x]  # noqa: E731
        tipi_nuovi = sorted({nome_di(t) for t, _, _ in proposti if t not in tipi_noti})
        ruoli_nuovi = sorted({nome_di(r) for _, lg, _ in proposti for r, _ in lg if r not in ruoli_noti})
        simili_tipi = self._simili(tipi_nuovi, [nomi[t] for t in tipi_noti])
        simili_ruoli = self._simili(ruoli_nuovi, [nomi[r] for r in ruoli_noti])
        dubbi = []
        for i, (tipo, indizi, _) in enumerate(ricerche):
            # In una correzione un tipo o un ruolo che non c'è non trova niente: si ritirerebbe nulla e il valore
            # nuovo si aggiungerebbe a quello vecchio. Meglio fermarsi e chiedere.
            if tipo not in tipi_noti:
                dubbi.append({"campo": f"sostituisce[{i}].tipo", "nome": nome_di(tipo), "che": "tipo di fatto",
                              "forse": self._simili([nome_di(tipo)], [nomi[t] for t in tipi_noti]).get(nome_di(tipo), [])})
                continue
            suoi = schema.ruoli[tipo]
            for r, _ in indizi:
                if r not in suoi:
                    dubbi.append({"campo": f"sostituisce[{i}].indizi.{nome_di(r)}", "nome": nome_di(r), "che": f"ruolo dei fatti «{nomi[tipo]}»",
                                  "forse": self._simili([nome_di(r)], [nomi[x] for x in suoi]).get(nome_di(r), []) or [nomi[x] for x in suoi]})
        if ricerche and not accetta_nuovi:
            # Sempre in una correzione, un tipo o un ruolo nuovo che somiglia a uno che c'è già è probabilmente un refuso.
            for i, (tipo, legami, _) in enumerate(proposti):
                if nome_di(tipo) in simili_tipi:
                    dubbi.append({"campo": f"fatti[{i}].tipo", "nome": nome_di(tipo), "che": "tipo di fatto", "forse": simili_tipi[nome_di(tipo)]})
                for r, _ in legami:
                    if nome_di(r) in simili_ruoli:
                        dubbi.append({"campo": f"fatti[{i}].legami.{nome_di(r)}", "nome": nome_di(r), "che": "ruolo", "forse": simili_ruoli[nome_di(r)]})
        if ambigui or dubbi:
            spiegazione = []
            if ambigui:
                spiegazione.append("un nome indica più cose: ripeti il racconto con la stessa frase, scrivendo al posto del nome l'identificativo "
                                   "giusto (per esempio neurone:17), oppure mettilo tra i «nuovi» se è un'altra cosa ancora")
            if dubbi:
                spiegazione.append("in una correzione c'è un tipo o un ruolo che la rete non conosce: correggi il nome, oppure, se nei fatti nuovi "
                                   "è voluto, ripeti con accetta_nuovi=true")
            return {"esito": "da_chiarire", "scritture_applicate": 0, "spiegazione": "; ".join(spiegazione),
                    "nomi": list(ambigui.values()), "vocabolario": dubbi, "sostituzioni": []}

        # 3. i fatti da ritirare: devono essere proprio quelli che chi racconta ha in mente
        def scattano(tipo, legami) -> list[int]:
            if isinstance(tipo, str) or any(isinstance(r, str) or isinstance(n, str) for r, n in legami):
                return []  # nomina qualcosa che non esiste ancora: nessun fatto può riceverlo
            return sorted(rete.scatta(tipo, legami))

        descrivi = lambda f: {"id": f"fatto:{f}", "fatto": rete.in_parole(f), "frase": rete.e_frase[rete.f_nato[f]]}  # noqa: E731
        ritirati: list[int] = []
        incerti = []
        for i, (tipo, indizi, s) in enumerate(ricerche):
            trovati = scattano(tipo, indizi)
            if s["attesi"] is not None:
                if trovati != s["attesi"]:
                    return {"esito": "conflitto", "scritture_applicate": 0, "trovati": [descrivi(f) for f in trovati],
                            "spiegazione": f"i fatti da sostituire non sono quelli attesi (sostituisce[{i}]): nel frattempo la rete è cambiata. "
                                           "Guarda quelli trovati e ripeti il racconto"}
            elif len(trovati) > 1 and not s["tutti"]:
                incerti.append({"campo": f"sostituisce[{i}]", "candidati": [descrivi(f) for f in trovati]})
            ritirati += [f for f in trovati if f not in ritirati]
        if incerti:
            return {"esito": "da_chiarire", "scritture_applicate": 0, "nomi": [], "vocabolario": [], "sostituzioni": incerti,
                    "spiegazione": "gli indizi di una correzione trovano più di un fatto: indica quali ritirare con «attesi» "
                                   "(per esempio [\"fatto:51\"]), oppure scrivi «tutti»: true se vanno ritirati tutti"}

        # 4. i fatti identici a uno che c'è già non si riscrivono, a meno che siano un altro accadimento
        nuovi_fatti, gia_presenti = [], []
        for tipo, legami, f in proposti:
            uguali = [] if f["nuova_occorrenza"] else [x for x in scattano(tipo, legami) if rete.f_n[x] == len(legami) and x not in ritirati]
            (gia_presenti if uguali else nuovi_fatti).append((tipo, legami, uguali))
        avvisi = [f"«{nome}» è un {che} nuovo, ma somiglia a {' e a '.join(f'«{x}»' for x in vicini)}, che c'è già: se è la stessa cosa conviene usare quel nome"
                  for che, simili in (("tipo di fatto", simili_tipi), ("ruolo", simili_ruoli)) for nome, vicini in simili.items()]
        comune = {"neuroni_nuovi": [], "tipi_nuovi": tipi_nuovi, "ruoli_nuovi": ruoli_nuovi, "avvisi": avvisi,
                  "gia_presenti": [descrivi(u[0]) for _, _, u in gia_presenti]}
        if not nuovi_fatti and not ritirati:
            invariato = {"esito": "nessun_cambiamento", "scritture_applicate": 0, **comune,
                         "spiegazione": "questi fatti ci sono già, identici: non è stato scritto niente. Se è un altro accadimento, e non lo stesso "
                                        "raccontato di nuovo, ripeti con \"nuova_occorrenza\": true nel fatto"}
            if chiave is None or prova:
                return invariato
            # Con una chiave anche questo esito deve durare (contratto, paragrafo 5): la stessa chiave deve ricevere
            # questa ricevuta anche dopo un riavvio e dopo un cambio di stato, e non scrivere allora quello che adesso
            # non ha scritto. L'unico posto che dura è il giornale: ci va un evento senza fatti, con la chiave e la
            # ricevuta, come per un rifiuto.
            invariato["evento"] = f"evento:{len(rete.e_frase)}"

            def nota_senza_fatti(decisione: dict) -> dict:
                invariato["decisione"] = json.loads(json.dumps(decisione, ensure_ascii=False))  # come si rilegge dal giornale
                return {"chiave": chiave, "impronta": impronta, "ricevuta": invariato}

            rete.racconta(richiesta["frase"], chi, [], quando=istante, neuroni_da=len(nomi), nota_della_scrittura=nota_senza_fatti)
            return invariato

        # 5. si scrive: nascono i neuroni che mancano, poi l'evento con i suoi fatti, tutto o niente.
        #    In una prova, o se una regola vieta, i neuroni appena nati vengono dimenticati: non resta traccia.
        segno = len(nomi)
        nati: dict[str, int] = {}

        def neurone(x: int | str) -> int:
            if isinstance(x, int):
                return x
            if x not in nati:
                nati[x] = rete.nuovo(da_creare[x])
            return nati[x]

        da_scrivere = [(neurone(tipo), [(neurone(r), neurone(n)) for r, n in legami]) for tipo, legami, _ in nuovi_fatti]
        comune["neuroni_nuovi"] = [{"id": None if prova else f"neurone:{i}", "nome": nomi[i]} for i in sorted(nati.values())]
        evento, primo_fatto = len(rete.e_frase), len(rete.f_tipo)
        prima = [descrivi(f) for f in ritirati]
        dopo = [{"id": None if prova else f"fatto:{primo_fatto + i}",
                 "fatto": f"{nomi[t]} ({', '.join(f'{nomi[r]}: {nomi[n]}' for r, n in lg)})"} for i, (t, lg) in enumerate(da_scrivere)]
        ricevuta = {"esito": "applicato", "evento": f"evento:{evento}", "scritture_applicate": len(da_scrivere) + len(ritirati), **comune,
                    "creati": dopo, "ritirati": prima}
        rifiuto: dict[str, Any] = {}

        def nel_giornale(r: dict) -> dict:
            """La nota che accompagna l'evento nel giornale. Ogni decisione, sì o no, ci lascia la sua ricevuta: con una
            chiave la ricevuta intera, così una richiesta ripetuta la ritrova; senza chiave quello che serve a verificarla."""
            if chiave is not None:
                return {"chiave": chiave, "impronta": impronta, "ricevuta": r}
            return {"ricevuta": {"esito": r["esito"], "evento": r["evento"], "decisione": r["decisione"]}}

        def nota_della_scrittura(decisione: dict) -> dict:
            """La ricevuta di un sì, nello stesso record dei fatti: dice su che cosa si è deciso e quali regole sono state consultate."""
            ricevuta["decisione"] = json.loads(json.dumps(decisione, ensure_ascii=False))  # come si rilegge dal giornale
            return nel_giornale(ricevuta)

        def nota_del_rifiuto(regole: list, decisione: dict) -> dict:
            """La ricevuta del rifiuto, scritta nel giornale nello stesso record dell'evento di rifiuto."""
            # I neuroni nati per questa scrittura vengono dimenticati: nella ricevuta restano i nomi, senza identificativo.
            senza_id = {**comune, "neuroni_nuovi": [{"id": None, "nome": x["nome"]} for x in comune["neuroni_nuovi"]]}
            rifiuto.update(json.loads(json.dumps({  # passata da JSON: uguale a come si rilegge dal giornale dopo un riavvio
                "esito": "violazione_regola", "evento": f"evento:{evento}", "scritture_applicate": 0, **senza_id, "regole": regole,
                "decisione": decisione,
                "spiegazione": "una regola della rete vieta questa scrittura: non è stato scritto niente"}, ensure_ascii=False)))
            return nel_giornale(rifiuto)

        try:
            esito = rete.racconta(richiesta["frase"], chi, da_scrivere, ritira=ritirati, prova=prova, neuroni_da=segno, quando=istante,
                                  nota_del_rifiuto=None if prova else nota_del_rifiuto,
                                  nota_della_scrittura=None if prova else nota_della_scrittura)
        except Vietato:
            return rifiuto
        except (NonNumerico, DefinizioneNonValida) as e:
            raise Rifiutato(str(e)) from None
        if prova:
            return {"esito": "prova", "scritture_applicate": 0, **comune, "creerebbe": dopo, "ritirerebbe": prima,
                    "regole_scattate": esito["regole_scattate"], "decisione": json.loads(json.dumps(esito["decisione"], ensure_ascii=False))}
        assert esito == evento and len(rete.f_tipo) == primo_fatto + len(da_scrivere)  # la ricevuta è stata scritta prima: deve dire il vero
        return ricevuta

    @staticmethod
    def _simili(nuovi: list[str], noti: list[str]) -> dict[str, list[str]]:
        """Per ogni nome nuovo, i nomi già noti a cui somiglia: forse è lo stesso scritto in un altro modo."""
        trovati = {}
        for nome in nuovi:
            vicini = difflib.get_close_matches(piana(nome), [piana(x) for x in noti], n=2, cutoff=0.75)
            simili = [x for x in noti if piana(x) in vicini]
            if simili:
                trovati[nome] = simili
        return trovati

    def chiudi(self) -> None:
        with self._lock:
            self.rete.chiudi()
