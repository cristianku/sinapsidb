"""Terzo giro di revisione interna della candidata 0.10.4, lente «giornale»: la coda giudicata su una seconda lettura.

Il secondo giro aveva trovato che ``_rileggi_il_giornale`` decideva che cos'è la coda con due letture diverse del
file, e che tra le due chi scrive poteva cambiarlo (``test_interna2_apertura_lettore_in_corsa_0104``). La correzione
prende la lunghezza una volta sola con ``fstat``: «il giornale è quello che c'è nel momento in cui lo si apre». Ma
``giudica_la_coda``, che è codice nuovo, la coda non la giudica sui byte che ``_rileggi`` ha letto e non è riuscito a
rileggere: torna sul file (``f.seek(fine)``, ``f.read``) e giudica quello che ci trova in quel momento. E
``blocchi._record``, che ora giudica la coda anche lui, la lunghezza la chiede a ``fstat`` DOPO essere arrivato in
fondo. In tutti e due i posti le letture sono di nuovo due, e chi scrive può passarci in mezzo.

Primo caso, chi apre per leggere (``Rete``, e quindi ``Memoria``). Il giornale finisce con una coda scritta a metà:
un arresto brusco, o una scrittura fallita che chi scrive sta per togliere. Chi legge la legge. Poi chi scrive
riparte: taglia la coda, come deve, e scrive altri eventi, più corti, nello stesso punto. Chi legge torna sul file
per giudicare la «coda» e ci trova record interi e buoni: «GiornaleGuasto ... va ripristinato da una copia». Il
giornale è buono in ogni istante. Tra la prima lettura e la seconda chi legge rifà in memoria tutti i record
dell'ultimo pezzo, fino a 64 MB: su una rete grande sono secondi, non un attimo.

Secondo caso, chi costruisce la rete sul disco (``blocchi.costruisci``). Arriva in fondo al giornale, che finisce
pulito; chi scrive aggiunge un evento; ``fstat`` dice che il file è più lungo, e l'evento appena scritto, intero e
buono, diventa «un record intero che non si rilegge». Qui non serve nemmeno una coda a metà. Il portale costruisce i
blocchi a rete chiusa, quindi oggi non ci arriva: resta che chi cammina sul giornale deve fermarsi dove si ferma chi
apre, e chi apre lì si ferma senza errori.

La prova non usa fili né orologi, come quella del secondo giro: quando la lettura di chi legge arriva dove il
giornale finiva, chi scrive (una ``Rete`` vera, aperta per scrivere) fa la sua parte.
"""

import struct

import pytest

from sinapsidb import blocchi, motore
from sinapsidb.motore import GiornaleGuasto, Rete
from test_revisione_circuiti_090 import aperta


def scrivi(r, frase):
    return r.racconta(frase, "test", [(r.n("nota"), [(r.n("cosa"), r.n("Ada"))])])


def record(dati):
    """Dove comincia e dove finisce ogni record del giornale."""
    pos, confini = 0, []
    while pos + 8 <= len(dati):
        lunghezza = struct.unpack_from("<I", dati, pos)[0]
        confini.append((pos, pos + 8 + lunghezza))
        pos += 8 + lunghezza
    return confini


def come_lo_vede_chi_legge(monkeypatch, modulo, giornale, fin_dove, chi_scrive):
    """Il giornale come lo vede chi legge in ``modulo``: appena una sua lettura arriva a ``fin_dove`` (o resta vuota),
    ``chi_scrive`` fa la sua parte, una volta sola. Le letture dopo trovano il file come lo ha lasciato lui."""
    vero, fatto = open, []

    def tocca_a_chi_scrive():
        if not fatto:
            fatto.append(True)
            chi_scrive()

    class Giornale:
        def __init__(self, f):
            self.f = f

        def __enter__(self):
            return self

        def __exit__(self, *_):
            self.f.close()

        def seek(self, *dove):
            return self.f.seek(*dove)

        def fileno(self):
            return self.f.fileno()

        def read(self, quanti=-1):
            dati = self.f.read(quanti)
            if not dati or self.f.tell() >= fin_dove:
                tocca_a_chi_scrive()
            return dati

    def apri(percorso, modo="r", *altro, **altri):
        f = vero(percorso, modo, *altro, **altri)
        return Giornale(f) if modo == "rb" and str(percorso) == str(giornale) and not fatto else f

    monkeypatch.setattr(modulo, "open", apri, raising=False)
    return tocca_a_chi_scrive


@pytest.mark.parametrize("dopo", [1, 2], ids=["un-evento-dopo", "due-eventi-dopo"])
def test_chi_legge_mentre_chi_scrive_taglia_la_coda_e_riparte_non_trova_un_guasto(tmp_path, monkeypatch, dopo):
    c = tmp_path / "rete"
    with aperta(Rete(str(c), sync="os")) as r:
        for nome in "abcde":
            scrivi(r, f"Nota {nome}")
        scrivi(r, "Nota lunga " + "x" * 1000)
    intero = (c / "giornale").read_bytes()
    inizio, _ = record(intero)[5]
    # un arresto brusco a metà del sesto record: i suoi primi trecento byte sono nel file, il resto no
    (c / "giornale").write_bytes(intero[:inizio + 300])

    def chi_scrive_riparte():
        with aperta(Rete(str(c), sync="os")) as scrittore:  # taglia la coda a metà, come deve
            assert scrittore.conteggi()["eventi"] == 5 and (c / "giornale").stat().st_size == inizio
            for n in range(dopo):
                scrivi(scrittore, f"Dopo {n}")

    riparte = come_lo_vede_chi_legge(monkeypatch, motore, c / "giornale", inizio + 300, chi_scrive_riparte)
    try:
        lettore = Rete(str(c), sola_lettura=True)
    except GiornaleGuasto as guasto:
        monkeypatch.undo()
        with aperta(Rete(str(c), sola_lettura=True)) as adesso:  # il giornale è buono: adesso si apre, con tutti i suoi eventi
            assert adesso.conteggi()["eventi"] == 5 + dopo
        pytest.fail(f"chi legge ha trovato un guasto in un giornale buono (cinque eventi e una coda a metà quando lo ha "
                    f"aperto, {5 + dopo} eventi interi adesso): {guasto}")
    with aperta(lettore):
        assert lettore.conteggi()["eventi"] in (5, 5 + dopo)  # quelli che c'erano quando ha aperto, o tutti
    monkeypatch.undo()
    riparte()  # se chi legge non è mai arrivato in fondo, chi scrive riparte adesso
    with aperta(Rete(str(c), sola_lettura=True)) as adesso:
        assert adesso.conteggi()["eventi"] == 5 + dopo


def test_chi_costruisce_la_rete_sul_disco_mentre_chi_scrive_aggiunge_non_trova_un_guasto(tmp_path, monkeypatch):
    c = tmp_path / "rete"
    scrittore = Rete(str(c), sync="os")
    try:
        for nome in "abcde":
            scrivi(scrittore, f"Nota {nome}")
        lungo = (c / "giornale").stat().st_size
        # il giornale finisce pulito; chi scrive aggiunge un evento quando chi costruisce è arrivato in fondo
        aggiunge = come_lo_vede_chi_legge(monkeypatch, blocchi, c / "giornale", lungo + 1, lambda: scrivi(scrittore, "Nota f"))
        try:
            numeri = blocchi.costruisci(c / "giornale", tmp_path / "blocchi")
        except GiornaleGuasto as guasto:
            monkeypatch.undo()
            with aperta(Rete(str(c), sola_lettura=True)) as adesso:  # per chi apre, lo stesso giornale è buono
                assert adesso.conteggi()["eventi"] == 6
            pytest.fail(f"chi costruisce la rete sul disco ha trovato un guasto in un giornale buono, mentre chi scrive ci "
                        f"aggiungeva un evento intero: {guasto}")
        assert numeri["fatti"] in (5, 6)
        monkeypatch.undo()
        aggiunge()
        assert scrittore.conteggi()["eventi"] == 6 and not scrittore.fuori_uso
    finally:
        scrittore.chiudi()
