"""Il servizio su socket locale di SinapsiDB per la prova del fido (docs/prova-del-fido.md).

Serve solo alla prova: mette la memoria di SinapsiDB dietro un socket Unix, come PostgreSQL e Redis, perché i tre
schieramenti si misurino attraverso lo stesso tipo di trasporto. Non è il server di SinapsiDB e non va installato:
si avvia in primo piano per la durata della prova e si chiude subito dopo.

    python prove/fido/servizio.py --cartella /percorso/della/rete --socket /percorso/del/socket

Il protocollo è una riga di JSON per richiesta e una per risposta, su una connessione che resta aperta. Ogni richiesta
ha un campo ``op`` e, se vuole, un ``id`` che torna nella risposta. Ogni scrittura porta la sua ``chiave`` di
ripetizione. La risposta ha ``ok``; se è falso c'è ``errore``.

Una scrittura risponde solo dopo che il suo evento è sul disco: prima durevole, poi confermata. La risposta porta
l'esito, il numero dell'evento, che è l'ordine di commit, la base su cui si è deciso, l'istante in cui la richiesta è
arrivata e quello in cui è stata scritta. Gli importi e i saldi sono testo decimale esatto.

Ogni richiesta può portare un ``id_tentativo``, distinto dalla chiave: torna nella risposta così com'è. Ripetendo una
richiesta con la stessa chiave cambia il tentativo, non l'evento né i suoi istanti, che restano quelli di allora.
``stato`` con una ``chiave`` dice, senza scrivere, se quella richiesta ha una ricevuta durevole: serve dopo una
conferma persa, prima di ripetere.
"""

from __future__ import annotations

import argparse

from sinapsidb.fido import Fido as Servizio

if __package__:
    from .trasporto import apri, attendi, ferma
else:  # avvio diretto da prove/fido/servizio.py
    from trasporto import apri, attendi, ferma


def avvia(cartella: str, socket: str):
    """Apre la memoria e si mette in ascolto sul socket. Restituisce il server e il filo che lo fa girare: chi chiama
    chiude con ``ferma``."""
    return apri(socket, Servizio(cartella))


def main() -> None:
    argomenti = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    argomenti.add_argument("--cartella", required=True, help="la cartella della rete")
    argomenti.add_argument("--socket", required=True, help="il percorso del socket Unix su cui ascoltare")
    scelti = argomenti.parse_args()
    attendi(*avvia(scelti.cartella, scelti.socket))


if __name__ == "__main__":
    main()
