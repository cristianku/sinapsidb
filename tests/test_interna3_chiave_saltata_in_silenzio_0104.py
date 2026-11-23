"""Terzo giro di revisione interna sulla candidata 0.10.4: la nota con chiave rovinata che ``Memoria.__init__``
adesso salta, e nessuno lo dice.

La correzione del secondo giro (``tests/test_interna2_nota_con_chiave_rovinata_0104.py``) fa aprire la memoria anche se
la nota di un evento scritto con una chiave ha perso l'impronta: «la sua chiave non si registra, e la verifica di
quell'evento dice che è guasto» (commento in ``Memoria.__init__``). La prova di allora chiede solo che ``verifica`` dia
uno dei quattro esiti. Quello che dà è «coerente»: il guasto non lo segnala nessuno. E la chiave, che nel giornale c'è
ancora con la sua ricevuta «applicato», per la memoria è assente: ``ricevuta_di`` risponde None, e la stessa richiesta
ripetuta con la stessa chiave viene valutata e scritta un'altra volta. Qui l'ordine nel frattempo era stato ritirato da
una correzione: la ripetizione lo riscrive, e il fido torna a scendere.

Regole: «una risposta assente smentita dal giornale», «una chiave che riceve due ricevute diverse». Contratto,
paragrafo 5: «Ripetere una richiesta con la stessa chiave restituisce la ricevuta di allora, sempre [...] e non scrive
allora quello che una correzione ha nel frattempo ritirato»; paragrafo 9: «Una ricevuta rovinata è un guasto segnalato».
"""

import pytest

from sinapsidb.memoria import Memoria
from test_decisioni import _record, _riscrivi
from test_revisione_circuiti_090 import aperta
from test_segno_e_disco import fido, movimento

ROVINA = {
    "senza-impronta": lambda nota: nota.pop("impronta"),  # la chiave e la ricevuta ci sono, l'impronta del racconto no
    "impronta-numero": lambda nota: nota.__setitem__("impronta", 7),  # al posto dell'impronta c'è un numero
}


def prepara(c, rovina):
    """Un fido di 100, un ordine da 60 con la chiave «o1» (evento 3), poi una correzione che ritira l'ordine (evento 4).
    Poi la nota dell'ordine viene rovinata nel giornale."""
    with aperta(Memoria(c, scritture=True)) as m:
        fido(m)  # eventi 0 e 1
        assert movimento(m, "accredito", "Rossi", "100", "a1")["esito"] == "applicato"  # evento 2
        allora = movimento(m, "ordine", "Rossi", "60", "o1")  # evento 3
        assert allora["esito"] == "applicato" and allora["evento"] == "evento:3"
        ritiro = m.racconta("L'ordine di Rossi era un errore", sostituisce=[{"tipo": "ordine", "indizi": {"cliente": "Rossi", "importo": "60"}}], chiave="s1")
        assert ritiro["esito"] == "applicato" and m.valore("fido", "Rossi")["valore"] == "100"
        assert movimento(m, "ordine", "Rossi", "60", "o1")["ripetuta"] is True  # finché la nota è intera: la ricevuta di allora
        assert m.valore("fido", "Rossi")["valore"] == "100"
    record = _record(c)
    assert record[3][7]["chiave"] == "o1" and record[3][7]["ricevuta"]["esito"] == "applicato"
    ROVINA[rovina](record[3][7])
    _riscrivi(c, record)
    return allora


@pytest.mark.parametrize("rovina", list(ROVINA))
def test_una_chiave_saltata_all_apertura_e_un_guasto_segnalato_o_non_e_assente(tmp_path, rovina):
    c = tmp_path / "rete"
    prepara(c, rovina)
    with aperta(Memoria(c)) as m:  # si apre: è la correzione del secondo giro
        verifica = m.verifica(3)
        try:
            ricevuta = m.ricevuta_di("o1")
        except ValueError as guasta:  # dire che la nota di quella chiave è rovinata non è dire «assente»
            ricevuta = {"guasta": str(guasta)}
    # nel giornale l'evento 3 porta la chiave «o1» e la sua ricevuta: o chi verifica quell'evento sente «guasto», come
    # promette il commento in Memoria.__init__, o la chiave si ritrova. «Coerente» e «assente» insieme no.
    assert verifica["esito"] == "guasto" or ricevuta is not None, (verifica, ricevuta)


@pytest.mark.parametrize("rovina", list(ROVINA))
def test_la_stessa_chiave_non_riceve_una_seconda_ricevuta_e_non_riscrive_un_ordine_ritirato(tmp_path, rovina):
    c = tmp_path / "rete"
    allora = prepara(c, rovina)
    with aperta(Memoria(c, scritture=True)) as m:
        try:
            adesso = movimento(m, "ordine", "Rossi", "60", "o1")  # chi ha perso la conferma ripete, con la stessa chiave
        except Exception as rifiuto:  # un rifiuto che dice del guasto andrebbe bene
            adesso = {"rifiutata": str(rifiuto)}
        residuo = m.valore("fido", "Rossi")["valore"]
    con_la_chiave = [i for i, r in enumerate(_record(c)) if len(r) > 7 and isinstance(r[7], dict) and r[7].get("chiave") == "o1"]
    assert con_la_chiave == [3] and residuo == "100", (
        f"eventi del giornale con la chiave «o1»: {con_la_chiave}; allora {allora['esito']} {allora['evento']}, "
        f"adesso {adesso.get('esito')} {adesso.get('evento')} ripetuta={adesso.get('ripetuta')}; fido residuo {residuo}")


@pytest.mark.parametrize("rovina", list(ROVINA))
def test_una_chiave_con_la_nota_rovinata_non_e_assente_e_non_viene_rivalutata(tmp_path, rovina):
    """Quello che la memoria fa con una chiave la cui nota è rovinata, detto per intero: la verifica dell'evento risponde
    «guasto», chi chiede la ricevuta si sente dire che la nota è rovinata (non «assente»), e chi ripete la richiesta
    riceve «conflitto» senza che niente venga scritto."""
    c = tmp_path / "rete"
    prepara(c, rovina)
    prima = (c / "giornale").read_bytes()
    with aperta(Memoria(c, scritture=True)) as m:
        assert m.verifica(3)["esito"] == "guasto" and m.verifica(2)["esito"] == "coerente"
        with pytest.raises(ValueError, match="rovinata"):
            m.ricevuta_di("o1")
        ripetuta = movimento(m, "ordine", "Rossi", "60", "o1")
        assert ripetuta["esito"] == "conflitto" and ripetuta["scritture_applicate"] == 0 and "rovinata" in ripetuta["spiegazione"]
        assert m.ricevuta_di("a1")["esito"] == "applicato"  # le altre chiavi si ritrovano
    assert (c / "giornale").read_bytes() == prima
