#!/usr/bin/env bash
# Prova della pagina delle decisioni attraverso il portale: l'esempio del fido dall'inizio alla fine.
# Azzera la memoria di prova delle decisioni, accorda un fido, si fa rifiutare un ordine, registra un pagamento che
# vale da prima, riesamina l'ordine rifiutato e controlla che la decisione di allora sia rimasta com'era.
# Alla fine la memoria di prova resta con l'esempio dentro, visibile nella pagina /decisioni.
#
# Uso: scripts/prova_decisioni.sh [URL]
#   URL    predefinito http://127.0.0.1:8419
#
# Richiede solo curl e python3. Esce con codice diverso da zero al primo errore.
# Tocca solo la memoria di prova delle decisioni: non la memoria vera, non la rete di prova del confronto, non PostgreSQL.
set -euo pipefail

URL="${1:-http://127.0.0.1:8419}"
URL="${URL%/}"

manda() { # $1 operazione, $2 corpo JSON: stampa la risposta
  curl -sS --max-time 30 -X POST -H 'x-sinapsidb-azione: 1' -H 'content-type: application/json' -d "${2:-{\}}" "$URL/api/decisioni/$1"
}

leggi() { # $1 espressione python su r (il JSON letto da stdin): stampa il risultato
  python3 -c '
import json, sys
r = json.load(sys.stdin)
print(eval(sys.argv[1]))
' "$1"
}

vero() { # $1 descrizione, $2 JSON, $3 espressione python su r
  local esito
  esito=$(printf '%s' "$2" | leggi "$3") || { echo "ERR  $1 (risposta non leggibile: $2)"; exit 1; }
  if [ "$esito" = True ]; then echo "ok   $1"; else echo "ERR  $1: $2"; exit 1; fi
}

codice=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 10 "$URL/decisioni")
if [ "$codice" = 200 ]; then echo "ok   la pagina /decisioni risponde"; else echo "ERR  /decisioni -> $codice (atteso 200)"; exit 1; fi

codice=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 10 -X POST "$URL/api/decisioni/esempio")
if [ "$codice" = 403 ]; then echo "ok   operazione senza l'intestazione della pagina -> 403"; else echo "ERR  senza intestazione -> $codice (atteso 403)"; exit 1; fi

vero "memoria di prova azzerata" "$(manda azzera)" 'r["esito"] == "azzerato"'
vero "fido di 800 accordato" "$(manda accorda '{"cliente":"Rossi","importo":"800"}')" 'r["esito"] == "applicato"'
NO=$(manda ordine '{"cliente":"Rossi","importo":"1000"}')
vero "ordine da 1000 rifiutato, con la sua decisione registrata" "$NO" 'r["esito"] == "violazione_regola" and r["evento"] == 3'
PAGATO=$(manda pagamento '{"cliente":"Rossi","importo":"400","vale_da":"2020-01-01"}')
vero "pagamento di 400 valido da prima registrato" "$PAGATO" 'r["esito"] == "applicato" and r["evento"] == 4'

QUADRO=$(curl -sS --max-time 30 "$URL/api/decisioni")
vero "il fido di Rossi adesso è 1200" "$QUADRO" '[(c["nome"], c["fido"]) for c in r["clienti"]] == [("Rossi", "1200")]'
vero "il rifiuto dice che cosa aveva letto: 800 prima, -200 dopo" "$QUADRO" \
  '[(y["prima"], y["dopo"]) for d in r["decisioni"] if d["evento"] == 3 for y in d["letture"]] == [("800", "-200")]'
vero "il fido tenuto pronto coincide con i fatti e con il giornale" "$QUADRO" 'r["circuiti"] == "coerente"'

vero "riesame sapendo del pagamento: sarebbe stato un sì" "$(manda riesamina '{"evento":3,"rettifiche":[4]}')" \
  'r["allora"]["esito"] == "violazione_regola" and r["ipotetico"]["esito"] == "applicato" and r["cambia"] and r["eventi_usati"] == ["evento:4"]'
vero "il pagamento tocca la decisione rifiutata" "$(manda candidate '{"rettifica":4}')" \
  '"evento:3" in r["candidate"] and [x["evento"] for x in r["esiti_cambiati"]] == ["evento:3"]'
vero "la ricevuta del rifiuto coincide con il giornale" "$(manda verifica '{"evento":3}')" 'r["esito"] == "coerente"'

DOPO=$(curl -sS --max-time 30 "$URL/api/decisioni")
vero "il riesame non ha scritto niente: stessi eventi, stesso fido" "$(printf '[%s,%s]' "$QUADRO" "$DOPO")" \
  'r[0]["conteggi"] == r[1]["conteggi"] and r[0]["clienti"] == r[1]["clienti"]'

echo "tutto ok"
