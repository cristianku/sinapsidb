"""Terzo giro di revisione interna sulla candidata 0.10.4: «azzera» di un servizio del fido che NON ha il giornale.

Due servizi del fido sulla stessa cartella (un secondo portale avviato sulla stessa cartella, o lo stesso avviato due
volte). Il secondo non riesce ad aprire la memoria: il giornale lo tiene il primo, «uno solo scrive». Ma il suo «azzera»
non guarda perché la memoria non è aperta: cancella la cartella (``shutil.rmtree``), con dentro il giornale che il primo
sta tenendo, ne apre una nuova allo stesso posto e risponde «azzerato». Da lì il primo scrive in un file che non ha più
un nome: il controllo «il giornale finisce dove lo avevo lasciato» passa (guarda il file aperto, non la cartella), ogni
ordine riceve «applicato» con il suo evento, ``stato`` con la chiave dice «presente», e al riavvio non c'è niente.

Regole: «una scrittura confermata che dopo la riapertura non c'è»; contratto, paragrafo 9: «Uno solo scrive, e lo si
controlla»; le note di aggiornamento della 0.10.4: «Un secondo portale avviato sulla stessa cartella, o lo stesso
avviato due volte, non scrive in quelle reti». Il banco del confronto in questa candidata ha ricevuto il file «blocco»
proprio perché «un secondo portale potrebbe azzerare o cambiare la rete sotto il primo»; il servizio del fido no.
"""

import subprocess
import sys
import textwrap

from sinapsidb.fido import Fido


def test_un_secondo_fido_non_azzera_la_memoria_che_il_primo_sta_scrivendo(tmp_path):
    primo = Fido(tmp_path / "fido")
    secondo = Fido(tmp_path / "fido")  # non cade: parte, e dice a ogni richiesta che la memoria non si apre
    try:
        assert primo.esegui({"op": "accorda", "cliente": "A", "importo": "100", "chiave": "fido"})["esito"] == "applicato"
        chiuso_fuori = secondo.esegui({"op": "stato"})
        assert chiuso_fuori["ok"] is False and chiuso_fuori["tipo"] == "non_aperta" and "uno solo scrive" in chiuso_fuori["errore"]

        azzera = secondo.esegui({"op": "azzera"})  # chi non ha il giornale butta via quello di chi ce l'ha?

        # il primo non si è accorto di niente e continua a confermare
        ordine = primo.esegui({"op": "ordine", "cliente": "A", "importo": "60", "chiave": "ordine-1"})
        visto = primo.esegui({"op": "stato", "chiave": "ordine-1"})
        confermato = ordine["ok"] and ordine["esito"] == "applicato" and visto["ok"] and visto["presente"]
    finally:
        primo.chiudi()
        secondo.chiudi()
    dopo = Fido(tmp_path / "fido")  # il riavvio: quello che c'è è quello che dice il giornale
    try:
        stato = dopo.esegui({"op": "stato", "chiave": "ordine-1"})
        fido = dopo.esegui({"op": "fido", "cliente": "A"})
    finally:
        dopo.chiudi()
    # o il secondo non azzera quello che non è suo, o il primo si ferma invece di confermare: ma una scrittura
    # confermata con la sua chiave, dopo il riavvio, deve esserci
    assert not confermato or (stato["presente"] and fido["valore"] == "40"), (
        f"azzera del secondo: {azzera}; ordine confermato dal primo: {ordine}; dopo il riavvio: {stato}, fido {fido}")


def test_lo_stesso_tra_due_processi(tmp_path):
    """Il caso del deploy: due processi. Il secondo, in un altro processo, azzera; il primo conferma un ordine che non resta."""
    primo = Fido(tmp_path / "fido")
    try:
        assert primo.esegui({"op": "accorda", "cliente": "A", "importo": "100", "chiave": "fido"})["esito"] == "applicato"
        altro = subprocess.run([sys.executable, "-c", textwrap.dedent("""
            import sys
            from sinapsidb.fido import Fido
            f = Fido(sys.argv[1])
            print(f.esegui({"op": "stato"})["tipo"], f.esegui({"op": "azzera"}))
            f.chiudi()
        """), str(tmp_path / "fido")], capture_output=True, text=True, timeout=60)
        assert altro.returncode == 0, altro.stderr
        ordine = primo.esegui({"op": "ordine", "cliente": "A", "importo": "60", "chiave": "ordine-1"})
        confermato = ordine["ok"] and ordine["esito"] == "applicato"
    finally:
        primo.chiudi()
    dopo = Fido(tmp_path / "fido")
    try:
        stato = dopo.esegui({"op": "stato", "chiave": "ordine-1"})
    finally:
        dopo.chiudi()
    assert not confermato or stato["presente"], f"l'altro processo: {altro.stdout.strip()}; ordine confermato: {ordine}; dopo il riavvio: {stato}"
