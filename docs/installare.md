# Installare SinapsiDB

Tre modi, dal più semplice: provarlo come utente, installare il portale come servizio di sistema, aggiornarlo.
Poi i dati: dove stanno, come si salvano e come si rimettono a posto.

SinapsiDB è un progetto sperimentale. Il portale non ha autenticazione propria: ascolta solo su `127.0.0.1` e va
lasciato così, a meno di metterlo dietro un proxy che chiede l'accesso.

## Provarlo come utente

Servono Linux e Python 3.11 o più recente. Il compilatore C è facoltativo.

```sh
git clone https://github.com/cristianku/sinapsidb.git
cd sinapsidb
installa/installa.sh          # crea .venv, installa, compila il nucleo se può
installa/installa.sh avvia    # portale in primo piano su http://127.0.0.1:8419, Ctrl-C per fermarlo
```

Lo script non usa `sudo` e non tocca niente fuori dal repository e dalla cartella dei dati, che di serie è
`~/.local/share/sinapsidb` e si sceglie con `SINAPSIDB_DATI`. Le dipendenze sono quelle di
`installa/requirements.lock`, con le versioni esatte usate per le misure.

Senza compilatore il nucleo in C non c'è e SinapsiDB funziona lo stesso: le somme su tutti i fatti di un tipo le fa
il Python, più lentamente. Si può compilare dopo con `.venv/bin/python -m sinapsidb.nucleo compila`; lo stesso
comando senza `compila` dice se il nucleo è attivo.

## Come servizio di sistema

Il portale gira come utente di sistema `sinapsidb`, con il codice in `/opt/sinapsidb` e i dati in
`/var/lib/sinapsidb`. L'unità `installa/sinapsidb-portale.service` lo chiude in una sandbox di systemd e gli dà un
tetto di memoria. I comandi, da root:

```sh
useradd --system --home-dir /var/lib/sinapsidb --shell /usr/sbin/nologin sinapsidb
git clone https://github.com/cristianku/sinapsidb.git /opt/sinapsidb
cd /opt/sinapsidb
python3 -m venv .venv
.venv/bin/pip install -r installa/requirements.lock
.venv/bin/pip install --no-deps .
.venv/bin/python -m sinapsidb.nucleo compila     # facoltativo
install -d -m 0755 /etc/sinapsidb
install -m 0644 installa/sinapsidb-portale.env.example /etc/sinapsidb/portale.env
install -m 0644 installa/sinapsidb-portale.service /etc/systemd/system/sinapsidb-portale.service
systemctl daemon-reload
systemctl enable --now sinapsidb-portale
```

Per vedere che risponde: `curl -sI http://127.0.0.1:8419/` deve dare `307` verso `/decisioni`. Le prove complete
sono `scripts/prova_portale.sh` e `scripts/prova_decisioni.sh`, lanciate sulla stessa macchina.

La configurazione sta in `/etc/sinapsidb/portale.env`, spiegata riga per riga. Non contiene segreti. Dopo una
modifica: `systemctl restart sinapsidb-portale`.

### PostgreSQL e Redis per il confronto

La pagina «Confronto» funziona solo se il portale trova un PostgreSQL. Si collega dal socket locale come ruolo
`sinapsidb`, senza password, e scrive solo nella base di dati di prova, che quel ruolo possiede:

```sh
sudo -u postgres createuser sinapsidb
sudo -u postgres createdb -O sinapsidb sinapsidb_prova
```

Redis è facoltativo: si accende con `SINAPSIDB_PROVA_REDIS` in `portale.env`. Il portale ci tocca solo le chiavi
che cominciano con `sinapsidb:prova:`. Se Redis ascolta su un socket che appartiene al gruppo `redis`, l'utente del
servizio deve farne parte: `usermod -aG redis sinapsidb`, poi un riavvio del portale.

Come si fa un confronto e come si leggono i numeri: [confronto-postgresql.md](confronto-postgresql.md).

### La memoria

L'unità ha `MemoryMax=1200M`. Basta per le taglie di serie, fino a un milione di record: la rete di prova occupa
nel portale circa 430 MB a riposo e 800 MB durante l'importazione e il confronto. Per taglie più grandi servono
due modifiche insieme: la taglia in `SINAPSIDB_PROVA_TAGLIE` e un tetto più alto. Per dieci milioni di record il
portale ha bisogno di circa 10 GB:

```sh
systemctl edit sinapsidb-portale      # e scrivere: [Service] e sotto MemoryMax=10G
```

Con il pulsante «Tieni in memoria solo quello che serve» la rete di prova va sul disco e in memoria resta solo la
parte che le domande toccano, entro `SINAPSIDB_PROVA_TETTO_BLOCCHI_MB`.

## Aggiornare

Prima un salvataggio dei dati (sotto). Poi, da root, con il tag o il commit scelto al posto di `<versione>`:

```sh
cd /opt/sinapsidb
git fetch --tags
git checkout <versione>
.venv/bin/pip install -r installa/requirements.lock
.venv/bin/pip install --no-deps .
.venv/bin/python -m sinapsidb.nucleo compila     # il nucleo va ricompilato a ogni versione
systemctl restart sinapsidb-portale
```

Un nucleo compilato da un altro sorgente non si carica: il portale lo dice e fa le somme in Python finché non si
ricompila. Per tornare indietro si rifà lo stesso con la versione di prima e, se serve, si rimettono i dati salvati.

## I dati

Tutto sta in una cartella: `/var/lib/sinapsidb` per il servizio, `~/.local/share/sinapsidb` per lo script.

| Che cosa | Dove | Se si perde |
|---|---|---|
| La memoria delle decisioni | `decisioni/` | si perdono le decisioni della pagina «Decisioni» |
| La rete di prova del confronto | `prova/` | si rifà: «Importa da PostgreSQL» |
| Le metriche | `metriche.db` | si ricomincia da zero |

Ogni rete è una cartella con un `giornale` e, di solito, un'`istantanea`. **Il giornale è la verità**: ogni evento
ha la sua lunghezza e il suo CRC32, e non si cancella mai niente. L'istantanea serve solo a riaprire in fretta;
se manca, o se non corrisponde a quello che era stato salvato, SinapsiDB non la usa e rilegge il giornale.

### Salvare

Il modo sicuro è con il portale fermo, così nessuno sta scrivendo:

```sh
systemctl stop sinapsidb-portale
tar -C /var/lib -czf /root/sinapsidb-dati-$(date +%F).tar.gz sinapsidb
systemctl start sinapsidb-portale
```

La rete di prova può essere grande e si rifà da PostgreSQL: per salvare solo il resto si aggiunge
`--exclude=sinapsidb/prova`.

### Rimettere a posto

```sh
systemctl stop sinapsidb-portale
mv /var/lib/sinapsidb /var/lib/sinapsidb.prima
tar -C /var/lib -xzf /root/sinapsidb-dati-AAAA-MM-GG.tar.gz
chown -R sinapsidb:sinapsidb /var/lib/sinapsidb
systemctl start sinapsidb-portale
```

Se di una rete resta solo il `giornale`, basta quello: si toglie l'istantanea e al riavvio la rete si ricostruisce
rileggendolo. Con molti fatti la prima apertura è più lenta.

## Togliere SinapsiDB

```sh
systemctl disable --now sinapsidb-portale
rm /etc/systemd/system/sinapsidb-portale.service && systemctl daemon-reload
rm -r /opt/sinapsidb /etc/sinapsidb
```

I dati in `/var/lib/sinapsidb` restano finché non si cancellano a mano, come la base di dati `sinapsidb_prova` e
l'utente `sinapsidb`.
