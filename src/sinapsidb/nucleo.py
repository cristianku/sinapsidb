"""Il nucleo compilato: i percorsi che passano molti fatti, fatti in C sulle colonne del motore così come sono.

È facoltativo. Se la libreria non c'è, o non è quella di questo sorgente, tutto funziona come prima, in Python, che
resta il riferimento: il nucleo deve dare le stesse righe, nello stesso ordine, oppure farsi da parte. Si fa da parte
da solo ogni volta che trova qualcosa che non sa trattare senza perdere niente: un numero con più di sei decimali, una
somma che non sta in 64 bit, un tipo di fatto in cui i ruoli non stanno sempre allo stesso posto.

Per ora fa una cosa: contare o sommare, gruppo per gruppo, **tutti** i fatti in vigore di un tipo. È la domanda senza
un punto di partenza, quella in cui il Python deve passare i fatti uno per uno.

    python -m sinapsidb.nucleo            dice se il nucleo è attivo, e se no perché
    python -m sinapsidb.nucleo compila    compila la libreria accanto a questo file: serve un compilatore C

``SINAPSIDB_NUCLEO=0`` lo spegne; ``SINAPSIDB_NUCLEO=/percorso/_nucleo.so`` dice dove sta la libreria.
"""

from __future__ import annotations

import ctypes
import hashlib
import os
import subprocess
import sys
import threading
from array import array
from decimal import MAX_EMAX, MIN_EMIN, ROUND_HALF_EVEN, Context, Decimal, Inexact, InvalidOperation, localcontext
from pathlib import Path

from sinapsidb.motore import BIT

SORGENTE = Path(__file__).with_name("nucleo.c")
LIBRERIA = Path(__file__).with_name("_nucleo.so")

FATTO, MANCA_UN_NUMERO, MANCA_UN_GRUPPO = 0, 1, 2
SCALA_IGNOTA, NON_NUMERO, FUORI_MISURA = 255, 254, 253
DECIMALI = 6  # i numeri si tengono in milionesimi, come nei circuiti
MASSIMO = 2 ** 62  # in milionesimi: oltre, un numero solo occupa già quasi tutti i 64 bit
ESATTO = Context(prec=2000, rounding=ROUND_HALF_EVEN, Emin=MIN_EMIN, Emax=MAX_EMAX, capitals=1, clamp=0,
                 traps=[Inexact, InvalidOperation])  # lo stesso della lingua
LINEE_TENUTE = 8  # di quante linee si tiene pronta la tabella dei gruppi

_caricata: tuple[ctypes.PyDLL | None, str] | None = None
# Una richiesta alla volta: tra la chiamata al nucleo e la lettura di quello che ha contato le tabelle di una rete non
# devono cambiare sotto i piedi, nemmeno se due fili interrogano la stessa rete.
_una_alla_volta = threading.Lock()


def impronta_del_sorgente() -> str:
    return hashlib.sha256(SORGENTE.read_bytes()).hexdigest()


def _dove() -> Path | None:
    scelta = os.environ.get("SINAPSIDB_NUCLEO", "").strip()
    if scelta.lower() in ("0", "no", "spento"):
        return None
    return Path(scelta) if scelta else LIBRERIA


def _carica() -> tuple[ctypes.PyDLL | None, str]:
    """La libreria, se c'è ed è quella di questo sorgente; altrimenti None e il perché."""
    global _caricata
    if _caricata is None:
        percorso = _dove()
        if percorso is None:
            _caricata = (None, "è spento da SINAPSIDB_NUCLEO")
        elif not percorso.exists():
            _caricata = (None, "la libreria non è stata compilata su questa macchina")
        else:
            try:
                # PyDLL e non CDLL: durante la chiamata Python resta fermo, e nessun altro filo può allungare una
                # colonna mentre il nucleo la sta leggendo
                libreria = ctypes.PyDLL(str(percorso))
                libreria.sinapsi_nucleo_sorgente.restype = ctypes.c_char_p
                libreria.sinapsi_nucleo_sorgente.argtypes = []
                di_chi = libreria.sinapsi_nucleo_sorgente().decode()
                if di_chi != impronta_del_sorgente():
                    _caricata = (None, "la libreria è stata compilata da un altro sorgente: va compilata di nuovo")
                else:
                    f = libreria.sinapsi_aggrega
                    f.restype = ctypes.c_int32
                    p, i64, i32 = ctypes.c_void_p, ctypes.c_int64, ctypes.c_int32
                    f.argtypes = [i64, p, p, p, i64, p, i32, i32, i32, i64, p, p, i64, p, i64, p, p, p,
                                  ctypes.POINTER(i64), ctypes.POINTER(i32)]
                    _caricata = (libreria, "")
            except (OSError, AttributeError) as e:
                _caricata = (None, f"la libreria non si carica: {e}")
    return _caricata


def ricarica() -> None:
    """Dimentica la libreria caricata: la prossima richiesta la cerca di nuovo. Serve dopo averla compilata."""
    global _caricata
    _caricata = None


def attivo() -> bool:
    return _carica()[0] is not None


def stato() -> dict:
    """Se il nucleo è attivo, e se no perché: lo mostra il portale."""
    libreria, perche = _carica()
    return {"attivo": libreria is not None, **({} if libreria is not None else {"perche": perche})}


def compila(compilatore: str | None = None, uscita: Path | None = None) -> Path:
    """Compila la libreria dal sorgente che sta accanto a questo file. Solleva se il compilatore non c'è o non riesce."""
    uscita = Path(uscita) if uscita else LIBRERIA
    provvisoria = uscita.with_name(uscita.name + ".nuova")
    # -ffreestanding e -nostdlib: il nucleo non usa la libreria C, e così per compilarlo basta il compilatore
    comando = [compilatore or os.environ.get("CC") or "cc", "-O2", "-fPIC", "-shared", "-ffreestanding", "-nostdlib", "-Wall", "-Wextra",
               f'-DSINAPSI_SORGENTE="{impronta_del_sorgente()}"', "-o", str(provvisoria), str(SORGENTE)]
    try:
        fatto = subprocess.run(comando, capture_output=True, text=True)
    except FileNotFoundError:
        raise RuntimeError(f"non c'è un compilatore C su questa macchina (ho cercato «{comando[0]}»)") from None
    if fatto.returncode:
        provvisoria.unlink(missing_ok=True)
        raise RuntimeError(f"il compilatore non è riuscito: {fatto.stderr.strip() or fatto.stdout.strip()}")
    os.chmod(provvisoria, 0o644)
    os.replace(provvisoria, uscita)
    ricarica()
    return uscita


# --- le tabelle che il nucleo legge ------------------------------------------------------------------------------

def numero_di(nome: str) -> tuple[int, int]:
    """Il numero scritto in un nome, come lo legge una somma della lingua: in milionesimi, e quanti decimali ha.
    Oppure (0, NON_NUMERO) se il nome non è un numero, e (0, FUORI_MISURA) se è un numero che così non si rappresenta
    senza perdere niente: allora la somma la fa il Python."""
    try:
        x = Decimal(nome)
    except InvalidOperation:
        return 0, NON_NUMERO
    if not x.is_finite():
        return 0, NON_NUMERO
    esponente = x.as_tuple().exponent
    decimali = max(0, -esponente)
    if decimali > DECIMALI or x.adjusted() > 30:
        return 0, FUORI_MISURA
    try:
        unita = int(x.scaleb(DECIMALI, context=ESATTO))
    except ArithmeticError:  # un numero che i milionesimi non sanno scrivere senza perdere niente: lo somma il Python
        return 0, FUORI_MISURA
    return (unita, decimali) if -MASSIMO < unita < MASSIMO else (0, FUORI_MISURA)


def in_testo(unita: int, decimali: int) -> str:
    """Una somma in milionesimi, scritta come la scrive la lingua: con i decimali dell'addendo che ne aveva di più."""
    intero, resto = divmod(unita, 10 ** (DECIMALI - decimali))
    assert resto == 0
    return format(Decimal(intero).scaleb(-decimali, context=ESATTO), "f")


class Tabelle:
    """Quello che il nucleo ha bisogno di sapere dei neuroni di una rete, tenuto accanto alla rete e riempito quando
    serve. Sono cose ricavate dai nomi: non vanno nel giornale né nell'istantanea, e si possono buttare."""

    def __init__(self) -> None:
        self.unita = array("q")  # per neurone: il numero che il suo nome scrive, in milionesimi
        self.scala = bytearray()  # per neurone: quanti decimali ha, oppure uno dei tre segni qui sopra
        self.gruppi: dict[int, tuple[array, list[int]]] = {}  # per linea: il posto di ogni neurone, e chi ha un posto

    def copri(self, quanti: int) -> None:
        """Le tabelle dei numeri arrivano fino a quel neurone: quelli nuovi non sono ancora stati guardati."""
        mancano = quanti - len(self.scala)
        if mancano > 0:
            self.unita.frombytes(bytes(8 * mancano))
            self.scala.extend(b"\xff" * mancano)

    def impara(self, nomi: list, neuroni) -> None:
        """Guarda i neuroni che non sono ancora stati guardati, tra quelli indicati."""
        self.copri(len(nomi))
        unita, scala = self.unita, self.scala
        with localcontext(ESATTO):  # un nome che non è un numero non deve lasciare un segno nel contesto di chi chiama
            for n in neuroni:
                if scala[n] == SCALA_IGNOTA:
                    unita[n], scala[n] = numero_di(nomi[n])

    def posti(self, linea: int, chiavi, quanti: int, rifai: bool) -> tuple[array, int]:
        """Il posto di ogni neurone di una linea, da zero in su, e quanti sono. La tabella è lunga quanto i neuroni
        della rete; chi non è nella linea ha -1. ``rifai``: quella tenuta da parte non va più bene."""
        tenuta = self.gruppi.get(linea)
        if tenuta is not None and not rifai and len(tenuta[0]) >= quanti:
            return tenuta[0], len(tenuta[1])
        if tenuta is None:
            if len(self.gruppi) >= LINEE_TENUTE:
                del self.gruppi[next(iter(self.gruppi))]
            indice, assegnati = array("i"), []
        else:
            indice, assegnati = tenuta
            for n in assegnati:  # chi aveva un posto lo perde: i posti si danno da capo
                if n < len(indice):
                    indice[n] = -1
        if len(indice) < quanti:
            indice.extend(array("i", [-1]) * (quanti - len(indice)))
        assegnati = list(chiavi)
        for posto, n in enumerate(assegnati):
            indice[n] = posto
        self.gruppi[linea] = (indice, assegnati)
        return indice, len(assegnati)

    def dimentica(self, da: int) -> None:
        """I neuroni da quel numero in poi non ci sono più: il loro numero può toccare a un altro nome."""
        if len(self.scala) > da:
            del self.scala[da:]
            del self.unita[da:]
        self.gruppi.clear()


def _indirizzo(colonna) -> int:
    return colonna.buffer_info()[0] if len(colonna) else 0


def aggrega(rete, tipo: int, ruolo_gruppo: int | None, ruolo_valore: int | None, taglio: int | None):
    """Conta o somma, gruppo per gruppo, tutti i fatti in vigore di un tipo. Restituisce le righe come le dà la lingua
    e quanti fatti ha passato, oppure None se il nucleo non c'è o si fa da parte: allora il conto lo fa il Python.

    ``ruolo_gruppo``: il ruolo che fa i gruppi, o None per un totale solo. ``ruolo_valore``: il ruolo da sommare, o
    None per contare. ``taglio``: quanti caratteri del nome del gruppo tenere (7 per il mese, 4 per l'anno), o None.
    """
    libreria = _carica()[0]
    if libreria is None:
        return None
    with _una_alla_volta:
        return _aggrega(libreria, rete, tipo, ruolo_gruppo, ruolo_valore, taglio)


def _aggrega(libreria, rete, tipo: int, ruolo_gruppo: int | None, ruolo_valore: int | None, taglio: int | None):
    forma = rete.forma.get(tipo)
    # i ruoli stanno allo stesso posto in ogni fatto del tipo, e ognuno una volta sola: altrimenti il posto non basta
    if not forma or len(set(forma)) != len(forma):
        return None
    try:
        posto_gruppo = forma.index(ruolo_gruppo) if ruolo_gruppo is not None else -1
        posto_valore = forma.index(ruolo_valore) if ruolo_valore is not None else -1
    except ValueError:
        return None
    tabelle = rete._tabelle_del_nucleo
    if tabelle is None:
        tabelle = rete._tabelle_del_nucleo = Tabelle()
    nomi, vivi = rete.nomi, rete.vivi
    bit = BIT
    chiave_gruppo = (ruolo_gruppo << bit) | tipo if ruolo_gruppo is not None else None
    linea_gruppo = vivi.get(chiave_gruppo) if chiave_gruppo is not None else None
    if chiave_gruppo is not None and not linea_gruppo:
        return None  # nessun fatto in vigore ha quel ruolo: non c'è niente da accelerare
    visti, chi = ctypes.c_int64(0), ctypes.c_int32(-1)
    rifai = False
    for _ in range(4):  # al più: una volta per imparare i numeri, una per rifare i gruppi, e quella buona
        if linea_gruppo is not None:
            indice, quanti_gruppi = tabelle.posti(chiave_gruppo, linea_gruppo, len(nomi), rifai)
        else:
            indice, quanti_gruppi = array("i"), 1
        tabelle.copri(len(nomi))
        somme, quanti = array("q", bytes(8 * quanti_gruppi)), array("q", bytes(8 * quanti_gruppi))
        scale = bytearray(quanti_gruppi)
        scale_c = (ctypes.c_uint8 * quanti_gruppi).from_buffer(scale)
        scala_c = (ctypes.c_uint8 * len(tabelle.scala)).from_buffer(tabelle.scala) if tabelle.scala else None
        try:
            esito = libreria.sinapsi_aggrega(
                len(rete.f_tipo), _indirizzo(rete.f_tipo), _indirizzo(rete.f_morto), _indirizzo(rete.f_inizio),
                len(rete.s_a), _indirizzo(rete.s_a), tipo, posto_gruppo, posto_valore,
                len(tabelle.scala), _indirizzo(tabelle.unita), ctypes.addressof(scala_c) if scala_c is not None else 0,
                len(indice), _indirizzo(indice), quanti_gruppi, _indirizzo(somme), ctypes.addressof(scale_c), _indirizzo(quanti),
                ctypes.byref(visti), ctypes.byref(chi))
        finally:
            del scale_c, scala_c  # lasciano libere le tabelle: finché esistono non si possono allungare
        if esito == FATTO:
            break
        if esito == MANCA_UN_NUMERO:
            linea_valore = vivi.get((ruolo_valore << bit) | tipo)
            if linea_valore is None or not 0 <= chi.value < len(nomi):
                return None
            tabelle.impara(nomi, linea_valore)
        elif esito == MANCA_UN_GRUPPO and not rifai:
            rifai = True
        else:
            return None  # una somma troppo grande, un numero che non si rappresenta: lo fa il Python
    else:
        return None
    if ruolo_gruppo is None:
        if ruolo_valore is None:
            return [(str(visti.value),)], visti.value
        return [(in_testo(somme[0], scale[0]),)], visti.value
    # I gruppi escono nell'ordine in cui la lingua li incontra passando la linea: un neurone alla volta, e per il
    # mese o l'anno più neuroni finiscono nello stesso gruppo.
    totali: dict[str, list[int]] = {}
    lungo = len(indice)
    for n in linea_gruppo:
        # un neurone senza posto non ha fatti in vigore in questa linea: il nucleo, altrimenti, si sarebbe fermato
        posto = indice[n] if n < lungo else -1
        if posto < 0 or not quanti[posto]:
            continue
        nome = nomi[n]
        gruppo = nome[:taglio] if taglio else nome
        totale = totali.get(gruppo)
        if totale is None:
            totali[gruppo] = [somme[posto], scale[posto], quanti[posto]]
        else:
            totale[0] += somme[posto]
            if scale[posto] > totale[1]:
                totale[1] = scale[posto]
            totale[2] += quanti[posto]
    if ruolo_valore is None:
        return [(gruppo, str(t[2])) for gruppo, t in totali.items()], visti.value
    return [(gruppo, in_testo(t[0], t[1])) for gruppo, t in totali.items()], visti.value


def main(argomenti: list[str] | None = None) -> int:
    argomenti = sys.argv[1:] if argomenti is None else argomenti
    if argomenti[:1] == ["compila"]:
        try:
            print(f"compilata: {compila()}")
        except RuntimeError as e:
            print(f"non compilata: {e}", file=sys.stderr)
            return 1
    visto = stato()
    print("il nucleo compilato è attivo" if visto["attivo"] else f"il nucleo compilato non è attivo: {visto['perche']}")
    return 0 if visto["attivo"] or argomenti[:1] != ["compila"] else 1


if __name__ == "__main__":
    sys.exit(main())
