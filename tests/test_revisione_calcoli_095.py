"""§9, 10c5ab7: compatibilità di una ricevuta, provenienza di un tratto e riesame non sono la stessa prova."""

import json
import shutil
from pathlib import Path

import pytest

from sinapsidb.memoria import Memoria
from sinapsidb.motore import Rete
from test_revisione_circuiti_090 import aperta


DATI = Path(__file__).parent / "dati"
INDISTINGUIBILE = DATI / "storico-092-093-indistinguibile"
INCREMENTALE = DATI / "storico-8cad802-incrementale"
NO_STORICO = DATI / "storico-a28cd35-ricevute"


def test_due_scrittori_con_giornale_identico_non_identificano_il_calcolo(tmp_path):
    # Sia 760859b sia a28cd35 hanno scritto questi stessi byte e verificato il no (v. generatore).
    cartella = shutil.copytree(INDISTINGUIBILE, tmp_path / "rete")
    with aperta(Memoria(cartella)) as db:
        v = db.verifica(3)
        assert v["esito"] in ("coerente", "compatibile") and v["differenze"] == []
        # Non attribuire un unico modo: tutti rifanno esattamente le letture «0 → −60» del no.
        # Lista proposta per il contratto: distingue le compatibilità dalla provenienza dichiarata.
        assert {1, 2}.issubset(v.get("calcoli_compatibili", [])), v


def test_il_dubbio_sul_calcolo_non_rende_incerto_il_no_originale(tmp_path):
    cartella = shutil.copytree(INDISTINGUIBILE, tmp_path / "rete")
    with aperta(Memoria(cartella)) as db:
        x = db.riesamina(3)
        assert not x["eventi_usati"] and x["ipotetico"] == x["allora"] and not x["cambia"]
        assert x["allora"]["esito"] == "violazione_regola"
        assert [y["valore"] for y in x["allora"]["letture"]] == ["0", "-60"]


def test_nessuna_attribuzione_singola_se_i_modi_compatibili_divergono_sulla_rettifica(tmp_path):
    cartella = shutil.copytree(INDISTINGUIBILE, tmp_path / "rete")
    prima = (cartella / "giornale").read_bytes()
    with aperta(Memoria(cartella)) as db:
        try:
            x = db.riesamina(3, [4])  # accredito di 70 intestato ad A e B, retroattivo
        except ValueError as exc:
            # Lecito fermarsi se manca la provenienza; un errore generico o «ricevuta guasta» non spiega l'ambiguità.
            assert any(k in str(exc).lower() for k in ("calcolo", "semantica", "ambigu", "indeterminat"))
        else:
            assert x["allora"]["esito"] == "violazione_regola" and x["eventi_usati"] == ["evento:4"]
            # La 0.9.2 dava sì (70→10), la 0.9.3 no (0→−60): il riepilogo non può scegliere uno dei due.
            assert x["ipotetico"]["esito"] not in ("applicato", "violazione_regola", "non_applicabile"), x["ipotetico"]
    assert (cartella / "giornale").read_bytes() == prima


@pytest.mark.parametrize("modo,esito,letture", [
    (1, "applicato", ["70", "10"]), (2, "violazione_regola", ["0", "-60"]),
])
def test_con_una_frontiera_attestata_lo_stesso_giornale_diventa_determinato(tmp_path, modo, esito, letture):
    cartella = shutil.copytree(INDISTINGUIBILE, tmp_path / "rete")
    Rete.attesta(str(cartella), modo, 0, 5, f"generazione della copia di prova con il modo {modo}")
    with aperta(Memoria(cartella)) as db:
        v = db.verifica(3)
        assert (v["esito"], v["calcolo"], v["calcolo_da"]) == ("coerente", modo, "attestazione")
        x = db.riesamina(3, [4])
        assert x["ipotetico"]["esito"] == esito and [y["valore"] for y in x["ipotetico"]["letture"]] == letture


def test_una_frontiera_nota_non_prova_altri_modi_per_salvare_un_no(tmp_path):
    cartella = shutil.copytree(NO_STORICO, tmp_path / "rete")
    Rete.attesta(str(cartella), 4, 3, 4, "controprova: fonte che attribuisce il modo sbagliato")
    with aperta(Memoria(cartella)) as db:
        assert db.verifica(3)["esito"] == "guasto"
        with pytest.raises(ValueError):
            db.riesamina(3)


def test_modo_ignoto_dichiarato_non_equivale_a_modo_assente(tmp_path):
    cartella = shutil.copytree(NO_STORICO, tmp_path / "rete")
    Rete.attesta(str(cartella), 2, 3, 4, "registro di prova")
    # Come l'avrebbe prodotta un programma che conosce il 99: tratto, fonte e impronta sono intatti.
    segno = Rete.segno(str(cartella))
    segno["attestati"][0]["modo"] = 99
    (cartella / "formato").write_text(json.dumps(segno))
    prima = (cartella / "formato").read_bytes()
    try:
        with aperta(Memoria(cartella)) as db:
            v = db.verifica(3)
            assert v["esito"] == "non_supportato", v
            with pytest.raises(ValueError):
                db.riesamina(3)
    except ValueError as exc:  # oppure il lettore può fermarsi già all'apertura o alla verifica
        assert any(k in str(exc).lower() for k in ("calcolo", "99", "sconosciut"))
    assert (cartella / "formato").read_bytes() == prima


def test_un_segno_presente_ma_illeggibile_non_si_sostituisce_con_una_nuova_frontiera(tmp_path):
    cartella = shutil.copytree(NO_STORICO, tmp_path / "rete")
    file = cartella / "formato"
    # Guasto simulato in una copia: la vecchia frontiera non può essere dedotta dalla lunghezza odierna.
    incompleto = b'{"formato":2,"dal":0,"calcoli":['
    file.write_bytes(incompleto)
    try:
        with aperta(Memoria(cartella)) as db:
            risposta = db.verifica(3)
    except (ValueError, OSError):
        pass
    else:
        assert risposta["esito"] not in ("coerente", "compatibile"), risposta
    finally:
        assert file.read_bytes() == incompleto


def test_un_lettore_non_attribuisce_gli_eventi_futuri_al_suo_binario(tmp_path):
    cartella = shutil.copytree(INCREMENTALE, tmp_path / "rete")
    prima = (cartella / "formato").read_bytes()
    with aperta(Memoria(cartella, scritture=False)) as db:
        # Sulla ricevuta del fatto con due occorrenze di A è davvero distinguibile il modo 3.
        v = db.verifica(2)
        assert v["esito"] in ("coerente", "compatibile") and (v.get("calcolo") == 3 or 3 in v.get("calcoli_compatibili", []))
    # Il prossimo evento può ancora essere scritto dalla 0.9.4. Il lettore non ne conosce l'autore.
    assert (cartella / "formato").read_bytes() == prima


def test_la_migrazione_fissa_solo_l_inizio_delle_scritture_nuove(tmp_path):
    cartella = shutil.copytree(INCREMENTALE, tmp_path / "rete")
    with aperta(Memoria(cartella, scritture=True)) as db:
        e = len(db.rete.e_frase)
        x = db.racconta("Nota nuova", [{"tipo": "nota", "legami": {"cosa": "X"}}])
        segno = Rete.segno(str(cartella))
        assert segno["dal"] == 0 and segno["calcoli"] == [[4, e]]
        assert db.verifica(x["evento"])["calcolo"] == 4
        v = db.verifica(2)
        assert v["esito"] in ("coerente", "compatibile") and (v.get("calcolo") == 3 or 3 in v.get("calcoli_compatibili", []))
