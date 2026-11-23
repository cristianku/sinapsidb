"""Terzo giro: su un ruolo ripetuto una condizione vale ancora solo per il primo nome, appena non è un «=» in un tipo.

La correzione del secondo giro (``tutti`` in ``Domanda._compila``) fa guardare tutti i nomi di un ruolo ripetuto solo
alla condizione ``ruolo = nome`` e solo se la domanda nomina il tipo. Restano fuori:

- i confronti (``>``, ``<``, ...): chi parte dai valori della linea trova il fatto da uno qualunque dei suoi numeri, chi
  lo guarda fatto per fatto legge solo il primo. Così ``dove A e B`` e ``dove B e A`` danno righe diverse, e aggiungere
  una condizione che il fatto soddisfa lo fa sparire;
- le domande su fatti di qualsiasi tipo (senza ``in <tipo>``): ``dove presente = Bea`` legge solo il primo presente,
  mentre lo stesso nome dato come secondo indizio (``Rete.riceve``, il percorso di riferimento) trova il fatto.

È la stessa regola della prova del secondo giro (``test_dove_vale_come_i_due_indizi_anche_per_il_secondo_nome``): la
stessa condizione sullo stesso fatto non può essere vera o falsa secondo da dove parte la domanda.
"""

import pytest

from sinapsidb import lingua
from sinapsidb.motore import Rete
from test_revisione_circuiti_090 import aperta


@pytest.fixture
def rete(tmp_path):
    with aperta(Rete(str(tmp_path / "rete"))) as r:
        n = r.n
        # due esami, ognuno con due voti: in A1 il voto basso è scritto per primo, in A2 per secondo
        r.racconta("Esame in A1", "test", [(n("esame"), [(n("voto"), n("3")), (n("voto"), n("8")), (n("aula"), n("A1"))])])
        r.racconta("Esame in A2", "test", [(n("esame"), [(n("voto"), n("9")), (n("voto"), n("2")), (n("aula"), n("A2"))])])
        for sala in ("S1", "S2"):
            r.racconta(f"Riunione in {sala}", "test", [(n("riunione"), [(n("presente"), n("Ada")), (n("presente"), n("Bea")), (n("sala"), n(sala))])])
        yield r


def righe(r, domanda):
    return sorted(lingua.prepara(r, domanda).esegui())


def test_due_confronti_sullo_stesso_ruolo_non_dipendono_dall_ordine_in_cui_sono_scritti(rete):
    assert righe(rete, "in esame dove voto > 5 e voto < 4 leggi aula") == righe(rete, "in esame dove voto < 4 e voto > 5 leggi aula")


def test_una_condizione_in_piu_che_il_fatto_soddisfa_non_lo_fa_sparire(rete):
    assert ("A1",) in righe(rete, "in esame dove voto > 5 leggi aula")  # l'esame in A1 ha un voto sopra il 5
    assert righe(rete, "in esame dove voto > 5 e aula = A1 leggi aula") == [("A1",)]
    assert righe(rete, "accendi aula = A1 in esame dove voto > 5 leggi aula") == [("A1",)]


def test_senza_il_tipo_dove_vale_come_il_secondo_indizio(rete):
    con_due_indizi = righe(rete, "accendi sala = S1 e presente = Bea leggi sala")  # il percorso di riferimento
    assert con_due_indizi == [("S1",)]
    assert righe(rete, "accendi presente = Bea dove sala = S1 leggi sala") == con_due_indizi
    assert righe(rete, "accendi sala = S1 dove presente = Bea leggi sala") == con_due_indizi
