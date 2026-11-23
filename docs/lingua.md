# La lingua di SinapsiDB

**Stato: in funzione dal 22 novembre 2026, solo per leggere.** È il modo di fare una domanda alla rete scrivendola,
senza programmare e senza un modello linguistico. Una domanda dice quali neuroni accendere, in quali fatti, e che
cosa leggere dai fatti che scattano. Si prova dalla pagina «Confronto» del portale, nella casella «Fai una domanda».

## In breve

- **Una domanda è una frase corta.** `accendi cliente = C00202 in fattura emessa leggi fattura, totale` dà le fatture
  di quel cliente.
- **Il motore la capisce e la esegue da solo**, anche quando ha più passi. Dice anche come ha risposto: quali neuroni
  ha acceso, quanti fatti sono scattati, in quanto tempo.
- **Non serve un modello linguistico.** Serve solo a chi parla in lingua libera: il modello traduce la frase della
  persona in una frase di questa lingua, e il motore fa il resto.
- **Legge soltanto.** Per scrivere oggi c'è solo la chiamata dal codice; una forma scritta anche per raccontare è il
  passo successivo.
- **Costa poco.** Capire una domanda richiede circa 0,2 millisecondi, una volta sola. Eseguirla costa quanto le
  domande scritte a mano nel codice, spesso meno.

## Come è fatta una domanda

```
accendi <ruolo> = <nome>  in <tipo di fatto>  leggi <ruolo>, <ruolo>
```

Nella rete ogni fatto è un neurone che riceve una sinapsi da ciascuno dei suoi partecipanti, e ogni sinapsi ha un
ruolo. «Fattura F2025/0000018 a C00202, totale 10947.47» è un fatto di tipo `fattura emessa` con i ruoli `fattura`,
`cliente`, `totale`. La domanda accende il neurone `C00202` nel ruolo `cliente`: scattano i fatti che lo ricevono lì,
e da quelli si leggono gli altri ruoli.

```
accendi cliente = C00202 in fattura emessa leggi fattura, quando, totale
```

### Più indizi

Con `e` si accendono più neuroni insieme. La soglia sale: scattano solo i fatti che li ricevono tutti.

```
accendi prodotto = P00007 e magazzino = "Magazzino Verona" in giacenza leggi quantità
accendi cliente = C00202 e stato = aperto in ticket di assistenza leggi ticket, priorità
```

Il motore parte dall'indizio che accende meno fatti e tiene quelli che ricevono anche gli altri.

### Più passi

Con `poi` la domanda prosegue: i neuroni letti da un passo vengono accesi nel passo dopo, nel ruolo con lo stesso nome.

```
accendi cliente = C00202 in fattura emessa leggi fattura
poi in riga di fattura leggi prodotto
poi in scheda prodotto leggi distinti fornitore
```

Sono i fornitori dei prodotti che quel cliente ha comprato: quattro cose collegate, tre passi. Ogni neurone si accende
una volta sola, anche se è stato letto più volte. Se un passo legge più cose, `poi da <ruolo> in …` dice da quale
proseguire; se nel passo dopo il ruolo si chiama in un altro modo, `… in <tipo> come <ruolo>` dice con quale entrare.

### Leggere

- `leggi ruolo, ruolo` dà una riga per ogni fatto scattato.
- `leggi distinti …` dà ogni risposta una volta sola.
- `frase` e `tipo` si possono leggere sempre. La frase è quella con cui il fatto è stato raccontato; il tipo è il tipo
  del fatto.
- Se un fatto non ha un ruolo, in quel posto la riga resta vuota.

```
accendi fattura = F2025/0000018 in riga di fattura leggi prodotto, quantità, frase
```

### Contare, sommare, raggruppare

```
accendi cliente = C00202 in fattura emessa conta
accendi cliente = C00202 in fattura emessa somma totale
in ordine ricevuto conta per stato
in fattura emessa somma totale per mese(quando)
```

`per <ruolo>` raggruppa per i neuroni di quel ruolo. `per mese(<ruolo>)` e `per anno(<ruolo>)` raggruppano una data
per mese o per anno. La somma di niente è `0`. Si somma solo quello che è un numero.

### Condizioni

`dove` tiene solo i fatti che passano una condizione: `=`, `>`, `>=`, `<`, `<=`.

```
in fattura emessa dove totale > 5000 leggi fattura, cliente, totale
accendi cliente = C00202 in fattura emessa dove totale >= 1000 leggi fattura
```

### Senza un punto di partenza

Una domanda può cominciare da `in <tipo di fatto>`, senza accendere niente. Il motore allora passa tutti i fatti di
quel tipo: funziona, ma costa in proporzione a quanti sono.

Se c'è una condizione su un numero (`>`, `>=`, `<`, `<=`) non li passa tutti: il motore tiene i valori di quel ruolo
in ordine di grandezza e parte dal primo che passa la condizione. L'ordine lo costruisce la prima volta che una
domanda lo chiede e poi lo aggiorna a ogni scrittura. Vale per la rete tutta in memoria; la rete letta dal disco
guarda i valori una volta ciascuno.

### Leggere una somma tenuta pronta

```
circuito fatturato per mese
circuito fido residuo di Rossi
```

Un circuito è una somma che la rete tiene pronta: la stessa scrittura che registra un fatto aggiorna la somma, e chi
la legge di solito non calcola niente. `circuito <nome>` dà una riga per ogni cosa di cui il circuito tiene il conto
(ogni mese, ogni cliente), `circuito <nome> di <cosa>` una sola. È la stessa somma che `somma ... per ...` calcola
passando i fatti: la differenza è che qui è già fatta. I circuiti li definisce chi imposta la rete, non la lingua, e
ci sono solo nella rete tutta in memoria.

- **Si legge a un istante, uno solo.** Senza altro, quello della domanda: una decorrenza o una scadenza passata nel
  frattempo cambia la somma anche se nessuno ha scritto. Con `come valeva il <data>` si legge a quella data.
- **Quando non si guarda la validità la somma non è pronta.** Con `come è registrato` e `come risultava all'evento N`
  contano tutti i fatti registrati e non ritirati, anche scaduti o non ancora cominciati: quella somma la rete non
  la tiene, e la rifà dai fatti. Il piano della risposta lo dice.
- **Sta nel circuito chi ha almeno un fatto.** Un mese con `+10` e `−10` c'è, e vale zero; un mese rimasto senza
  fatti non c'è.
- **Due circuiti con lo stesso nome** non si scelgono a caso: la risposta dice gli identificativi, e la frase si
  precisa con `circuito neurone:17`.
- **`neurone:17`, scritto così, è sempre il neurone 17**: per il circuito e per l'entità dopo `di`. Se non è un
  circuito la frase è sbagliata, anche quando un circuito si chiama proprio in quel modo. Tra virgolette,
  `"neurone:17"`, è sempre un nome.
- **Un nome tra virgolette è un pezzo solo**, anche se dentro c'è «di»: `circuito ricavi di "Studio di Ada"`.
- **Una lettura preparata segue il suo circuito e la sua entità, non i loro nomi.** Se il circuito è stato
  ridefinito si prepara di nuovo, così le colonne dicono quello che contengono. Se è stato ritirato la frase non
  vale più, anche se un altro circuito ne ha preso il nome; lo stesso se non c'è più l'entità per cui era stata
  preparata. Non risponde zero, e non risponde per un altro: l'entità non si cerca di nuovo per nome, nemmeno quando
  il circuito è stato ridefinito.
- **Un circuito raggruppato per mese vuole una data per esteso**, `2026-11-22`, una sola, in ogni fatto che osserva.
  Un fatto che non la porta non entra: la scrittura viene rifiutata.

### In qualsiasi tipo di fatto

Senza `in`, gli indizi valgono per i fatti di qualsiasi tipo.

```
accendi cliente = C00202 e quando = 2025-03-04 leggi tipo, frase
```

Dà tutto quello che riguarda quel cliente in quel giorno: ordini, fatture, ticket. In un database a tabelle sarebbero
tre domande diverse.

## I nomi

- Un nome si scrive com'è: `C00202`, `fattura emessa`, `ordine d'acquisto`, `2025-03-04`. Maiuscole e accenti non
  contano.
- Le parole della lingua sono dodici: `accendi`, `e`, `in`, `dove`, `leggi`, `distinti`, `conta`, `somma`, `per`,
  `poi`, `da`, `come`. Un nome che ne contiene una va tra virgolette: `stato = "in lavorazione"`.
- Vanno tra virgolette anche i nomi con i segni `= , ( ) < > ?`.
- Se un nome indica più cose, il motore non sceglie: dice quali sono e che cosa le distingue.
- Se un tipo di fatto o un ruolo non esiste, il motore lo dice e propone quelli che gli somigliano.

## Due frasi per orientarsi

```
vocabolario
```

Elenca i tipi di fatto della rete, con i ruoli e quanti fatti hanno.

```
percorsi da cliente a fornitore
```

Propone le strade che portano da una cosa all'altra, già scritte come domande, dalla più corta. Spesso ce n'è più di
una, e sono domande diverse: i fornitori dei prodotti che il cliente ha comprato non sono quelli dei prodotti su cui
ha aperto un ticket. Per questo il motore le propone e la strada la sceglie chi chiede. Da un tipo di fatto al
successivo passa solo attraverso un riferimento vero, come la fattura o il prodotto, non attraverso una data o un
prezzo.

## Come risponde

Insieme alle righe il motore restituisce:

- **il piano in parole**, un passo per riga: «Accendo cliente = C00202 nei fatti "fattura emessa"; leggo fattura»;
- **quanti fatti sono scattati** a ogni passo;
- **il tempo** dell'esecuzione, e a parte quello servito a capire la domanda.

## Domande preparate

Al posto di un valore si può scrivere `?`. La domanda si capisce una volta e si esegue quante volte si vuole con valori
diversi, come le domande preparate di un database.

```python
from sinapsidb import lingua

domanda = lingua.prepara(rete, "accendi cliente = ? in fattura emessa leggi fattura, totale")
domanda.esegui("C00202")          # [("F2025/0000018", "10947.47"), ...]
domanda.esegui("C00007")

lingua.chiedi(rete, "in ordine ricevuto conta per stato")   # righe, colonne, piano, tempi
```

Una domanda preparata che nomina qualcuno, come `accendi cliente = Rossi …`, è preparata per quel neurone. Se era
appena nato in una prova ed è stato dimenticato, eseguirla di nuovo dice che va preparata di nuovo: non risponde per
chi ne ha preso il numero o il nome, nemmeno tutti e due insieme. Finché c'è, resta lui: un omonimo nato dopo non la
rende ambigua. Se invece nominava qualcuno che la rete non conosceva ancora, lo trova quando nasce.

Il confronto con PostgreSQL usa queste domande preparate: le quindici domande del confronto sono scritte in questa
lingua, accanto al loro SQL.

## Guardare il passato

Ogni fatto ha due tempi: quando è stato registrato e da quando a quando vale. Chi lo racconta può dichiararlo con i
ruoli `vale da` e `vale fino a`, con una data intera (`2026-11-16`, oppure `2026-11-16T10:30`); la fine è esclusa. Un
fatto che non lo dichiara vale da quando è registrato, senza scadenza.

In fondo a una domanda si può dire quale passato guardare:

| Si scrive | Che cosa guarda |
|---|---|
| niente | quello che vale adesso |
| `come è registrato` | tutto quello che è registrato e non ritirato, anche scaduto o non ancora cominciato |
| `come risultava il 2026-11-16` | solo quello che a quell'istante era già scritto, e che valeva allora |
| `come risultava all'evento 41` | quello che era registrato dopo quell'evento, senza guardare la validità |
| `come valeva il 2026-11-16` | quello che valeva a quell'istante, sapendo tutto quello che è registrato oggi |
| `come valeva adesso` | quello che vale in questo momento: lo stesso che senza clausola |

Un pagamento registrato martedì e valido da domenica non compare in «come risultava lunedì» e compare in «come
valeva lunedì».

Queste letture valgono per la memoria a cui si racconta, non per la rete di prova del confronto, dove nessun fatto
dichiara un periodo. La risposta dice sempre quale lettura ha fatto, nel campo `lettura`. Per una lettura del
passato la memoria rifà dal giornale la rete com'era: il tempo cresce con la lunghezza del giornale.

## Che cosa non fa ancora

- **Scrivere.** La lingua legge soltanto.
- **Il passato nella rete di prova.** Lì la lingua legge solo i fatti in vigore. Nella memoria a cui si racconta il
  passato si guarda con le clausole del paragrafo «Guardare il passato».
- **Oppure.** Gli indizi e le condizioni valgono tutti insieme; non c'è «questo oppure quello».
- **Ordinare e fermarsi ai primi.** Le righe escono nell'ordine in cui scattano i fatti; la lettura di un circuito dà
  le entità in ordine di nascita, e una condizione su un numero dà i valori in ordine di grandezza. È un ordine che
  non dipende dalla storia della rete: riaperta, risponde con le stesse righe nello stesso ordine. Chiederne un altro
  non si può ancora.
- **Un ruolo ripetuto nello stesso fatto** (una riunione con due presenti). Il fatto conta una volta sola, e una
  condizione su quel ruolo è vera se uno qualunque dei suoi nomi la passa. Ma chi legge quel ruolo, o raggruppa per
  quel ruolo, vede solo il primo nome: «leggi presente» dà il primo presente di ogni riunione. Per avere una riga per
  ogni nome va scritto un fatto per ogni nome.
- **Cercare una parola dentro le frasi.**
- **Tra due valori in un passo solo.** `dove totale >= 10 e totale < 100` parte dal primo confine e controlla il
  secondo fatto per fatto.
- **Definire un circuito.** La lingua lo legge; a scriverlo è chi imposta la rete.

Sono proposte, non promesse.
