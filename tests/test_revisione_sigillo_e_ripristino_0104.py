"""Dalla revisione: né una scorciatoia di lettura né uno zero devono nascondere la risposta del giornale."""

import pytest

from sinapsidb.confronto import Banco
from sinapsidb.fido import CIRCUITO, Fido
from sinapsidb.memoria import Memoria
from sinapsidb.motore import GiornaleGuasto, Rete
from test_revisione_circuiti_090 import aperta
from test_revisione_lotti_0100 import nota


@pytest.mark.parametrize("prima_del_salvataggio", [False, True], ids=["dopo-la-foto", "prima-della-foto"])
def test_un_errore_del_supporto_non_diventa_buono_con_un_istantanea(tmp_path, prima_del_salvataggio):
    c, g = str(tmp_path), tmp_path / "giornale"
    with aperta(Rete(c)) as r:
        nota(r, "Ada")
        nota(r, "Bea")
        if not prima_del_salvataggio:
            r.istantanea()
        # Stesso inode e stessa lunghezza. Errore di un bit nei dati del primo record, non nell'ultimo.
        sani = g.read_bytes()
        i = sani.index(b"Ada")
        guasti = sani[:i] + bytes([sani[i] ^ 1]) + sani[i + 1:]
        with g.open("r+b") as f:
            f.seek(i)
            f.write(guasti[i:i + 1])
        if prima_del_salvataggio:
            try:
                r.istantanea()
            except (GiornaleGuasto, OSError, RuntimeError):
                pass  # lecito rifiutare già il salvataggio; conta quello che verrà servito al riavvio
    # Lo stesso diario senza istantanea è riconosciuto guasto. Con l'istantanea deve valere lo stesso.
    controllo = tmp_path / "solo-giornale"
    controllo.mkdir()
    (controllo / "giornale").write_bytes(guasti)
    for cartella in (str(controllo), c):
        with pytest.raises(GiornaleGuasto), aperta(Rete(cartella, sola_lettura=True)):
            pass
    assert g.read_bytes() == guasti


def test_il_banco_non_serve_blocchi_di_un_altro_giornale_lungo_uguale(tmp_path):
    cartella = tmp_path / "banco"
    g = cartella / "rete" / "giornale"
    q = "in nota leggi frase, cosa"
    with aperta(Banco(cartella, "host=/nessun-socket")) as b:
        nota(b.rete(), "Ada")
        b._disco()
        assert b.chiedi(q)["righe"] == [("Ada", "Ada")]
    altra = tmp_path / "copia"
    with aperta(Rete(str(altra))) as r:
        nota(r, "Bea")  # un giornale autentico, con istanti uguali e dati diversi
    dati = (altra / "giornale").read_bytes()
    assert len(dati) == g.stat().st_size and dati != g.read_bytes()
    g.write_bytes(dati)  # ripristino a portale fermo
    with aperta(Banco(cartella, "host=/nessun-socket")) as b:
        assert b.chiedi(q)["righe"] == [("Bea", "Bea")]


def test_un_cliente_ambiguo_non_e_un_cliente_senza_fido(tmp_path):
    with aperta(Fido(tmp_path)) as f:
        assert f.esegui({"op": "accorda", "cliente": "Rossi", "importo": "100", "chiave": "a1"})["esito"] == "applicato"
    with aperta(Memoria(tmp_path, scritture=True)) as m:
        assert m.racconta("Un altro Rossi", [{"tipo": "nota", "legami": {"cosa": "Rossi"}}],
                          nuovi=["Rossi"])["esito"] == "applicato"
        ids = [f"neurone:{i}" for i in m.rete.trova("Rossi")]
        assert [m.valore(CIRCUITO, i)["valore"] for i in ids] == ["100", "0"]
    with aperta(Fido(tmp_path)) as f:
        def fido(nome):
            return f.esegui({"op": "fido", "cliente": nome})

        assert [fido(i)["valore"] for i in ids] == ["100", "0"]
        assert fido("Mai visto")["valore"] == "0"
        risposta = fido("Rossi")
        assert not risposta["ok"] or "valore" not in risposta, risposta
