"""Una domanda preparata che non riesce a prepararsi di nuovo non deve poi rispondere con il piano di prima.

La frase nominava qualcuno che la rete non conosceva. Poi nascono due neuroni con quel nome: la domanda si prepara di
nuovo e dice, giustamente, che il nome indica due cose. Ma ``Domanda._prepara`` ha già aggiornato i suoi contrassegni
(``_valida``, ``_ignoti``, ``_neuroni``) prima di fallire, e ``_esegui`` è rimasto il piano compilato di prima: alla
chiamata successiva la domanda non si prepara più e risponde «nessuna riga», come se quel cliente non avesse vendite.
"""

import pytest

from sinapsidb import lingua
from sinapsidb.memoria import Memoria
from test_revisione_circuiti_090 import aperta

DI_ZED = "accendi cliente = Zed in vendita leggi totale"


def test_dopo_un_errore_la_preparata_non_risponde_vuoto_per_un_nome_che_indica_due_clienti(tmp_path):
    with aperta(Memoria(tmp_path / "rete", scritture=True)) as m:
        m.racconta("Vendita a C", [{"tipo": "vendita", "legami": {"cliente": "C", "totale": "5"}}])
        preparata = lingua.prepara(m.rete, DI_ZED)
        assert preparata.esegui() == []  # Zed non c'è ancora
        for totale in ("70", "80"):  # due clienti diversi che si chiamano Zed, tutti e due nel giornale
            x = m.racconta(f"Vendita a un nuovo Zed: {totale}", [{"tipo": "vendita", "legami": {"cliente": "Zed", "totale": totale}}],
                           nuovi=["Zed"])
            assert x["esito"] == "applicato"
        assert len(m.rete.trova("Zed")) == 2
        with pytest.raises(lingua.ErroreDomanda):
            lingua.prepara(m.rete, DI_ZED)  # una domanda fresca: il nome va precisato
        with pytest.raises(lingua.ErroreDomanda):
            preparata.esegui()  # la prima volta lo dice anche la preparata
        # La seconda volta la rete è la stessa: deve dirlo ancora (o rispondere per i due Zed), non «nessuna vendita».
        try:
            righe = preparata.esegui()
        except lingua.ErroreDomanda:
            return
        assert sorted(righe) == [("70",), ("80",)]
