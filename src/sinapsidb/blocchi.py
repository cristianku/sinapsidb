"""Una sola rete su disco, attiva in memoria solo in parte. Sola lettura.

È una memoria su due livelli: non si carica subito tutto in memoria, ma solo quello che serve, come una memoria
veloce accanto a una più profonda che richiede più tempo. La memoria profonda è il disco, dove sta tutta la rete;
quella veloce sono i blocchi che il lettore sta usando.

Su disco vive tutta la rete: neuroni, fatti con le loro sinapsi, eventi con le loro frasi, e le linee
che da (tipo, ruolo, neurone) portano ai fatti. In RAM vivono solo i blocchi che il lettore sta usando,
dentro un tetto di byte che si sceglie. Se una domanda segue una sinapsi verso un blocco che non c'è,
il lettore lo carica e continua: «non caricato» non diventa mai «inesistente». Gli identificativi di
neuroni, fatti ed eventi sono quelli del motore attuale e non cambiano.

Quello che resta in RAM sono blocchi della rete, non risposte: un blocco caricato per una domanda serve
qualsiasi altra domanda che passi di lì.

I file (tutti numeri little-endian):

- ``fatti.dat``     blocchi di 256 fatti consecutivi; ogni blocco ha la tabella dei suoi inizi e poi i
                    record: tipo, evento che l'ha scritto, evento che l'ha ritirato (-1 se in vigore),
                    numero di sinapsi, e le sinapsi (ruolo, neurone);
- ``nomi.dat``      blocchi di 256 nomi di neuroni consecutivi;
- ``eventi.dat``    blocchi di 128 eventi consecutivi: quando, origine, genere, frase;
- ``chiavi.dat``    le linee: voci ordinate per (in vigore o ritirato, linea, neurone), in pagine di 800
                    voci; una voce dice quanti fatti ricevono quel neurone su quella linea e dove sta
                    l'elenco (se il fatto è uno solo, è scritto nella voce stessa);
- ``liste.dat``     gli elenchi dei fatti, uno dopo l'altro;
- ``nomi.hash``     dal nome al neurone: voci (impronta del nome, neurone) raggruppate per secchio;
- ``*.idx``, ``testa.json``  quello che resta sempre in RAM: dove comincia ogni blocco, la prima chiave
                    di ogni pagina, dove comincia ogni secchio, il vocabolario delle linee.

``costruisci`` scrive questi file una volta, leggendo il giornale del motore dall'inizio alla fine senza
tenere la rete in memoria. ``ReteParziale`` li apre e risponde. ``ComeRete`` la presenta con la forma che la
lingua di SinapsiDB si aspetta dal motore, così le stesse frasi girano su tutti e due.

Il giornale resta la verità: i blocchi si costruiscono da lì e si possono sempre rifare. Oggi il portale li usa
per la rete di prova del confronto; le misure sono in ``docs/confronto-postgresql.md``.
"""

from __future__ import annotations

import bisect
import gzip
import hashlib
import json
import mmap
import os
import struct
import tempfile
import time
import zlib
from array import array
from collections import OrderedDict
from pathlib import Path

from sinapsidb.motore import BIT, MASSIMO_RECORD, Rete, piana, record_buono  # il modo di appiattire i nomi deve essere quello del motore

BLOCCO = 16 * 1024  # byte dei blocchi a taglia fissa (liste, hash)
FATTI_PER_BLOCCO = 256
NOMI_PER_BLOCCO = 256
EVENTI_PER_BLOCCO = 128
VOCE = struct.Struct("<QIQ")  # chiave, quanti fatti, dove (20 byte)
VOCI_PER_PAGINA = 800
PAGINA = VOCE.size * VOCI_PER_PAGINA  # 16.000 byte
TESTA_FATTO = struct.Struct("<IiiB")  # tipo, nato, morto, numero di sinapsi
TESTA_EVENTO = struct.Struct("<dHB")  # quando, origine, genere
VOCE_HASH = struct.Struct("<QI")  # impronta del nome, neurone
NEURONI_PER_SECCHIO = 64
MASCHERA = (1 << BIT) - 1
RITIRATO = 1 << 62
_SINAPSI = {n: struct.Struct(f"<{2 * n}I") for n in range(0, 128)}
FATTI, NOMI, EVENTI, CHIAVI, LISTE, HASH = range(6)
NOMI_FILE = {FATTI: "fatti.dat", NOMI: "nomi.dat", EVENTI: "eventi.dat", CHIAVI: "chiavi.dat", LISTE: "liste.dat", HASH: "nomi.hash"}


def impronta(nome: str) -> int:
    return int.from_bytes(hashlib.blake2b(piana(nome).encode(), digest_size=8).digest(), "little")


# --- costruzione ------------------------------------------------------------------


class _ScriviBlocchi:
    """Scrive record a lunghezza variabile in blocchi di ``per_blocco`` record, e ricorda dove comincia ogni blocco."""

    def __init__(self, percorso: Path, per_blocco: int, posizioni: bool = False) -> None:
        self.f = open(percorso, "wb", buffering=1 << 20)
        self.per_blocco = per_blocco
        self.inizi = array("Q")
        self.attesa: list[bytes] = []
        self.pos = 0
        self.posizioni = array("Q") if posizioni else None  # dove sta ogni record nel file

    def aggiungi(self, record: bytes) -> None:
        self.attesa.append(record)
        if len(self.attesa) == self.per_blocco:
            self._scarica()

    def _scarica(self) -> None:
        if not self.attesa:
            return
        dove = 4 * (len(self.attesa) + 1)
        tabella = []
        for r in self.attesa:
            tabella.append(dove)
            dove += len(r)
        tabella.append(dove)
        self.inizi.append(self.pos)
        if self.posizioni is not None:
            self.posizioni.extend(self.pos + t for t in tabella[:-1])
        self.f.write(struct.pack(f"<{len(tabella)}I", *tabella))
        self.f.write(b"".join(self.attesa))
        self.pos += dove
        self.attesa = []

    def chiudi(self) -> array:
        self._scarica()
        self.inizi.append(self.pos)  # la fine dell'ultimo blocco
        self.f.close()
        return self.inizi


def _record(percorso: Path, letto: dict | None = None):
    """I record del giornale del motore, uno alla volta. Si ferma dove si ferma il motore: a una coda scritta a metà.
    Un record guasto nel mezzo non è una coda: solleva ``GiornaleGuasto``, come fa il motore quando apre. Una rete a
    blocchi costruita solo fin lì risponderebbe come se le scritture venute dopo non ci fossero mai state.

    Con ``letto`` (``{"byte": 0, "impronta": hashlib.sha256()}``) tiene il conto dei byte dei record letti e la loro
    impronta: l'identità del giornale da cui i blocchi vengono, presa da quello che si è letto davvero."""
    compresso = str(percorso).endswith(".gz")
    with (gzip.open if compresso else open)(percorso, "rb") as f:
        # Il giornale è quello che c'è quando lo si apre: quello che chi scrive aggiunge dopo non si guarda, né per
        # leggerlo né per giudicare la coda. Una copia compressa si legge fino in fondo e basta.
        fine_file = None if compresso else os.fstat(f.fileno()).st_size
        fine = 0
        while fine_file is None or fine + 8 <= fine_file:
            testa = f.read(8)
            if len(testa) < 8:
                break
            lunghezza, crc = struct.unpack("<II", testa)
            dentro = lunghezza <= MASSIMO_RECORD and (fine_file is None or fine + 8 + lunghezza <= fine_file)
            corpo = f.read(lunghezza) if dentro else b""
            if not record_buono(lunghezza, crc, corpo):
                break
            fine += 8 + lunghezza
            if letto is not None:
                letto["impronta"].update(testa)
                letto["impronta"].update(corpo)
                letto["byte"] = fine
            yield json.loads(corpo)
        if fine_file is not None:
            Rete.giudica_la_coda(f, fine, fine_file)


def costruisci(giornale: Path | str, uscita: Path | str, avanza=None) -> dict:
    """Legge il giornale del motore dall'inizio alla fine e scrive la rete a blocchi. Restituisce i numeri della costruzione.

    In memoria tiene solo quello che cresce piano: un'impronta per neurone, l'evento che ha ritirato ogni
    fatto, la posizione di ogni fatto nel file. Le sinapsi passano da file provvisori, una linea alla volta.
    """
    inizio = time.perf_counter()
    uscita = Path(uscita)
    uscita.mkdir(parents=True, exist_ok=True)
    fatti_w = _ScriviBlocchi(uscita / "fatti.dat", FATTI_PER_BLOCCO, posizioni=True)
    nomi_w = _ScriviBlocchi(uscita / "nomi.dat", NOMI_PER_BLOCCO)
    eventi_w = _ScriviBlocchi(uscita / "eventi.dat", EVENTI_PER_BLOCCO)
    impronte = array("Q")  # una per neurone
    morto = array("i")  # per fatto: l'evento che lo ha ritirato, -1 se è in vigore
    forma: dict[int, list | None] = {}
    origini: dict[str, int] = {}
    generi: dict[str, int] = {}
    n_eventi = n_fatti = n_sinapsi = 0
    paio = struct.Struct("<II")  # (fatto, neurone): letto come un intero a 64 bit vale neurone * 2^32 + fatto
    with tempfile.TemporaryDirectory(dir=uscita, prefix="linee-") as provvisoria:
        code: dict[int, object] = {}  # linea -> file provvisorio con le sue coppie
        letto = {"byte": 0, "impronta": hashlib.sha256()}
        for frase, origine, quando, genere, nuovi, piatti, ritirati, *_ in _record(Path(giornale), letto):
            for nome in nuovi:
                nomi_w.aggiungi(nome.encode())
                impronte.append(impronta(nome))
            for f in ritirati:
                morto[f] = n_eventi
            for tipo, lg in piatti:
                n = len(lg) // 2
                fatti_w.aggiungi(TESTA_FATTO.pack(tipo, n_eventi, -1, n) + _SINAPSI[n].pack(*lg))
                morto.append(-1)
                ruoli = lg[0::2]
                nota = forma.get(tipo, 0)
                if nota == 0:
                    forma[tipo] = ruoli
                elif nota is not None and nota != ruoli:
                    forma[tipo] = None
                gia = None  # un fatto che nomina due volte lo stesso neurone nello stesso ruolo sta sulla linea una volta sola, come nel motore
                for j in range(0, len(lg), 2):
                    if lg.count(lg[j]) > 1:
                        gia = set() if gia is None else gia
                        if (lg[j], lg[j + 1]) in gia:
                            continue
                        gia.add((lg[j], lg[j + 1]))
                    linea = (lg[j] << BIT) | tipo
                    coda = code.get(linea)
                    if coda is None:
                        coda = code[linea] = open(os.path.join(provvisoria, str(linea)), "wb", buffering=1 << 16)
                    coda.write(paio.pack(n_fatti, lg[j + 1]))
                n_fatti += 1
                n_sinapsi += n
            eventi_w.aggiungi(TESTA_EVENTO.pack(quando, origini.setdefault(origine, len(origini)), generi.setdefault(genere, len(generi)))
                              + frase.encode())
            n_eventi += 1
            if avanza and n_eventi % 20_000 == 0:
                avanza("lettura del giornale", n_fatti)
        posizioni = fatti_w.posizioni
        inizi_fatti, inizi_nomi, inizi_eventi = fatti_w.chiudi(), nomi_w.chiudi(), eventi_w.chiudi()
        lettura = time.perf_counter() - inizio

        if avanza:
            avanza("scrittura delle linee", n_fatti)
        # i fatti ritirati: l'evento che li ha ritirati va scritto nel loro record
        ritirati_tutti = [f for f in range(n_fatti) if morto[f] >= 0] if any(m >= 0 for m in morto) else []
        with open(uscita / "fatti.dat", "r+b") as f:
            for x in ritirati_tutti:
                f.seek(posizioni[x] + 8)
                f.write(struct.pack("<i", morto[x]))
        del posizioni

        # le linee: per ogni linea le coppie in ordine di neurone, divise tra fatti in vigore e ritirati
        for coda in code.values():
            coda.close()
        indice_linea = {linea: i for i, linea in enumerate(sorted(code))}
        primi = array("Q")  # la prima chiave di ogni pagina
        n_voci = 0
        dove_liste = 0
        dopo: list[tuple[int, list[int]]] = []  # le voci dei fatti ritirati: vanno in fondo, dopo tutte quelle in vigore
        voci_per_linea: dict[int, list[int]] = {linea: [0, 0] for linea in code}  # neuroni con fatti in vigore, e con fatti ritirati
        with open(uscita / "chiavi.dat", "wb", buffering=1 << 20) as chiavi, open(uscita / "liste.dat", "wb", buffering=1 << 20) as liste:

            def voce(chiave: int, fatti: list[int]) -> None:
                nonlocal n_voci, dove_liste
                if n_voci % VOCI_PER_PAGINA == 0:
                    primi.append(chiave)
                if len(fatti) == 1:
                    chiavi.write(VOCE.pack(chiave, 1, fatti[0]))  # un fatto solo: sta nella voce
                else:
                    chiavi.write(VOCE.pack(chiave, len(fatti), dove_liste))
                    liste.write(struct.pack(f"<{len(fatti)}I", *fatti))
                    dove_liste += 4 * len(fatti)
                n_voci += 1

            for linea in sorted(code):
                coppie = array("Q")
                with open(os.path.join(provvisoria, str(linea)), "rb") as f:
                    coppie.frombytes(f.read())
                base = indice_linea[linea] << 32
                corrente, vivi, morti = -1, [], []
                for x in sorted(coppie):
                    neurone, fatto = x >> 32, x & 0xFFFFFFFF
                    if neurone != corrente:
                        if vivi:
                            voce(base | corrente, vivi)
                            voci_per_linea[linea][0] += 1
                        if morti:
                            dopo.append((RITIRATO | base | corrente, morti))
                            voci_per_linea[linea][1] += 1
                        corrente, vivi, morti = neurone, [], []
                    (vivi if morto[fatto] < 0 else morti).append(fatto)
                if vivi:
                    voce(base | corrente, vivi)
                    voci_per_linea[linea][0] += 1
                if morti:
                    dopo.append((RITIRATO | base | corrente, morti))
                    voci_per_linea[linea][1] += 1
                del coppie
            for chiave, fatti in dopo:
                voce(chiave, fatti)

    # dal nome al neurone: le impronte raggruppate per secchio
    n_neuroni = len(impronte)
    secchi = max(1, n_neuroni // NEURONI_PER_SECCHIO)
    ordinate = sorted((h % secchi, h, i) for i, h in enumerate(impronte))
    inizi_secchi = array("Q", [0] * (secchi + 1))
    with open(uscita / "nomi.hash", "wb", buffering=1 << 20) as f:
        for secchio, h, i in ordinate:
            inizi_secchi[secchio + 1] += 1
            f.write(VOCE_HASH.pack(h, i))
    for s in range(secchi):
        inizi_secchi[s + 1] += inizi_secchi[s]
    del ordinate

    for nome, tabella in (("fatti.idx", inizi_fatti), ("nomi.idx", inizi_nomi), ("eventi.idx", inizi_eventi), ("chiavi.idx", primi), ("nomi.hash.idx", inizi_secchi)):
        (uscita / nome).write_bytes(tabella.tobytes())
    testa = {"formato": 1, "fatti": n_fatti, "neuroni": n_neuroni, "eventi": n_eventi, "sinapsi": n_sinapsi, "voci": n_voci, "secchi": secchi,
             "linee": [[linea >> BIT, linea & MASCHERA] for linea in sorted(code)], "voci_per_linea": [voci_per_linea[linea] for linea in sorted(code)],
             "forma": {str(t): r for t, r in forma.items()},
             "origini": list(origini), "generi": list(generi), "nomi": {}, "ritirati": len(ritirati_tutti),
             # da quale giornale vengono: quanti byte e la loro impronta. Chi li riusa controlla che il giornale sia questo
             "giornale": {"byte": letto["byte"], "sha256": letto["impronta"].hexdigest()}}
    (uscita / "testa.json").write_text(json.dumps(testa, ensure_ascii=False), encoding="utf-8")
    # il vocabolario delle linee: i nomi di tipi e ruoli, riletti dai blocchi appena scritti
    lettore = ReteParziale(uscita, tetto=8 << 20)
    testa["nomi"] = {str(i): lettore.nome(i) for i in sorted({x for linea in code for x in (linea >> BIT, linea & MASCHERA)})}
    lettore.chiudi()
    (uscita / "testa.json").write_text(json.dumps(testa, ensure_ascii=False), encoding="utf-8")
    disco = {p.name: p.stat().st_size for p in sorted(uscita.iterdir()) if p.is_file()}
    return {"secondi": round(time.perf_counter() - inizio, 1), "secondi_di_lettura_del_giornale": round(lettura, 1), "giornale": testa["giornale"], "fatti": n_fatti,
            "neuroni": n_neuroni, "sinapsi": n_sinapsi, "eventi": n_eventi, "voci_delle_linee": n_voci, "disco": disco,
            "disco_totale_mb": round(sum(disco.values()) / 1e6, 1),
            "sempre_in_ram_mb": round(sum(v for k, v in disco.items() if k.endswith(".idx") or k == "testa.json") / 1e6, 2)}


def giornale_dei_blocchi(cartella: Path | str) -> dict | None:
    """Da quale giornale sono stati costruiti i blocchi di questa cartella: ``{"byte", "sha256"}``. None se non lo
    dicono (quelli costruiti prima della 0.10.5) o se la loro testa non si legge: blocchi che non provano da dove
    vengono non si riusano, si ricostruiscono."""
    try:
        giornale = json.loads((Path(cartella) / "testa.json").read_text(encoding="utf-8")).get("giornale")
    except (OSError, ValueError, AttributeError):
        return None
    if (not isinstance(giornale, dict) or type(giornale.get("byte")) is not int or giornale["byte"] < 0
            or not isinstance(giornale.get("sha256"), str)):
        return None
    return giornale


def sono_del_giornale(cartella: Path | str, giornale: Path | str) -> bool:
    """I blocchi di ``cartella`` vengono proprio da questo giornale? Si guarda il contenuto, non solo la lunghezza:
    il giornale deve essere lungo quanto quello letto per costruirli e avere la stessa impronta. Legge tutto il
    giornale, una volta."""
    identita = giornale_dei_blocchi(cartella)
    if identita is None:
        return False
    impronta, resta = hashlib.sha256(), identita["byte"]
    try:
        with open(giornale, "rb") as f:
            if os.fstat(f.fileno()).st_size != resta:
                return False
            while resta > 0:
                pezzo = f.read(min(resta, 1 << 20))
                if not pezzo:
                    return False
                impronta.update(pezzo)
                resta -= len(pezzo)
    except OSError:
        return False
    return impronta.hexdigest() == identita["sha256"]


# --- il lettore -------------------------------------------------------------------


class Blocchi:
    """I blocchi attivi: quelli caricati dal disco, dentro un tetto di byte. Esce per primo quello usato meno di recente."""

    def __init__(self, tetto: int) -> None:
        self.tetto = tetto
        self.blocchi: OrderedDict[tuple[int, int], bytes] = OrderedDict()
        self.usati = 0
        self.richieste = self.presenti = self.caricati = self.byte_letti = self.espulsi = self.ns_di_lettura = 0
        self.visti: dict[tuple[int, int], bool] | None = None  # durante una misura: blocco -> era già in RAM al primo uso

    def prendi(self, file: int, numero: int, carica) -> bytes:
        chiave = (file, numero)
        self.richieste += 1
        b = self.blocchi.get(chiave)
        if b is not None:
            self.presenti += 1
            self.blocchi.move_to_end(chiave)
            if self.visti is not None:
                self.visti.setdefault(chiave, True)
            return b
        t = time.perf_counter_ns()
        b = carica()
        self.ns_di_lettura += time.perf_counter_ns() - t
        self.caricati += 1
        self.byte_letti += len(b)
        if self.visti is not None:
            self.visti.setdefault(chiave, False)
        self.blocchi[chiave] = b
        self.usati += len(b)
        while self.usati > self.tetto and len(self.blocchi) > 1:
            _, fuori = self.blocchi.popitem(last=False)
            self.usati -= len(fuori)
            self.espulsi += 1
        return b


class ReteParziale:
    """La rete letta dal disco un blocco alla volta. Stessi identificativi e stesse risposte del motore attuale."""

    def __init__(self, cartella: Path | str, tetto: int = 256 << 20, diretto: bool = False) -> None:
        inizio = time.perf_counter()
        self.cartella = Path(cartella)
        testa = json.loads((self.cartella / "testa.json").read_text(encoding="utf-8"))
        self.testa = testa
        self.n_fatti, self.n_neuroni, self.n_eventi = testa["fatti"], testa["neuroni"], testa["eventi"]
        self.diretto = diretto
        marca = os.O_RDONLY | (os.O_DIRECT if diretto else 0)
        self.fd = {f: os.open(self.cartella / nome, marca) for f, nome in NOMI_FILE.items()}
        self.lunghezza = {f: os.fstat(fd).st_size for f, fd in self.fd.items()}

        def tabella(nome: str) -> array:
            t = array("Q")
            t.frombytes((self.cartella / nome).read_bytes())
            return t

        # Quello che resta sempre in RAM: dove comincia ogni blocco, la prima chiave di ogni pagina, dove
        # comincia ogni secchio dei nomi, e il vocabolario delle linee (tipi e ruoli con i loro nomi).
        self.inizi = {FATTI: tabella("fatti.idx"), NOMI: tabella("nomi.idx"), EVENTI: tabella("eventi.idx")}
        self.primi = tabella("chiavi.idx")
        self.secchi = tabella("nomi.hash.idx")
        self.linea = {(r << BIT) | t: i for i, (r, t) in enumerate(testa["linee"])}
        self.forma = {int(t): r for t, r in testa["forma"].items()}
        self.origini, self.generi = testa["origini"], testa["generi"]
        self.vocabolario = {piana(n): int(i) for i, n in testa["nomi"].items()}
        self.blocchi = Blocchi(tetto)
        # quello che non entra e non esce mai: le tabelle qui sopra
        self.sempre_in_memoria = sum(t.itemsize * len(t) for t in (*self.inizi.values(), self.primi, self.secchi)) + (self.cartella / "testa.json").stat().st_size
        self.apertura_s = time.perf_counter() - inizio

    def chiudi(self) -> None:
        for fd in self.fd.values():
            os.close(fd)
        self.fd = {}

    def svuota(self) -> None:
        """Butta via tutti i blocchi che ha in memoria: la prossima lettura li riprende dal disco. Serve a misurare
        quanto costa una domanda la prima volta."""
        self.blocchi.blocchi.clear()
        self.blocchi.usati = 0

    def occupazione(self) -> dict:
        """Quanta rete c'è in memoria adesso e quanta sul disco, in byte, con i contatori dei blocchi."""
        c = self.blocchi
        return {"in_memoria": c.usati, "tetto": c.tetto, "sempre_in_memoria": self.sempre_in_memoria, "su_disco": sum(self.lunghezza.values()),
                "blocchi": len(c.blocchi), "richiesti": c.richieste, "trovati": c.presenti, "caricati": c.caricati, "espulsi": c.espulsi}

    # i blocchi

    def _leggi(self, file: int, dove: int, quanti: int) -> bytes:
        if not self.diretto:
            return os.pread(self.fd[file], quanti, dove)
        # Lettura diretta, senza passare dalla memoria del sistema: posizione, lunghezza e memoria devono essere allineate.
        inizio = dove & ~4095
        fine = min((dove + quanti + 4095) & ~4095, (self.lunghezza[file] + 4095) & ~4095)
        tampone = mmap.mmap(-1, fine - inizio)
        letti = os.preadv(self.fd[file], [tampone], inizio)
        dati = tampone[dove - inizio:min(dove - inizio + quanti, letti)]
        tampone.close()
        return dati

    def _blocco(self, file: int, numero: int) -> bytes:
        """Un blocco di record (fatti, nomi, eventi): comincia e finisce dove dice la tabella."""
        inizi = self.inizi[file]
        return self.blocchi.prendi(file, numero, lambda: self._leggi(file, inizi[numero], inizi[numero + 1] - inizi[numero]))

    def _tratto(self, file: int, dove: int, quanti: int, taglia: int = BLOCCO) -> bytes:
        """Un tratto di un file a blocchi di taglia fissa, preso dai blocchi che lo contengono."""
        primo, ultimo = dove // taglia, (dove + quanti - 1) // taglia
        if primo == ultimo:
            b = self.blocchi.prendi(file, primo, lambda: self._leggi(file, primo * taglia, taglia))
            return b[dove - primo * taglia:dove - primo * taglia + quanti]
        pezzi = [self.blocchi.prendi(file, n, lambda n=n: self._leggi(file, n * taglia, taglia)) for n in range(primo, ultimo + 1)]
        return b"".join(pezzi)[dove - primo * taglia:dove - primo * taglia + quanti]

    # neuroni, fatti, eventi

    def nome(self, n: int) -> str:
        if not 0 <= n < self.n_neuroni:
            raise IndexError(f"il neurone {n} non esiste")
        b = self._blocco(NOMI, n // NOMI_PER_BLOCCO)
        da, a = struct.unpack_from("<II", b, 4 * (n % NOMI_PER_BLOCCO))
        return b[da:a].decode()

    def trova(self, nome: str) -> list[int]:
        """I neuroni con quel nome. Si legge il secchio del nome e si controlla sul nome vero: due nomi diversi possono avere la stessa impronta."""
        chiave = piana(nome)
        h = impronta(nome)
        secchio = h % (len(self.secchi) - 1)
        da, a = self.secchi[secchio], self.secchi[secchio + 1]
        if da == a:
            return []
        voci = self._tratto(HASH, da * VOCE_HASH.size, (a - da) * VOCE_HASH.size)
        return [i for x, i in VOCE_HASH.iter_unpack(voci) if x == h and piana(self.nome(i)) == chiave]

    def fatto(self, f: int) -> tuple[int, int, int, tuple[int, ...]]:
        """(tipo, evento che l'ha scritto, evento che l'ha ritirato o -1, sinapsi come ruolo, neurone, ruolo, neurone...)."""
        if not 0 <= f < self.n_fatti:
            raise IndexError(f"il fatto {f} non esiste")
        b = self._blocco(FATTI, f // FATTI_PER_BLOCCO)
        dove = struct.unpack_from("<I", b, 4 * (f % FATTI_PER_BLOCCO))[0]
        tipo, nato, morto, n = TESTA_FATTO.unpack_from(b, dove)
        return tipo, nato, morto, _SINAPSI[n].unpack_from(b, dove + TESTA_FATTO.size)

    def leggi(self, f: int, ruolo: int) -> int:
        """Il partecipante del fatto con quel ruolo, -1 se non c'è."""
        sinapsi = self.fatto(f)[3]
        for j in range(0, len(sinapsi), 2):
            if sinapsi[j] == ruolo:
                return sinapsi[j + 1]
        return -1

    def evento(self, e: int) -> tuple[str, str, float, str]:
        """(frase, origine, quando, genere)."""
        if not 0 <= e < self.n_eventi:
            raise IndexError(f"l'evento {e} non esiste")
        b = self._blocco(EVENTI, e // EVENTI_PER_BLOCCO)
        da, a = struct.unpack_from("<II", b, 4 * (e % EVENTI_PER_BLOCCO))
        quando, origine, genere = TESTA_EVENTO.unpack_from(b, da)
        return b[da + TESTA_EVENTO.size:a].decode(), self.origini[origine], quando, self.generi[genere]

    # le linee

    def _voce(self, chiave: int) -> tuple[int, int] | None:
        pagina = bisect.bisect_right(self.primi, chiave) - 1
        if pagina < 0:
            return None
        b = self.blocchi.prendi(CHIAVI, pagina, lambda: self._leggi(CHIAVI, pagina * PAGINA, PAGINA))
        basso, alto = 0, len(b) // VOCE.size
        while basso < alto:
            mezzo = (basso + alto) // 2
            x = struct.unpack_from("<Q", b, mezzo * VOCE.size)[0]
            if x < chiave:
                basso = mezzo + 1
            elif x > chiave:
                alto = mezzo
            else:
                _, quanti, dove = VOCE.unpack_from(b, mezzo * VOCE.size)
                return quanti, dove
        return None

    def accesi(self, tipo: int, ruolo: int, neurone: int, ritirati: bool = False) -> tuple[int, ...]:
        """I fatti di quel tipo che ricevono quel neurone in quel ruolo: quelli in vigore, o quelli ritirati."""
        i = self.linea.get((ruolo << BIT) | tipo)
        if i is None or neurone < 0:
            return ()
        v = self._voce((RITIRATO if ritirati else 0) | (i << 32) | neurone)
        if v is None:
            return ()
        quanti, dove = v
        if quanti == 1:
            return (dove,)
        return struct.unpack(f"<{quanti}I", self._tratto(LISTE, dove, 4 * quanti))

    def quanti(self, tipo: int, ruolo: int, neurone: int, ritirati: bool = False) -> int:
        """Quanti fatti accende quel neurone su quella linea: lo dice la voce, senza leggere l'elenco."""
        i = self.linea.get((ruolo << BIT) | tipo)
        if i is None or neurone < 0:
            return 0
        v = self._voce((RITIRATO if ritirati else 0) | (i << 32) | neurone)
        return v[0] if v else 0

    def scorri(self, tipo: int, ruolo: int, ritirati: bool = False):
        """Tutti i neuroni di una linea con i loro fatti, in ordine di neurone: è il modo di passare tutti i fatti di un tipo."""
        i = self.linea.get((ruolo << BIT) | tipo)
        if i is None:
            return
        base = (RITIRATO if ritirati else 0) | (i << 32)
        fine = base | 0xFFFFFFFF
        pagina = max(0, bisect.bisect_right(self.primi, base) - 1)
        while pagina < len(self.primi) and self.primi[pagina] <= fine:
            b = self.blocchi.prendi(CHIAVI, pagina, lambda p=pagina: self._leggi(CHIAVI, p * PAGINA, PAGINA))
            for chiave, quanti, dove in VOCE.iter_unpack(b):
                if chiave < base:
                    continue
                if chiave > fine:
                    return
                yield chiave & 0xFFFFFFFF, ((dove,) if quanti == 1 else struct.unpack(f"<{quanti}I", self._tratto(LISTE, dove, 4 * quanti)))
            pagina += 1

    def storia(self, tipo: int, ruolo: int, neurone: int, chiesto: int) -> list[tuple[int, int, int]]:
        """Tutti i valori avuti, dal più vecchio: (valore, evento che l'ha scritto, evento che l'ha ritirato o -1)."""
        fatti = sorted(self.accesi(tipo, ruolo, neurone) + self.accesi(tipo, ruolo, neurone, ritirati=True))
        out = []
        for f in fatti:
            _, nato, morto, sinapsi = self.fatto(f)
            valore = next((sinapsi[j + 1] for j in range(0, len(sinapsi), 2) if sinapsi[j] == chiesto), -1)
            out.append((valore, nato, morto))
        return out

    # il vocabolario delle linee: tipi e ruoli per nome, senza toccare il disco

    def id(self, nome: str) -> int:
        try:
            return self.vocabolario[piana(nome)]
        except KeyError:
            trovati = self.trova(nome)
            if len(trovati) != 1:
                raise KeyError(nome) from None
            return trovati[0]

    # misure

    def misura(self, fa):
        """Esegue ``fa()`` e dice quanti blocchi ha chiesto, quanti erano già in RAM, quanti byte ha letto e in quanto tempo."""
        c = self.blocchi
        prima = (c.richieste, c.caricati, c.byte_letti, c.espulsi)
        c.visti = {}
        t = time.perf_counter_ns()
        try:
            risposta = fa()
        finally:
            ns = time.perf_counter_ns() - t
            visti, c.visti = c.visti, None
        return risposta, {"blocchi_richiesti": len(visti), "gia_in_ram": sum(visti.values()), "caricati": c.caricati - prima[1],
                          "letture_di_blocco": c.richieste - prima[0], "byte_letti": c.byte_letti - prima[2], "espulsi": c.espulsi - prima[3],
                          "microsecondi": round(ns / 1000, 1), "blocchi": sorted(visti)}


# --- la rete a blocchi vista dalla lingua ---------------------------------------------

POSTI = 7  # le sinapsi di un fatto sono al massimo 127: il posto di una sinapsi sta nei 7 bit bassi


class _Linea:
    """Una linea come la vede la lingua: da un neurone ai fatti che lo ricevono lì (uno solo, oppure un elenco)."""

    def __init__(self, r: ReteParziale, tipo: int, ruolo: int, voci: int, ritirati: bool = False) -> None:
        self.r, self.tipo, self.ruolo, self.voci, self.ritirati = r, tipo, ruolo, voci, ritirati

    def get(self, neurone: int, se_manca=None):
        fatti = self.r.accesi(self.tipo, self.ruolo, neurone, self.ritirati)
        return se_manca if not fatti else fatti[0] if len(fatti) == 1 else fatti

    def __contains__(self, neurone: int) -> bool:
        return bool(self.r.accesi(self.tipo, self.ruolo, neurone, self.ritirati))

    def __len__(self) -> int:
        return self.voci

    def __iter__(self):
        for neurone, _ in self.r.scorri(self.tipo, self.ruolo, self.ritirati):
            yield neurone

    def items(self):
        for neurone, fatti in self.r.scorri(self.tipo, self.ruolo, self.ritirati):
            yield neurone, (fatti[0] if len(fatti) == 1 else fatti)

    def values(self):
        for _, fatti in self.items():
            yield fatti


class _Indice:
    """Una colonna del motore (un valore per indice), calcolata al momento da una funzione."""

    def __init__(self, leggi, quanti: int) -> None:
        self.leggi, self.quanti = leggi, quanti

    def __getitem__(self, i: int):
        return self.leggi(i)

    def __len__(self) -> int:
        return self.quanti


class _PerNome:
    def __init__(self, r: ReteParziale) -> None:
        self.r = r

    def get(self, chiave: str, se_manca=None):
        return self.r.trova(chiave) or se_manca

    def __contains__(self, chiave: str) -> bool:
        return bool(self.r.trova(chiave))

    def __getitem__(self, chiave: str) -> list[int]:
        trovati = self.r.trova(chiave)
        if not trovati:
            raise KeyError(chiave)
        return trovati


class ComeRete:
    """La rete a blocchi con la forma che la lingua si aspetta dal motore.

    ``sinapsidb.lingua`` è scritta per il motore, che tiene tutta la rete in memoria: legge le sue linee come
    dizionari e i suoi fatti come colonne. Qui dietro ogni lettura c'è un blocco preso dal disco o dalla memoria.
    Non ci sono i rilevatori di coppia: una domanda su fatti di qualsiasi tipo parte dalle linee del primo indizio,
    come fa la lingua quando non li trova.
    """

    def __init__(self, r: ReteParziale) -> None:
        self.r = r
        self.forma = {t: (tuple(f) if f else None) for t, f in r.forma.items()}
        self.vivi, self.morti = {}, {}
        for (ruolo, tipo), (in_vigore, ritirate) in zip(r.testa["linee"], r.testa["voci_per_linea"]):
            linea = (ruolo << BIT) | tipo
            self.vivi[linea] = _Linea(r, tipo, ruolo, in_vigore)
            if ritirate:
                self.morti[linea] = _Linea(r, tipo, ruolo, ritirate, ritirati=True)
        vocabolario = {int(i): nome for i, nome in r.testa["nomi"].items()}  # tipi e ruoli: i loro nomi sono sempre in RAM
        self.nomi = _Indice(lambda i: vocabolario.get(i) or r.nome(i), r.n_neuroni)
        self.per_nome = _PerNome(r)
        self.f_inizio = _Indice(lambda f: f << POSTI, r.n_fatti)
        self.s_a = _Indice(lambda i: self._sinapsi(i >> POSTI)[2 * (i & ((1 << POSTI) - 1)) + 1], r.n_fatti << POSTI)
        self.f_tipo = _Indice(lambda f: r.fatto(f)[0], r.n_fatti)
        self.f_nato = _Indice(lambda f: r.fatto(f)[1], r.n_fatti)
        self.f_morto = _Indice(lambda f: r.fatto(f)[2], r.n_fatti)
        self.e_frase = _Indice(lambda e: r.evento(e)[0], r.n_eventi)
        self.ruoli_coppia: set[int] = set()
        self.coppie: dict = {}
        self._ultimo = (-1, ())

    def conteggi(self) -> dict:
        """Gli stessi conteggi del motore, letti dalla testa dei blocchi."""
        t = self.r.testa
        return {"neuroni": t["neuroni"], "fatti": t["fatti"], "fatti_in_vigore": t["fatti"] - t["ritirati"], "sinapsi": t["sinapsi"], "eventi": t["eventi"]}

    def svuota(self) -> None:
        self._ultimo = (-1, ())
        self.r.svuota()

    def chiudi(self) -> None:
        self.r.chiudi()

    def _sinapsi(self, f: int) -> tuple[int, ...]:
        """Le sinapsi di un fatto. Chi legge più ruoli dello stesso fatto lo fa di seguito: l'ultimo fatto letto resta a portata di mano."""
        if self._ultimo[0] != f:
            self._ultimo = (f, self.r.fatto(f)[3])
        return self._ultimo[1]

    def leggi(self, f: int, ruolo: int) -> int:
        sinapsi = self._sinapsi(f)
        for j in range(0, len(sinapsi), 2):
            if sinapsi[j] == ruolo:
                return sinapsi[j + 1]
        return -1

    def riceve(self, f: int, ruolo: int, neurone: int) -> bool:
        sinapsi = self._sinapsi(f)
        return any(sinapsi[j] == ruolo and sinapsi[j + 1] == neurone for j in range(0, len(sinapsi), 2))

    def scatta(self, tipo: int, indizi):
        """I fatti in vigore di quel tipo che ricevono tutti gli indizi: si parte da quello che ne accende meno."""
        accesi = []
        for ruolo, neurone in indizi:
            quanti = self.r.quanti(tipo, ruolo, neurone)  # la voce della linea dice quanti sono: l'elenco si legge solo per l'indizio più raro
            if not quanti:
                return ()
            accesi.append((quanti, ruolo, neurone))
        accesi.sort()
        _, ruolo, neurone = accesi[0]
        resto = [(r, n) for _, r, n in accesi[1:]]
        return [f for f in self.r.accesi(tipo, ruolo, neurone) if all(self.riceve(f, r, n) for r, n in resto)]

    def intorno(self, neurone: int, limite: int = 6) -> list[str]:
        """I primi fatti in cui il neurone compare, in parole: serve a distinguere gli omonimi."""
        out = []
        for linea, d in self.vivi.items():
            fatti = self.r.accesi(linea & MASCHERA, linea >> BIT, neurone)
            for f in fatti[:limite - len(out)]:
                tipo, _, _, sinapsi = self.r.fatto(f)
                out.append(f"{self.nomi[tipo]} ({', '.join(f'{self.nomi[sinapsi[j]]}: {self.nomi[sinapsi[j + 1]]}' for j in range(0, len(sinapsi), 2))})")
            if len(out) >= limite:
                break
        return out
