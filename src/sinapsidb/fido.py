"""Il fido: una memoria di dimostrazione con il suo circuito e la sua regola.

È l'esempio del contratto delle decisioni (``docs/contratto-decisioni.md``) reso usabile: a ogni cliente si accorda
un fido; il fido residuo è il fido accordato, meno gli ordini, più i pagamenti; un ordine che lo manderebbe sotto zero
è rifiutato, e la decisione resta scritta con quello che ha letto. Un pagamento può dire da quando vale, e una
decisione passata si può riesaminare sapendo di lui, senza toccarla.

Lo usano la pagina «Decisioni» del portale e il servizio su socket della prova del fido (``prove/fido``). Lavora su
una cartella sua: «azzera» tocca solo quella.
"""

from __future__ import annotations

import fcntl
import hashlib
import os
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sinapsidb.memoria import Memoria, NomeAmbiguo, NomeAssente, Rifiutato, ScenarioIncompleto

CIRCUITO, REGOLA = "fido residuo", "fido non superato"


class Fido:
    """Le operazioni sul fido, sopra una memoria di SinapsiDB tutta sua: una cartella che non è né la memoria vera né
    la rete di prova del confronto. Un'operazione alla volta."""

    def __init__(self, cartella: str | Path) -> None:
        self.cartella = Path(cartella)
        self._lock = threading.RLock()  # «azzera» cambia la memoria sotto i piedi: una cosa alla volta
        self.memoria: Memoria | None = None
        self._non_aperta: str | None = None  # perché la memoria non si è aperta, se non si è aperta
        self._chiuso = False
        # Se la memoria non si apre (il giornale è guasto, o lo tiene un altro programma) il servizio parte lo stesso
        # e lo dice a ogni richiesta, riprovando ad aprirla: chi lo ospita, come il portale, non cade con lui.
        self._riapri()

    def _riapri(self) -> bool:
        """Apre la memoria dal giornale, chiudendo quella che c'era. Dice se adesso è aperta. È quello che fa chi tiene
        aperta una memoria che si è fermata: non le crede più, e rilegge il giornale."""
        if self.memoria is not None:
            try:
                self.memoria.chiudi()
            except Exception:  # una memoria ferma può non riuscire a chiudere pulita: il giornale si rilegge comunque
                pass
            self.memoria = None
        try:
            self._apri()
            self._non_aperta = None
            return True
        except Exception as perche:
            self._non_aperta = f"{type(perche).__name__}: {perche}"
            if self.memoria is not None:  # aperta ma non pronta (il circuito o la regola non si sono definiti): non si usa
                try:
                    self.memoria.chiudi()
                except Exception:
                    pass
                self.memoria = None
            return False

    def _apri(self) -> None:
        self.memoria = Memoria(self.cartella, scritture=True)
        rete = self.memoria.rete
        # Una rete nuova: il fido residuo e la sua regola si definiscono una volta sola. Sono due eventi: se il primo
        # avvio si è fermato tra l'uno e l'altro, chi riapre trova il circuito senza la regola, e la definisce adesso.
        # Guardare solo il circuito lascerebbe la memoria senza regola per sempre, mentre il quadro la dichiara.
        n = rete.n
        if not rete.circuiti:
            rete.proponi_circuito("Il fido residuo è il fido accordato, meno gli ordini, più i pagamenti", "prova", CIRCUITO, [
                {"osserva": n("fido accordato"), "lega": n("cliente"), "somma": n("importo"), "peso": 1},
                {"osserva": n("ordine ricevuto"), "lega": n("cliente"), "somma": n("importo"), "peso": -1},
                {"osserva": n("pagamento incassato"), "lega": n("cliente"), "somma": n("importo"), "peso": 1}])
        if not rete.regole:
            rete.proponi_regola("Il fido residuo non può diventare negativo", "prova", REGOLA, 1, "vieta", [
                {"nome": "residuo negativo", "circuito": rete.trova(CIRCUITO)[0], "limite": 0, "peso": 1}])

    def chiudi(self) -> None:
        with self._lock:
            self._chiuso = True  # una richiesta arrivata tardi non la riapre
            if self.memoria is not None:
                try:
                    self.memoria.chiudi()
                finally:
                    self.memoria = None

    def azzera(self, r: dict | None = None) -> dict:
        """Butta via questa memoria di dimostrazione e ne comincia una nuova, con il suo circuito e la sua regola."""
        with self._lock:
            if self.memoria is not None:
                try:
                    self.memoria.chiudi()
                except Exception:  # si butta via comunque
                    pass
                self.memoria = None
            rimasto = None
            try:
                if self.cartella.exists():
                    self._cancella_se_e_mia()
            except OSError as perche:
                rimasto = perche
            aperta = self._riapri()  # comunque: il servizio resta in piedi, con la memoria nuova o con quella di prima
            if rimasto is not None:
                # Non si dice «azzerato» se la memoria di prima è ancora lì, tutta o in parte.
                raise ValueError(f"la memoria di prima non si è potuta cancellare ({rimasto.strerror or rimasto}): non è stata azzerata")
            if not aperta:
                raise ValueError(f"la memoria nuova non si apre: {self._non_aperta}")
            return {"esito": "azzerato"}

    def _cancella_se_e_mia(self) -> None:
        """Cancella la cartella della memoria, ma solo se nessun altro programma ne sta scrivendo il giornale: lo prende
        per sé per il tempo di cancellarla. Altrimenti chi lo tiene aperto continuerebbe a confermare scritture in un
        file che non c'è più."""
        giornale = self.cartella / "giornale"
        if not giornale.exists():
            shutil.rmtree(self.cartella)
            return
        fd = os.open(giornale, os.O_RDONLY)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise PermissionError("un altro programma sta scrivendo in questa memoria: non si azzera da qui") from None
            shutil.rmtree(self.cartella)
        finally:
            os.close(fd)

    def _riferimento(self, r: dict, campo: str, sigla: str) -> str:
        """Il nome che distingue un fatto dagli altri (l'atto, il numero d'ordine, la causale): quello della richiesta.
        Se la richiesta non lo dice, uno fatto con la sua chiave: così la stessa richiesta ripetuta con la stessa
        chiave è la stessa richiesta, e riceve la risposta di allora. Fatto con il numero dell'evento sarebbe ogni
        volta un altro nome, quindi un altro racconto: chi ha perso la conferma e ripete si sentirebbe dire «conflitto».
        Solo senza chiave, dove ripetere non vuol dire niente, si usa il numero dell'evento che sta per essere scritto."""
        if r.get(campo) not in (None, ""):
            return str(r[campo])
        chiave = r.get("chiave")
        if chiave is not None:
            # Dall'impronta della chiave, non dalla chiave com'è scritta: due chiavi diverse devono dare due nomi
            # diversi anche quando cambiano solo per una maiuscola o uno spazio, che nei nomi della rete non contano.
            return f"{sigla}-{hashlib.sha256(f'{type(chiave).__name__}:{chiave}'.encode('utf-8', 'surrogatepass')).hexdigest()[:16]}"
        return f"{sigla}{len(self.memoria.rete.e_frase)}"

    # --- le operazioni -----------------------------------------------------------

    def _della_ricevuta(self, ricevuta: dict) -> dict:
        """Quello che di una ricevuta va in una risposta: l'esito, l'evento scritto, la base su cui si è deciso, i due
        istanti. Sono quelli di allora anche quando la richiesta viene ripetuta."""
        risposta: dict[str, Any] = {"esito": ricevuta["esito"]}
        if "evento" in ricevuta:
            evento = int(ricevuta["evento"].removeprefix("evento:"))
            rete = self.memoria.rete
            risposta |= {"evento": evento, "base": ricevuta["decisione"]["base"], "arrivo": rete.e_quando[evento], "scritto": rete.e_scritto[evento]}
        if ricevuta["esito"] not in ("applicato", "violazione_regola"):
            risposta["spiegazione"] = ricevuta.get("spiegazione")
        return risposta

    def _scrivi(self, frase: str, r: dict, fatti: list[dict] | None = None, sostituisce: list[dict] | None = None) -> dict:
        ricevuta = self.memoria.racconta(frase, fatti, sostituisce=sostituisce, chiave=r.get("chiave"), chi=str(r.get("chi", "prova")))
        return {**self._della_ricevuta(ricevuta), "ripetuta": bool(ricevuta.get("ripetuta"))}

    @staticmethod
    def _periodo(r: dict) -> dict:
        return {nome: r[campo] for campo, nome in (("vale_da", "vale da"), ("vale_fino_a", "vale fino a")) if r.get(campo)}

    def fido(self, r: dict) -> dict:
        """Il fido residuo di un cliente, per nome o per identificativo (``neurone:17``). Zero solo per un nome che la
        memoria non ha mai visto: un nome che indica più clienti non ha un fido, e si risponde che va detto quale
        (``tipo: "ambiguo"``, con i ``candidati``); un identificativo che non c'è, o un circuito che manca, sono errori."""
        try:
            letto = self.memoria.valore(CIRCUITO, r["cliente"])
        except NomeAssente as manca:
            if manca.che != "il nome":
                raise  # manca il circuito: non è un cliente senza fido, è una memoria che non è pronta
            return {"valore": "0", "aggiornato_a": len(self.memoria.rete.e_frase) - 1, "valido_al": None}
        return {"valore": letto["valore"], "aggiornato_a": int(letto["aggiornato_a"].removeprefix("evento:")), "valido_al": letto["valido_al"]}

    def accorda(self, r: dict) -> dict:
        fatto = {"tipo": "fido accordato", "legami": {"cliente": r["cliente"], "importo": r["importo"],
                                                    "atto": self._riferimento(r, "atto", "A"), **self._periodo(r)}}
        return self._scrivi(f"A {r['cliente']} è accordato un fido di {r['importo']}", r, [fatto])

    def ordine(self, r: dict) -> dict:
        fatto = {"tipo": "ordine ricevuto", "legami": {"cliente": r["cliente"], "importo": r["importo"], "numero": self._riferimento(r, "numero", "O")}}
        return self._scrivi(f"{r['cliente']} ordina per {r['importo']}", r, [fatto])

    def pagamento(self, r: dict) -> dict:
        fatto = {"tipo": "pagamento incassato", "legami": {"cliente": r["cliente"], "importo": r["importo"],
                                                         "causale": self._riferimento(r, "causale", "P"), **self._periodo(r)}}
        quando = f", valido dal {r['vale_da']}" if r.get("vale_da") else ""
        return self._scrivi(f"{r['cliente']} paga {r['importo']}{quando}", r, [fatto])

    def storno(self, r: dict) -> dict:
        return self._scrivi(f"Il pagamento {r['causale']} era un errore", r,
                            sostituisce=[{"tipo": "pagamento incassato", "indizi": {"causale": r["causale"]}}])

    def sposta(self, r: dict) -> dict:
        fatto = {"tipo": "pagamento incassato", "legami": {"cliente": r["cliente"], "importo": r["importo"], "causale": r["causale"], **self._periodo(r)}}
        return self._scrivi(f"Il pagamento {r['causale']} era di {r['cliente']}", r, [fatto],
                            sostituisce=[{"tipo": "pagamento incassato", "indizi": {"causale": r["causale"]}}])

    def candidate(self, r: dict) -> dict:
        return self.memoria.candidate(r["rettifica"], riesamina=bool(r.get("riesamina")))

    def riesamina(self, r: dict) -> dict:
        return self.memoria.riesamina(r["evento"], r.get("rettifiche"), fino_a=r.get("fino_a"))

    def verifica(self, r: dict) -> dict:
        return self.memoria.verifica(r["evento"])

    def quadro(self, r: dict | None = None) -> dict:
        """Quello che la pagina mostra: il fido di ogni cliente adesso, le ultime decisioni, e lo stato della memoria."""
        memoria = self.memoria
        rete = memoria.rete
        return {
            "clienti": [{"nome": x["di"], "id": x["di_id"], "fido": x["valore"]} for x in memoria.valori(CIRCUITO)],
            "decisioni": [d for d in memoria.decisioni(200) if not d["definizione"]],
            "conteggi": memoria.conteggi(), "ultimo_evento": len(rete.e_frase) - 1,
            "circuiti": memoria.verifica_circuiti()["esito"], "segno": memoria.segno(),
            "circuito": "fido residuo = fido accordato − ordini + pagamenti, per i fatti che valgono in quel momento",
            "regola": "un ordine è rifiutato se il fido residuo, dopo di lui, sarebbe negativo",
        }

    def esempio(self, r: dict | None = None) -> dict:
        """L'esempio del contratto, da capo: a un cliente nuovo si accordano 800 di fido; chiede un ordine da 1.000 ed è
        rifiutato; poi si registra un pagamento da 400 che vale da ieri. Restituisce gli eventi, per chi vuole
        riesaminare l'ordine rifiutato sapendo del pagamento."""
        quanti = sum(1 for x in self.memoria.valori(CIRCUITO) if x["di"].startswith("Rossi"))
        cliente = "Rossi" if not quanti else f"Rossi {quanti + 1}"
        ieri = datetime.fromtimestamp(datetime.now(timezone.utc).timestamp() - 86400, timezone.utc).strftime("%Y-%m-%d")
        passi = [self.accorda({"cliente": cliente, "importo": "800"}), self.ordine({"cliente": cliente, "importo": "1000"}),
                 self.pagamento({"cliente": cliente, "importo": "400", "vale_da": ieri})]
        return {"cliente": cliente, "fido": passi[0].get("evento"), "ordine": passi[1].get("evento"), "pagamento": passi[2].get("evento"),
                "esiti": [p["esito"] for p in passi]}

    def stato(self, r: dict) -> dict:
        if "chiave" in r:  # sola lettura: c'è una ricevuta durevole per questa chiave?
            ricevuta = self.memoria.ricevuta_di(r["chiave"])
            return {"chiave": r["chiave"], "presente": ricevuta is not None, **(self._della_ricevuta(ricevuta) if ricevuta else {})}
        rete = self.memoria.rete
        return {**self.memoria.conteggi(), "ultimo_evento": len(rete.e_frase) - 1, "segno": self.memoria.segno(),
                "circuiti": self.memoria.verifica_circuiti()["esito"]}

    OPERAZIONI = ("fido", "accorda", "ordine", "pagamento", "storno", "sposta", "candidate", "riesamina", "verifica", "stato",
                  "quadro", "esempio", "azzera")

    def esegui(self, richiesta: Any) -> dict:
        """Una richiesta, una risposta. Non solleva mai: un errore torna come risposta con ``ok`` falso.

        Se la memoria si è fermata (un evento è nel giornale e non in memoria, o una scrittura è rimasta a metà sul
        disco) non le si crede più: la si riapre dal giornale, prima di servire la richiesta che viene dopo. Chi ha
        chiesto proprio l'operazione che l'ha fermata riceve ``tipo: "da_ripetere"``: l'esito non si sa da qui. Lo dice
        il giornale: ripetendo con la stessa chiave, o chiedendo ``stato`` con quella chiave."""
        identificativo = richiesta.get("id") if isinstance(richiesta, dict) else None
        try:
            if not isinstance(richiesta, dict) or richiesta.get("op") not in self.OPERAZIONI:
                raise ValueError(f"serve «op», una tra: {', '.join(self.OPERAZIONI)}")
            with self._lock:
                if self._chiuso:
                    return {"id": identificativo, "ok": False, "tipo": "chiuso", "errore": "il servizio è stato chiuso"}
                if richiesta["op"] != "azzera" and (self.memoria is None or self.memoria.rete.fuori_uso) and not self._riapri():
                    return {"id": identificativo, "ok": False, "tipo": "non_aperta",
                            "errore": f"la memoria delle decisioni non si apre: {self._non_aperta}"}
                try:
                    risposta = {"id": identificativo, "ok": True, **getattr(self, richiesta["op"])(richiesta)}
                except Exception as perche:
                    # Prima di tutto: la memoria si è fermata? Allora, qualunque errore sia arrivato qui (anche uno che
                    # sembra un rifiuto), l'esito non si sa: la richiesta può essere nel giornale. Non si risponde no.
                    fermata = self.memoria is None or bool(self.memoria.rete.fuori_uso)
                    if not fermata:
                        if isinstance(perche, (KeyError, ValueError)):
                            raise  # la richiesta non sta in piedi, o è stata rifiutata: lo si dice qui sotto
                        return {"id": identificativo, "ok": False, "tipo": "errore", "errore": str(perche)}
                    riaperta = self._riapri()  # chi viene dopo trova quello che dice il giornale
                    return {"id": identificativo, "ok": False, "tipo": "da_ripetere", "errore": f"{perche}. " + (
                        "La memoria è stata riaperta dal giornale" if riaperta else "La memoria non si è riaperta, si riprova alla prossima richiesta")
                        + ": per sapere se la richiesta è entrata, ripetila con la stessa chiave o chiedi «stato» con quella chiave"}
            if "id_tentativo" in richiesta:  # il tentativo è di chi chiede: torna com'è, fuori dalla ricevuta
                risposta["id_tentativo"] = richiesta["id_tentativo"]
            return risposta
        except KeyError as manca:
            return {"id": identificativo, "ok": False, "tipo": "richiesta", "errore": f"alla richiesta manca {manca}"}
        except ScenarioIncompleto as perche:
            return {"id": identificativo, "ok": False, "tipo": "scenario_incompleto", "errore": str(perche)}
        except Rifiutato as perche:
            return {"id": identificativo, "ok": False, "tipo": "rifiutato", "errore": str(perche)}
        except NomeAmbiguo as perche:  # non si sceglie al posto di chi chiede: dice quali sono, e chiede quale
            return {"id": identificativo, "ok": False, "tipo": "ambiguo", "errore": str(perche), "candidati": perche.candidati}
        except ValueError as perche:
            return {"id": identificativo, "ok": False, "tipo": "richiesta", "errore": str(perche)}


