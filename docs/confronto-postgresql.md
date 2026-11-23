# SinapsiDB a confronto con PostgreSQL e con Redis

**Stato: in funzione dal 22 novembre 2026.** I dati di un'azienda inventata stanno in un database relazionale,
PostgreSQL; SinapsiDB li importa, e ai due si fanno le stesse domande per vedere chi risponde prima. Accanto c'è
Redis, come cache di risposte preparate prima. Tutto si fa dalla pagina «Confronto» del portale, con dei pulsanti.
Questo documento dice che cosa si confronta, come leggere i numeri, che cosa è uscito e come rifare le misure.

## In breve

- **Un'azienda di venti tabelle, della taglia che si sceglie.** Dal portale si generano in PostgreSQL da 100.000 a
  un milione di record; la pagina propone il milione. I dati sono a caso e cambiano a ogni generazione.
- **Stessi dati, stesse domande, risposte controllate.** SinapsiDB importa ogni record come un fatto. Le quindici
  domande vanno ai due con gli stessi valori e le risposte si confrontano una per una: finora sono sempre state
  tutte uguali.
- **Ognuno nella sua lingua.** A PostgreSQL le domande arrivano in SQL, a SinapsiDB nella lingua di SinapsiDB
  (vedi `docs/lingua.md`): nessuna è scritta a mano nel codice.
- **Dove si parte da un neurone, SinapsiDB è più rapido:** in 13 domande su 15, a ogni taglia provata. Con un
  milione di record il vantaggio va da 4 a 15 volte sul tempo che vede un programma.
- **La ricerca non rallenta con i dati.** Trovare una fattura dal numero costa 2,6 microsecondi con 300.000 record
  e 2,7 con un milione.
- **Dove non c'è un punto di partenza vinceva PostgreSQL,** di poco nella somma di tutte le fatture per mese e di 14
  volte nella ricerca delle fatture sopra un importo. Dal 22 novembre sera la seconda è rimediata: il motore tiene
  gli importi in ordine di grandezza, e con un milione di record risponde in 0,20 millisecondi contro 0,46. La
  prima resta di PostgreSQL finché la somma si calcola al momento; quando ognuno la tiene pronta a modo suo,
  SinapsiDB la legge in 18 microsecondi contro 38. Vedi [Le due domande che non
  viaggiavano](#le-due-domande-che-non-viaggiavano).
- **Il prezzo è la memoria.** SinapsiDB tiene tutta la rete in memoria: circa 430 MB per un milione di record, contro
  i 135 MB che PostgreSQL occupa su disco.
- **Fino a dieci milioni di record il disegno non cambia:** 13 domande su 15 e risposte identiche. Le ricerche
  mirate costano come prima; le due domande che passano tutti i fatti peggiorano, fino a 17 volte più lente di
  PostgreSQL; la memoria arriva a 7,8 GB di picco.
- **Redis è il terzo sistema: una cache di risposte preparate prima.** Non risponde a domande: restituisce quello
  che gli è stato messo sotto una chiave, e prepararlo costa. Con un milione di record SinapsiDB resta il più rapido dei tre in 12 domande su 15.
  Redis vince le due domande che passano tutte le fatture, perché la risposta ce l'ha già pronta, e di poco una
  terza. Vedi [E Redis?](#e-redis).
- **I numeri di questo documento sono della macchina di sviluppo,** tranne quelli della prova a dieci milioni. Quelli
  che contano sono quelli che mostra il portale dove SinapsiDB è installato, e cambiano un poco da un lancio all'altro.

## L'azienda di prova

Vende all'ingrosso strumenti musicali e ricambi. I dati sono inventati.

| Che cosa | Tabelle |
|---|---|
| Che cosa vende | `categorie`, `prodotti`, `fornitori` |
| Dove lo tiene | `magazzini`, `giacenze`, `movimenti` (ogni carico e ogni scarico) |
| A chi vende | `clienti`, `agenti` (ogni cliente ha il suo agente) |
| Come vende | `ordini`, `righe_ordine`, `spedizioni`, `corrieri`, `fatture`, `righe_fattura`, `pagamenti`, `resi` |
| Come compra | `ordini_acquisto`, `righe_acquisto` |
| Chi ci lavora e l'assistenza | `dipendenti`, `ticket` |

Si sceglie quanti record si vogliono in tutto e il generatore dimensiona l'azienda di conseguenza. Con 300.000
record: 20.000 ordini di 2.500 clienti su 1.250 prodotti, 70.000 righe d'ordine, 18.000 spedizioni e altrettante
fatture, 64.000 righe di fattura, 18.500 pagamenti, 72.000 movimenti di magazzino, 1.100 ticket.

I dati sono a caso ma i conti tornano: una fattura è la somma delle sue righe, si fattura quello che è stato
ordinato, un pagamento non supera la fattura, un reso riguarda un prodotto davvero fatturato, niente accade nel
futuro. Pochi clienti comprano molto e molti comprano poco; qualche ordine è annullato, qualche fattura non è ancora
pagata. A ogni generazione il seme cambia e con lui i dati; il seme usato si vede nella pagina.

In PostgreSQL le tabelle hanno chiavi primarie, colonne uniche, riferimenti tra tabelle e un indice su ogni colonna
che le domande usano.

## Come un record diventa un fatto

L'importazione legge le tabelle e racconta ogni record alla rete, con la sua frase. Una fattura diventa:

> Fattura F2025/0000018 a C00202 per l'ordine O2025/0000021 del 2025-01-04, totale 10947.47

cioè un fatto di tipo «fattura emessa» che riceve cinque sinapsi: il numero nel ruolo `fattura`, il cliente nel
ruolo `cliente`, l'ordine nel ruolo `ordine`, la data nel ruolo `quando`, l'importo nel ruolo `totale`. Il numero,
il cliente, l'ordine, la data e l'importo sono neuroni. Lo stesso neurone del cliente riceve le sue fatture, i suoi
ordini, i suoi ticket: per questo dalla rete si passa da una cosa all'altra senza giunzioni.

Le chiavi numeriche delle tabelle non entrano nella rete: non servono. Un cliente è il suo codice, una fattura è il
suo numero. Ogni tabella diventa fatti, nessuna esclusa: i fatti sono tanti quanti i record.

## Le sedici domande

| Domanda | In SQL | Nella rete |
|---|---|---|
| Una fattura dal numero | ricerca per chiave e una giunzione | dal neurone del numero al suo fatto |
| Una spedizione dal numero | ricerca per chiave e due giunzioni | dal neurone del numero al suo fatto |
| Le fatture di un cliente | una giunzione | dal cliente ai fatti che lo ricevono |
| Le righe di una fattura | due giunzioni | dalla fattura alle sue righe |
| I pagamenti di una fattura | una giunzione | dalla fattura ai suoi pagamenti |
| I prodotti di un fornitore | una giunzione | dal fornitore alle schede dei prodotti |
| Giacenza di un prodotto in un magazzino | due condizioni insieme | due neuroni accesi, soglia due |
| I ticket aperti di un cliente | due condizioni insieme | due neuroni accesi, soglia due |
| Fatturato di un cliente | `SUM` sulle sue fatture | dal cliente ai fatti, somma dei totali |
| I pagamenti di un ordine | due giunzioni | due passi: ordine, fatture, pagamenti |
| Chi ha comprato un prodotto | tre giunzioni e `DISTINCT` | tre passi: prodotto, righe, fatture, clienti |
| I fornitori dietro agli acquisti di un cliente | quattro giunzioni e `DISTINCT` | quattro passi, fino ai fornitori |
| Un cliente in un giorno | tre tabelle unite con `UNION ALL` | il cliente e il giorno accesi insieme |
| Fatturato mese per mese | `GROUP BY` su tutte le fatture | nessun neurone da cui partire: tutte le fatture |
| Fatturato mese per mese, tenuto pronto | una vista materializzata | un circuito: la somma di ogni mese, aggiornata a ogni scrittura |
| Fatture sopra un importo | `WHERE totale > …` con l'indice | nessun neurone da cui partire: i valori sono in ordine di grandezza |

Le domande erano quindici fino al 22 novembre sera: le tabelle dei risultati più sotto sono di allora, e la riga
«tenuto pronto» non c'è.

**La riga «tenuto pronto» non fa punteggio.** La pagina la mostra, con i suoi tempi, ma chi è «più rapido» si conta
sulle altre quindici, quelle in cui la risposta si calcola al momento. Dove ognuno legge una risposta preparata prima
nessuno calcola niente: contarla come una domanda vinta sarebbe una scorciatoia.

«Un cliente in un giorno» chiede ordini, fatture e ticket di un cliente in una data. Nella rete un cliente e un
giorno sono collegati direttamente ai fatti che li ricevono tutti e due, di qualsiasi tipo. In SQL sono tre tabelle.

Le ultime due sono nel confronto di proposito. Una rete trova in fretta quello che è collegato a un neurone nominato.
«Tutte le fatture sopra mille euro» non nomina nessun neurone: oggi la rete deve passarle tutte.

## Come si misura

1. **Prima la correttezza.** Per ogni domanda si prendono a caso dei valori dai dati (300 per le domande mirate, 15
   per le due che passano tutto), si fa la domanda ai due e si confrontano le risposte, riga per riga. Un tempo vale
   solo se le risposte sono uguali.
2. **Poi i tempi.** Dopo un giro di riscaldamento, la stessa serie di domande va prima a PostgreSQL e poi a SinapsiDB.
   Di ogni serie si riportano il valore mediano e il 99° percentile.
3. **Di PostgreSQL due tempi.** SinapsiDB risponde dentro lo stesso programma che fa la domanda. PostgreSQL è un
   servizio a parte: la domanda gli arriva dal socket locale e la risposta torna indietro. Il «tempo totale» è quello
   che vede il programma. Il tempo «solo nel server» toglie il viaggio e lascia la sola esecuzione: lo misura
   PostgreSQL stesso, su un campione più piccolo.

4. **Redis, se c'è.** Le stesse domande, con gli stessi valori, vanno anche a Redis. Ogni sua risposta si confronta
   riga per riga con quella di PostgreSQL, e anche di lui si riportano due tempi: quello che vede il programma e
   quello della sola esecuzione nel suo server, che Redis stesso dichiara.

A PostgreSQL non si toglie niente: chiavi, riferimenti e indici ci sono, le domande sono preparate una volta sola, i
dati stanno tutti nella sua memoria. Nemmeno a Redis: il programma gli parla dal socket locale come a PostgreSQL, con
il lettore delle risposte scritto in C.

## Risultati

Macchina di sviluppo, 22 novembre 2026, PostgreSQL 17, tempi mediani. Sotto i mille microsecondi i tempi sono in
microsecondi (µs, milionesimi di secondo), sopra in millisecondi (ms).

### Un milione di record

| Domanda | SinapsiDB | PostgreSQL, totale | PostgreSQL, solo nel server | Più rapido |
|---|---:|---:|---:|---|
| Una fattura dal numero | 2,7 µs | 37,5 µs | 14 µs | SinapsiDB, 14 volte |
| Una spedizione dal numero | 3,1 µs | 41,4 µs | 20 µs | SinapsiDB, 13 volte |
| Le fatture di un cliente | 5,2 µs | 44,0 µs | 19 µs | SinapsiDB, 8,5 volte |
| Le righe di una fattura | 4,1 µs | 47,6 µs | 24 µs | SinapsiDB, 12 volte |
| I pagamenti di una fattura | 2,5 µs | 38,0 µs | 13 µs | SinapsiDB, 15 volte |
| I prodotti di un fornitore | 9,8 µs | 71,4 µs | 39 µs | SinapsiDB, 7,3 volte |
| Giacenza di un prodotto in un magazzino | 5,9 µs | 38,8 µs | 20 µs | SinapsiDB, 6,6 volte |
| I ticket aperti di un cliente | 4,3 µs | 35,2 µs | 14 µs | SinapsiDB, 8,3 volte |
| Fatturato di un cliente | 5,9 µs | 44,6 µs | 26 µs | SinapsiDB, 7,6 volte |
| I pagamenti di un ordine | 3,7 µs | 41,9 µs | 19 µs | SinapsiDB, 11 volte |
| Chi ha comprato un prodotto | 47,9 µs | 201,8 µs | 204 µs | SinapsiDB, 4,2 volte |
| I fornitori dietro agli acquisti di un cliente | 21,0 µs | 111,4 µs | 97 µs | SinapsiDB, 5,3 volte |
| Un cliente in un giorno | 7,0 µs | 55,7 µs | 37 µs | SinapsiDB, 7,9 volte |
| Fatturato mese per mese | 37,9 ms | 31,8 ms | 31,6 ms | PostgreSQL, 1,2 volte |
| Fatture sopra un importo | 6,8 ms | 0,48 ms | 0,28 ms | PostgreSQL, 14 volte |

Le 3.796 risposte confrontate sono tutte uguali.

### Come cambia con la taglia

| | 300.000 record | 500.000 record | 1.000.000 di record |
|---|---:|---:|---:|
| Domande in cui SinapsiDB è più rapido | 13 su 15 | 13 su 15 | 13 su 15 |
| Risposte uguali | 3.680 su 3.680 | 3.713 su 3.713 | 3.796 su 3.796 |
| Una fattura dal numero, SinapsiDB | 2,6 µs | 2,8 µs | 2,7 µs |
| Una fattura dal numero, PostgreSQL | 37,1 µs | 36,6 µs | 37,5 µs |
| I fornitori dietro agli acquisti di un cliente, SinapsiDB | 17,1 µs | 17,4 µs | 21,0 µs |
| I fornitori dietro agli acquisti di un cliente, PostgreSQL | 102,3 µs | 103,0 µs | 111,4 µs |
| Fatturato mese per mese, SinapsiDB | 10,3 ms | 17,1 ms | 37,9 ms |
| Fatturato mese per mese, PostgreSQL | 9,6 ms | 15,5 ms | 31,8 ms |
| Fatture sopra un importo, SinapsiDB | 1,95 ms | 3,19 ms | 6,84 ms |
| Fatture sopra un importo, PostgreSQL | 0,19 ms | 0,28 ms | 0,48 ms |
| Generazione in PostgreSQL | 1,9 s | 2,8 s | 5,1 s |
| Importazione in SinapsiDB | 7,5 s | 12,5 s | 26,2 s |
| Memoria di SinapsiDB con la rete caricata | 143 MB | 224 MB | 433 MB |
| Memoria di picco durante la prova | 260 MB | 419 MB | 793 MB |
| Spazio su disco, PostgreSQL | 48 MB | 73 MB | 135 MB |
| Spazio su disco, SinapsiDB | 120 MB | 200 MB | 401 MB |
| Riapertura della rete dal disco | 1,3 s | 2,2 s | 4,6 s |

Prima che le domande passassero dalla lingua erano scritte a mano nel codice, una per una. La lingua le esegue
meglio: legge ogni ruolo dal posto che ha nel fatto, e quando c'è una condizione su un valore guarda i valori una
volta ciascuno. Le fatture sopra un importo sono passate da 42 a 7 millisecondi, la somma per mese da 78 a 38.

### Dieci milioni di record

Il 22 novembre 2026, sulla macchina dove SinapsiDB è installato, con la memoria aumentata apposta: 10.021.711 record
nelle venti tabelle, generati dal portale e importati in 299 secondi. Le quindici domande hanno dato risposte
identiche, e SinapsiDB è stato più rapido in 13 su 15, come alle taglie più piccole. Tempi mediani:

| Domanda | SinapsiDB | PostgreSQL | PostgreSQL, solo nel server |
|---|---:|---:|---:|
| Una fattura dal numero | 3,9 µs | 41,5 µs | 16 µs |
| Una spedizione dal numero | 3,8 µs | 45,8 µs | 22 µs |
| Le fatture di un cliente | 8,5 µs | 55,1 µs | 25 µs |
| Le righe di una fattura | 6,3 µs | 79,0 µs | 46 µs |
| I pagamenti di una fattura | 4,2 µs | 43,2 µs | 17 µs |
| I prodotti di un fornitore | 27,6 µs | 76,0 µs | 38 µs |
| Giacenza di un prodotto in un magazzino | 7,7 µs | 46,0 µs | 24 µs |
| I ticket aperti di un cliente | 5,7 µs | 42,3 µs | 16 µs |
| Fatturato di un cliente | 8,6 µs | 55,7 µs | 34 µs |
| I pagamenti di un ordine | 5,5 µs | 48,5 µs | 25 µs |
| Chi ha comprato un prodotto | 74,8 µs | 758,9 µs | 689 µs |
| I fornitori dietro agli acquisti di un cliente | 33,6 µs | 170,6 µs | 138 µs |
| Un cliente in un giorno | 9,9 µs | 66,0 µs | 45 µs |
| Fatturato mese per mese | 546 ms | 117 ms | 122 ms |
| Fatture sopra un importo | 98,4 ms | 5,8 ms | 3,5 ms |

Che cosa si vede passando da uno a dieci milioni:

- **Le ricerche mirate restano dove erano.** Una fattura dal numero costa 3,9 µs, contro i 3,4 misurati sulla stessa
  macchina con un milione di record. Il vantaggio sul tempo che vede un programma resta tra 3 e 12 volte.
- **Dove non c'è un punto di partenza PostgreSQL si allontana.** Nel fatturato mese per mese è 4,7 volte più rapido
  (con un milione era 1,4 volte); nelle fatture sopra un importo 17 volte. SinapsiDB lì passa tutti i fatti, e il
  suo tempo cresce più dei dati.
- **La memoria è il limite.** Il portale ha occupato 7,8 GB al picco e 4,7 GB a riposo; su disco la rete di prova
  occupa 1,3 GB e i dati di PostgreSQL 1,2 GB. Oltre questa taglia la rete tutta in memoria non regge: serve la
  rete sul disco attiva solo in parte, descritta più sotto in [La rete sul
  disco](#la-rete-sul-disco-memoria-veloce-e-memoria-profonda).

### Quasi cinque milioni

Una prova precedente, con la prima versione dei dati (sei tabelle, 4,8 milioni di righe), ha dato lo stesso
disegno: una fattura dal numero in 3,9 µs contro 43,6 µs, tutte le 2.130 risposte uguali, le due domande senza
punto di partenza a favore di PostgreSQL (fino a 61 volte). La rete occupava 2 GB di memoria e si riapriva in 23
secondi.

## E Redis?

Redis è un archivio di chiavi e valori tenuto in memoria, molto veloce. Non è un database a cui si fanno domande:
restituisce quello che gli è stato messo sotto una chiave. Per metterlo nel confronto gli si prepara, leggendo
PostgreSQL, la risposta di ognuna delle quindici domande per ogni valore possibile: una lista per ogni fattura, per
ogni cliente, per ogni coppia cliente e giorno, e così via. Per le fatture sopra un importo si usa un insieme ordinato
per totale, che è la struttura di Redis fatta per quello. È il modo in cui Redis si usa accanto a un database: come
una cache, cioè una copia delle risposte, da tenere allineata.

Poi le stesse domande vanno anche a lui, con gli stessi valori, e ogni risposta si confronta con quella di PostgreSQL.
Dalla pagina sono due pulsanti: «Prepara le risposte in Redis» e «Lancia il confronto».

### Un milione di record, i tre insieme

Macchina di sviluppo, 22 novembre 2026, PostgreSQL 17 e Redis 8.0, tempi mediani, misurati attraverso il portale come
li mostra la pagina. Per questo i tempi di SinapsiDB sono un poco più alti di quelli della tabella più sopra, presi
senza il portale di mezzo: sugli stessi dati il motore è veloce uguale.

| Domanda | SinapsiDB | PostgreSQL, totale | Redis, totale | Redis, solo nel server | Più rapido |
|---|---:|---:|---:|---:|---|
| Una fattura dal numero | 4,8 µs | 41,2 µs | 38,9 µs | 0,5 µs | SinapsiDB, 8,1 volte |
| Una spedizione dal numero | 4,8 µs | 45,7 µs | 38,4 µs | 0,4 µs | SinapsiDB, 8,0 volte |
| Le fatture di un cliente | 10,0 µs | 50,6 µs | 40,3 µs | 0,6 µs | SinapsiDB, 4,0 volte |
| Le righe di una fattura | 6,8 µs | 52,0 µs | 38,3 µs | 0,5 µs | SinapsiDB, 5,6 volte |
| I pagamenti di una fattura | 4,8 µs | 41,9 µs | 37,7 µs | 0,4 µs | SinapsiDB, 7,9 volte |
| I prodotti di un fornitore | 19,2 µs | 85,4 µs | 42,7 µs | 1,2 µs | SinapsiDB, 2,2 volte |
| Giacenza di un prodotto in un magazzino | 7,7 µs | 42,2 µs | 37,3 µs | 0,4 µs | SinapsiDB, 4,8 volte |
| I ticket aperti di un cliente | 5,8 µs | 37,9 µs | 36,5 µs | 0,3 µs | SinapsiDB, 6,3 volte |
| Fatturato di un cliente | 10,9 µs | 51,8 µs | 43,4 µs | 0,4 µs | SinapsiDB, 4,0 volte |
| I pagamenti di un ordine | 4,3 µs | 55,3 µs | 36,5 µs | 0,4 µs | SinapsiDB, 8,5 volte |
| Chi ha comprato un prodotto | 54,6 µs | 219,9 µs | 47,7 µs | 2,0 µs | Redis, 1,1 volte |
| I fornitori dietro agli acquisti di un cliente | 24,0 µs | 122,4 µs | 39,9 µs | 1,0 µs | SinapsiDB, 1,7 volte |
| Un cliente in un giorno | 8,2 µs | 58,1 µs | 37,0 µs | 0,3 µs | SinapsiDB, 4,5 volte |
| Fatturato mese per mese | 47,4 ms | 33,1 ms | 43,6 µs | 1,0 µs | Redis, 760 volte |
| Fatture sopra un importo | 8,1 ms | 0,51 ms | 0,14 ms | 25,9 µs | Redis, 3,6 volte |

«Più rapido» è il più rapido dei tre, e di quante volte stacca il secondo. Le 3.615 risposte di SinapsiDB e le
altrettante di Redis sono tutte uguali a quelle di PostgreSQL. Le risposte pronte sono 1.134.660 righe sotto 462.118
chiavi, occupano 88 MB nella memoria di Redis e si preparano in 13 secondi. Con centomila record sono 9 MB: circa 92
byte per record, quindi circa 900 MB per dieci milioni.

### Come leggere Redis in questa tabella

**Dentro il suo server Redis è il più rapido di tutti.** Trova una risposta pronta in meno di un microsecondo: meno
di SinapsiDB, il cui motore oggi è scritto in Python e che la risposta la costruisce al momento. Quello che il
programma vede, però, sono quasi 40 microsecondi, perché Redis è un servizio a parte: la domanda gli va portata e la
risposta va riportata indietro. SinapsiDB risponde dentro il programma, e per questo nelle domande mirate arriva
prima.

**Dove Redis vince, vince perché non calcola.** «Fatturato mese per mese» per gli altri due vuol dire passare tutte le
fatture. Redis restituisce una lista di trentasei righe scritta prima. È la stessa idea delle «somme tenute
aggiornate» che a SinapsiDB mancano ancora: quando ci saranno, questa domanda costerà anche a lui pochi microsecondi.

**Che cosa Redis non fa, e il confronto non misura.**

- *Risponde solo a queste domande.* Ogni risposta ha la sua struttura, decisa prima. Una domanda in più vuol dire
  scrivere un'altra preparazione; a PostgreSQL e a SinapsiDB basta farla.
- *Le risposte sono una copia.* A ogni scrittura nei dati vanno aggiornate tutte le risposte che ne dipendono, e
  farlo senza errori è lavoro di chi scrive il programma. Qui si rifanno da capo, e quel costo non entra nei tempi.
- *Occupano memoria in più,* oltre ai dati che restano in PostgreSQL.
- *Non dice perché.* Una risposta pronta non ha un piano, una storia, né i fatti da cui viene.

In una frase: Redis accanto a PostgreSQL è una memoria veloce per le risposte già note. SinapsiDB prova a essere
veloce così, o quasi, tenendo però i dati, la storia e le domande nuove nello stesso posto.

## Le due domande che non viaggiavano

Con dieci milioni di record le due domande senza punto di partenza erano lontane da tutti: il fatturato mese per mese
in 453 millisecondi contro 112 di PostgreSQL e 45 microsecondi di Redis, le fatture sopra un importo in 70
millisecondi contro 5,4 e 1,1. Il perché è che SinapsiDB passava tutte le fatture, una per una, in Python. I rimedi sono due, e sono diversi.

**Fatture sopra un importo: i valori in ordine di grandezza.** Il motore tiene in ordine i valori di un ruolo, dalla
prima volta che una domanda «sopra», «sotto», «almeno» o «al massimo» glielo chiede, e li aggiorna a ogni scrittura.
La domanda parte dal primo valore che passa la condizione, come fa l'indice di PostgreSQL. È la stessa domanda di
prima, fatta meglio.

**Fatturato mese per mese: una somma tenuta pronta.** Qui non c'è un modo più furbo di calcolare: per sommare tutte
le fatture bisogna passarle. Si può non calcolare: tenere la somma di ogni mese pronta e aggiornarla nella stessa
scrittura che registra la fattura. In SinapsiDB è un circuito. È quello che fa Redis con la sua copia e PostgreSQL
con una vista materializzata. Per questo la domanda nel confronto c'è due volte:

- *calcolata al momento*: PostgreSQL e SinapsiDB passano le fatture. Vince PostgreSQL, come prima;
- *tenuta pronta*: ognuno legge la sua risposta già fatta. A PostgreSQL è stata data la vista materializzata:
  confrontare un circuito con un `GROUP BY` non sarebbe un confronto.

Un milione di record, macchina di sviluppo, 22 novembre 2026, attraverso il portale, tempi mediani:

| Domanda | SinapsiDB | PostgreSQL, totale | PostgreSQL, solo nel server | Redis, totale | Più rapido |
|---|---:|---:|---:|---:|---|
| Fatturato mese per mese, calcolato al momento | 41,5 ms | 31,7 ms | 31,2 ms | 40,5 µs | Redis, che non calcola; tra i due che calcolano PostgreSQL, 1,3 volte |
| Fatturato mese per mese, tenuto pronto | 17,6 µs | 37,8 µs | 9,0 µs | 40,0 µs | SinapsiDB, 2,1 volte |
| Fatture sopra un importo | 0,20 ms | 0,46 ms | 0,26 ms | 0,13 ms | Redis, 1,6 volte; SinapsiDB secondo |

Con queste due, su sedici domande SinapsiDB è più rapido di PostgreSQL in quindici e di Redis in tredici. Le
risposte restano tutte uguali.

Come leggerle:

- **La somma pronta di SinapsiDB non può restare indietro.** La aggiorna la stessa scrittura che registra la
  fattura: o entrano tutte e due o nessuna. La vista di PostgreSQL va rinfrescata quando i dati cambiano, e la
  copia in Redis la deve aggiornare il programma. Il confronto misura solo la lettura: questa differenza non si
  vede nei tempi, ed è la più importante.
- **Tenere pronto ha un prezzo.** Ogni fattura raccontata fa un piccolo lavoro in più. Nell'importazione di un
  milione di record si vede appena.
- **Dentro il server PostgreSQL legge la sua vista in 9 microsecondi,** meno dei 18 di SinapsiDB. SinapsiDB arriva
  prima perché risponde dentro il programma, come nelle altre domande.
- **Sopra un importo Redis resta davanti:** restituisce un pezzo di un insieme già ordinato, in codice compilato.
  SinapsiDB dall'ordine prende i valori, e poi legge le fatture una per una.
- **Vale per la rete tutta in memoria.** La rete letta dal disco non ha né l'ordine né i circuiti: la domanda
  «tenuto pronto» lì resta fuori dal confronto, dichiarata, e «sopra un importo» guarda tutti i valori.
- **Con dieci milioni di record** ci si aspetta lo stesso disegno, da verificare sulla macchina dove SinapsiDB è
  installato.

## Il nucleo compilato

Il fatturato mese per mese **calcolato al momento** restava la domanda di PostgreSQL: per sommare tutte le fatture
bisogna passarle, e SinapsiDB le passava in Python, PostgreSQL in codice compilato. Dal 23 novembre 2026 SinapsiDB
ha un piccolo nucleo in C che fa proprio quel percorso: contare o sommare, gruppo per gruppo, tutti i fatti di un
tipo. Legge le colonne della rete così come sono, senza copiarle.

Un milione di record, la rete tutta in memoria, attraverso il portale:

| Domanda | SinapsiDB, in Python | SinapsiDB, con il nucleo | PostgreSQL, totale | PostgreSQL, solo nel server |
|---|---:|---:|---:|---:|
| Fatturato mese per mese | 41,5 ms | 1,0 ms | 32,7 ms | 32,0 ms |

Con il nucleo SinapsiDB è più rapido di PostgreSQL in tutte e quindici le domande che si calcolano al momento.

Come leggere questa riga:

- **È lo stesso lavoro.** Tutti e due passano tutte le fatture e sommano, al momento della domanda, in codice
  compilato. Non è una risposta tenuta pronta: quella è l'altra riga, che non fa punteggio.
- **Il nucleo è facoltativo, e il Python resta il riferimento.** Il nucleo deve dare le stesse righe nello stesso
  ordine. Dove non sa farlo senza perdere niente (un numero con più di sei decimali, una somma troppo grande, un tipo
  di fatto in cui i ruoli non stanno sempre allo stesso posto) si fa da parte e la domanda la fa il Python. Su una
  macchina senza compilatore il nucleo non c'è, e i tempi sono quelli della colonna «in Python».
- **La pagina lo dice.** Scrive se durante la misura il nucleo era attivo, e segna la riga che ha calcolato lui.
- **La prima volta costa di più.** Il nucleo legge una volta i numeri scritti nei nomi e se li tiene: con un milione
  di record è un decimo di secondo, poi non più.
- **Fa una cosa sola.** Le domande con un punto di partenza o con una condizione le fa ancora il Python, che lì è
  già rapido. Scrivere un fatto è ancora tutto in Python: è lì che l'importazione spende il suo tempo.
- **Dieci milioni di record:** ancora da misurare con il nucleo.

## La rete sul disco: memoria veloce e memoria profonda

Fin qui la rete di SinapsiDB stava tutta in memoria: è il suo limite più concreto, 430 MB per milione di record.
Dalla pagina ora si può portare sul disco e tenere in memoria solo i pezzi che le domande toccano, dentro un tetto
che si sceglie. È la stessa rete e dà le stesse risposte: cambia quanta memoria occupa e quanto costa una domanda.
Il codice è in `src/sinapsidb/blocchi.py`.

Un milione di record, macchina di sviluppo, misurato attraverso il portale. «La prima volta» è il tempo di una
domanda dopo aver buttato via apposta i pezzi di rete che erano in memoria.

| Domanda | Tutta in memoria | Sul disco, già in memoria | Sul disco, la prima volta | PostgreSQL, totale | Redis, totale |
|---|---:|---:|---:|---:|---:|
| Una fattura dal numero | 3,6 µs | 26,6 µs | 60,0 µs | 39,5 µs | 39,1 µs |
| Una spedizione dal numero | 3,3 µs | 30,4 µs | 65,3 µs | 47,6 µs | 37,3 µs |
| Le fatture di un cliente | 5,2 µs | 44,8 µs | 72,9 µs | 44,6 µs | 36,4 µs |
| Le righe di una fattura | 3,9 µs | 43,0 µs | 80,4 µs | 51,1 µs | 37,3 µs |
| I pagamenti di una fattura | 2,8 µs | 25,5 µs | 57,5 µs | 38,4 µs | 36,6 µs |
| I prodotti di un fornitore | 9,7 µs | 121,7 µs | 147,7 µs | 73,4 µs | 38,2 µs |
| Giacenza di un prodotto in un magazzino | 6,5 µs | 55,6 µs | 75,4 µs | 39,5 µs | 36,7 µs |
| I ticket aperti di un cliente | 5,0 µs | 35,2 µs | 53,1 µs | 36,3 µs | 36,6 µs |
| Fatturato di un cliente | 6,7 µs | 35,5 µs | 76,1 µs | 45,5 µs | 38,9 µs |
| I pagamenti di un ordine | 4,5 µs | 35,8 µs | 78,7 µs | 42,7 µs | 36,4 µs |
| Chi ha comprato un prodotto | 54,0 µs | 413,5 µs | 507,4 µs | 214,5 µs | 41,2 µs |
| I fornitori dietro agli acquisti di un cliente | 22,6 µs | 236,4 µs | 305,9 µs | 116,4 µs | 38,4 µs |
| Un cliente in un giorno | 7,2 µs | 151,9 µs | 189,9 µs | 57,3 µs | 36,9 µs |
| Fatturato mese per mese | 44,3 ms | 153,3 ms | 158,4 ms | 29,3 ms | 40,9 µs |
| Fatture sopra un importo | 7,5 ms | 30,8 ms | 31,4 ms | 0,52 ms | 0,18 ms |

- **La memoria.** Il portale passa da 524 MB a 208 MB senza riavviarlo; appena riavviato ne occupa 58, più i pezzi di
  rete in uso. Sul disco i blocchi occupano 174 MB. Durante il confronto in memoria ci sono stati al massimo pochi
  megabyte di rete.
- **Il prezzo.** Una domanda mirata costa da 25 a 55 microsecondi invece di 3-7: più o meno come a PostgreSQL e a
  Redis. Con la rete sul disco SinapsiDB è più rapido di PostgreSQL in 7 domande su 15 invece di 13, e di Redis in 6
  invece di 12. Le domande che leggono molti fatti peggiorano di più: quello che costa è leggere ogni fatto da un
  blocco di byte, e il motore è in Python.
- **Le risposte** restano tutte uguali a quelle di PostgreSQL.
- **La prima volta** qui costa solo il doppio, perché il sistema operativo tiene in memoria i file letti di
  recente. Su un disco davvero a riposo costa di più, e da dove gira la prova non si può misurare.
- **Che cosa non c'è ancora.** L'importazione passa dalla rete tutta in memoria: sul disco ci si va dopo. Per i
  cento milioni di record serve importare direttamente sul disco.

## Che cosa dicono questi numeri, e che cosa no

**Perché SinapsiDB vince dove vince.** Nella rete la risposta è già collegata alla domanda: dal neurone nominato si
arriva ai fatti con un passo, senza analizzare una frase SQL, senza scegliere un piano, senza scendere un indice ad
albero e senza giunzioni. In più non c'è il viaggio verso un altro processo. Il secondo vantaggio vale per qualsiasi
database tenuto dentro il programma; il primo è della struttura, e si vede nella colonna «solo nel server», dove
SinapsiDB resta da 3 a 6 volte più rapido.

**Perché non rallenta con i dati.** Arrivare da un neurone ai suoi fatti costa lo stesso che i fatti in tutto siano
centomila o cinque milioni. Cresce solo quando cresce la risposta.

**Perché perde dove perde.** Le due domande senza punto di partenza chiedono di passare tutte le fatture. PostgreSQL lo
fa in codice compilato, e per gli importi ha un indice ordinato che va dritto alle righe giuste. La
rete oggi non ha valori ordinati, e il motore è scritto in Python. Nella somma per mese i due sono quasi alla pari;
negli importi sopra una soglia il distacco resta, e cresce con i dati.

**Che cosa il confronto non misura.**

- *Le scritture.* PostgreSQL garantisce ogni scrittura sul disco. La rete di prova viene importata senza aspettare il
  disco a ogni fatto, perché si può sempre rifare. PostgreSQL carica un milione di record in 5 secondi, SinapsiDB li
  importa in 25.
- *Più utenti insieme.* PostgreSQL serve molti programmi nello stesso momento. SinapsiDB oggi ha un solo scrittore, e
  chi legge sta nello stesso programma.
- *Le domande non previste.* In SQL si può chiedere quasi qualsiasi cosa. La lingua di SinapsiDB è giovane: non ha
  «oppure», non ordina, non si ferma ai primi, non cerca dentro le frasi. E la strada tra due cose la sceglie chi
  chiede: il motore propone quelle possibili.
- *La memoria.* SinapsiDB tiene tutta la rete in memoria, storia compresa: è il suo limite più concreto. Con la rete
  sul disco il limite si sposta, e il prezzo è nei tempi: vedi la sezione qui sopra.

## Come rifare le misure

Le tabelle di questo documento vengono dalla versione 0.10.7, su una sola macchina Linux, con Python 3.13,
PostgreSQL 17 e Redis 8.0. Per rifarle serve lo stesso: Python 3.11 o più recente, un PostgreSQL e, se si vuole il
terzo sistema, un Redis, tutti sulla stessa macchina. I tempi cambiano con la macchina e da un lancio all'altro.
Quello che deve restare uguale sono le risposte, che il confronto controlla una per una, e il disegno: dove vince chi.

1. **Installare SinapsiDB** con le versioni esatte delle misure e, se c'è un compilatore C, il nucleo:

   ```bash
   python3 -m venv .venv
   .venv/bin/pip install -r installa/requirements.lock
   .venv/bin/pip install --no-deps .
   .venv/bin/python -m sinapsidb.nucleo compila    # facoltativo: senza, i tempi sono quelli «in Python»
   ```

   In alternativa, senza versioni fisse: `.venv/bin/pip install '.[confronto]'`.

2. **Preparare PostgreSQL.** Il portale si collega dal socket locale, senza password, come l'utente che lo avvia, a
   una base di dati `sinapsidb_prova` che quell'utente possiede. È l'unica in cui scrive:

   ```bash
   sudo -u postgres createuser "$USER"
   sudo -u postgres createdb -O "$USER" sinapsidb_prova
   ```

   Un altro modo di collegarsi si scrive in `SINAPSIDB_PROVA_PG`, nella forma che accetta psycopg.

3. **Avviare il portale** in primo piano, solo su questa macchina:

   ```bash
   SINAPSIDB_METRICHE_DB="$HOME/sinapsidb-dati/metriche.db" \
   SINAPSIDB_PROVA_REDIS="unix:///run/redis/redis-server.sock" \
   .venv/bin/sinapsidb-portale
   ```

   Redis va raggiunto dal suo socket locale, come PostgreSQL: le misure di questo documento sono fatte così. Il
   percorso del socket cambia da una macchina all'altra; dalla porta si scrive `redis://127.0.0.1:6379/0`, e la
   pagina lo dichiara nelle note dei risultati. Senza `SINAPSIDB_PROVA_REDIS` il confronto è tra SinapsiDB e
   PostgreSQL soltanto. In Redis il portale tocca solo
   le chiavi che cominciano con `sinapsidb:prova:`; meglio comunque un Redis, o un numero di base di dati, che non
   serve ad altro.

4. **Misurare.** Dalla pagina `http://127.0.0.1:8419/confronto`, nell'ordine: «Genera i dati in PostgreSQL» con la
   taglia scelta, «Importa da PostgreSQL», «Prepara le risposte in Redis», «Lancia il confronto». Oppure tutto da
   riga di comando, con lo stesso risultato nella pagina:

   ```bash
   scripts/prova_confronto.sh http://127.0.0.1:8419 1000000
   ```

   Lo script si ferma al primo errore e alla prima risposta diversa da quella di PostgreSQL. Con `sul-disco` ripete
   il confronto con la rete portata sul disco; con `senza-redis` lascia fuori Redis.

**La memoria.** Un milione di record occupa nel portale circa 430 MB a riposo e 800 MB di picco durante
l'importazione; in Redis le risposte pronte sono circa 90 MB. Le taglie che il portale lascia generare arrivano al
milione. Per dieci milioni si aggiunge `10000000` a `SINAPSIDB_PROVA_TAGLIE` e servono circa 8 GB liberi.

## Che cosa viene dopo

- **Valori ordinati e somme tenute pronte: fatti il 22 novembre 2026.** Vedi [Le due domande che non
  viaggiavano](#le-due-domande-che-non-viaggiavano).
- **Gli stessi due rimedi per la rete sul disco,** che oggi non ha né l'ordine né i circuiti.
- **Il nucleo compilato per le somme su tutti i fatti di un tipo: fatto il 23 novembre 2026.** Vedi [Il nucleo
  compilato](#il-nucleo-compilato).
- **La scrittura in codice compilato.** Oggi scrivere un fatto costa circa 27 microsecondi, tutti in Python: è il
  tempo dell'importazione, 27 secondi per un milione di record. È il prossimo pezzo grosso.
- **Meno memoria per fatto.**
- **La pagina dei dati sulla rete di prova**, per camminare tra clienti, fatture e prodotti importati.

Sono proposte, non promesse.
