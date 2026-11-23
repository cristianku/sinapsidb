"""Terzo giro di revisione interna della candidata 0.10.4, lente «mutazioni»: una protezione che nessuna
prova tiene ferma.

Contratto, paragrafo 9: «Una ricevuta rovinata è un guasto segnalato, non un errore di chi verifica». Paragrafo 8:
«Una ricevuta guasta non si riesamina». Tra il secondo giro e la candidata ``Memoria.verifica`` è passata da un elenco
di eccezioni a ``except Exception``, e ``Memoria.riesamina`` ha preso una conversione in ValueError. Tornando indietro

    verifica:   except Exception as rovinata:   ->   except (ValueError, KeyError, TypeError, AttributeError, IndexError) as rovinata:
    riesamina:  except (KeyError, TypeError, AttributeError, IndexError, OverflowError) as rovinata:   ->   except ZeroDivisionError as rovinata:

tutta la suite resta verde, una mutazione alla volta. Le ricevute rovinate provate finora (``test_interna_ricevuta_guasta``,
``test_interna2_decisioni_ricevuta_rovinata``) fanno uscire solo KeyError e AttributeError, e non chiamano ``riesamina``.
Qui: un numero fuori misura al posto del tipo di un fatto della proposta (OverflowError dentro la verifica), e altre
quattro ricevute rovinate che ``riesamina`` deve rifiutare dicendolo.
"""

import json
import struct
import zlib

import pytest

from sinapsidb.memoria import Memoria
from test_revisione_circuiti_090 import aperta
from test_segno_e_disco import fido, movimento


def leggi_record(cartella):
    dati, pos, record = (cartella / "giornale").read_bytes(), 0, []
    while pos + 8 <= len(dati):
        lunghezza, crc = struct.unpack_from("<II", dati, pos)
        corpo = dati[pos + 8:pos + 8 + lunghezza]
        assert zlib.crc32(corpo) == crc
        record.append(json.loads(corpo))
        pos += 8 + lunghezza
    return record


def riscrivi(cartella, record):
    with open(cartella / "giornale", "wb") as f:
        for r in record:
            corpo = json.dumps(r, ensure_ascii=False, separators=(",", ":")).encode()
            f.write(struct.pack("<II", len(corpo), zlib.crc32(corpo)) + corpo)


def memoria_del_fido(c):
    with aperta(Memoria(c, scritture=True)) as m:
        fido(m)  # eventi 0 e 1: il circuito e la regola
        assert movimento(m, "accredito", "Rossi", "100", "a1")["esito"] == "applicato"  # evento 2
        assert movimento(m, "ordine", "Rossi", "150", "o1")["esito"] == "violazione_regola"  # evento 3
        assert movimento(m, "ordine", "Rossi", "60", "o2")["esito"] == "applicato"  # evento 4
    return leggi_record(c)


FUORI_MISURA = {
    "tipo-del-fatto": lambda d: d["proposta"]["fatti"][0].__setitem__(0, 10 ** 400),
    "ruolo-del-fatto": lambda d: d["proposta"]["fatti"][0][1].__setitem__(0, 10 ** 400),
    "neurone-del-fatto": lambda d: d["proposta"]["fatti"][0][1].__setitem__(1, 10 ** 400),
}


@pytest.mark.parametrize("cambia", FUORI_MISURA.values(), ids=FUORI_MISURA.keys())
def test_un_numero_fuori_misura_nella_proposta_di_un_no_e_un_guasto_segnalato(tmp_path, cambia):
    c = tmp_path / "rete"
    record = memoria_del_fido(c)
    cambia(record[3][7]["ricevuta"]["decisione"])
    riscrivi(c, record)
    with aperta(Memoria(c)) as m:
        esito = m.verifica(3)  # sulla copia mutata: OverflowError: Python int too large to convert to C long
        assert esito["esito"] == "guasto", esito
        assert m.verifica(2)["esito"] == "coerente" and m.verifica(4)["esito"] == "coerente"  # le altre si verificano ancora


ROVINATE = {
    **FUORI_MISURA,
    "regola-lista": lambda d: d["regole"][0].__setitem__("regola_id", []),
    "nuovo-numero": lambda d: d["proposta"]["nuovi"].__setitem__(0, 7),
    "nuovi-vuoti": lambda d: d["proposta"].__setitem__("nuovi", []),
    "proposta-vuota": lambda d: d.__setitem__("proposta", {}),
}


@pytest.mark.parametrize("cambia", ROVINATE.values(), ids=ROVINATE.keys())
def test_una_ricevuta_rovinata_non_si_riesamina_e_lo_si_dice(tmp_path, cambia):
    c = tmp_path / "rete"
    record = memoria_del_fido(c)
    cambia(record[3][7]["ricevuta"]["decisione"])
    riscrivi(c, record)
    with aperta(Memoria(c)) as m:
        with pytest.raises(ValueError):  # sulla copia mutata: TypeError, AttributeError, IndexError, KeyError, OverflowError
            m.riesamina(3)
        assert m.riesamina(4)["evento"] == "evento:4"  # una decisione con la ricevuta buona si riesamina ancora
