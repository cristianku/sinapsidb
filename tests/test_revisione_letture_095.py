"""Controlli della 0.9.5 sul soggetto di una preparata e sull'origine registrata."""

from enum import Enum

import pytest

from sinapsidb import lingua
from sinapsidb.memoria import Memoria
from sinapsidb.motore import Rete
from test_revisione_circuiti_090 import aperta, circuito, evento


@pytest.mark.parametrize("per_id", [True, False], ids=["numero-riusato", "omonimo-rinato"])
def test_la_ridefinizione_non_puo_sostituire_il_destinatario_di_una_preparata(tmp_path, per_id):
    with aperta(Memoria(tmp_path / "rete", scritture=True)) as db:
        r, n = db.rete, db.rete.n
        c = circuito(r, "ricavi", per=None, lega="cliente")
        segno = len(r.nomi)
        a = n("Ada")
        p = lingua.prepara(r, f"circuito ricavi di neurone:{a}" if per_id else "circuito ricavi di Ada")
        assert p.esegui() == [("Ada", "0")]
        r.dimentica(segno)
        b, altra_ada = n("Bea"), n("Ada")
        assert a == b != altra_ada
        r.racconta("Due vendite", "test", [(n("vendita"), [(n("cliente"), chi), (n("totale"), n(valore))])
                                         for chi, valore in ((b, "90"), (altra_ada, "5"))])
        f, = r.scatta(n("termine"), [(n("termine di"), c)])
        r.racconta("Raddoppia il peso", "test", [(n("termine"), [
            (k, n("2") if k == n("peso") else v) for k, v in r.legami(f)])], ritira=[f])
        assert db.valore("ricavi", "Bea")["valore"] == "180" and db.valore("ricavi", "Ada")["valore"] == "10"
        # La nuova versione può aggiornare le colonne; non può rimpiazzare il destinatario prima di controllarlo.
        with pytest.raises(lingua.ErroreDomanda):
            p.esegui()


def test_una_enum_di_testo_registra_il_valore_non_il_nome_del_membro(tmp_path):
    class Canale(str, Enum):
        ORDINI = "contabilità"

    cartella = tmp_path / "rete"
    with aperta(Memoria(cartella, scritture=True)) as db:
        fatti = [{"tipo": "nota", "legami": {"cosa": "X"}}]
        x = db.racconta("Nota", fatti, chi=Canale.ORDINI, chiave="N")
        assert x["esito"] == "applicato" and db.racconta("Nota", fatti, chi=Canale.ORDINI.value, chiave="N")["ripetuta"]
        riletta, _ = Rete.com_era(str(cartella), evento(x) + 1)
        assert riletta.e_origine[evento(x)] == db.rete.e_origine[evento(x)] == Canale.ORDINI.value
