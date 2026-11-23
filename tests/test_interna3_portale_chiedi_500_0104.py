"""Terzo giro di revisione interna sulla candidata 0.10.4: ``POST /api/confronto/chiedi`` risponde 500 a una
domanda scritta male.

``"\\ud800"`` è JSON valido, e Python lo legge come un carattere a metà (un surrogato da solo). La lingua rifiuta la
domanda con ``ErroreDomanda`` e nel messaggio cita il pezzo che non capisce («ma c'è «\\ud800»», oppure «... non è un
tipo di fatto della rete»); il portale mette quel messaggio in una ``JSONResponse``, che non si lascia scrivere in UTF-8:
``UnicodeEncodeError`` fuori dal percorso, 500. Una richiesta scritta male va respinta con 400, come fa lo stesso
percorso per ogni altra domanda che non si capisce, e come il secondo giro ha chiesto per i numeri fuori misura
(``tests/test_interna2_portale_500_0104.py``: «una richiesta scritta male va respinta, non fa cadere il percorso»).
"""

import pytest
from starlette.testclient import TestClient

from sinapsidb import portale
from sinapsidb.portale import ConfigPortale

BASE = "http://127.0.0.1:8419"
LOCALE = ("127.0.0.1", 5000)
AZIONE = {"x-sinapsidb-azione": "1", "content-type": "application/json"}


@pytest.mark.parametrize("corpo", [b'{"domanda": "in nota conta \\ud800"}', b'{"domanda": "in \\ud800 conta"}'], ids=["in-fondo", "nel-tipo"])
def test_una_domanda_con_un_carattere_a_meta_e_respinta_non_un_500(tmp_path, corpo):
    config = ConfigPortale(metriche_db=tmp_path / "m.db", sonda_intervallo=0, prova_pg=f"host={tmp_path}/nessuno port=1 dbname=x")
    with TestClient(portale.crea_app(config), base_url=BASE, client=LOCALE, raise_server_exceptions=False) as c:
        assert c.post("/api/confronto/chiedi", content=b'{"domanda": "vocabolario"}', headers=AZIONE).status_code == 200  # la rete di prova è aperta
        assert c.post("/api/confronto/chiedi", content=b'{"domanda": "in nota conta ancora"}', headers=AZIONE).status_code == 400  # una domanda sbagliata: 400
        risposta = c.post("/api/confronto/chiedi", content=corpo, headers=AZIONE)
        assert risposta.status_code == 400, (risposta.status_code, risposta.text)
