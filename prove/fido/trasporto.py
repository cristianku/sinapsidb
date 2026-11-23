"""Trasporto Unix/JSONL uguale per gli schieramenti, senza dipendenze dai motori."""

from __future__ import annotations

import json
import os
import signal
import socketserver
import threading
from contextlib import nullcontext
from pathlib import Path


class _Conversazione(socketserver.StreamRequestHandler):
    def handle(self):
        servizio = self.server.servizio
        sessione = servizio.sessione() if hasattr(servizio, "sessione") else nullcontext(servizio)
        with sessione as connessione:
            for riga in self.rfile:
                if not riga.strip():
                    continue
                try:
                    richiesta = json.loads(riga)
                except ValueError:
                    richiesta = None
                risposta = connessione.esegui(richiesta)
                self.wfile.write(json.dumps(risposta, ensure_ascii=False, separators=(",", ":")).encode() + b"\n")
                self.wfile.flush()


class Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    request_queue_size = 64


def apri(percorso, servizio):
    presa = Path(percorso)
    if presa.is_socket():
        # Il socket di una precedente esecuzione interrotta nel percorso privato della prova.
        presa.unlink()
    server = Server(str(presa), _Conversazione)
    try:
        os.chmod(presa, 0o600)
        server.servizio = servizio
        filo = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        filo.start()
    except BaseException:
        server.server_close()
        presa.unlink(missing_ok=True)
        servizio.chiudi()
        raise
    return server, filo


def ferma(server, filo):
    server.shutdown()
    filo.join()
    server.server_close()
    server.servizio.chiudi()
    Path(server.server_address).unlink(missing_ok=True)


def attendi(server, filo):
    fine = threading.Event()
    for segnale in (signal.SIGINT, signal.SIGTERM):
        signal.signal(segnale, lambda *_: fine.set())
    print(f"in ascolto su {server.server_address}", flush=True)
    try:
        fine.wait()
    finally:
        ferma(server, filo)
