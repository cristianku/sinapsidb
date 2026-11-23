"""Attestare con quale modo di fare le somme è stato scritto un tratto di un giornale.

Il giornale non può dire quale programma ha scritto le decisioni che non dichiarano il loro modo: lo sa chi conosce la
storia della macchina, per esempio dal registro degli aggiornamenti. Questo comando non lo deduce. Registra nel segno
del giornale (il file «formato») la parola di chi attesta, con la fonte, e la lega al contenuto del giornale: lo stesso
segno accanto a un altro giornale non vale. Va usato a giornale fermo, cioè con il servizio che ci scrive spento.

    python -m sinapsidb.attesta <cartella>
        mostra il segno e, tratto per tratto, da dove viene il modo di ogni decisione

    python -m sinapsidb.attesta <cartella> <modo> <da> <a> --fonte "da dove lo si sa"
        attesta che gli eventi da <da> ad <a> escluso sono stati decisi con il modo <modo>

I modi sono in ``motore.CALCOLI``. Le ricevute non vengono riscritte, il giornale non viene toccato.
"""

from __future__ import annotations

import argparse
import json
import sys

from sinapsidb.motore import CALCOLI, Rete


def tratti(cartella: str) -> list[dict]:
    """Gli eventi che portano una decisione, raggruppati in tratti consecutivi in cui il modo viene dalla stessa parte."""
    segno = Rete.segno(cartella)
    if segno is not None:
        # Il segno si confronta con il giornale vero, come fa chi verifica: se parla di eventi che il giornale non ha
        # non è il suo, e non si usa per dire con quale modo è stato scritto niente.
        oltre, quanti = Rete._fin_dove(segno), sum(1 for _ in Rete._record(cartella))
        if oltre > quanti:
            raise ValueError(f"il segno del giornale parla dell'evento {oltre}, ma il giornale ne ha {quanti}: non è il segno di "
                             "questo giornale, o il giornale ha perso degli eventi")
    fila: list[dict] = []
    for e, record in enumerate(Rete._record(cartella)):
        nota = record[7] if len(record) > 7 else None
        ricevuta = nota.get("ricevuta") if isinstance(nota, dict) else None
        decisione = ricevuta.get("decisione") if isinstance(ricevuta, dict) else None
        if not isinstance(decisione, dict):
            voce = {"stato": "senza decisione"}
        else:
            stato, modi, _, fonte = Rete.calcolo_di(segno, e, decisione, cartella)
            voce = {"stato": stato, "modi": list(modi), **({"da_dove": fonte} if fonte else {})}
        if fila and {k: v for k, v in fila[-1].items() if k not in ("da", "a")} == voce and fila[-1]["a"] == e:
            fila[-1]["a"] = e + 1
        else:
            fila.append({"da": e, "a": e + 1, **voce})
    return fila


def main(argomenti: list[str] | None = None) -> int:
    analisi = argparse.ArgumentParser(prog="python -m sinapsidb.attesta", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    analisi.add_argument("cartella", help="la cartella della memoria: quella con i file «giornale» e «formato»")
    analisi.add_argument("modo", nargs="?", type=int, choices=sorted(CALCOLI), help="il modo con cui il tratto è stato scritto")
    analisi.add_argument("da", nargs="?", type=int, help="il primo evento del tratto")
    analisi.add_argument("a", nargs="?", type=int, help="l'evento dopo l'ultimo del tratto")
    analisi.add_argument("--fonte", help="da dove si sa chi ha scritto il tratto: resta scritta nel segno")
    scelte = analisi.parse_args(argomenti)
    try:
        if scelte.modo is None:
            print(json.dumps({"segno": Rete.segno(scelte.cartella), "tratti": tratti(scelte.cartella)}, ensure_ascii=False, indent=2))
            return 0
        if scelte.da is None or scelte.a is None or not scelte.fonte:
            analisi.error("per attestare servono il modo, il primo evento, l'evento dopo l'ultimo e --fonte")
        print(json.dumps(Rete.attesta(scelte.cartella, scelte.modo, scelte.da, scelte.a, scelte.fonte), ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError) as e:
        print(f"non fatto: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
