"""Esporta la storia durevole della prova in JSONL. Non chiama le API del motore o il suo verificatore."""

from __future__ import annotations

import argparse
import json
import struct
import zlib
from datetime import datetime, timezone
from pathlib import Path


def _data(s):
    if s is None:
        return None
    d = datetime.fromisoformat(s)
    return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).timestamp()


def _record(f):
    while testa := f.read(8):
        if len(testa) != 8:
            raise ValueError("intestazione incompleta nel giornale riaperto")
        n, crc = struct.unpack("<II", testa)
        b = f.read(n)
        if len(b) != n or zlib.crc32(b) != crc:
            raise ValueError("record incompleto o checksum diverso nel giornale riaperto")
        yield json.loads(b)


def _fatti(nomi, piatti):
    specie = {"fido accordato": ("accorda", "atto"), "ordine ricevuto": ("ordine", "numero"),
              "pagamento incassato": ("pagamento", "causale")}
    for tipo, legami in piatti:
        op, rif = specie[nomi[tipo]]
        lg = {nomi[r]: nomi[v] for r, v in zip(legami[::2], legami[1::2], strict=True)}
        yield {"specie": op, "cliente": lg["cliente"], "riferimento": lg[rif], "importo": lg["importo"],
               "da": _data(lg.get("vale da")), "fine": _data(lg.get("vale fino a"))}


def _letture(d):
    gruppi = [("arrivo", d["regole"], None)]
    altra = d.get("alla_scrittura", {})
    if altra:
        gruppi.append(("scrittura", d["regole"] if altra.get("come_all_arrivo") else altra["regole"], altra["istante"]))
    for giro, regole, t in gruppi:
        for r in regole:
            for y in r["letture"]:
                yield {**y, "quando": giro, "su": r["su"], "verdetto": r["verdetto"], "regola": r["regola"],
                       "versione": r["versione"], "valido_al": y["valido_al"] if t is None else t}


def giornale(cartella):
    """Legge una cartella della prova dopo il recupero, senza aprirla come Memoria (neppure in lettura)."""
    cartella = Path(cartella)
    segno = json.loads((cartella / "formato").read_text())
    yield {"inizio": 1, "segno": segno}
    nomi, origini = [], []
    with (cartella / "giornale").open("rb") as f:
        for e, rec in enumerate(_record(f)):
            _, _, arrivo, tipo, nuovi, piatti, ritirati, nota, *tempi = rec
            prima = len(nomi)
            nomi.extend(nuovi)
            if e < 2:  # i soli due eventi ammessi prima del piano sono definizioni
                attesi = ("circuito", "termine") if e == 0 else ("regola", "segnale")
                if not piatti or any(nomi[t] not in attesi for t, _ in piatti) or ritirati:
                    raise ValueError("la prova non parte dalle due definizioni attese")
            else:
                r = nota["ricevuta"]
                d = r["decisione"]
                scritti = list(_fatti(nomi, piatti))
                tolti = [origini[x] for x in ritirati]
                proposti, da_togliere = scritti, tolti
                if tipo == "rifiuto":
                    proposta = d["proposta"]
                    proposti = list(_fatti(nomi[:prima] + proposta["nuovi"], proposta["fatti"]))
                    da_togliere = [origini[x] for x in proposta["ritira"]]
                yield {"evento": e, "chiave": nota["chiave"], "tipo": tipo, "arrivo": arrivo,
                       "scritto": tempi[0] if tempi else arrivo, "base": d["base"], "istante": d["istante"],
                       "evento_ricevuta": r["evento"], "esito": r["esito"], "letture": list(_letture(d)),
                       "proposti": proposti, "da_togliere": da_togliere, "scritti": scritti, "ritirati": tolti}
            origini.extend([e] * len(piatti))


def postgresql(dsn):
    """Solo SELECT sulle tabelle durevoli della prova, stesso snapshot anche per i ritiri."""
    from .postgres.base import collega  # solo connessione; il giudice offline non richiede psycopg

    with collega(dsn) as db, db.transaction():
        db.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        yield db.execute("SELECT dal-1 AS inizio,jsonb_build_object('formato',formato,'dal',dal) AS segno FROM testa").fetchone()
        with db.cursor(name="esporta_fido") as cursore:
            cursore.execute("""SELECT e.evento,e.chiave,e.arrivo,e.scritto,e.esito,e.letture,
                              (e.nuovo).cliente AS c,(e.nuovo).specie AS specie,(e.nuovo).riferimento AS rif,
                              CASE WHEN (e.nuovo).specie='ordine' THEN -(e.nuovo).valore ELSE (e.nuovo).valore END::text AS v,
                              CASE WHEN (e.nuovo).esplicito THEN (e.nuovo).da END AS da,nullif((e.nuovo).fine,'Infinity') AS fine,
                              e.ritira,f.cliente AS fc,f.specie AS fs,f.riferimento AS fr,
                              CASE WHEN f.specie='ordine' THEN -f.valore ELSE f.valore END::text AS fv,
                              CASE WHEN f.esplicito THEN f.da END AS fd,nullif(f.fine,'Infinity') AS ff,
                              ARRAY(SELECT nato FROM fatti WHERE morto=e.evento ORDER BY nato) AS tolti
                              FROM eventi e LEFT JOIN fatti f ON f.nato=e.evento ORDER BY e.evento""")
            for x in cursore:
                def fatto(c, s, r, v, d, f):
                    return [{"cliente": x[c], "specie": x[s], "riferimento": x[r], "importo": x[v],
                             "da": x[d], "fine": x[f]}] if x[c] is not None else []
                yield {k: x[k] for k in ("evento", "chiave", "arrivo", "scritto", "esito", "letture")} | {
                    "tipo": "scrittura" if x["esito"] == "applicato" else "rifiuto", "base": x["evento"]-1,
                    "istante": x["arrivo"], "evento_ricevuta": x["evento"], "da_togliere": [] if x["ritira"] is None else [x["ritira"]],
                    "proposti": fatto("c", "specie", "rif", "v", "da", "fine"), "scritti": fatto("fc", "fs", "fr", "fv", "fd", "ff"),
                    "ritirati": x["tolti"]}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--giornale", type=Path, help="cartella di una memoria di prova riaperta e poi fermata")
    g.add_argument("--dsn", help="DSN esplicito della base di prova sul socket privato")
    p.add_argument("uscita", type=Path)
    args = p.parse_args()
    righe = giornale(args.giornale) if args.giornale else postgresql(args.dsn)
    with args.uscita.open("x", encoding="utf-8") as f:
        for l in righe:
            f.write(json.dumps(l, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n")


if __name__ == "__main__":
    main()
