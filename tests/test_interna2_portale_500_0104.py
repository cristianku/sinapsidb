"""Secondo giro di revisione interna sulla candidata 0.10.4: due strade per cui il portale risponde 500.

1. ``POST /api/decisioni/{verifica,riesamina,candidate}`` con un numero JSON fuori misura (``1e999``, che è JSON valido
   e che Python legge come infinito): ``int(corpo["evento"])`` solleva ``OverflowError``, che non è tra gli errori che
   il percorso trasforma in 400. Una richiesta scritta male va respinta, non fa cadere il percorso.
2. ``POST /api/confronto/chiedi`` dopo che un lavoro ha lasciato la rete di prova fuori uso su un disco che dà errori:
   ``Banco._scarta_se_fuori_uso`` chiude la rete ferma senza proteggersi (``try/finally``), la chiusura solleva
   (l'``fsync`` di ``Rete.chiudi``) e l'errore arriva a chi ha fatto la domanda. ``Fido._riapri`` nello stesso caso lo
   mette in conto («una memoria ferma può non riuscire a chiudere pulita»). AGENTS: «Chi tiene aperta una memoria la sa
   riaprire. Il banco del confronto e il servizio del fido riaprono dal giornale una rete che si è fermata».
"""

import errno
import os

import pytest
from starlette.testclient import TestClient

from sinapsidb import confronto, portale
from sinapsidb.portale import ConfigPortale

BASE = "http://127.0.0.1:8419"
LOCALE = ("127.0.0.1", 5000)
AZIONE = {"x-sinapsidb-azione": "1"}


def config(tmp_path):
    return ConfigPortale(metriche_db=tmp_path / "m.db", sonda_intervallo=0, prova_pg=f"host={tmp_path}/nessuno port=1 dbname=x")


@pytest.mark.parametrize("op, corpo", [("verifica", b'{"evento": 1e999}'), ("riesamina", b'{"evento": 2, "rettifiche": [1e999]}'),
                                       ("candidate", b'{"rettifica": 1e999}')], ids=["verifica", "riesamina", "candidate"])
def test_un_numero_fuori_misura_e_una_richiesta_scritta_male_non_un_500(tmp_path, op, corpo):
    with TestClient(portale.crea_app(config(tmp_path)), base_url=BASE, client=LOCALE, raise_server_exceptions=False) as c:
        assert c.post("/api/decisioni/accorda", json={"cliente": "Rossi", "importo": "800"}, headers=AZIONE).status_code == 200
        risposta = c.post(f"/api/decisioni/{op}", content=corpo, headers={**AZIONE, "content-type": "application/json"})
        assert risposta.status_code == 400, (risposta.status_code, risposta.text)
        assert c.get("/api/decisioni").status_code == 200


def test_una_domanda_alla_rete_di_prova_rimasta_fuori_uso_non_e_un_500(tmp_path, monkeypatch):
    banchi = []

    class Spia(confronto.Banco):  # il banco che il portale si costruisce, per poterci mettere le mani come farebbe un lavoro
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            banchi.append(self)

    monkeypatch.setattr(portale, "Banco", Spia)
    with TestClient(portale.crea_app(config(tmp_path)), base_url=BASE, client=LOCALE, raise_server_exceptions=False) as c:
        conta = lambda: c.post("/api/confronto/chiedi", json={"domanda": "in nota conta"}, headers=AZIONE)  # noqa: E731
        assert c.post("/api/confronto/chiedi", json={"domanda": "vocabolario"}, headers=AZIONE).status_code == 200  # la rete di prova è aperta
        banco = banchi[0]
        with banco._lock_rete:  # come un lavoro: scrive nella rete di prova tenendola per sé
            rete = banco.rete()
            n = rete.n
            rete.racconta("Una nota", "prova", [(n("nota"), [(n("cosa"), n("A"))])])
        assert conta().json()["righe"] == [["1"]]
        # Il disco comincia a dare errori: la scrittura dopo resta a metà nel giornale, non si riesce a toglierla, e
        # anche l'fsync con cui la rete ferma viene chiusa fallisce. Ogni guasto una volta sola: poi il disco torna buono.
        scrivi, tronca, sincronizza, fd, fatti = os.write, os.ftruncate, os.fsync, rete._fd, set()

        def una_volta(nome, vera, a_meta=False):
            def finta(d, *a):
                if d == fd and nome not in fatti:
                    fatti.add(nome)
                    if a_meta:
                        vera(d, bytes(a[0])[:5])
                    raise OSError(errno.EIO, "errore di ingresso/uscita")
                return vera(d, *a)
            return finta

        with monkeypatch.context() as mp:
            mp.setattr(os, "write", una_volta("write", scrivi, a_meta=True))
            mp.setattr(os, "ftruncate", una_volta("ftruncate", tronca))
            mp.setattr(os, "fsync", una_volta("fsync", sincronizza))
            with banco._lock_rete, pytest.raises(OSError):
                rete.racconta("Un'altra", "prova", [(n("nota"), [(n("cosa"), n("B"))])])
            assert rete.fuori_uso  # il lavoro ha lasciato la rete di prova fuori uso
            risposta = conta()
        # o risponde quello che dice il giornale (una nota), o dice che la rete si sta riaprendo: non cade
        assert risposta.status_code in (200, 409), (risposta.status_code, risposta.text)
        if risposta.status_code == 200:
            assert risposta.json()["righe"] == [["1"]]
