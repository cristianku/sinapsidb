"""Genera ``tests/dati/scritto-dalla-098``: una memoria del fido e una piccola rete con la sua istantanea, scritte dal
codice della 0.9.8. Va lanciato con il codice di quella versione: ``git archive 362e0c7 src | tar -x -C <cartella>`` e poi
``PYTHONPATH=<cartella>/src python genera_scritto_dalla_098.py <uscita> scrivi``; con ``leggi`` stampa quello che quella
versione risponde sugli stessi file (``risposte-della-098.json``), con ``scrivi-ancora`` ci scrive sopra."""
import json, sys, time
from pathlib import Path
import sinapsidb
from sinapsidb.fido import Fido
from sinapsidb.motore import Rete
from sinapsidb import lingua

def risposte_fido(cartella):
    f = Fido(cartella)
    try:
        q = f.esegui({"op": "quadro"})
        eventi = q["ultimo_evento"] + 1
        return {"clienti": q["clienti"], "decisioni": [(d["evento"], d["esito"]) for d in q["decisioni"]], "circuiti": q["circuiti"],
                "verifiche": [f.esegui({"op": "verifica", "evento": e}).get("esito") for e in range(eventi)],
                "chiavi": {k: f.esegui({"op": "stato", "chiave": k}).get("esito") for k in ("fido-r", "ordine-r", "paga-r", "ordine-2", "storno-r")}}
    finally:
        f.chiudi()

DOMANDE = ("in vendita conta per cliente", "in vendita somma totale per cliente", "accendi cliente = Ada in vendita leggi numero, totale",
           "in vendita dove totale > 15 leggi numero", "vocabolario")

def risposte_rete(cartella, **come):
    r = Rete(cartella, **come)
    try:
        return {"conteggi": r.conteggi(), "scartata": getattr(r, "istantanea_scartata", None),
                "righe": {d: lingua.chiedi(r, d)["righe"] for d in DOMANDE}}
    finally:
        r.chiudi()

if __name__ == "__main__":
    base, azione = Path(sys.argv[1]), sys.argv[2]
    if azione == "scrivi":
        f = Fido(base / "fido")
        ieri = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 86400))
        passi = [f.esegui({"op": "accorda", "cliente": "Rossi", "importo": "800", "atto": "A1", "chiave": "fido-r"}),
                 f.esegui({"op": "ordine", "cliente": "Rossi", "importo": "1000", "numero": "O1", "chiave": "ordine-r"}),
                 f.esegui({"op": "pagamento", "cliente": "Rossi", "importo": "400", "causale": "P1", "vale_da": ieri, "chiave": "paga-r"}),
                 f.esegui({"op": "ordine", "cliente": "Rossi", "importo": "300", "numero": "O2", "chiave": "ordine-2"}),
                 f.esegui({"op": "storno", "causale": "P1", "chiave": "storno-r"})]
        f.chiudi()
        r = Rete(str(base / "rete"), sync="os")
        n = r.n
        for i, (cliente, totale) in enumerate((("Ada", "10"), ("Bea", "20"), ("Ada", "30"), ("Cleo", "5.50"))):
            r.racconta(f"Vendita {i}", "prova", [(n("vendita"), [(n("numero"), n(f"V{i}")), (n("cliente"), n(cliente)), (n("totale"), n(totale))])])
        r.racconta("Reso della prima", "prova", [(n("vendita"), [(n("numero"), n("V9")), (n("cliente"), n("Ada")), (n("totale"), n("12"))])],
                   ritira=r.scatta(n("vendita"), [(n("numero"), n("V0"))]))
        r.istantanea()
        r.racconta("Dopo l'istantanea", "prova", [(n("vendita"), [(n("numero"), n("V10")), (n("cliente"), n("Bea")), (n("totale"), n("1"))])])
        r.chiudi()
        print(json.dumps({"versione": sinapsidb.__version__, "passi": [p.get("esito") for p in passi]}))
    elif azione == "leggi":
        print(json.dumps({"versione": sinapsidb.__version__, "fido": risposte_fido(base / "fido"), "rete": risposte_rete(str(base / "rete"), sync="os")}, ensure_ascii=False, sort_keys=True))
    elif azione == "scrivi-ancora":
        f = Fido(base / "fido")
        fatti = [f.esegui({"op": "pagamento", "cliente": "Rossi", "importo": "50", "causale": "P2", "chiave": "paga-2"}),
                 f.esegui({"op": "ordine", "cliente": "Rossi", "importo": "2000", "numero": "O3", "chiave": "ordine-3"})]
        f.chiudi()
        r = Rete(str(base / "rete"), sync="os")
        n = r.n
        r.racconta("Scritta dopo", "prova", [(n("vendita"), [(n("numero"), n("V11")), (n("cliente"), n("Cleo")), (n("totale"), n("2"))])])
        r.istantanea()
        r.chiudi()
        print(json.dumps({"versione": sinapsidb.__version__, "fatti": [(p.get("esito"), p.get("evento")) for p in fatti]}))
