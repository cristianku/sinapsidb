"""Una prova annullata non deve lasciare tracce: né una linea vuota né la forma cambiata di un tipo già nel giornale.

Quando la proposta provata usa un tipo e un ruolo che il giornale conosce già, ma mai insieme, ``Rete._annulla`` toglie
il fatto e lascia nella rete la linea vuota (ruolo, tipo) e ``forma[tipo] = None``. ``Rete.dimentica`` le ripulisce
solo per i neuroni che dimentica. Da lì: una domanda preparata conta le linee per sapere se la rete ha cambiato forma,
e quando la stessa scrittura poi avviene davvero la linea c'è già: non si accorge di niente e risponde diversa da una
domanda fresca. E la rete in memoria non è più quella che si rilegge dal giornale.
"""

from sinapsidb import lingua
from sinapsidb.memoria import Memoria
from test_revisione_circuiti_090 import aperta

RECLAMO_DI_C = [{"tipo": "reclamo", "legami": {"cliente": "C", "testo": "arrivato rotto"}}]
DI_C = "accendi cliente = C leggi tipo"


def test_dopo_una_prova_e_la_scrittura_vera_la_preparata_risponde_come_una_fresca(tmp_path):
    with aperta(Memoria(tmp_path / "rete", scritture=True)) as m:
        m.racconta("Vendita a C", [{"tipo": "vendita", "legami": {"cliente": "C", "totale": "5"}}])
        m.racconta("Reclamo senza cliente", [{"tipo": "reclamo", "legami": {"testo": "in ritardo"}}])
        preparata = lingua.prepara(m.rete, DI_C)
        assert preparata.esegui() == [("vendita",)]
        # Prima si prova, poi si scrive: è l'uso normale di una prova. In mezzo la domanda preparata viene eseguita.
        assert m.racconta("Reclamo di C", RECLAMO_DI_C, prova=True)["esito"] == "prova"
        assert preparata.esegui() == [("vendita",)]
        assert m.racconta("Reclamo di C", RECLAMO_DI_C)["esito"] == "applicato"
        fresca = lingua.prepara(m.rete, DI_C).esegui()
        assert sorted(fresca) == sorted(m.chiedi(DI_C)["righe"]) == [("reclamo",), ("vendita",)]
        # Nessun neurone è stato dimenticato e C è nel giornale dal primo evento: è la stessa rete, lo stesso neurone.
        assert sorted(preparata.esegui()) == sorted(fresca)


def test_dopo_una_prova_la_rete_in_memoria_e_quella_riletta_dal_giornale(tmp_path):
    cartella = tmp_path / "rete"
    with aperta(Memoria(cartella, scritture=True)) as m:
        m.racconta("Fattura F1", [{"tipo": "fattura emessa", "legami": {"fattura": "F1", "cliente": "C1"}}])
        m.racconta("Fattura F2", [{"tipo": "fattura emessa", "legami": {"fattura": "F2", "cliente": "C2"}}])
        m.racconta("Riga di F1", [{"tipo": "riga di fattura", "legami": {"fattura": "F1", "prodotto": "P1"}}])
        m.racconta("Riga di F2", [{"tipo": "riga di fattura", "legami": {"fattura": "F2", "prodotto": "P2"}}])
        m.racconta("Altra riga di F1", [{"tipo": "riga di fattura", "legami": {"fattura": "F1", "prodotto": "P3"}}])
        strade = "percorsi da cliente a prodotto"
        prima = m.chiedi(strade)["righe"]
        assert prima and "fattura emessa" in prima[0][1]
        forma_prima = dict(m.rete.forma)
        linee_prima = sorted(m.rete.vivi)
        # una proposta solo provata: una fattura con un ruolo, «prodotto», che le fatture non hanno mai avuto
        provata = m.racconta("Fattura F3", [{"tipo": "fattura emessa", "legami": {"fattura": "F3", "cliente": "C1", "prodotto": "P1"}}],
                             prova=True)
        assert provata["esito"] == "prova"
        with aperta(Memoria(cartella)) as riaperta:
            dal_giornale = riaperta.chiedi(strade)["righe"]
            assert dal_giornale == prima  # il giornale non ha visto niente: la prova non ha scritto
            assert riaperta.rete.forma == forma_prima and sorted(riaperta.rete.vivi) == linee_prima
            # la stessa frase, alla memoria rimasta aperta: deve dare le strade che dà il giornale
            assert m.chiedi(strade)["righe"] == dal_giornale
            assert m.rete.forma == forma_prima and sorted(m.rete.vivi) == linee_prima
