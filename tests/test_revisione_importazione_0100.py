"""I limiti dei lotti vanno rispettati anche da chi li usa: ammissione prima dei preparativi, letture dopo un guasto."""

import errno

import pytest

from sinapsidb import confronto, motore
from sinapsidb.memoria import Memoria
from sinapsidb.motore import Rete
from test_confronto import cluster, dati, pg
from test_revisione_circuiti_090 import aperta


def test_un_import_rifiutato_sulla_memoria_delle_decisioni_non_ci_lascia_la_sua_definizione(tmp_path, pg):
    c = tmp_path / "decisioni"
    with aperta(Memoria(c, scritture=True)) as m:
        a = m.racconta("Prima", [{"tipo": "nota", "legami": {"cosa": "A"}}], chiave="A")
        prima = m.conteggi(), list(m.rete.nomi), (c / "giornale").read_bytes(), (c / "formato").read_bytes()
        with confronto.collega(pg) as conn, pytest.raises(ValueError, match="decisioni"):
            confronto.importa(conn, m.rete)
        assert (m.conteggi(), m.rete.nomi, (c / "giornale").read_bytes(), (c / "formato").read_bytes()) == prima
    with aperta(Memoria(c)) as m:
        assert m.ricevuta_di("A") == a and not m.rete.circuiti


def test_dopo_un_errore_a_fine_import_il_banco_non_risponde_con_record_mai_arrivati(tmp_path, pg, monkeypatch):
    c = tmp_path / "prova"
    with aperta(confronto.Banco(c, pg)) as banco:
        rete = banco.rete()
        vero = motore.os.write
        fallito = False

        def senza_spazio(fd, b):
            nonlocal fallito
            # È il gruppo con le categorie, non l'evento che definisce il circuito. Non dipende da quando
            # l'importazione sceglie di scaricarlo né dal suo modo di sincronizzazione.
            if fd == rete._fd and b"categoria del reparto" in bytes(b):
                fallito = True
                raise OSError(errno.ENOSPC, "ultimo gruppo non arrivato")
            return vero(fd, b)

        with monkeypatch.context() as mp:
            mp.setattr(motore, "LOTTO", 1 << 28)  # i 6.000 record della prova stanno in un solo gruppo
            mp.setattr(motore.os, "write", senza_spazio)
            with pytest.raises(OSError):
                banco._importa()
        assert fallito and not (c / "importazione.json").exists()
        # Nessun record di categoria è arrivato, neanche se il circuito è stato scritto a parte prima dei lotti.
        assert all("categoria del reparto" not in rec[0] for rec in Rete._record(str(c / "rete")))
        try:
            risposta = banco.chiedi("in scheda categoria conta")
        except (RuntimeError, ValueError):
            pass  # rete da riaprire/azzerare oppure riaperta senza i tipi dell'azienda
        else:
            assert risposta["righe"] == [("0",)]
