"""Il confronto con PostgreSQL con molti più dati.

Per ogni taglia avvia un PostgreSQL privato e temporaneo su un disco vero, ci genera l'azienda di prova,
la importa in una rete di SinapsiDB, lancia le stesse domande del confronto del portale e misura
tempi, memoria, spazio su disco e tempo di riapertura. Alla fine spegne e cancella tutto.

    .venv/bin/python tests/scala_confronto.py 100000 300000 1000000 --esito /percorso/esito.json

Gli argomenti sono le righe totali dell'azienda. Non è un test di pytest: va lanciato a mano, perché
con le taglie alte usa alcuni gigabyte di memoria e di disco per qualche minuto. Il processo di
SinapsiDB ha un tetto di memoria (``--tetto-gb``): se lo supera fallisce lui, non la macchina.

A PostgreSQL qui si dà più agio che a quello appena installato: più memoria condivisa e più memoria
per ordinare, come farebbe chi lo tiene bene con tanti dati. Può usare più core; SinapsiDB ne usa uno.
"""

from __future__ import annotations

import argparse
import json
import os
import resource
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

QUI = Path(__file__).resolve().parent
sys.path.insert(0, str(QUI))

PG_A_SUO_AGIO = {"shared_buffers": "2GB", "work_mem": "64MB", "effective_cache_size": "4GB",
                 "wal_level": "minimal", "max_wal_senders": "0", "max_wal_size": "4GB"}


def _rss_mb() -> int:
    for riga in Path("/proc/self/status").read_text().splitlines():
        if riga.startswith("VmRSS:"):
            return round(int(riga.split()[1]) / 1024)
    return 0


def _picco_mb() -> int:
    return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024)


def figlio_importa(dsn: str, cartella: str, quante: int) -> None:
    from sinapsidb import confronto
    from sinapsidb.motore import Rete

    prima = _rss_mb()
    rete = Rete(cartella, sync="os")
    with confronto.collega(dsn) as conn:
        esito = confronto.importa(conn, rete)
        dopo_importazione = _rss_mb()
        t = time.perf_counter()
        rete.istantanea()
        istantanea_s = time.perf_counter() - t
        t = time.perf_counter()
        misure = confronto.misura(conn, rete, quante=quante)
        misura_s = time.perf_counter() - t
        pg_mb = conn.execute("SELECT pg_database_size(current_database())").fetchone()[0] / 1e6
        righe = {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in confronto.TABELLE}
    rete.chiudi()
    print(json.dumps({"righe": righe, "importazione": esito, "memoria_prima_mb": prima, "memoria_dopo_importazione_mb": dopo_importazione,
                      "picco_mb": _picco_mb(), "istantanea_s": round(istantanea_s, 2), "misura_s": round(misura_s, 1),
                      "postgres_su_disco_mb": round(pg_mb), "confronto": misure}))


def figlio_riapri(cartella: str) -> None:
    from sinapsidb.motore import Rete

    t = time.perf_counter()
    rete = Rete(cartella, sync="os")
    secondi = time.perf_counter() - t
    print(json.dumps({"riapertura_s": round(secondi, 2), "memoria_mb": _rss_mb(), "fatti": rete.conteggi()["fatti"]}))
    rete.chiudi()


def _figlio(argomenti: list[str], tetto_gb: float) -> dict:
    def limite() -> None:  # un tetto alla memoria del figlio: se lo supera fallisce lui, non la macchina
        resource.setrlimit(resource.RLIMIT_AS, (int(tetto_gb * 2**30), int(tetto_gb * 2**30)))

    r = subprocess.run([sys.executable, __file__, *argomenti], capture_output=True, text=True, preexec_fn=limite)
    if r.returncode != 0:
        raise RuntimeError(f"{argomenti[0]} fallito:\n{r.stderr[-2000:]}")
    return json.loads(r.stdout.strip().splitlines()[-1])


def _tempo(us: float) -> str:
    return f"{us:.1f} µs" if us < 1000 else f"{us / 1000:.2f} ms" if us < 1_000_000 else f"{us / 1_000_000:.2f} s"


def una_taglia(righe: int, dove: str, quante: int, tetto_gb: float) -> dict:
    import pg_temporaneo

    base = tempfile.mkdtemp(prefix="sinapsidb-scala-", dir=dove)
    try:
        with pg_temporaneo.avvia(dove=dove, opzioni=PG_A_SUO_AGIO) as dsn:
            t = time.perf_counter()
            subprocess.run([sys.executable, "-m", "sinapsidb.dati_prova", "--dsn", dsn, "--righe", str(righe), "--seme", "417"],
                           check=True, capture_output=True)
            carico_s = time.perf_counter() - t
            esito = _figlio(["--figlio-importa", dsn, f"{base}/rete", str(quante)], tetto_gb)
        esito["riapertura"] = _figlio(["--figlio-riapri", f"{base}/rete"], tetto_gb)
        esito["rete_su_disco_mb"] = round(sum(f.stat().st_size for f in Path(base, "rete").iterdir()) / 1e6)
        esito["giornale_mb"] = round(Path(base, "rete", "giornale").stat().st_size / 1e6)
        esito["caricamento_postgres_s"] = round(carico_s, 1)
        esito["righe_richieste"] = righe
        return esito
    finally:
        shutil.rmtree(base, ignore_errors=True)


def stampa(e: dict) -> None:
    c, imp = e["confronto"], e["importazione"]
    print(f"\n=== {sum(e['righe'].values()):,} righe in PostgreSQL in {len(e['righe'])} tabelle; {imp['fatti']:,} fatti e {imp['neuroni']:,} neuroni in SinapsiDB ===".replace(",", "."))
    print(f"generazione in PostgreSQL {e['caricamento_postgres_s']} s · importazione in SinapsiDB {imp['secondi']} s"
          f" ({imp['fatti'] / max(imp['secondi'], 0.01):,.0f} fatti al secondo) · istantanea {e['istantanea_s']} s".replace(",", "."))
    print(f"memoria di SinapsiDB con la rete caricata {e['riapertura']['memoria_mb']} MB (picco durante la prova {e['picco_mb']} MB)"
          f" · riapertura {e['riapertura']['riapertura_s']} s")
    print(f"su disco: PostgreSQL {e['postgres_su_disco_mb']} MB · SinapsiDB {e['rete_su_disco_mb']} MB (giornale {e['giornale_mb']} MB)")
    print(f"risposte diverse: {c['risposte_diverse']} su {sum(d['prove'] for d in c['domande'])}")
    print(f"{'domanda (tempo mediano)':<48}{'righe':>9}{'SinapsiDB':>12}{'PostgreSQL':>12}{'nel server':>12}  più rapido")
    for d in c["domande"]:
        si, pg = d["sinapsi"]["p50_us"], d["postgres"]["p50_us"]
        chi = f"SinapsiDB {pg / si:.1f}x" if si <= pg else f"PostgreSQL {si / pg:.1f}x"
        print(f"{d['nome']:<48}{d['righe_medie']:>9.1f}{_tempo(si):>12}{_tempo(pg):>12}{_tempo(d['postgres_nel_server_p50_us']):>12}  {chi}")


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "--figlio-importa":
        return figlio_importa(sys.argv[2], sys.argv[3], int(sys.argv[4]))
    if len(sys.argv) > 1 and sys.argv[1] == "--figlio-riapri":
        return figlio_riapri(sys.argv[2])
    parser = argparse.ArgumentParser(description="Confronto tra PostgreSQL e SinapsiDB con aziende di prova di più taglie.")
    parser.add_argument("righe", nargs="+", type=int, help="righe totali dell'azienda di prova, per esempio 300000")
    parser.add_argument("--dove", default="/var/tmp", help="cartella su un disco vero, raggiungibile dall'utente postgres")
    parser.add_argument("--quante", type=int, default=300, help="domande per tipo")
    parser.add_argument("--tetto-gb", type=float, default=8.0, help="memoria massima del processo di SinapsiDB")
    parser.add_argument("--esito", help="file JSON in cui salvare tutte le misure")
    argomenti = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit("serve root: il PostgreSQL temporaneo gira come utente postgres")
    esiti = []
    for righe in argomenti.righe:
        e = una_taglia(righe, argomenti.dove, argomenti.quante, argomenti.tetto_gb)
        esiti.append(e)
        stampa(e)
        if argomenti.esito:
            Path(argomenti.esito).write_text(json.dumps(esiti, ensure_ascii=False, indent=1), encoding="utf-8")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
