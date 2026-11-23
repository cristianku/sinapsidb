"""Secondo giro di revisione interna sulla candidata 0.10.4: «azzera» del servizio del fido quando la cartella
non si cancella.

``Fido.azzera`` cancella la cartella con ``shutil.rmtree(..., ignore_errors=True)`` e poi riapre: se la cartella non si
cancella (un permesso, un file system in sola lettura per quei nomi), riapre il giornale di prima e risponde lo stesso
``{"esito": "azzerato"}``. È un sì falso: l'operazione confermata a chi chiama non è avvenuta, i clienti, le decisioni e
le chiavi di prima sono ancora lì. Se si cancella solo una parte (resta il giornale, va via il segno «formato») risponde
ancora «azzerato», e alla prima scrittura il giornale di prima riceve un segno nuovo che comincia da quell'evento.
"""

import errno
import os

import pytest

from sinapsidb.fido import Fido


@pytest.mark.parametrize("resta", ["tutto", "il-giornale"])
def test_azzera_non_dice_azzerato_se_la_memoria_di_prima_e_ancora_li(tmp_path, monkeypatch, resta):
    f = Fido(tmp_path / "fido")
    try:
        assert f.esegui({"op": "accorda", "cliente": "A", "importo": "100", "chiave": "fido"})["esito"] == "applicato"
        togli = os.unlink

        def non_si_cancella(percorso, *a, **k):
            if resta == "tutto" or str(percorso).endswith("giornale"):
                raise PermissionError(errno.EPERM, "operazione non permessa", str(percorso))
            return togli(percorso, *a, **k)

        def non_si_toglie(percorso, *a, **k):
            raise PermissionError(errno.EPERM, "operazione non permessa", str(percorso))

        with monkeypatch.context() as mp:  # solo per il tempo di «azzera»: la cartella non si lascia cancellare
            mp.setattr(os, "unlink", non_si_cancella)
            mp.setattr(os, "rmdir", non_si_toglie)
            risposta = f.esegui({"op": "azzera"})  # non solleva
        quadro = f.esegui({"op": "quadro"})
        chiave = f.esegui({"op": "stato", "chiave": "fido"})
        azzerata = quadro["ok"] and quadro["clienti"] == [] and quadro["ultimo_evento"] == 1 and chiave["ok"] and not chiave["presente"]
        # o la memoria è nuova davvero, o «azzera» non dice di averla azzerata
        assert azzerata or not risposta["ok"], (risposta, quadro.get("clienti"), quadro.get("ultimo_evento"), chiave.get("presente"))
    finally:
        f.chiudi()
