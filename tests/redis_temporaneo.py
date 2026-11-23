"""Un Redis privato per le prove.

Fa la stessa cosa che ``pg_temporaneo.py`` fa per PostgreSQL: un processo figlio in una cartella
temporanea, che ascolta solo su un socket Unix in quella cartella e alla fine viene spento e
cancellato. Con la porta di rete disattivata (``--port 0``) è fisicamente impossibile finire sul
Redis di sistema. Il percorso del socket non può superare i 107 caratteri: la cartella nasce corta,
in ``/tmp``, con prefisso ``rp.``.

``avvia`` si usa con ``with`` e dà il percorso del socket, accanto alla cartella e al processo così
che il test possa controllare che all'uscita non resti niente. ``opzioni`` cambia le sue impostazioni
(servono per provare anche ``appendfsync always``) e ``dove`` sceglie la cartella. Se il programma o
la libreria non ci sono, solleva ``NonDisponibile`` e i test che lo usano vengono saltati.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import signal
import subprocess
import tempfile
import time
from collections import namedtuple

LUNGHEZZA_MAX_SOCKET = 107

# Impostazioni di serie del servizio temporaneo: nessuna porta di rete, persistenza solo a file.
# Sono il punto di partenza della prova: il manifesto le fissa, e chi misura le cambia da qui.
OPZIONI = {
    "port": "0",
    "unixsocketperm": "600",
    "save": "",
    "appendonly": "yes",
    "appendfsync": "everysec",
    "maxmemory": "256mb",
}

Servizio = namedtuple("Servizio", "cartella socket processo")


class NonDisponibile(Exception):
    pass


@contextlib.contextmanager
def avvia(opzioni: dict[str, str] | None = None, dove: str = "/tmp"):
    try:
        import redis
    except ImportError as e:
        raise NonDisponibile("redis non è installato") from e
    if shutil.which("redis-server") is None:
        raise NonDisponibile("redis-server non è installato")
    base = tempfile.mkdtemp(prefix="rp.", dir=dove)
    socket_path = os.path.join(base, "r.sock")
    processo = None
    try:
        if len(socket_path) > LUNGHEZZA_MAX_SOCKET:
            raise NonDisponibile(f"il percorso del socket supererebbe i {LUNGHEZZA_MAX_SOCKET} caratteri: {socket_path}")
        comandi = ["redis-server", "--unixsocket", socket_path, "--dir", base]
        for nome, valore in {**OPZIONI, **(opzioni or {})}.items():
            comandi += [f"--{nome}", str(valore)]
        processo = subprocess.Popen(comandi, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):  # dieci secondi d'attesa per il primo PING
            try:
                conn = redis.Redis(unix_socket_path=socket_path, socket_connect_timeout=2)
                conn.ping()
                conn.close()
                break
            except (redis.RedisError, OSError) as e:
                if processo.poll() is not None:
                    raise NonDisponibile("il Redis temporaneo è morto all'avvio") from e
                time.sleep(0.1)
        else:
            raise NonDisponibile("il Redis temporaneo non è partito")
        yield Servizio(base, socket_path, processo)
    finally:
        if processo is not None:
            try:
                processo.send_signal(signal.SIGTERM)
                processo.wait(10)
            except subprocess.TimeoutExpired:
                processo.kill()
                processo.wait(5)
        shutil.rmtree(base, ignore_errors=True)
