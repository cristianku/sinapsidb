"""0.10.0: confronti C/Python che attraversano i confini della preparata, del tempo e del contesto decimale."""

import json
import os
import subprocess
import sys
from decimal import InvalidOperation, localcontext
from pathlib import Path

import pytest

from sinapsidb import lingua, nucleo
from sinapsidb.motore import Rete
from test_nucleo import libreria
from test_revisione_circuiti_090 import aperta


SOMMA = "in vendita somma totale per cliente"


@pytest.fixture
def modo(libreria, monkeypatch):
    def scegli(compilato):
        monkeypatch.setenv("SINAPSIDB_NUCLEO", str(libreria) if compilato else "0")
        nucleo.ricarica()
        assert nucleo.attivo() is compilato
    yield scegli
    nucleo.ricarica()


def fatti(r, cliente, totale, altri=()):
    n = r.n
    return [(n("vendita"), [(n("cliente"), cliente), (n("totale"), n(totale)), *altri])]


def test_l_ordine_parte_dal_primo_addendo_non_dal_primo_nome_nel_giornale(modo):
    r = Rete()
    # Due ID «Ada». La prima Ada non entra nella somma, ma conta per il conteggio.
    a, b, a2 = r.nuovo("Ada"), r.nuovo("Bea"), r.nuovo("Ada")
    for chi, x in ((a, "n/d"), (b, "1.0"), (a2, "2.00"), (a, "NaN"), (b, "0.25")):
        r.racconta("Vendita", "test", fatti(r, chi, x))
    for q, attese in ((SOMMA, [("Bea", "1.25"), ("Ada", "2.00")]),
                      ("in vendita conta per cliente", [("Ada", "3"), ("Bea", "2")])):
        for compilato in (False, True):
            modo(compilato)
            domanda = lingua.prepara(r, q)
            assert domanda.esegui() == attese and domanda.conteggi == [5]
    mai_usato = r.n("0.001")
    r.racconta("Solo il nome, per ora", "test", [(r.n("nota"), [(r.n("dato"), mai_usato)])])
    modo(True)
    lingua.prepara(r, SOMMA).esegui()
    r.racconta("Storno", "test", ritira=[1, 4])  # perde Bea e la riceve dopo le due Ada, senza che nasca alcun nome
    r.racconta("Di nuovo Bea", "test", fatti(r, b, "0.001"))
    for compilato in (False, True):
        modo(compilato)
        assert lingua.prepara(r, SOMMA).esegui() == [("Ada", "2.00"), ("Bea", "0.001")]


@pytest.mark.parametrize("vista", ["registrata", "valida", "scenario"])
def test_il_nucleo_rispetta_anche_i_fatti_esclusi_dal_passato_e_dallo_scenario(tmp_path, modo, vista):
    c = str(tmp_path / "rete")
    with aperta(Rete(c)) as r:
        cl = r.n("C")
        for e, cifra in enumerate(("2.00", "900", "40.5", "3.0")):
            validita = [(r.n("vale da"), r.n("2000-01-01" if e == 1 else "1970-01-01"))]
            r.racconta(cifra, "test", fatti(r, cl, cifra, validita), quando=10.0 + e, scritto=10.0 + e)
        r.racconta("Storno", "test", ritira=[0], quando=14.0, scritto=14.0)
    if vista == "scenario":
        # Base prima di 1, più la sola rettifica 3: i fatti 1 e 2 sono numerati ma messi da parte.
        rete, _, _, _ = Rete.scenario(c, 1, [3])
        atteso, quanti = "5.00", 2
    else:
        rete = Rete.vista(c, 5, 100.0 if vista == "valida" else None)
        atteso, quanti = ("43.5", 2) if vista == "valida" else ("943.5", 3)
    for compilato in (False, True):
        modo(compilato)
        p = lingua.prepara(rete, SOMMA)
        for _ in range(2):
            assert p.esegui() == [("C", atteso)] and p.conteggi == [quanti]


@pytest.mark.parametrize("temporaneo,ultimo,atteso", [("100.00", "0.5", "12.5"), ("100.0000001", "0.0000001", "12.0000001")],
                         ids=["tabelle-del-nucleo", "ripiego-python"])
def test_c_e_ripiego_non_riusano_il_vecchio_decimale_dopo_un_rollback(modo, temporaneo, ultimo, atteso):
    modo(True)
    r = Rete()
    cl = r.n("C")
    r.racconta("Base", "test", fatti(r, cl, "10"))
    p = lingua.prepara(r, SOMMA)
    assert p.esegui() == [("C", "10")]
    posto = len(r.nomi)

    def calcola_la_nota(d):
        p.esegui()  # la proposta con centesimi scalda le tabelle C, quella con sette decimali il ripiego Python
        raise ValueError("nota non pronta")

    with pytest.raises(ValueError, match="nota non pronta"):
        r.racconta("Provvisoria", "test", fatti(r, cl, temporaneo), neuroni_da=posto, nota_della_scrittura=calcola_la_nota)
    r.racconta("Il numero libero ora vale 2", "test", fatti(r, cl, "2"))
    assert r.trova("2") == [posto] and p.esegui() == [("C", "12")]
    r.racconta("Ancora un importo", "test", fatti(r, cl, ultimo))
    modo(False)
    riferimento = lingua.prepara(r, SOMMA).esegui()
    modo(True)
    assert p.esegui() == riferimento == [("C", atteso)]


def test_il_lavoro_di_numero_di_non_sporca_il_contesto_decimale_del_chiamante(modo):
    r = Rete()
    cl = r.n("C")
    r.racconta("Stima", "test", fatti(r, cl, "non disponibile"))
    r.racconta("Contabilizzata", "test", fatti(r, cl, "٣_٠.٥٠"))
    for compilato in (False, True):
        modo(compilato)
        with localcontext() as ctx:
            ctx.clear_flags()
            p = lingua.prepara(r, SOMMA)
            assert p.esegui() == [("C", "30.50")]
            assert not ctx.flags[InvalidOperation]  # Python esamina i nomi nel proprio contesto, anche a tabelle fredde


def test_il_segno_dello_zero_resta_quello_del_riferimento_con_round_floor(libreria):
    # Configurazione lecitamente fissata dall'app PRIMA dell'import, senza toccare i contesti privati del motore.
    programma = """\
import json, os, sys
from decimal import DefaultContext, ROUND_FLOOR
DefaultContext.rounding = ROUND_FLOOR
from sinapsidb import lingua, nucleo
from sinapsidb.motore import Rete
r = Rete()
n = r.n
for cliente, x in [('A', '-0.00'), ('B', '2'), ('B', '-2.0')]:
    r.racconta('Vendita', 'test', [(n('vendita'), [(n('cliente'), n(cliente)), (n('totale'), n(x))])])
risposte = []
for via in ('0', sys.argv[1]):
    os.environ['SINAPSIDB_NUCLEO'] = via
    nucleo.ricarica()
    assert nucleo.attivo() == (via != '0')
    risposte.append(lingua.prepara(r, 'in vendita somma totale per cliente').esegui())
print(json.dumps(risposte))
"""
    env = {**os.environ, "PYTHONPATH": os.pathsep.join((str(Path(lingua.__file__).resolve().parents[1]), os.environ.get("PYTHONPATH", "")))}
    esito = subprocess.run([sys.executable, "-c", programma, str(libreria)], check=True, capture_output=True, text=True, env=env)
    python, compilato = json.loads(esito.stdout)
    assert compilato == python
