"""Su 36a7495: un fatto alla volta è il riferimento, anche se il record non si può comporre o scade un fido."""

from enum import Enum
from types import SimpleNamespace

import pytest

from sinapsidb import lingua, motore
from sinapsidb.memoria import Memoria
from sinapsidb.motore import Rete, Vietato
from test_calcolo_dichiarato import fido
from test_revisione_circuiti_090 import aperta, circuito
from test_velocita import _uno_alla_volta


VIE = (("uno", _uno_alla_volta), ("molti", Rete.racconta_molti))
MENSILE = "circuito vendite mensili"


@pytest.fixture
def orologio(monkeypatch):
    """La stessa sequenza per le due storie; dopo l'ultima lettura il tempo rimane fermo."""
    def metti(*ore):
        sequenza = iter(ore)
        monkeypatch.setattr(motore, "time", SimpleNamespace(time=lambda: next(sequenza, ore[-1])))

    metti(123456.5)
    return metti


@pytest.mark.parametrize("sync", ["os", "lotti"])
def test_un_record_non_codificabile_non_lascia_una_vendita_nel_mensile_o_nella_foto(tmp_path, orologio, sync):
    attese = [("2027-03", "10"), ("2027-05", "2")]
    # Una frase proveniente da un file con un byte non UTF-8, letto con surrogateescape.
    illeggibile = b"Vendita \xff".decode("utf-8", "surrogateescape")
    for via, scrivi in VIE:
        c = tmp_path / via
        with aperta(Rete(str(c), sync=sync)) as r:
            circuito(r)

            def vendita(frase, giorno, totale):
                return frase, r.n("vendita"), [(r.n("quando"), r.n(giorno)), (r.n("totale"), r.n(totale))]

            def righe():
                yield vendita("Marzo", "2027-03-01", "10")
                yield vendita(illeggibile, "2027-04-01", "90")
                pytest.fail("il lettore deve fermarsi sulla riga che non può scrivere")

            with pytest.raises(UnicodeEncodeError):
                scrivi(r, "importazione", righe())
            dopo_errore = lingua.prepara(r, MENSILE).esegui()
            assert scrivi(r, "importazione", [vendita("Maggio", "2027-05-01", "2")]) == 1
            r.istantanea()
        giornale, dopo = Rete.com_era(str(c), 3)
        assert dopo is None and lingua.prepara(giornale, MENSILE).esegui() == attese
        with aperta(Rete(str(c))) as da_foto:
            assert (dopo_errore, lingua.prepara(da_foto, MENSILE).esegui()) == (attese[:1], attese)
            assert da_foto.conteggi() == giornale.conteggi() and not da_foto.trova("2027-04")
    assert (tmp_path / "uno/giornale").read_bytes() == (tmp_path / "molti/giornale").read_bytes()


@pytest.mark.parametrize("campo", ["tipo", "partecipante", "interi-ordinari"])
def test_gli_id_nel_record_sono_numeri_anche_se_hanno_un_nome_di_enumerazione(tmp_path, orologio, campo):
    for via, scrivi in VIE:
        c = tmp_path / via
        with aperta(Rete(str(c))) as r:
            tipo, ruolo, ada = (r.n(s) for s in ("vendita", "cliente", "Ada"))
            # Un'enumerazione che è anche int: la colonna del motore e il codificatore JSON ne usano il valore.
            rif = Enum("Rif", {"VENDITA": tipo, "ADA": ada}, type=int)
            fatto = ("Vendita", rif.VENDITA if campo == "tipo" else tipo, [(ruolo, rif.ADA if campo == "partecipante" else ada)])
            assert scrivi(r, "test", [fatto]) == 1 and r.conteggi()["eventi"] == 1
    a, b = (tmp_path / "uno/giornale").read_bytes(), (tmp_path / "molti/giornale").read_bytes()
    assert b == a
    with aperta(Rete(str(tmp_path / "molti"))) as r:
        assert r.nomi[r.leggi(0, r.n("cliente"))] == "Ada"


@pytest.mark.parametrize("periodo,esito,valori", [("vale da", "violazione_regola", ["0", "-60"]),
                                                ("vale fino a", "applicato", ["100", "40"])])
def test_il_passaggio_a_racconta_non_sposta_l_arrivo_oltre_il_confine_del_fido(tmp_path, orologio, periodo, esito, valori):
    confine = motore.istante("2027-02-01")
    for via, scrivi in VIE:
        c = tmp_path / via
        orologio(confine - 60)
        with aperta(Memoria(c, scritture=True)) as m:
            r, n = m.rete, m.rete.n
            fido(m)
            r.racconta("Credito di 100", "test", [(n("accredito"), [(n("cliente"), n("C")), (n("importo"), n("100")),
                                                                   (n(periodo), n("2027-02-01"))])])
            ordine = ("Ordine di 60", n("ordine"), [(n("cliente"), n("C")), (n("importo"), n("60"))])
            orologio(confine - 1, confine)  # all'arrivo manca un secondo; una seconda lettura cade già sul confine
            try:
                assert scrivi(r, "test", [ordine]) == 1
            except Vietato:
                pass  # il verdetto da confrontare è quello nel giornale
            assert m.verifica(3)["esito"] == "coerente"
        ricevuta = list(Rete._record(str(c)))[3][7]["ricevuta"]
        d = ricevuta["decisione"]
        assert (ricevuta["esito"], d["istante"], [x["valore"] for x in d["regole"][0]["letture"]]) == (esito, confine - 1, valori)
    assert (tmp_path / "molti/giornale").read_bytes() == (tmp_path / "uno/giornale").read_bytes()


def test_anche_il_ripiego_per_orologio_arretrato_conserva_il_primo_campionamento(tmp_path, orologio):
    for via, scrivi in VIE:
        with aperta(Rete(str(tmp_path / via))) as r:
            tipo, ruolo, ada = (r.n(s) for s in ("nota", "cliente", "Ada"))
            orologio(100.0, 80.0, 110.0)
            assert scrivi(r, "test", [(s, tipo, [(ruolo, ada)]) for s in ("Prima", "Seconda")]) == 2
            assert (list(r.e_quando), list(r.e_scritto)) == ([100.0, 80.0], [100.0, 100.0])


def test_un_ruolo_di_validita_che_nasce_nel_flusso_viene_controllato_subito(tmp_path, orologio):
    for via, scrivi in VIE:
        c = tmp_path / via
        with aperta(Rete(str(c))) as r:
            def righe():
                tipo, ruolo, chi = (r.n(s) for s in ("vendita", "cliente", "Ada"))
                yield "Prima vendita", tipo, [(ruolo, chi)]
                # Il ruolo non esisteva al primo fatto. La scadenza del secondo precede la sua registrazione.
                yield "Già scaduta", tipo, [(ruolo, chi), (r.n("vale fino a"), r.n("1970-01-01"))]
                pytest.fail("è stata accettata una validità vuota")

            with pytest.raises(motore.NonNumerico):
                scrivi(r, "test", righe())
            assert r.e_frase == ["Prima vendita"] and r.conteggi()["fatti"] == 1
    assert (tmp_path / "molti/giornale").read_bytes() == (tmp_path / "uno/giornale").read_bytes()
