#!/usr/bin/env bash
# Prova del confronto tra PostgreSQL, Redis e SinapsiDB attraverso il portale, dall'inizio alla fine:
# genera l'azienda di prova (se si indica una taglia), azzera la rete di prova, importa i dati da
# PostgreSQL, prepara le risposte in Redis (se il portale è collegato a un Redis), lancia il confronto e
# controlla che le risposte coincidano. Alla fine la rete di prova resta importata, le risposte restano
# in Redis e l'ultimo confronto resta visibile nella pagina /confronto.
#
# Uso: scripts/prova_confronto.sh [URL] [RIGHE] [senza-redis] [sul-disco]
#   URL          predefinito http://127.0.0.1:8419
#   RIGHE        se c'è, prima genera in PostgreSQL un'azienda di prova di quella taglia (deve essere una
#                di quelle che il portale ammette, per esempio 300000); se manca (o è ""), usa i dati
#                che ci sono già
#   senza-redis  salta Redis anche se il portale ci è collegato: serve quando i dati sono troppi per la
#                memoria concessa a Redis
#   sul-disco    dopo il confronto con la rete tutta in memoria, porta la rete di prova sul disco e lo
#                ripete: le risposte devono restare uguali. Alla fine la rete resta sul disco
#
# Richiede solo curl e python3. Esce con codice diverso da zero al primo errore.
# In PostgreSQL scrive solo la generazione. In Redis tocca solo le chiavi del confronto (sinapsidb:prova:*).
# Tutto il resto tocca solo la rete di prova.
set -euo pipefail

URL="${1:-http://127.0.0.1:8419}"
URL="${URL%/}"
RIGHE="${2:-}"
REDIS=""
DISCO=""
for opzione in "${@:3}"; do
  case "$opzione" in
    senza-redis) REDIS=senza-redis ;;
    sul-disco) DISCO=sul-disco ;;
    *) echo "ERR  opzione sconosciuta: $opzione (si può scrivere senza-redis, sul-disco)"; exit 1 ;;
  esac
done

stato() { curl -sS --max-time 30 "$URL/api/confronto"; }

leggi() { # $1 espressione python su r (lo stato letto da stdin): stampa il risultato
  python3 -c '
import json, sys
r = json.load(sys.stdin)
print(eval(sys.argv[1]))
' "$1"
}

vero() { # $1 descrizione, $2 espressione python su r
  local esito
  esito=$(stato | leggi "$2") || { echo "ERR  $1 (stato non leggibile)"; exit 1; }
  if [ "$esito" = True ]; then echo "ok   $1"; else echo "ERR  $1"; stato | leggi 'r["lavoro"]'; exit 1; fi
}

lancia() { # $1 lavoro, $2 secondi di attesa al massimo, $3 parametri (facoltativi, per esempio ?righe=300000)
  local codice n
  codice=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 10 -X POST -H 'x-sinapsidb-azione: 1' "$URL/api/confronto/$1${3:-}")
  if [ "$codice" != 202 ]; then echo "ERR  $1 non partito: HTTP $codice (atteso 202)"; exit 1; fi
  for n in $(seq 1 "$2"); do
    if [ "$(stato | leggi 'r["lavoro"]["in_corso"]')" = False ]; then
      vero "$1 finito senza errori" 'r["lavoro"]["nome"] == "'"$1"'" and r["lavoro"]["errore"] is None'
      return
    fi
    sleep 1
  done
  echo "ERR  $1 non è finito in $2 secondi"; exit 1
}

# Appena partito, il portale riapre la rete di prova in sottofondo: con molti dati dura un minuto. Si aspetta che finisca.
for n in $(seq 1 600); do
  if [ "$(stato | leggi 'r["lavoro"]["in_corso"]')" = False ]; then break; fi
  if [ "$n" = 1 ]; then echo "     il portale sta lavorando ($(stato | leggi 'r["lavoro"]["nome"]')): aspetto che finisca"; fi
  sleep 1
done

if [ "$(stato | leggi 'r["postgres"]["raggiungibile"]')" != True ]; then
  echo "ERR  PostgreSQL non risponde: $(stato | leggi 'r["postgres"]["errore"]')"; exit 1
fi
echo "ok   PostgreSQL $(stato | leggi 'r["postgres"]["versione"]') risponde"

codice=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 10 -X POST "$URL/api/confronto/misura")
if [ "$codice" = 403 ]; then echo "ok   lavoro senza l'intestazione della pagina -> 403"; else echo "ERR  lavoro senza intestazione -> $codice (atteso 403)"; exit 1; fi

if [ -n "$RIGHE" ]; then
  if [ "$(stato | leggi 'r["postgres"]["puo_scrivere"]')" != True ]; then
    echo "ERR  il portale non ha il permesso di scrivere in PostgreSQL: il suo ruolo deve possedere la base di dati di prova"; exit 1
  fi
  lancia genera 1800 "?righe=$RIGHE"
  vero "azienda di prova generata: 20 tabelle, nessuna vuota, taglia entro il 10% di $RIGHE" \
    'r["postgres"]["disponibile"] and len(r["postgres"]["tabelle"]) == 20 and all(t["righe"] > 0 for t in r["postgres"]["tabelle"]) and abs(r["postgres"]["totale"] / '"$RIGHE"' - 1) < 0.1 and r["postgres"]["generazione"]["righe_richieste"] == '"$RIGHE"
elif [ "$(stato | leggi 'r["postgres"]["disponibile"]')" != True ]; then
  echo "ERR  in PostgreSQL non c'è l'azienda di prova: $(stato | leggi 'r["postgres"]["errore"]'). Si genera dalla pagina, o indicando la taglia a questo script."; exit 1
fi
stato | python3 -c '
import json, sys
pg = json.load(sys.stdin)["postgres"]
g = pg["generazione"] or {}
print("     %d record in %d tabelle, seme %s" % (pg["totale"], len(pg["tabelle"]), g.get("seme", "non noto")))
for t in pg["tabelle"]:
    print("       %-24s %9d" % (t["etichetta"], t["righe"]))
'

lancia azzera 120
vero "rete di prova vuota dopo l'azzeramento" 'r["sinapsi"]["fatti"] == 0 and r["confronto"] is None'

lancia importa 1800
vero "un fatto per ogni record" 'r["sinapsi"]["fatti"] == r["postgres"]["totale"] == r["importazione"]["fatti"]'
echo "     $(stato | leggi '"%d fatti, %d neuroni, importati in %.1f secondi" % (r["sinapsi"]["fatti"], r["sinapsi"]["neuroni"], r["importazione"]["secondi"])')"

# Una domanda libera, scritta nella lingua di SinapsiDB: quante fatture ci sono? Deve dare lo stesso numero di PostgreSQL.
attese=$(stato | leggi 'r["postgres"]["righe"]["fatture"]')
risposta=$(curl -sS --max-time 30 -X POST -H 'x-sinapsidb-azione: 1' -H 'content-type: application/json' \
  -d '{"domanda": "in fattura emessa conta"}' "$URL/api/confronto/chiedi" | leggi 'r["righe"][0][0]') || risposta="nessuna risposta"
if [ "$risposta" = "$attese" ]; then
  echo "ok   domanda nella lingua di SinapsiDB: «in fattura emessa conta» -> $risposta, come in PostgreSQL"
else
  echo "ERR  domanda nella lingua di SinapsiDB: «in fattura emessa conta» -> $risposta (attese $attese)"; exit 1
fi

# Redis: se il portale ci è collegato, gli si preparano le risposte dei dati che ci sono in PostgreSQL.
con_redis=no
if [ "$REDIS" = senza-redis ]; then
  echo "     Redis saltato, come chiesto"
elif [ "$(stato | leggi 'r["redis"]["configurato"]')" != True ]; then
  echo "     Redis non è collegato a questo portale: il confronto è solo con PostgreSQL"
elif [ "$(stato | leggi 'r["redis"]["raggiungibile"]')" != True ]; then
  echo "ERR  Redis è collegato al portale ma non risponde: $(stato | leggi 'r["redis"]["errore"]')"; exit 1
else
  echo "ok   Redis $(stato | leggi 'r["redis"]["versione"]') risponde"
  lancia redis 3600
  vero "in Redis ci sono le risposte pronte dei dati che ci sono in PostgreSQL" \
    'r["redis"]["allineato"] and r["redis"]["chiavi"] > 0 and r["redis"]["caricamento"]["righe"] > 0'
  echo "     $(stato | leggi '"%d chiavi, %d righe di risposta, %.0f MB, preparate in %.1f secondi" % (r["redis"]["chiavi"], r["redis"]["caricamento"]["righe"], r["redis"]["memoria_risposte"] / 2**20, r["redis"]["caricamento"]["secondi"])')"
  con_redis=si
fi

lancia misura 1800
vero "16 domande, nessuna risposta diversa tra SinapsiDB e PostgreSQL, nessuna rimasta fuori" \
  'len(r["confronto"]["domande"]) == 16 and r["confronto"]["saltate"] == [] and r["confronto"]["risposte_diverse"] == 0 and all(d["prove"] > 0 for d in r["confronto"]["domande"])'
vero "la somma tenuta pronta è nel confronto, con le stesse righe di quella calcolata" \
  '[d["righe_medie"] for d in r["confronto"]["domande"] if d["pronta"]] == [d["righe_medie"] for d in r["confronto"]["domande"] if d["nome"] == "Fatturato mese per mese"]'
vero "di ogni domanda anche il tempo medio, di SinapsiDB e di PostgreSQL" \
  'all(d["sinapsi"]["media_us"] > 0 and d["postgres"]["media_us"] > 0 and d["postgres_nel_server_media_us"] > 0 for d in r["confronto"]["domande"])'
if [ "$con_redis" = si ]; then
  vero "Redis nel confronto: 16 domande, nessuna risposta diversa da PostgreSQL" \
    'r["confronto"]["redis"]["risposte_diverse"] == 0 and all(d["redis"] and d["redis_risposte_diverse"] == 0 and d["redis"]["p50_us"] > 0 and d["redis"]["media_us"] > 0 and d["redis_nel_server_media_us"] is not None for d in r["confronto"]["domande"])'
  vero "il prezzo della cache è nel confronto: quanto è durata la preparazione delle risposte" 'r["confronto"]["redis"]["preparazione_s"] > 0'
else
  vero "senza Redis il confronto è solo con PostgreSQL" 'r["confronto"]["redis"] is None and all(d["redis"] is None for d in r["confronto"]["domande"])'
fi

tabella() {
stato | python3 -c '
import json, sys
c = json.load(sys.stdin)["confronto"]
def t(us):
    return "%8.1f µs" % us if us < 1000 else "%8.2f ms" % (us / 1000)
print()
con_redis = c["redis"] is not None
dal_disco = c["rete"]["modo"] == "disco"
print("     %-48s %11s" % ("domanda (tempo mediano)", "SinapsiDB") + ("%12s" % "prima volta" if dal_disco else "") + "%12s %11s" % ("PostgreSQL", "nel server")
      + ("%12s %11s" % ("Redis", "nel server") if con_redis else ""))
for d in c["domande"]:
    print("     %-48s %s" % (d["nome"], t(d["sinapsi"]["p50_us"])) + (" %s" % t(d["sinapsi_prima_volta"]["p50_us"]) if dal_disco else "")
          + " %s %s" % (t(d["postgres"]["p50_us"]), t(d["postgres_nel_server_p50_us"]))
          + (" %s %s" % (t(d["redis"]["p50_us"]), t(d["redis_nel_server_p50_us"])) if con_redis else ""))
contate = [d for d in c["domande"] if not d["pronta"]]  # dove tutti leggono una risposta tenuta pronta non si fa punteggio
print("     nucleo compilato: " + ("attivo, ha calcolato «%s»" % "», «".join(d["nome"] for d in c["domande"] if d["nucleo"]) if c["nucleo"]["attivo"] else "non attivo (%s)" % c["nucleo"]["perche"]))
vinte = sum(d["sinapsi"]["p50_us"] <= d["postgres"]["p50_us"] for d in contate)
print()
print("     SinapsiDB più rapido di PostgreSQL in %d domande su %d, su %d fatti" % (vinte, len(contate), c["fatti"]))
if con_redis:
    su_redis = sum(d["sinapsi"]["p50_us"] <= d["redis"]["p50_us"] for d in contate)
    print("     SinapsiDB più rapido di Redis in %d domande su %d; le risposte pronte occupano %.0f MB in Redis" % (su_redis, len(contate), c["redis"]["memoria_risposte"] / 2**20))
# quante volte più rapido: la media geometrica dei rapporti domanda per domanda, come nella pagina, e quante domande
# stanno oltre 2, 5 e 10 volte. La media dei tempi non si usa: la farebbe la domanda più lenta
from math import exp, log
def rapporti(nome, mio, altro):
    xs = [altro(d) / mio(d) for d in contate if mio(d) and altro(d)]
    g = exp(sum(map(log, xs)) / len(xs))
    st = [x if g >= 1 else 1 / x for x in xs]
    print("     contro %-36s %-14s %5.1f volte più rapido in media; oltre 2/5/10 volte in %d/%d/%d, indietro in %d, su %d domande" % (
        nome, "SinapsiDB" if g >= 1 else nome.split(",")[0], g if g >= 1 else 1 / g,
        sum(x > 2 for x in st), sum(x > 5 for x in st), sum(x > 10 for x in st), sum(x < 1 for x in st), len(xs)))
sinapsi = lambda d: d["sinapsi"]["p50_us"]
rapporti("PostgreSQL, totale", sinapsi, lambda d: d["postgres"]["p50_us"])
rapporti("PostgreSQL, solo nel server", sinapsi, lambda d: d["postgres_nel_server_p50_us"])
if con_redis:
    rapporti("Redis (cache), totale", sinapsi, lambda d: d["redis"]["p50_us"])
    rapporti("Redis (cache), solo nel server", sinapsi, lambda d: d["redis_nel_server_p50_us"])
    print("     preparazione delle risposte in Redis: %s" % ("%.1f secondi" % c["redis"]["preparazione_s"] if c["redis"].get("preparazione_s") is not None else "non registrata"))
if dal_disco:
    rapporti("PostgreSQL, SinapsiDB la prima volta", lambda d: d["sinapsi_prima_volta"]["p50_us"], lambda d: d["postgres"]["p50_us"])
mediana = lambda xs: sorted(xs)[len(xs) // 2] if len(xs) % 2 else sum(sorted(xs)[len(xs) // 2 - 1:len(xs) // 2 + 1]) / 2
tipici = ["SinapsiDB %s" % t(mediana([sinapsi(d) for d in contate])).strip(),
          "PostgreSQL %s" % t(mediana([d["postgres"]["p50_us"] for d in contate])).strip()]
if con_redis:
    tipici.append("Redis (cache) %s" % t(mediana([d["redis"]["p50_us"] for d in contate])).strip())
print("     tempo tipico di una domanda (mediana delle domande): " + ", ".join(tipici))
if dal_disco:
    r = c["rete"]
    print("     rete sul disco: %.0f MB; in memoria %.1f MB su %.0f al massimo; blocchi entrati %d, usciti %d" % (r["su_disco"] / 2**20, r["in_memoria"] / 2**20, r["tetto"] / 2**20, r["caricati"], r["espulsi"]))
'
}
tabella
echo "     memoria del portale con la rete tutta in memoria: $(stato | leggi '"%.0f MB" % (r["rete"]["portale"] / 2**20)')"

# La rete di prova sul disco, con in memoria solo quello che serve: stessi fatti, stesse risposte.
if [ "$DISCO" = sul-disco ]; then
  fatti=$(stato | leggi 'r["sinapsi"]["fatti"]')
  lancia disco 3600
  vero "la rete di prova è sul disco, con gli stessi fatti" 'r["rete"]["modo"] == "disco" and r["sinapsi"]["fatti"] == '"$fatti"' and r["rete"]["su_disco"] > 0'
  risposta=$(curl -sS --max-time 30 -X POST -H 'x-sinapsidb-azione: 1' -H 'content-type: application/json' \
    -d '{"domanda": "in fattura emessa conta"}' "$URL/api/confronto/chiedi" | leggi 'r["righe"][0][0]') || risposta="nessuna risposta"
  if [ "$risposta" = "$attese" ]; then
    echo "ok   la stessa domanda dalla rete sul disco: «in fattura emessa conta» -> $risposta"
  else
    echo "ERR  domanda dalla rete sul disco: «in fattura emessa conta» -> $risposta (attese $attese)"; exit 1
  fi
  lancia misura 3600
  vero "con la rete sul disco: 15 domande, nessuna risposta diversa da PostgreSQL, la prima volta misurata, la somma pronta dichiarata fuori" \
    'r["confronto"]["rete"]["modo"] == "disco" and len(r["confronto"]["domande"]) == 15 and [s["nome"] for s in r["confronto"]["saltate"]] == ["Fatturato mese per mese, tenuto pronto"] and r["confronto"]["risposte_diverse"] == 0 and all(d["sinapsi_prima_volta"]["p50_us"] > 0 for d in r["confronto"]["domande"]) and r["confronto"]["rete"]["in_memoria"] <= r["confronto"]["rete"]["tetto"]'
  tabella
  echo "     memoria del portale con la rete sul disco: $(stato | leggi '"%.0f MB" % (r["rete"]["portale"] / 2**20)')"
fi
echo "tutto ok: $URL/confronto"
