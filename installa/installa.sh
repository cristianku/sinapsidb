#!/usr/bin/env bash
# Installa SinapsiDB per l'utente che lo lancia, dentro questa copia del repository, e lo avvia in primo piano.
#
# Uso, da qualsiasi cartella:
#   installa/installa.sh          crea .venv accanto al repository, installa le versioni di installa/requirements.lock
#                                 e SinapsiDB, compila il nucleo in C se trova un compilatore
#   installa/installa.sh avvia    avvia il portale in primo piano su http://127.0.0.1:8419 (Ctrl-C per fermarlo)
#
# Non usa sudo e non cambia niente fuori dal repository e dalla cartella dei dati, che di serie è
# ~/.local/share/sinapsidb e si sceglie con SINAPSIDB_DATI. Per installarlo come servizio: docs/installare.md.
set -euo pipefail

RADICE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$RADICE/.venv"
DATI="${SINAPSIDB_DATI:-${XDG_DATA_HOME:-$HOME/.local/share}/sinapsidb}"
PYTHON="${PYTHON:-python3}"

installa() {
  if ! "$PYTHON" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
    echo "ERR  serve Python 3.11 o più recente (provato: $PYTHON)"; exit 1
  fi
  if [ ! -x "$VENV/bin/python" ]; then
    echo "...  creo l'ambiente in $VENV"
    "$PYTHON" -m venv "$VENV"
  fi
  echo "...  installo le dipendenze (installa/requirements.lock) e SinapsiDB"
  "$VENV/bin/pip" install --quiet --upgrade pip
  "$VENV/bin/pip" install --quiet -r "$RADICE/installa/requirements.lock"
  "$VENV/bin/pip" install --quiet --no-deps "$RADICE"
  if "$VENV/bin/python" -m sinapsidb.nucleo compila; then
    :
  else
    echo "nota il nucleo in C non è compilato: SinapsiDB funziona lo stesso, le somme su tutti i fatti le fa il Python"
  fi
  echo "ok   installato. Per avviare il portale: $RADICE/installa/installa.sh avvia"
}

avvia() {
  if [ ! -x "$VENV/bin/sinapsidb-portale" ]; then
    echo "ERR  SinapsiDB non è installato: prima $RADICE/installa/installa.sh"; exit 1
  fi
  if [ ! -d "$DATI" ]; then mkdir -p "$(dirname "$DATI")" && mkdir -m 700 "$DATI"; fi
  echo "ok   dati in $DATI"
  echo "ok   portale su http://127.0.0.1:${SINAPSIDB_PORTALE_PORT:-8419}/ (Ctrl-C per fermarlo)"
  SINAPSIDB_METRICHE_DB="${SINAPSIDB_METRICHE_DB:-$DATI/metriche.db}" exec "$VENV/bin/sinapsidb-portale"
}

case "${1:-installa}" in
  installa) installa ;;
  avvia) avvia ;;
  *) echo "uso: $0 [installa|avvia]"; exit 1 ;;
esac
