#!/usr/bin/env bash
# Prova di fumo di sinapsidb-portale: healthz, pagine e file statici, API del riepilogo, stato del confronto.
# Il confronto con PostgreSQL dall'inizio alla fine si prova con scripts/prova_confronto.sh.
# Uso: scripts/prova_portale.sh [URL]   (predefinito http://127.0.0.1:8419)
# Richiede solo curl e python3. Esce con codice diverso da zero al primo errore.
set -euo pipefail

URL="${1:-http://127.0.0.1:8419}"
URL="${URL%/}"

vero() { # $1 espressione python su r (risposta JSON letta da stdin); esce 0 se è vera
  python3 -c '
import json, sys
try:
    r = json.load(sys.stdin)
    sys.exit(0 if eval(sys.argv[1]) else 1)
except Exception as e:
    print("     " + repr(e)[:300]); sys.exit(1)
' "$1"
}

controlla() { # $1 descrizione, $2 percorso, $3 espressione python su r
  if curl -sS --max-time 10 "$URL$2" | vero "$3"; then echo "ok   $1"; else echo "ERR  $1"; exit 1; fi
}

statico() { # $1 percorso, $2 inizio del content-type atteso
  local esito
  esito=$(curl -sS -o /dev/null --max-time 10 -w '%{http_code} %{content_type}' "$URL$1")
  case "$esito" in
    "200 $2"*) echo "ok   GET $1 -> $esito" ;;
    *) echo "ERR  GET $1 -> $esito (atteso 200 $2)"; exit 1 ;;
  esac
}

controlla "healthz" /healthz 'r["status"] == "ok"'
esito=$(curl -sS -o /dev/null --max-time 10 -w '%{http_code} %{redirect_url}' "$URL/")
case "$esito" in
  "307 "*/decisioni) echo "ok   GET / -> $esito" ;;
  *) echo "ERR  GET / -> $esito (atteso 307 verso /decisioni)"; exit 1 ;;
esac
statico /portale.css text/css
statico /icona.svg image/svg+xml
statico /dati text/html
statico /confronto text/html
statico /confronto.js text/javascript
statico /decisioni text/html
statico /decisioni.js text/javascript

# Le intestazioni si leggono per intero prima di cercare: con grep in pipe, curl potrebbe trovare la pipe chiusa.
intestazioni=$(curl -sS --max-time 10 -D - -o /dev/null "$URL/decisioni")
if grep -qi "^content-security-policy: default-src 'self'" <<<"$intestazioni"; then
  echo "ok   Content-Security-Policy presente"
else
  echo "ERR  Content-Security-Policy assente"; exit 1
fi

controlla "riepilogo dell'ultima ora" '/api/riepilogo?finestra=1h' \
  'r["finestra"] == "1h" and len(r["intervalli"]) == 60 and "totale" in r'

# Lo stato del confronto si legge anche quando PostgreSQL non c'è: in quel caso dice perché.
controlla "stato del confronto leggibile" /api/confronto \
  '"lavoro" in r and (r["postgres"]["disponibile"] or r["postgres"]["errore"])'
# Lo stesso per Redis: se il portale ci è collegato, o risponde o lo stato dice perché no.
controlla "stato di Redis nel confronto leggibile" /api/confronto \
  'not r["redis"]["configurato"] or r["redis"]["raggiungibile"] or r["redis"]["errore"]'

codice=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$URL/api/riepilogo?finestra=2h")
if [ "$codice" = 400 ]; then echo "ok   finestra non valida -> 400"; else echo "ERR  finestra non valida -> $codice (atteso 400)"; exit 1; fi

codice=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 -H 'Host: altro.example' "$URL/healthz")
if [ "$codice" = 421 ]; then echo "ok   Host non ammesso -> 421"; else echo "ERR  Host non ammesso -> $codice (atteso 421)"; exit 1; fi

echo "tutto ok: $URL"
