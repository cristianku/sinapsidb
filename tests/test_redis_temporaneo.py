"""Test del Redis privato e temporaneo: parte sul suo socket, risponde, si spegne e non lascia niente.

Non usano mai il Redis di sistema: il servizio è un processo figlio in una cartella temporanea.
Vengono saltati se ``redis-server`` o la libreria ``redis`` non sono installati.
"""

from __future__ import annotations

import contextlib
import os

import pytest

import redis_temporaneo


@contextlib.contextmanager
def servizio(opzioni: dict[str, str] | None = None):
    try:
        import redis
    except ImportError as e:
        pytest.skip(f"redis non è installato: {e}")
    try:
        with redis_temporaneo.avvia(opzioni=opzioni) as s:
            yield s
    except redis_temporaneo.NonDisponibile as e:
        pytest.skip(str(e))


def test_scrive_e_rilegge_e_alluscita_non_lascia_niente():
    with servizio() as s:
        assert len(s.socket) <= redis_temporaneo.LUNGHEZZA_MAX_SOCKET
        import redis
        conn = redis.Redis(unix_socket_path=s.socket, socket_timeout=2)
        conn.set("fido:rossi", "800")
        assert conn.get("fido:rossi") == b"800"
        assert conn.config_get("appendfsync")["appendfsync"] == "everysec"  # la variante di serie
        conn.close()
    # all'uscita: il processo è spento, la cartella e il socket cancellati
    assert s.processo.returncode is not None
    assert not os.path.exists(s.cartella)
    assert not os.path.exists(s.socket)


def test_le_opzioni_si_possono_cambiare_variant_durevole():
    with servizio(opzioni={"appendfsync": "always", "maxmemory": "64mb"}) as s:
        import redis
        conn = redis.Redis(unix_socket_path=s.socket, socket_timeout=2)
        assert conn.config_get("appendfsync")["appendfsync"] == "always"
        assert conn.config_get("maxmemory")["maxmemory"] == str(64 * 1024 * 1024)
        conn.close()
    assert s.processo.returncode is not None
    assert not os.path.exists(s.cartella)
    assert not os.path.exists(s.socket)


def test_un_percorso_del_socket_demodo_salta(tmp_path):
    cartella = tmp_path / "una-cartella-molto-lunga-che-serviva-a-far-traccheggiare-il-percorso-del-socket-oltre-i-centosette-caratteri"
    cartella.mkdir()
    try:
        with redis_temporaneo.avvia(dove=str(cartella)) as s:
            pytest.fail("con un percorso lungo doveva sollevare NonDisponibile")
    except redis_temporaneo.NonDisponibile as e:
        if "107" not in str(e):
            pytest.skip(str(e))  # su una macchina senza il servizio il salto arriva prima
    # la cartella temporanea creata per il tentativo è cancellata comunque: nessun residuo
    assert not os.listdir(cartella)
