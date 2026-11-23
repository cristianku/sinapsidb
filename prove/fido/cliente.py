"""Il richiedente della prova del fido: parla con uno schieramento attraverso il suo socket locale.

È lo stesso per tutti gli schieramenti: manda una riga di JSON e ne legge una. La connessione resta aperta.
"""

from __future__ import annotations

import json
import socket
from typing import Any


class Cliente:
    def __init__(self, percorso: str, attesa: float = 30.0) -> None:
        self._socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._socket.settimeout(attesa)
        self._socket.connect(percorso)
        self._righe = self._socket.makefile("rwb")
        self._conta = 0

    def chiedi(self, op: str, **campi: Any) -> dict:
        """Manda una richiesta e aspetta la sua risposta."""
        self._conta += 1
        self._righe.write(json.dumps({"id": self._conta, "op": op, **campi}, ensure_ascii=False, separators=(",", ":")).encode() + b"\n")
        self._righe.flush()
        riga = self._righe.readline()
        if not riga:
            raise ConnectionError("lo schieramento ha chiuso la connessione senza rispondere")
        risposta = json.loads(riga)
        if risposta.get("id") != self._conta:
            raise ConnectionError("la risposta non è quella della richiesta")
        return risposta

    def chiudi(self) -> None:
        self._righe.close()
        self._socket.close()

    def __enter__(self) -> "Cliente":
        return self

    def __exit__(self, *_: object) -> None:
        self.chiudi()
