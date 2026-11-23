"""Secondo giro, sulla candidata 0.10.4: lo stesso ingresso sbagliato dà due errori diversi nei due percorsi.

``racconta_molti`` «fa esattamente quello che farebbe ``racconta`` chiamata una volta per fatto» e, appena non è il caso
semplice, «quel fatto lo racconta ``racconta``: qui non si decide niente». Ma la condizione con cui decide se delegare
solleva lei, prima di delegare: ``tipo in definizioni`` per un tipo che non si può mettere in un insieme, e
``[x for coppia in legami for x in coppia]`` per un legame che non si può scorrere. Esce un TypeError del linguaggio
(«unhashable type: 'list'», «'NoneType' object is not iterable») dove ``racconta`` rifiuta con il suo ValueError e il
suo messaggio. Chi importa e tratta il rifiuto di un fatto (``except ValueError``) nel percorso dei molti non lo
prende.
"""

import pytest

from sinapsidb.motore import Rete
from test_revisione_circuiti_090 import aperta
from test_velocita import _uno_alla_volta


def storto(r, guasto):
    tipo, ruolo, bea = (r.n(s) for s in ("nota", "cosa", "Bea"))
    if guasto == "tipo-lista":
        return [tipo], [(ruolo, bea)]
    if guasto == "tipo-dizionario":
        return {"tipo": tipo}, [(ruolo, bea)]
    if guasto == "legame-none":
        return tipo, [(ruolo, bea), None]
    return tipo, [(ruolo, bea), bea]  # un numero solo dove va una coppia


@pytest.mark.parametrize("guasto", ["tipo-lista", "tipo-dizionario", "legame-none", "legame-numero"])
def test_i_due_percorsi_rifiutano_lo_stesso_ingresso_con_lo_stesso_errore(tmp_path, guasto):
    errori = {}
    for via, scrivi in (("uno", _uno_alla_volta), ("molti", Rete.racconta_molti)):
        with aperta(Rete(str(tmp_path / via))) as r:
            tipo, ruolo, ada = (r.n(s) for s in ("nota", "cosa", "Ada"))
            r.racconta("Prima", "test", [(tipo, [(ruolo, ada)])])
            with pytest.raises(Exception) as visto:
                scrivi(r, "test", [("Non entra", *storto(r, guasto))])
            assert not r.fuori_uso
            errori[via] = (type(visto.value).__name__, str(visto.value))
    assert errori["uno"][0] == "ValueError"  # il rifiuto pulito di racconta
    assert errori["molti"] == errori["uno"]
