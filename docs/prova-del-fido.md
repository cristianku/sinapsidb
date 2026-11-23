# La prova del fido: che cosa si misura e come

**Stato: protocollo approvato dai revisori il 22 novembre 2026.** Scritta da chi ha scritto il motore e completata
con il testo dei revisori: le configurazioni dei concorrenti le hanno scritte loro, non chi ha scritto il motore.
Nessun numero di questa pagina esiste ancora: finché i casi di correttezza non passano con il verificatore esterno e
il manifesto non è fissato non si misura niente.

## A che cosa serve

A mettere alla prova una tesi, con un criterio per bocciarla:

> Una sola definizione della regola serve a leggere il presente, decidere una scrittura e riesaminare le decisioni
> passate, compresi i rifiuti.

Oggi un'azienda ottiene queste tre cose con più sistemi: un database per i dati, una cache per leggere in fretta, e
del codice scritto a mano per tenere le richieste rifiutate e rifare i conti dopo una correzione. La prova confronta
SinapsiDB con la migliore combinazione di quei sistemi **sullo stesso problema**, non su tre gare scelte a nostro
favore.

Non è una prova di primato. Dice se, su questo problema, un motore solo regge il confronto con la combinazione
abituale, e quanto costa.

## Il problema, uguale per tutti

Un'azienda con dei clienti. Ogni cliente ha un fido.

| Operazione | Che cosa fa |
|---|---|
| leggere il fido | il fido residuo di un cliente, adesso |
| chiedere un ordine | accettato se il fido residuo dopo l'ordine non è negativo, altrimenti rifiutato; in tutti e due i casi resta la ricevuta |
| registrare un pagamento | aumenta il fido residuo; può dire da quando vale |
| rettificare | un pagamento o un addebito registrato adesso e valido da prima, uno storno, uno spostamento da un cliente a un altro |
| riesaminare | dopo una rettifica: quali decisioni passate avrebbero avuto un altro esito, e perché |

Il fido residuo è: fido accordato, meno gli ordini accettati, più i pagamenti, per i fatti che valgono in quel
momento.

## Le garanzie che tutti devono dare

Uno schieramento che non ne dà una è bocciato su quella, qualunque sia la sua velocità.

1. **Durata.** Una scrittura è durevole prima di essere confermata. Più conferme insieme in una sola
   sincronizzazione sono ammesse, se la garanzia è la stessa. La prova con l'arresto del processo non dimostra la
   durata dopo una perdita di alimentazione: sono due prove, e si dice quale è stata fatta.
2. **Nessuna doppia spesa.** Due ordini che insieme superano il fido non passano tutti e due, comunque arrivino e
   in qualunque ordine siano serviti.
3. **Nessuna lettura stantia.** Una lettura cominciata dopo la conferma di una scrittura vede quella scrittura.
4. **Scrittura e ricevuta insieme.** Dopo un arresto brusco ci sono tutte e due oppure nessuna. Se chi ha scritto
   aveva già ricevuto la conferma, devono esserci tutte e due.
5. **Le ricevute non cambiano.** Una rettifica e un riesame non toccano le decisioni già prese.
6. **Ripetere non è decidere di nuovo.** La stessa richiesta con la stessa chiave riceve la risposta di allora.

## Gli schieramenti

Tre, ognuno completo. Si confrontano configurazioni intere: non si prende il numero migliore di un sistema diverso
per ogni operazione. Ogni schieramento ha lo stesso tetto di processore, memoria e disco, contando per i concorrenti
anche Redis e l'adattatore. Se la memoria non basta si rinvia la taglia per tutti, non la si riduce a uno solo.

### SinapsiDB

La memoria del ramo `contratto/decisioni`: fatti, circuito del fido, regola, ricevute, riesame. Il giornale è
durevole prima della conferma.

### PostgreSQL da solo

Scritta da un revisore. È un concorrente a pieno titolo: tiene il saldo nella propria transazione.

- **Versione e avvio.** PostgreSQL 17.12, un nodo, socket Unix, dati e registro delle scritture su disco vero.
- **Parametri.** `fsync=on`, `synchronous_commit=on`, `full_page_writes=on`, autovacuum attivo,
  `shared_buffers=256MB`, `work_mem=4MB`, `max_connections=32`; il resto ai valori di serie. I valori effettivi
  vanno nel manifesto della prova. Il PostgreSQL temporaneo dei test del repository ha `fsync=off`: **così com'è non
  va usato**.
- **Garanzia.** Il registro è durevole prima della conferma. Più conferme insieme in una sola sincronizzazione sono
  ammesse, se la garanzia è la stessa.
- **Transazione.** Una sola registra la richiesta, la ricevuta anche del no, i fatti e il saldo del cliente. Chiave
  di ripetizione unica; importi esatti; le righe dei clienti bloccate in ordine di identificativo per gli
  spostamenti; riesame con la stessa regola e le versioni di allora.
- **Tabelle e indici.** Intervalli di validità con fine esclusa, confini di validità del saldo, registro durevole
  e dipendenze; indici per cliente, istante ed evento, e per evento di creazione e di ritiro; domande preparate.
- **Ordine dei commit.** Per sapere che cosa era registrato a un certo punto serve un ordine dei commit visibili,
  per esempio un contatore preso sotto blocco nel punto in cui la transazione diventa seriale. Un numero di sequenza
  preso prima del commit non lo garantisce.
- **Conflitti.** Una transazione fallita per conflitto si riprova, contando tutto il tempo e tenendo l'istante di
  arrivo originale.

### PostgreSQL più Redis

Criterio di un secondo revisore. Stesso PostgreSQL e stessa transazione: è l'unico che autorizza e che conferma, anche i rifiuti.
Redis 8.0.2, su socket locale, è una cache dei saldi che si può sempre ricostruire.

- **Che cosa tiene la cache.** Per ogni cliente il valore, con la revisione del saldo, la versione della formula e
  l'intervallo in cui vale. La transazione di PostgreSQL aggiorna anche la revisione e una coda di aggiornamenti.
- **Chi la aggiorna.** Un solo consumatore pubblica dopo il commit, senza sovrascrivere una revisione più recente.
- **Quando la si usa.** Una lettura usa Redis solo se revisione e formula coincidono con quelle lette dal primario
  per quella richiesta e l'istante è dentro l'intervallo. Altrimenti legge da PostgreSQL.
- **Dopo un arresto.** Uno spazio dei nomi nuovo, ricostruito da PostgreSQL. La sola scadenza non basta.
- **Che cosa si conta.** Il controllo di freschezza, le letture mancate e la ricostruzione.
- **Persistenza di Redis.** `appendonly yes` con `appendfsync everysec` come configurazione di riferimento, e a
  parte con `appendfsync always`. Nessuna delle due rende atomiche due scritture su due motori diversi.
- **Da verificare prima dei tempi.** La perdita di Redis, un commit di PostgreSQL prima dell'aggiornamento della
  cache, una scadenza senza scritture, uno spostamento tra due clienti.

Un percorso più veloce è ammesso se dà garanzie equivalenti, è dichiarato ed è collaudato prima di misurare.

Oracle non è tra gli schieramenti: la licenza limita la pubblicazione delle misure.

## Il verificatore esterno

È un programma a parte, che non usa nessuno dei motori. È il giudice.

- **Aritmetica.** Milionesimi interi, senza arrotondamenti. Nel carico ordinario gli importi sono in centesimi; un
  caso di collaudo esercita anche la sesta cifra decimale.
- **Tracciato.** Un file di righe, una per operazione: seme, identificativo e chiave dell'operazione, richiedente,
  invocazione, istante di validità, importo, operazioni e rettifiche per identificativo, e la risposta: ritorno,
  conferma, oppure risposta persa.
- **Richieste che si sovrappongono.** L'ordine delle conferme non è l'ordine dei commit. Il verificatore cerca un
  ordine seriale compatibile con i tempi osservati; per i casi deterministici si usa una barriera esplicita.
- **Dopo un arresto.** Confermato: presente una volta. In corso al momento dell'arresto: assente, oppure presente
  una volta con la ricevuta identica alla ripetizione. Mai richiesto: assente.
- **Conferma persa.** Dopo una conferma persa è ammesso un commit intero non osservato: prima si legge lo stato
  durevole, poi si ripete con la stessa chiave.
- **Casi da includere.** Un commit riuscito seguito dalla perdita della conferma; l'arresto tra il commit di
  PostgreSQL e l'aggiornamento di Redis. L'arresto del processo e la perdita di alimentazione sono due prove
  diverse.

Le risposte attese per i casi del contratto delle decisioni fanno parte del tracciato di collaudo: uno schieramento
che non li passa non viene cronometrato.

## Il carico

- **Taglie.** Una di controllo: 1.000 clienti e 10.000 operazioni. Poi 10.000 clienti con 100.000 e con un milione
  di operazioni.
- **Miscele.** Letture e scritture 95 a 5 e 50 a 50. Tra le scritture: ordini, pagamenti e rettifiche 60, 30 e 10.
  Una versione uniforme e una contesa, con metà delle richieste sull'uno per cento dei clienti. Una miscela con le
  rettifiche concentrate su un cliente.
- **Insieme.** 1, 4 e 16 richiedenti. SinapsiDB ha un solo scrittore: lo si dichiara, e si misura che cosa costa.
- **Ripetizioni.** Stessi dati e stessi semi, pubblicati; almeno cinque repliche; riscaldamento a parte.
- **Arresti.** Il processo viene ucciso a caso durante il carico, più volte.
- **Riesame.** Per ogni rettifica, la ricerca delle candidate e il riesame. Due prove di selettività: dieci volte
  i dati degli altri clienti tenendo fisse le candidate; poi un cliente con moltissime candidate e nessun esito
  cambiato.

## Le misure

Si tengono separate, perché rispondono a domande diverse.

| Misura | Come |
|---|---|
| lettura del fido | mediana, 95° e 99° percentile; separate la lettura calda e la prima lettura dopo una scadenza |
| scrittura | operazioni al secondo, mediana e percentili, con la regola attiva e la conferma durevole |
| ricevuta e spazio | separati: byte della ricevuta per decisione, byte di registro, spazio totale con gli indici |
| candidate | separati: costruzione dell'indice a freddo, candidate trovate, riesaminate, incomplete, cambiate, proposte a vuoto |
| riesame | tempo per decisione |
| riapertura | tempo per ripartire dopo un arresto, e per ricostruire quello che è derivato |
| memoria e disco | a riposo e al picco |
| logica scritta a mano | quante tabelle, funzioni, trigger e passi di aggiornamento servono a ogni schieramento: è un'informazione, non il verdetto |

**Il trasporto.** I numeri che si confrontano passano tutti da un socket locale, con le stesse richieste,
connessioni tenute aperte e gli stessi richiedenti. Il tempo interno al motore è una diagnosi, non un confronto.
SinapsiDB oggi è una libreria: prima dei numeri comparativi serve un piccolo servizio su socket locale, avviato
solo per la prova.

Si pubblicano anche gli scarti e i risultati sfavorevoli.

## Come si boccia la tesi

1. **Prima la correttezza.** Una lettura stantia, una doppia spesa, una scrittura confermata che manca, una
   ricevuta cambiata: lo schieramento che le produce è bocciato, noi compresi.
2. **Poi il costo.** Se la migliore combinazione dei concorrenti dà le stesse risposte e resta entro una volta e
   mezza su letture, scritture e riesame, la tesi si riduce a una comodità: un motore al posto di tre, non un motore
   migliore. Prima di misurare si fissano i percentili, i rapporti (più bassa la latenza, più alte le operazioni al
   secondo), le miscele e come si tratta l'incertezza.
3. **Il riesame deve crescere con le candidate.** Se il suo tempo cresce con tutto l'archivio e quello dei
   concorrenti no, su quel punto abbiamo perso.

## Dove gira

Sulla macchina di sviluppo, che è condivisa: ogni prova con un tetto di memoria, guardando prima quanta ne è libera.
PostgreSQL e Redis sono istanze private e temporanee, su un socket in una cartella temporanea, avviate dai
programmi installati sulla macchina. Niente di quello che la prova avvia resta acceso dopo.

Le misure valgono per questa macchina e per un nodo solo. Un disco davvero freddo da qui non si può misurare.

## Che cosa si sa già che non va a nostro favore

Va scritto prima di misurare, non dopo.

- Il motore è in Python, con un solo scrittore.
- Ogni lettura del passato e ogni riesame rifanno la rete dal giornale: il tempo cresce con il giornale.
- Alla riapertura le somme si rifanno dai fatti.
- La ricevuta di una decisione pesa più di mille byte quando sono state consultate due regole.
- Il segno durevole dell'inizio delle decisioni è un file accanto al giornale: protegge da una traccia tolta, non
  da chi cancella anche il file.

## Che cosa va chiuso prima del cronometro

1. I casi aperti nella revisione del riesame.
2. Il segno durevole del formato e dell'inizio delle decisioni. È fatto; nel collaudo di riapertura si controlla
   anche quel file.
3. Un errore di lettura non porta a un commit.
4. I casi di correttezza e di ripresa passano con il verificatore esterno, su tutti e tre gli schieramenti.
5. La sincronizzazione durevole è verificata su tutti gli schieramenti.
6. Il servizio su socket locale di SinapsiDB.

Non servono, prima di misurare, un indice dei fatti ritirati per evento né una rete del passato che non si rifaccia
ogni volta da capo: si misura il costo di oggi, e una variante a parte dopo.

## Il manifesto

Prima del cronometro si fissano e si scrivono in un manifesto: i carichi e i semi, le risorse date a ogni
schieramento, i percentili, i rapporti e la soglia, e come si tratta l'incertezza. Ogni schieramento riporta
l'istante e l'ordine dei suoi commit. Con le misure si pubblicano il manifesto, i tracciati e i risultati grezzi.

Un cambiamento al protocollo fatto dopo richiede una serie di prove nuova.

## Chi fa che cosa

| Che cosa | Chi |
|---|---|
| tracciato e verificatore esterno | il primo revisore; li rivede il secondo |
| configurazione e schema di PostgreSQL | il primo revisore; li rivede il secondo |
| configurazione di PostgreSQL più Redis | il secondo revisore; la rivede il primo |
| servizio su socket e adattatore di SinapsiDB | chi ha scritto il motore |
| esecuzione dei tre schieramenti con lo stesso programma di prova; pubblicazione di manifesto, tracciati e risultati grezzi | chi ha scritto il motore |

I revisori non hanno scritto il motore. Le prove non usano un Redis o un PostgreSQL di sistema: ne avviano uno
privato e temporaneo, con la configurazione scritta in questa pagina.
