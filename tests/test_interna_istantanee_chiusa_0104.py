"""Controesempi sulle istantanee: una memoria già chiusa conferma scritture che il giornale non riceve, e
un'istantanea presa in quel momento le pubblica a chi riapre.

Dopo ``chiudi()`` il giornale non è più aperto, ma ``racconta`` non lo dice: salta la scrittura sul disco e conta
l'evento in memoria. È quello che succede quando un servizio chiude la memoria mentre una richiesta aspetta il suo
turno (``prove/fido/trasporto.py`` chiude il servizio senza aspettare i fili delle conversazioni).
"""

from sinapsidb.memoria import Memoria
from sinapsidb.motore import Rete
from test_revisione_circuiti_090 import aperta
from test_segno_e_disco import nota


def _racconta_dopo_la_chiusura(c):
    m = Memoria(c, scritture=True)
    a = nota(m, "A")
    m.chiudi()
    try:
        b = nota(m, "B")
    except (RuntimeError, ValueError, OSError):
        b = None  # rifiutare la scrittura è la risposta corretta
    return m, a, b


def test_una_memoria_chiusa_non_conferma_una_scrittura_che_il_giornale_non_ha(tmp_path):
    c = tmp_path / "rete"
    m, a, b = _racconta_dopo_la_chiusura(c)
    nel_giornale = len(list(Rete._record(str(c))))
    try:
        calda = m.ricevuta_di("B")
    except RuntimeError:
        calda = None  # è lecito esigere la riapertura
    with aperta(Memoria(c)) as riaperta:
        assert riaperta.conteggi()["eventi"] == nel_giornale and riaperta.ricevuta_di("A") == a
        dal_giornale = riaperta.ricevuta_di("B")
    assert b is None or b["esito"] != "applicato" or dal_giornale == b, \
        f"confermato {b['esito']} {b['evento']}, ma il giornale ha {nel_giornale} eventi e per quella chiave dice {dal_giornale}"
    assert calda == dal_giornale


def test_un_istantanea_presa_dopo_la_chiusura_non_pubblica_eventi_che_il_giornale_non_ha(tmp_path):
    c = tmp_path / "rete"
    m, a, b = _racconta_dopo_la_chiusura(c)
    try:
        m.rete.istantanea()
    except RuntimeError:
        pass  # anche rifiutare qui l'istantanea è corretto
    nel_giornale = len(list(Rete._record(str(c))))
    dal_solo_giornale, _ = Rete.com_era(str(c), nel_giornale)
    with aperta(Memoria(c)) as lettore:  # un lettore: non scrive niente, apre quello che trova
        r = lettore.rete
        assert not r.istantanea_scartata or r.conteggi() == dal_solo_giornale.conteggi()
        assert (r.conteggi(), r.nomi, r.e_frase) == (dal_solo_giornale.conteggi(), dal_solo_giornale.nomi, dal_solo_giornale.e_frase)
        assert lettore.ricevuta_di("A") == a
        assert (lettore.ricevuta_di("B") is not None) == (nel_giornale == 2), "una ricevuta che il giornale non ha"


def test_chi_riapre_per_scrivere_dopo_quell_istantanea_scrive_ricevute_verificabili(tmp_path):
    c = tmp_path / "rete"
    m, _, _ = _racconta_dopo_la_chiusura(c)
    try:
        m.rete.istantanea()
    except RuntimeError:
        pass
    with aperta(Memoria(c, scritture=True)) as scrittore:
        r = nota(scrittore, "C")
        assert r["esito"] == "applicato"
        numero = int(r["evento"].removeprefix("evento:"))
        assert numero == len(list(Rete._record(str(c)))) - 1, "il numero d'evento confermato non è quello che l'evento ha nel giornale"
        assert scrittore.verifica(r["evento"])["esito"] == "coerente"
