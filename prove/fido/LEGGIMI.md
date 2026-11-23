# Tracciato del fido e giudice esterno

Da una radice del repository, per il **collaudo**, su una memoria di prova nuova:

```sh
python3 -m prove.fido.tracciato genera piano.jsonl
python3 -m prove.fido.tracciato registra --socket /percorso/corto/s piano.jsonl osservato.jsonl
python3 -m prove.fido.verificatore --piano piano.jsonl osservato.jsonl
```

La prima e la terza riga richiedono solo Python 3.11+ standard. Il registratore usa il `Cliente` comune, anche per
PostgreSQL e per PostgreSQL + Redis. Non sovrascrive un file di risultati esistente. Il servizio di SinapsiDB si avvia
con `PYTHONPATH=src python3 prove/fido/servizio.py --cartella ... --socket ...` e si chiude dopo il collaudo.
I tempi del collaudo **non** sono un confronto di prestazioni.

- **Piano:** una riga per tentativo, `seme`, `gruppo` (barriera fra gruppi), `richiedente`, `richiesta` e, dove
  serve, `fase` o `perdita`. `id_tentativo` non si riusa, `chiave` rimane identica al retry. `@chiave` in un
  riferimento significa l'evento osservato per quella chiave. Un confine `{"scritto_di":"CONFINE"}` usa l'istante
  di quel commit convertito in ISO UTC: non si finge che un server abbia l'orologio di un altro.
- **Osservazioni:** richiesta effettivamente inviata, `invio_ns` e `ritorno_ns` monotoni del client, e i due orari
  UTC (`invio_utc_ns`, `ritorno_utc_ns`) per confrontare gli istanti Unix del server sullo stesso nodo. Le
  risposte conservano l'ordine dei commit anche se le conferme arrivano in altro ordine. Una risposta persa è
  `null`, mai una copia nascosta della ricevuta. Il collaudo sopprime deliberatamente un ACK dopo il commit,
  perde un'altra richiesta prima della consegna, cambia connessione e interroga `stato(chiave)` **prima** di
  ciascun retry. La prima chiave dev'essere presente, la seconda assente. Il test interrompe davvero il processo
  SinapsiDB con SIGKILL in quel punto e riapre lo stesso giornale. Non è una prova di mancanza di corrente.
- **Giudice:** confronta tentativi e payload con il piano, ricostruisce i fatti esclusivamente da richieste e
  ricevute osservate e calcola gli esiti con interi in milionesimi. L'ordine di commit testimoniato deve essere
  contiguo e compatibile con gli intervalli invio/risposta. Una lettura non può dichiarare una frontiera anteriore
  a un commit già confermato all'invocazione, né vedere un commit non ancora invocato al ritorno. Per il
  riesame ricalcola separatamente i valori di allora e quelli ipotetici; per le candidate scandisce le decisioni,
  ammette falsi positivi, non omissioni di una decisione cambiata. Un errore dà un codice di uscita non zero.
- **Dominio del tracciato:** un'unica regola sul fido; identificativi di atti, ordini e pagamenti espliciti e
  univoci; rettifiche per causale, importi decimali testuali, date ISO. Il caso 1–11 usa due fondi temporali
  diversi, ritiri e trasferimenti interi; rilegge le chiavi e riesamina le decisioni dopo la riapertura. Il
  controllo sul filo da solo non è una verifica dei dati su disco.

**Controllo obbligatorio del registro per il collaudo di ripresa:** dopo il recupero, a scrittori fermi, esportare
il giornale in JSONL e passarlo allo stesso giudice. `registro.py` decodifica direttamente record, lunghezze,
checksum e riferimenti di SinapsiDB; per PostgreSQL legge in un solo snapshot le tabelle permanenti, senza usare
le funzioni `valuta`, `riesamina`, il saldo o Redis. Il giudice non apre database: verifica chiavi, coordinate,
ricevute e letture rispetto alle osservazioni e ricostruisce dalle richieste i fatti attesi, i ritiri e la
proposta conservata dai no. Anche un fatto in più non richiesto, senza un saldo finale diverso, fa fallire la prova.

```sh
python3 -m prove.fido.registro --giornale /percorso/della/rete registro.jsonl
# Per PG o PG+Redis, in alternativa: --dsn "$DSN_PRIVATO"
python3 -m prove.fido.verificatore --piano piano.jsonl --registro registro.jsonl osservato.jsonl
```

Il rapporto dichiara se è stato controllato anche il registro. I test fanno entrambe le verifiche. Le prove
fisiche di perdita di alimentazione e l'iniezione di un errore di lettura nel motore sono collaudi distinti.

Per eseguire il collaudo in due segmenti, dare `--prima-del-riavvio` a `registra` salvando `prima.jsonl`.
Alla barriera il programma esce e libera le connessioni: il responsabile del collaudo ferma e riavvia il proprio
schieramento **sullo stesso archivio**. Richiamare `registra --riprendi-da prima.jsonl` con lo stesso piano,
scrivendo `dopo.jsonl`, e dare al giudice la concatenazione dei due file. Non rigenerare o ritentare le scritture
prima di aver interrogato lo stato delle chiavi. Per uno scenario che inietta una vera caduta *durante* l'invio,
marcare il tentativo `perdita: "in_volo"`: un ACK ricevuto resta osservato, altrimenti occorre la sonda per
risolverne l'esito. I guasti fuori dai punti previsti interrompono il collaudo.

Per generare un carico, senza eseguirlo: `python3 -m prove.fido.tracciato genera --tipo carico --seme 417
--clienti 1000 --operazioni 10000 --letture 95 --richiedenti 4 --distribuzione contesa carico.jsonl` (su una
sola riga). Le fasi `preparazione`, `carico` e `ricerca` sono distinte; inizializzazione e riesame non alterano
le quote 95/5, 50/50 e 60/30/10 del generatore. La scansione esaustiva del giudice avviene **fuori** dai tempi
misurati. Taglie grandi e configurazioni si eseguono solo dopo il manifesto e il collaudo dei tre schieramenti.

`--tipo selettivita` prende `--clienti` clienti estranei al bersaglio e `--operazioni` ordini del bersaglio:
moltiplicare prima il primo parametro per dieci, poi il secondo a clienti fissi. La rettifica di un milionesimo
tocca le letture del bersaglio senza cambiare nessun esito.

## PostgreSQL e punto di innesto della cache

Il concorrente PostgreSQL ha un'implementazione separata dal modello: fatti, intervalli, dipendenze e ricevute in
`postgres/schema.sql`; `valuta` usa aritmetica `numeric` nel database. La stessa funzione decide sia sulla base
corrente (saldo mantenuto) sia sulla base storica con rettifiche selezionate. `testa` dà l'ordine seriale dei
commit e mantiene il blocco fino al commit; la chiave e le causali hanno lock transazionali, i clienti sono bloccati
in ordine di nome. Anche un no committa una ricevuta. Le letture multi-query di verifica, candidate e riesame
usano un solo snapshot `REPEATABLE READ`.

Preparare un PostgreSQL **privato della prova**, database vuoto `prova`, socket dedicato, con i parametri di
`docs/prova-del-fido.md` (`PARAMETRI` in `postgres/base.py`). Esempio di avvio dell'adattatore:

```sh
python -m prove.fido.postgres --dsn "host=/percorso/pg-privato port=54417 user=postgres dbname=prova" --socket /percorso/corto/s
```

Il servizio crea lo schema `prova_fido` se manca, non azzera nulla alla riapertura, e rifiuta i parametri di
durabilità disattivati. Il database della prova non è quello del portale. Per il collaudo automatico, i test usano
`tests/pg_temporaneo.py` con i parametri durevoli, fermano il postmaster temporaneo in modo immediato e lo
riavviano prima della sonda sulla chiave. È recovery del WAL dopo l'arresto del processo; la directory temporanea
dei test può anche essere su tmpfs e non dimostra durata alla perdita di alimentazione.

Per il concorrente con Redis:

- `Database(dsn, con_cache=True)` su un altro cluster temporaneo abilita **solo** la coda transazionale
  `prova_fido.coda_cache(evento,cliente)`. Lo spostamento può pubblicare due clienti con lo stesso evento:
  consumare l'evento intero o usare un cursore composto. Il rifiuto non invalida il saldo.
- `Database.sessione()` gestisce una connessione persistente. L'adattatore può specializzare `Sessione.fido(q,t)`
  mantenendo lo stesso `esegui`, `scrivi` e lo stesso protocollo Unix/JSONL (`trasporto.apri`).
- `sessione.revisione(cliente)` restituisce revisione, formula e frontiera viste sul primario; `proiezione(cliente,t)`
  restituisce anche `valore`, `valido_al` e `[da,fine)` (`None` = estremo illimitato). La formula del carico è
  fissa alla versione 0. Un cache hit serve solo per revisione e formula uguali e a intervallo valido; la frontiera
  della risposta è quella vista dal primario, non la revisione della chiave. Fuori finestra `proiezione` rifà
  la somma dai fatti e la conserva per la finestra successiva.

Per eseguire i test dalla radice con l'ambiente di sviluppo e `runuser` nel `PATH`:

```sh
PATH="$PATH:/usr/sbin" PYTHONPATH=src python -m pytest -q tests/test_giudice_fido.py tests/test_pg_fido.py
```
