"""Un PostgreSQL privato per le prove.

Parte in una cartella temporanea come utente ``postgres``, ascolta solo su un socket in quella
cartella (nessuna porta di rete) e alla fine viene spento e cancellato. Non tocca il cluster di
sistema. Serve essere root e avere i programmi di PostgreSQL installati: altrimenti ``avvia``
solleva ``NonDisponibile`` e i test che lo usano vengono saltati.

``avvia`` accetta tre scelte: ``dove`` mette il servizio su un disco vero invece che in ``/tmp``, ``opzioni``
cambia le sue impostazioni (servono alla prova con molti dati, ``scala_confronto.py``) e ``locale`` sceglie
la lingua del cluster. Con ``locale="C"`` la codifica è SQL_ASCII, come su una macchina appena installata
senza lingua configurata: lì PostgreSQL restituisce il testo come byte se chi si collega non chiede UTF-8.
"""

from __future__ import annotations

import contextlib
import glob
import os
import shutil
import subprocess
import tempfile
import time

PORTA = 54417
# Impostazioni di serie del servizio temporaneo: nessuna porta di rete, memoria come quella di un PostgreSQL appena installato.
OPZIONI = {"listen_addresses": "", "fsync": "off", "shared_buffers": "128MB", "max_connections": "20"}


class NonDisponibile(Exception):
    pass


@contextlib.contextmanager
def avvia(dove: str = "/tmp", opzioni: dict[str, str] | None = None, locale: str = "C.UTF-8"):
    try:
        import psycopg
    except ImportError as e:
        raise NonDisponibile("psycopg non è installato") from e
    programmi = sorted(glob.glob("/usr/lib/postgresql/*/bin"))
    if not programmi or os.geteuid() != 0 or not shutil.which("runuser"):
        raise NonDisponibile("servono i programmi di PostgreSQL, runuser e l'utente root")
    cartella = programmi[-1]
    base = tempfile.mkdtemp(prefix="sinapsidb-pg-", dir=dove)
    os.chmod(base, 0o755)
    shutil.chown(base, "postgres", "postgres")
    come = ["runuser", "-u", "postgres", "--"]
    dati = f"{base}/dati"
    processo = None
    try:
        subprocess.run(come + [f"{cartella}/initdb", "-D", dati, "-A", "trust", "-U", "postgres", "--no-sync", f"--locale={locale}"],
                       check=True, capture_output=True)
        impostazioni = [x for nome, valore in {**OPZIONI, **(opzioni or {})}.items() for x in ("-c", f"{nome}={valore}")]
        processo = subprocess.Popen(come + [f"{cartella}/postgres", "-D", dati, "-k", base, "-p", str(PORTA)] + impostazioni,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        amministratore = f"host={base} port={PORTA} user=postgres dbname=postgres"
        for _ in range(150):
            try:
                with psycopg.connect(amministratore, autocommit=True, connect_timeout=2) as conn:
                    conn.execute("CREATE DATABASE prova")
                break
            except psycopg.OperationalError:
                time.sleep(0.1)
        else:
            raise NonDisponibile("il PostgreSQL temporaneo non è partito")
        yield f"host={base} port={PORTA} user=postgres dbname=prova"
    finally:
        if processo is not None:
            subprocess.run(come + [f"{cartella}/pg_ctl", "-D", dati, "stop", "-m", "immediate"], capture_output=True)
            try:
                processo.wait(15)
            except subprocess.TimeoutExpired:
                processo.kill()
        shutil.rmtree(base, ignore_errors=True)
