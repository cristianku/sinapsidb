# Il contratto delle decisioni

**Stato: seconda stesura, dopo una revisione indipendente** (22 novembre 2026). Descrive
che cosa il motore dovrà fare. Quello che il codice fa già è detto nel paragrafo 12.

## In parole semplici

Ogni volta che qualcuno chiede di scrivere qualcosa, SinapsiDB decide: sì o no. Questo contratto dice tre cose.

1. **La decisione resta scritta.** Un sì sta nello stesso record della scrittura che ha autorizzato: non può esistere
   un ordine senza la sua decisione. Un no conserva la richiesta e la decisione, non la modifica negata.
2. **La decisione ricorda che cosa ha guardato.** Non una copia dei dati: ricorda quale domanda ha fatto, su quale
   stato, e il numero che ha letto.
3. **Una decisione si può riesaminare senza toccarla.** Si può chiedere «se avessimo saputo di quel pagamento, che
   cosa avremmo deciso?» e ottenere una risposta. La decisione di allora resta com'era, e nessun ordine viene
   autorizzato a posteriori.

L'esempio che accompagna tutta la pagina: lunedì un cliente ha 800 € di fido disponibile e chiede un ordine da
1.000 €. La risposta è no. Martedì si registra un pagamento da 400 € fatto domenica.

La regola dell'esempio è fissata così: **il residuo dopo la prenotazione non può essere negativo**. Lunedì il fido
prima della richiesta è 800 € e il residuo dopo sarebbe −200 €: no. Con 1.200 € il residuo dopo è +200 €: sì.

## 1. I due tempi di un fatto

Ogni fatto ha due tempi, e non vanno confusi.

| Tempo | Che cosa dice | Da dove viene |
|---|---|---|
| **registrato** | quando il motore lo ha saputo | il numero dell'evento nel giornale; c'è già |
| **vale da … a …** | per quale periodo è vero | lo dichiara chi racconta il fatto; se manca, vale dall'istante in cui è scritto nel giornale |

Gli intervalli comprendono l'inizio ed escludono la fine. Una fine non dichiarata vuol dire «senza scadenza».

Il pagamento dell'esempio è registrato martedì e vale da domenica.

Da qui due modi di guardare il passato, con due parole diverse nella lingua:

- **come risultava il** lunedì: solo quello che lunedì era già scritto nel giornale. Il pagamento non c'è.
- **come valeva il** lunedì: tutto quello che vale lunedì, sapendo quello che è registrato oggi. Il pagamento c'è.

## 2. Lo stato su cui si decide

Una decisione lavora su due strati, che restano distinti.

- **La base** è l'ultimo evento già scritto nel giornale prima della decisione. È durevole e chiunque la può
  ricostruire.
- **La proposta** è la scrittura richiesta. Viene applicata sopra la base **una volta sola**, in uno stato
  provvisorio. Diventa giornale solo se la decisione è sì.

L'**istante della decisione** lo fissa il motore una volta sola, quando la richiesta arriva, non quando ottiene il
turno per scrivere. La base invece è quella che la richiesta trova quando tocca a lei. Lo registra e lo riusa:
non è la validità dichiarata dai fatti e non è il numero che ordina i commit. Una richiesta retrodatata è valutata
all'istante in cui arriva e non riapre le regole sul passato.

L'**istante di scrittura** è quello in cui l'evento entra nel giornale. Lo fissa il motore, non torna mai indietro
anche se l'orologio arretra, e sta nel record quando è diverso dall'istante di arrivo. Se una richiesta è servita
dopo il suo arrivo, le regole si valutano due volte: sull'istante di arrivo e sull'istante di scrittura, dove vale
tutto quello che nel frattempo è stato registrato. La scrittura passa solo se passano tutte e due, e la ricevuta
le porta entrambe. Così una richiesta rimasta in coda non spende una disponibilità che un'altra, arrivata dopo e
servita prima, ha già preso.

## 3. La lettura

Una **lettura** è una domanda fatta alla rete in un punto preciso. È descritta da cinque cose:

- **che cosa** si legge: un tipo di fatto oppure un circuito mantenuto, per esempio «fido residuo»;
- **di chi**: l'entità, per esempio il cliente C;
- **la base**: l'ultimo evento che la lettura vede;
- **il momento di validità**: l'istante per cui i fatti devono valere;
- **con o senza la proposta**: se la lettura guarda la sola base oppure la base con sopra la proposta.

Una somma conta i fatti che valgono al momento di validità, tra quelli registrati entro la base.

La decisione di lunedì ha fatto due letture del fido del cliente C, valide lunedì, sulla base di lunedì: senza la
proposta ha trovato 800 €, con la proposta −200 €.

## 4. La ricevuta

Ogni decisione lascia una ricevuta nel giornale. La ricevuta di un sì sta nello stesso record della scrittura.

La ricevuta contiene:

- la chiave e l'impronta della richiesta, e la richiesta in forma canonica;
- l'esito, il numero dell'evento, la base e l'istante della decisione;
- per **ogni regola consultata**, anche quelle che non sono scattate: nome, versione, su chi è stata valutata,
  attivazione, soglia e verdetto;
- la versione di ogni circuito letto;
- per **ogni lettura**: le cinque cose del paragrafo 3 e il valore letto.

Il valore letto è una **testimonianza**: quello che il motore dichiara di aver visto. La ricevuta non contiene le
fatture e i pagamenti che lo giustificano: quelli stanno nel giornale.

La lettura registrata è anche la **dipendenza** della decisione. È una domanda («pagamenti del cliente C validi
lunedì»), non l'elenco dei fatti trovati: così una rettifica che porta un fatto che allora non c'era ritrova comunque
la decisione.

La ricevuta ha una **forma logica**, quella descritta qui, e una **forma su disco**. La forma su disco si può
comprimere, purché da essa si riottenga la stessa ricevuta logica: `verifica` e i confronti «byte per byte» guardano
la forma logica. Non si possono togliere la richiesta, gli identificativi e le versioni, la base, le letture e i
valori: senza di essi la ricevuta non è più verificabile.

Regole ed entità si indicano con il loro identificativo, che non cambia, oltre che con il nome: due entità con lo
stesso nome restano due.

La **versione** di una regola o di un circuito è l'ultimo evento che ne ha cambiato la definizione: quello che l'ha
scritta, oppure l'ultimo che le ha aggiunto o tolto un segnale o un termine.

Ogni ricevuta dichiara il suo **formato**. Quello di oggi è il 2, e ammette una forma sola: ogni differenza è un
guasto. Le ricevute scritte prima non lo dichiarano, e hanno avuto tre forme: la versione numerata con l'evento che
aveva scritto la definizione, con o senza l'identificativo del tipo nelle letture di presenza, e poi già la versione
di oggi. Una ricevuta senza formato si confronta con ognuna di quelle forme, **per intero**: è coerente se coincide
con almeno una, e la risposta dice con quali. Una convenzione vale per tutta la ricevuta, mai campo per campo. Il
riesame parla nella forma che ha riconosciuto. Le ricevute vecchie non si riscrivono.

Accanto al giornale c'è un **segno durevole**: dice da quale evento in poi ogni evento porta la sua decisione, e in
quale formato. Lo mette **chi scrive**, subito prima del primo evento che scrive: in un giornale nuovo è l'evento
zero, in uno scritto prima è il primo evento del motore di oggi. Chi apre il giornale per leggere, per verificare o
per provare non lo tocca. Va conservato insieme al giornale.

Un segno che c'è ma non si legge **non è un segno assente**. Finché non viene ripristinato da una copia si può
ancora chiedere, ma non si scrive e non si verifica, e il motore non lo sostituisce con uno nuovo: quello che
diceva non si può ricavare da quanto è lungo il giornale oggi. Lo stesso se sparisce dopo che il motore lo ha
scritto, o se parla di eventi che il giornale non ha.

## 5. Ripetere non è riesaminare

- **Ripetere** una richiesta con la stessa chiave restituisce la ricevuta di allora, sempre, anche se nel frattempo
  lo stato è cambiato. Non crea un evento nuovo.
- Vale anche quando la richiesta **non ha cambiato niente**, perché i fatti c'erano già identici. Con una chiave,
  quell'esito lascia nel giornale un evento senza fatti che porta la chiave e la ricevuta, come fa un rifiuto: dopo un
  riavvio la stessa chiave la ritrova, e non scrive allora quello che una correzione ha nel frattempo ritirato. Senza
  chiave non resta niente. Quell'evento si verifica come gli altri; non si riesamina, perché non ha deciso niente.
- Una **prova** non lascia niente, nemmeno la sua chiave: la richiesta vera con la stessa chiave viene valutata.
- **Riprovare adesso** è una richiesta nuova, con una chiave nuova e una ricevuta nuova.
- **Riesaminare** non scrive niente: è una domanda.

## 6. Il riesame

Una **rettifica** è un evento registrato dopo la base di una decisione che aggiunge, ritira o sostituisce fatti.

Si chiede di riesaminare una decisione indicando **quali rettifiche includere**, con gli identificativi degli eventi.
Il motore li prende interi, senza doppioni e nell'ordine del giornale. Poi:

1. ricostruisce la base, le regole e i circuiti nella versione di allora, e la richiesta dalla ricevuta;
2. sovrappone alla base **solo** le rettifiche indicate;
3. applica la richiesta **una volta sola** e valuta le regole all'istante di allora;
4. risponde con l'esito e i valori di allora, l'esito e i valori ipotetici, la versione delle regole e **l'elenco
   degli eventi usati**.

Se una rettifica indicata ne richiede un'altra che non è né nella base né nell'elenco, per esempio uno storno senza
il suo pagamento, il motore risponde «scenario incompleto». Non aggiunge altri eventi di nascosto.

Si può anche chiedere «tutte le rettifiche registrate fino all'evento N». È una comodità: la risposta elenca comunque
gli eventi usati, e non vuol dire «tutto quello che si è saputo dopo».

Una rettifica che aggiunge un fatto senza dichiarare da quando vale non cambia una decisione passata: quel fatto
vale da quando è scritto, cioè dopo. Vale anche se l'orologio segnava lo stesso istante: a parità di istante decide
l'ordine degli eventi. Una rettifica che ritira un fatto, invece, lo toglie per tutto il periodo in cui valeva.

Una rettifica può portare la regola su un ramo che allora non aveva percorso. Le letture in più sono ammesse e
restano separate, nella risposta, da quelle di allora.

Nella prima versione il riesame è **per decisione**: ogni richiesta è riesaminata da sola. La risposta lo dichiara.
La **storia alternativa**, in cui un sì ipotetico consuma il fido per le richieste successive, non è nella prima
versione.

Prima di riesaminare, il motore controlla che la ricevuta sia coerente con il giornale, come fa `verifica`, in
qualunque formato. Una ricevuta guasta non si riesamina: la risposta avrebbe l'aria di una risposta ordinaria.

Se il giornale non dice con quale modo di fare le somme è stata presa la decisione (paragrafo 9), l'ipotesi si fa
con **ognuno** dei modi con cui la ricevuta coincide. Se danno tutti la stessa risposta, con le stesse letture, la
risposta è quella. Se no l'esito ipotetico è «indeterminato»: la risposta riporta ogni alternativa con i modi che la
danno, e non dice se la decisione cambia. La decisione di allora resta certa in ogni caso.

Il riesame non consulta il valore di oggi e non registra gli effetti ipotetici. Ricevute, fatti e circuiti sono
identici subito prima e subito dopo. Un sì ipotetico non autorizza niente.

## 7. Trovare le decisioni da riesaminare

Una rettifica ha un **intervallo di effetto** su ogni lettura che tocca: di chi, e per quale periodo. Una rettifica
può avere un ambito vecchio e uno nuovo. Un pagamento spostato dal cliente C al cliente D tocca tutti e due, anche se
D non aveva pagamenti; lo stesso vale per uno storno e per un cambio di decorrenza.

Sono **candidate** le decisioni che hanno una lettura su una di quelle entità, con il momento di validità dentro
l'intervallo di effetto e la base precedente alla rettifica. La ricerca guarda chi partecipa ai fatti toccati, non
com'è definito oggi un circuito, perché la definizione può essere cambiata dopo la decisione. Può proporre una
decisione che al riesame non cambia; non deve perderne una che cambia. Dice se la sua copertura è completa: dove gli
eventi non hanno le letture nella ricevuta non può vedere, e non presenta la sua lista come completa. Un pagamento che
vale da domenica senza scadenza rende
candidata anche una decisione presa mercoledì, se è stato registrato dopo.

Si cercano dalle dipendenze, non guardando se il valore di oggi è cambiato: un pagamento seguito da un reso lascia
il valore di oggi uguale, ma una decisione presa in mezzo può cambiare.

Il motore conta e restituisce: decisioni candidate, decisioni riesaminate, esiti cambiati, scenari incompleti, e la
copertura tra gli eventi scritti prima della rettifica. Il conteggio dei circuiti attraversati per strato arriverà
con i circuiti che leggono altri circuiti: oggi misurerebbe i termini di oggi, non il lavoro fatto allora.

## 8. La verifica

`verifica` prende una ricevuta e rifà la decisione, per i sì e per i no:

1. ricostruisce dal giornale lo stato fino alla base scritta nella ricevuta;
2. prende le regole e i circuiti che a quella base si applicavano alla richiesta, nella versione di allora;
3. applica la richiesta della ricevuta una volta sola, in uno stato provvisorio;
4. confronta con la ricevuta l'insieme delle regole e delle letture, i valori e l'esito.

Ogni differenza è un guasto segnalato: un valore diverso, un esito diverso, ma anche una regola che andava
consultata e nella ricevuta non c'è. `verifica` non usa mai la lettura di oggi al posto di quella registrata.

Si confrontano anche l'evento e l'esito scritti nella ricevuta, perché sono quelli che riceve chi ripete la
richiesta. Le consultazioni si contano una per una, per identificativo.

Tutto quello che `verifica` confronta lo legge dal giornale in quel momento: il record dell'evento e la rete com'era
prima. Una copia tenuta in memoria, o ripresa da un'istantanea, non fa testo.

Dal punto indicato dal segno durevole ogni evento deve avere la sua decisione, nel formato che il segno dice: se
manca, o se dichiara un formato più vecchio, è un guasto. In un giornale senza segno vale la posizione: dalla prima
decisione in poi. Solo un evento precedente è «non verificabile».

Gli esiti di `verifica` sono quattro. «coerente»: la ricevuta coincide con la decisione rifatta. «guasto»: non
coincide, e la risposta dice in che cosa. «non_supportato»: la decisione dichiara un modo di fare le somme che
questo programma non conosce, e non la si rifà con un altro. «non_verificabile»: l'evento è di prima che le
decisioni venissero registrate, oppure il segno del giornale non si legge.

**Che cosa `verifica` non dimostra.** Dimostra che la ricevuta è coerente con il giornale, non che sia fedele a
quello che è successo.

- Per un sì riapplica i fatti e i ritiri già risolti nel record: non rifà la scelta che la richiesta aveva fatto,
  per esempio quali fatti trovavano gli indizi di una sostituzione.
- Per un no la proposta sta nella ricevuta stessa: una proposta falsa scritta insieme alle sue letture è coerente.
- Usa lo stesso valutatore del motore: non è un verificatore indipendente.
- Trova le regole che il motore consulta per i fatti toccati, non una regola che avrebbe dovuto guardare altro.

Per questo serve il controllo esterno del capoverso che segue.

Un controllo esterno confronta inoltre il giornale con le richieste e le conferme viste da chi ha scritto: il
giornale da solo non può denunciare quello che ha omesso.

## 9. I circuiti mantenuti

Un circuito mantenuto tiene pronta una risposta, per esempio il fido residuo di ogni cliente.

- È **derivato**: non va nel giornale e si ricostruisce dai fatti.
- Il commit lo aggiorna **prima** di valutare le regole, e le regole leggono lo stato con la proposta applicata.
- I circuiti sono **a strati**: un circuito legge fatti o circuiti dello strato sotto, mai se stesso.
- I contributi sono ordinati per **tempo di validità**, non per tempo di registrazione.
- Il circuito tiene il valore di oggi. I valori del passato si ricalcolano dal giornale.
- Se una lettura necessaria fallisce non c'è commit. Un errore di lettura non è un segnale «ignoto».
- Le somme sono **esatte**. Ogni contributo è un numero finito con al più sei decimali e trenta cifre intere; il
  totale è un intero senza limite di grandezza, quindi non trabocca e non si arrotonda. Un contributo fuori da
  questo dominio fa rifiutare tutta la scrittura prima che cambi qualcosa: non esiste un sì arrotondato. Esatti sono
  la somma e il confronto con il limite; i pesi dei segnali di una regola e la sua soglia sono numeri in virgola
  mobile, come prima dei circuiti.
- Una regola o un circuito che non si possono leggere **non entrano nel giornale**: la definizione è controllata
  prima di scrivere l'evento, e se non sta in piedi restano i valori e le regole di prima.
- Un circuito può tenere la somma **per mese o per anno** invece che per entità: il termine dice «raggruppa per
  mese». *Aggiunto il 22 novembre 2026 e corretto in quattro giri sui controesempi della revisione, che ha
  approvato il perimetro «somme raggruppate in lettura, regole ancora vietate», la frase sulle letture senza
  validità e il criterio di ammissione. Il paragrafo non è ancora chiuso.*
  - Il ruolo che lega deve portare una **data per esteso**, `2026-11-22`, anche con l'ora dopo: una sola, e
    presente in ogni fatto che il circuito osserva. Una data impossibile (`2026-13-07`), scritta in forma compatta
    (`20261122`) o come settimana (`2020-W53-5`), che manca o che c'è due volte fa rifiutare la scrittura, o la
    definizione se il fatto c'è già. Di quelle forme l'inizio del nome non è un mese, e un fatto senza mese
    resterebbe fuori dalla somma senza che nessuno lo sappia.
  - Il mese è un neurone come gli altri, che ha per nome l'inizio della data (`2026-11`). **Nasce dentro la
    scrittura che lo usa per prima ed entra nel giornale con lei**, così chi rilegge il giornale lo ritrova con lo
    stesso numero. Se la scrittura non avviene, per una prova o per un no, il mese non resta: lo ha fatto nascere il
    motore, nessuno ne ha l'identificativo, e un nome rimasto senza evento sposterebbe i numeri di tutti quelli che
    nascono dopo. Per la stessa ragione l'istantanea salva solo i neuroni che sono già nel giornale.
  - **Stare nel circuito non è avere una somma diversa da zero.** Un mese con `+10` e `−10` c'è, e vale zero. Un
    mese rimasto senza fatti in vigore non c'è più, né in memoria né rileggendo il giornale: il circuito conta
    quanti fatti contribuiscono a ogni entità, e la toglie quando l'ultimo se ne va.
  - **Si legge sempre a un istante.** Adesso, sulla rete viva: una decorrenza o una scadenza passata nel frattempo
    cambia la somma anche senza scritture. All'istante della vista, in una lettura del passato. Quando si chiede
    quello che è registrato senza guardare la validità, le somme pronte non sono la risposta: si rifanno dai fatti,
    e il piano lo dice.
  - Due circuiti con lo stesso nome non si scelgono a caso: la frase va precisata con l'identificativo.
  - **Ammettere non è rileggere.** I controlli qui sopra (la data per esteso, una sola, presente) valgono per le
    scritture nuove e per i circuiti definiti adesso sui fatti che ci sono già. Una decisione già presa si rilegge
    con la semantica di quando è stata presa: una memoria scritta da una versione che accettava `2026-13-07` si
    riapre, e le sue ricevute si verificano ancora. Altrimenti una ricevuta integra risulterebbe guasta.
  - **Verificare non è fare un'ipotesi.** `verifica`, e un riesame senza rettifiche, rileggono la decisione sulla
    base di allora, con la semantica di allora. Un riesame con delle rettifiche dentro è un'ipotesi su una base
    diversa: alla richiesta si chiede quello che si chiederebbe a una scrittura di oggi, e se oggi non entrerebbe
    l'esito è «non applicabile», con il perché. La ricevuta di allora non si tocca. Conta quello che è entrato
    nella base, non quello che si è chiesto di guardare: se tutti gli eventi indicati restano fuori, non è
    un'ipotesi.
  - **Di chi è un fatto.** Un circuito ordinario lo dà a ogni entità che il fatto nomina nel ruolo che lega, **una
    volta ciascuna**: un accredito intestato a due clienti vale per tutti e due, e uno che nomina due volte lo
    stesso cliente vale una volta. È così a ogni scrittura e rifacendo le somme: 70 di credito non diventano 140.
    Un circuito raggruppato lo dà a un mese solo: il ruolo ripetuto è rifiutato in una scrittura nuova.
  - **Il modo di fare le somme ha una storia, e la dichiara chi scrive.** Il 22 novembre il modo di dire di chi è
    un fatto è cambiato tre volte, e con ognuno sono state scritte ricevute vere: la stessa storia dava un sì con
    la 0.9.2 e un no con la 0.9.3, tutti e due verificati allora, e i due programmi lasciavano un giornale identico
    byte per byte. I modi sono numerati (`motore.CALCOLI`), e i numeri non si riusano.
    - **Dichiarato.** Con quale modo è stata presa una decisione lo dicono due cose soltanto. La **ricevuta**:
      dalla 0.9.6 ogni decisione porta il suo modo (`calcolo`) nello stesso record dell'evento, ed è del motore fino
      al giornale: chi prepara la ricevuta non lo può togliere né cambiare. Oppure un'**attestazione**, per i tratti
      che non lo dichiarano (vedi sotto). Un modo dichiarato si usa da solo: non se ne provano altri, nemmeno per
      salvare una ricevuta che con un altro tornerebbe. La risposta dice da quale delle due viene (`calcolo_da`).
    - **Dichiarato ma sconosciuto.** Se è dichiarato un modo che il programma non conosce, la verifica risponde
      «non supportato» e il riesame si ferma. Non è un modo assente: non si ripiega su quelli noti.
    - **Non dichiarato.** Per una decisione di cui nessuna delle due dice il modo la verifica prova **tutti i modi
      noti** e dice con quali la ricevuta coincide (`calcoli_compatibili`). È una **compatibilità**: non dice quale
      programma l'ha scritta. Se non coincide con nessuno è guasta. Il riesame fa l'ipotesi con tutti i modi
      compatibili (paragrafo 6). Nessun modo è escluso in partenza.
    - **Il passato si attesta, non si deduce.** Il giornale da solo non può dire chi ha scritto un tratto non
      dichiarato. Chi lo sa da un'altra fonte, per esempio dal registro degli aggiornamenti, lo attesta con
      `python -m sinapsidb.attesta`: nel segno resta il modo, il tratto, **la fonte** e **l'impronta del giornale
      fino alla fine del tratto**. Chi verifica controlla l'impronta ogni volta che il file del giornale cambia: lo
      stesso segno accanto a un giornale con un altro contenuto non vale, e la risposta lo dice. Senza la fonte
      restano un tratto e un'impronta, che dicono quali byte e non chi lo sa: il segno è guasto. L'impronta
      riconosce uno scambio di copie; non dimostra chi ha scritto due giornali identici, che resta la parola di chi
      attesta. Un tratto con una decisione che dichiara già il suo modo non si attesta. Le ricevute non si
      riscrivono.
    - **Una riga di `calcoli` nel segno è un indizio, non una dichiarazione.** La lasciava la 0.9.5 anche solo
      aprendo un giornale, e scritta a mano non porta né una fonte né un legame con il giornale. Resta nel segno,
      la si può leggere, e **non restringe i modi possibili**: nemmeno quando chi scrive oggi ci aggiunge la sua
      frontiera (`calcoli`, `calcolo_dal`, `dichiarano_da`), che serve a chi legge il segno e ai programmi di prima.
  - **Il segno si confronta con il giornale vero.** Un segno che parla di eventi che il giornale non ha non è il
    suo: chi scrive si ferma, e chi verifica risponde «non verificabile» invece di usarlo. Si guarda la lunghezza
    del giornale sul disco, non quanti eventi ha letto chi sta verificando. Il segno si rilegge **a ogni
    scrittura**: se si guasta o sparisce dopo la prima, la scrittura seguente si ferma.
  - **Chi apre per leggere non tocca il giornale.** Una memoria aperta in sola lettura non scrive niente sul
    disco: non mette il segno, non salva istantanee e non taglia la coda di un giornale che non si rilegge fino in
    fondo, perché può essere un record che chi scrive sta ancora scrivendo. Quel taglio lo fa solo chi apre per
    scrivere, che è uno solo.
  - **Una scrittura torna alla base qualunque cosa vada storta.** Da quando i fatti proposti entrano nella rete a
    quando l'evento è nel giornale c'è una strada sola per tornare indietro, e la prende ogni errore, anche uno che
    nessuno aveva previsto: i fatti creati spariscono, quelli ritirati **da questa richiesta** tornano in vigore, i
    neuroni nati per lei si dimenticano. Un fatto si ritira una volta sola: ripresentare un ritiro già registrato è
    rifiutato prima di toccare qualsiasi cosa. Mentre una scrittura viene decisa non si salva un'istantanea e non
    ne comincia un'altra.
  - **La decisione nella ricevuta è del motore.** In una memoria che registra le decisioni, chi prepara la
    ricevuta può aggiungere quello che vuole, ma la decisione che finisce nel giornale è quella che il motore ha
    preso, tutta: non si può omettere né cambiare. Una nota senza ricevuta non si scrive.
  - **Quello che la memoria consegna è una copia.** La ricevuta custodita è quella del giornale: chi la riceve la
    può arricchire o cambiare senza cambiare quello che una richiesta ripetuta, una verifica o un'istantanea
    diranno dopo.
  - **Un esito incerto non è un no.** Se dopo un errore del disco una scrittura resta in sospeso, né confermata né
    tolta, la memoria non risponde «questa richiesta non c'è»: dice che va riaperta. La risposta è quella del
    giornale dopo la riapertura, per la stessa chiave.
  - **Una scrittura arriva intera sul disco, oppure non è successa.** Prima il giornale, poi la memoria. Il record
    si scrive fino all'ultimo byte prima di confermare. Se non arriva intero, quello che ne è arrivato viene tolto
    e la rete torna com'era prima della richiesta: il fatto non resta in memoria, non finisce in un'istantanea e
    non dà il suo numero all'evento dopo. Vale anche per un rifiuto, e se a fallire è chi prepara la ricevuta. Se
    non si riesce nemmeno a togliere il record a metà, la rete non scrive più e non salva istantanee finché non
    viene riaperta: chi riapre taglia la coda che non si rilegge.
  - **Nel riesame di un no i nomi che il no ha scritto nel giornale restano quelli.** Rinascono con un numero nuovo
    solo quelli che il no aveva dimenticato: altrimenti l'ipotesi parlerebbe di un altro cliente.
  - **Un fatto senza legami** è rifiutato prima di toccare qualsiasi cosa. Vale per le scritture nuove: uno
    scritto prima si rilegge e si verifica.
  - **L'origine di un evento è un testo.** Se arriva come un'enumerazione diventa il suo **valore** prima di
    qualsiasi effetto: una scrittura non resta a metà, e in memoria, nel giornale e nell'impronta della richiesta
    c'è lo stesso testo. Vale anche per la frase e per i nomi.
  - **Una lettura preparata resta di chi era.** La lettura di un circuito, e una domanda che nomina qualcuno, sono
    preparate per quel neurone, non per il suo nome né per il suo numero. Se era nato in una prova ed è stato
    dimenticato, chi esegue lo vede e dice che la domanda va preparata di nuovo: anche se nel frattempo è nato un
    altro con lo stesso nome e lo stesso numero. Non lo cerca di nuovo per nome, nemmeno quando il circuito è
    stato ridefinito o la rete ha cambiato forma: un omonimo nato dopo non rende ambigua una domanda già
    preparata, e non ne prende il posto.
  - **Una definizione cambiata insieme ai dati non cambia da quando valgono i dati.** Se la stessa scrittura
    definisce un circuito e registra un fatto, le somme si rifanno a evento scritto: il fatto vale dall'istante di
    scrittura, non da quello di arrivo. Un accredito arrivato alle 9 e scritto alle 11 non copre un ordine arrivato
    alle 10.
  - **Una domanda ha un istante solo**, quello che la risposta dichiara: il circuito si legge lì, non all'orologio
    di un momento dopo. `valore`, `valori` e la lingua leggono lo stesso adesso.
  - **La definizione si controlla anche nella prova**: una regola che non si può scrivere non «si potrebbe
    scrivere».
  - **Un'istantanea vale se prova di contenere solo neuroni scritti nel giornale.** Quelle di prima non lo dicono:
    non si usano, si rilegge il giornale, e il giornale non si tocca.
  - **Un'istantanea porta il suo sigillo**, l'impronta di tutto il suo contenuto, oltre all'impronta del giornale a
    cui si riferisce. Rovinata, troncata o senza sigillo non si usa e non impedisce di aprire: si rilegge il
    giornale, che ha un controllo per ogni record.
  - **L'impronta del giornale che l'istantanea porta è quella dei byte che la rete ha riletto o scritto**, non quella
    del file com'è nel momento in cui la salva. Se il giornale è cambiato sotto la rete (un byte rovinato dal
    supporto, un file rimesso a mano) l'istantanea non si salva e la rete smette di scrivere: va riaperta, e un
    giornale con un record guasto nel mezzo non si apre. Un'istantanea non rende buono un giornale guasto.
  - **Il valore di un circuito è zero solo per un nome mai visto.** Un nome che indica più entità non ha un valore:
    la risposta è «ambiguo», con i candidati (`neurone:17`), e si chiede di nuovo dicendo quale. Un identificativo
    che non c'è e un circuito che manca sono errori, non zero.
  - **Una scrittura che non avviene non lascia tracce, nemmeno nell'ordine.** Dopo una prova, un rifiuto o un errore
    del disco ogni entità è al suo posto sulle linee e nelle somme, e la forma di ogni tipo di fatto è quella di
    prima: la stessa domanda dà le stesse righe nello stesso ordine, come a chi riapre dal giornale. Lo stesso dopo
    una lettura, che mette da parte per un momento i fatti che adesso non valgono.
  - **Un fatto che non può entrare intero è rifiutato prima di toccare la rete**: troppi legami (il massimo è 127), un
    legame che non è una coppia di neuroni che ci sono, un istante che non è un numero finito. Un tipo di fatto deve
    essere tra i primi 2.097.152 neuroni della rete: è un limite di oggi, dichiarato e controllato; ruoli e
    partecipanti non ce l'hanno.
  - **Un record guasto in mezzo al giornale non è una coda scritta a metà.** Una coda a metà è un record incompleto
    in fondo, o dello spazio mai scritto: è una scrittura mai confermata, e chi apre per scrivere la taglia. Un record
    intero che non si rilegge, o qualunque cosa sia seguita più avanti da un record buono, è un guasto: il giornale
    non si apre, né per scrivere né per leggere, finché non viene ripristinato. Tagliare da lì toglierebbe scritture
    confermate; leggere solo fin lì le nasconderebbe. Alla lunghezza che un record guasto dichiara non si crede: può
    essere proprio lei quella rovinata. Tutti quelli che camminano sul giornale (chi apre, chi verifica, chi conta gli
    eventi, chi costruisce la rete sul disco) riconoscono un record allo stesso modo e si fermano nello stesso punto;
    che cosa c'è dopo lo giudica chi apre e chi costruisce la rete sul disco, gli altri contano fin lì. Il giornale
    non lega un record al precedente: due record scambiati o uno ripetuto non vengono riconosciuti. È un limite di oggi.
  - **Uno solo scrive, e lo si controlla.** Chi apre per scrivere prende il giornale per sé finché non lo chiude; un
    secondo programma è rifiutato all'apertura. Chi legge soltanto non prende niente. Un tratto si attesta a giornale
    fermo. Una rete chiusa non scrive più e non salva istantanee. Un programma di prima, che il giornale non lo prende,
    non viene fermato: ma prima di ogni scrittura si controlla che il giornale finisca dove lo si era lasciato, e se
    non è così la rete si ferma invece di confermare un evento con un numero che il giornale non le dà.
  - **L'ordine delle righe non dipende dalla storia.** Le righe di una domanda escono nell'ordine in cui i fatti
    stanno sulle linee, che è lo stesso per la rete viva e per quella riaperta; la lettura di un circuito dà le entità
    in ordine di nascita; una condizione su un numero dà i valori in ordine di grandezza. Con un limite, la rete viva
    e quella riaperta danno le stesse righe, e una domanda tenuta da parte le stesse di una preparata adesso. La rete
    sul disco ha un ordine suo, per numero di neurone.
  - **Una chiave con la nota rovinata non è una chiave assente.** Se nel giornale l'evento di una chiave non ha più
    l'impronta del racconto o la ricevuta, la memoria si apre lo stesso; ma chi ripete quella chiave non viene valutato
    di nuovo, perché potrebbe scrivere due volte, e la verifica di quell'evento risponde «guasto».
  - **L'istante di scrittura non torna indietro**, nemmeno se chi chiama ne passa uno: una scrittura con un istante
    più vecchio di quello dell'ultimo evento è rifiutata. La frase e l'origine di un evento sono testi, e un nome che
    il giornale non sa scrivere non nasce.
  - **Un giornale che registra le decisioni si scrive sempre con le decisioni**, anche se chi lo apre questa volta
    non lo dice: da dove comincia il segno non resta mai un evento senza ricevuta.
  - **Una memoria che non corrisponde più al giornale non risponde a niente**: non legge, non prova, non dice «già
    scritto». Chi la tiene aperta la riapre dal giornale. Il servizio del fido lo fa da solo, e a chi aveva chiesto
    l'operazione rimasta a metà risponde che l'esito lo dice il giornale: ripetendo con la stessa chiave.
  - **Una ricevuta rovinata è un guasto segnalato**, non un errore di chi verifica; e nella ricerca delle decisioni
    da riesaminare è uno degli eventi in cui non si può vedere, non la fine della ricerca.
  - **Una regola non sa ancora leggere un circuito raggruppato.** Quella definizione viene rifiutata, in tutti e due
    i versi (la regola dopo il circuito, o un termine raggruppato aggiunto a un circuito che una regola legge già),
    invece di decidere su una somma che non è la sua. Prima di aprirla va deciso a quale mese si riferisce il
    segnale, e che cosa succede quando un fatto viene spostato da un mese a un altro.

## 10. I casi che il codice dovrà superare

Stesso cliente, stessa regola, stessa versione, salvo dove è detto.

| N. | Situazione | Atteso |
|---|---|---|
| 1 | Lunedì fido 800 €, richiesta da 1.000 € | no; la ricevuta dichiara 800 € prima e −200 € dopo |
| 2 | Martedì pagamento A di 400 € valido da domenica; riesame di lunedì **con la sola A** | sì ipotetico: 1.200 € prima, +200 € dopo |
| 3 | Mercoledì rettifica B di −500 € valida da domenica; riesame di lunedì **con A e B** | no ipotetico: 700 € prima, −300 € dopo |
| 4 | Giovedì si ripete il riesame del caso 2 | risposta identica byte per byte, con l'elenco degli eventi usati: non il saldo di oggi |
| 5 | Subito prima e subito dopo ogni riesame | ricevute, fatti e fido reale identici; i pagamenti veri invece cambiano il fido |
| 6 | Fido 1.200 €, ordine da 1.000 € accettato; riesame senza rettifiche | sì, e gli stessi due valori della ricevuta: 1.200 € prima, +200 € dopo. L'ordine non è contato due volte |
| 7 | Due richieste da 1.000 € rifiutate con 800 €, poi A; riesame per decisione | due sì ipotetici, e la risposta dichiara «per decisione» |
| 8 | Pagamento di 400 € valido da domenica e reso di 400 € valido da martedì: il valore di oggi non cambia; riesame di lunedì con entrambi | la decisione di lunedì è candidata; sì ipotetico |
| 9 | Decisione presa mercoledì; poi si registra un pagamento valido da domenica | la decisione di mercoledì è candidata |
| 10 | Un pagamento attribuito a C viene spostato a D | sono candidate le decisioni di C e quelle di D, anche se D non aveva pagamenti |
| 11 | Riesame con uno storno indicato e il suo pagamento né nella base né nell'elenco | «scenario incompleto» |
| 12 | Arresto del processo durante una scrittura accettata | scrittura e ricevuta ci sono entrambe oppure nessuna; se chi ha scritto aveva già ricevuto la conferma, devono esserci entrambe |
| 13 | Stessa chiave dopo il riavvio e dopo un cambio di stato | la ricevuta di allora, nessun evento nuovo |
| 14 | `verifica` su un no la cui regola legge lo stato con la proposta | nessun guasto: ritrova −200 €, non 800 € |
| 15 | `verifica` su una decisione letta «come risultava», dopo una rettifica retrodatata | nessun guasto |
| 16 | Su una copia di prova si toglie dalla ricevuta di un sì una delle due regole consultate | `verifica` fallisce, anche se l'esito è lo stesso |
| 17 | Errore di lettura durante la valutazione | nessun commit, errore dichiarato |

Gli stessi casi vanno dati anche al sistema con cui ci si confronta, libero di tenere lo storico e le somme come
preferisce. Prima si controllano gli esiti e le ricevute dopo il riavvio; poi si confrontano i tempi, le scritture
al secondo, i byte per decisione e il costo per trovare le candidate.

## 11. Decisioni prese nella revisione

1. **Una richiesta retrodatata è valutata all'istante in cui arriva.** La ricevuta lo dichiara.
2. **Le rettifiche del riesame si indicano con gli identificativi degli eventi.** «Fino all'evento N» è una comodità
   e la risposta elenca sempre gli eventi usati.
3. **Il peso della ricevuta dei sì si misura prima di fissare la forma su disco**, insieme al costo del riesame.
   Quello che il paragrafo 4 dice di non togliere non si toglie.

## 12. Che cosa c'è già nel codice

Dal passo B, nella libreria della memoria (`memoria.py`, `motore.py`), che non è collegata al server:

- **La ricevuta di ogni decisione**, sì o no, nello stesso record del giornale: base, istante, regole consultate
  anche se non sono scattate, con versione, attivazione, soglia e verdetto, e le letture con il loro valore. Un
  rifiuto conserva anche la proposta.
- **`verifica`**, per i sì e per i no: ricostruisce la base dal solo giornale, riapplica la richiesta una volta sola,
  rivaluta le regole di allora e confronta regole, letture, valori ed esito. Segnala anche una regola che manca
  dalla ricevuta.
- **Ripetere con la stessa chiave** restituisce la ricevuta di allora.

I casi 12, 13, 14 e 16 del paragrafo 10 sono nei test (`tests/test_decisioni.py`, `tests/test_memoria.py`); il caso
12 solo per l'arresto a metà della scrittura. Dalla revisione indipendente del codice vengono altri
sei controlli, anch'essi nei test: due entità con lo stesso nome, l'esito e l'evento dichiarati dalla ricevuta, la
decisione tolta dalla ricevuta, l'istante preso all'arrivo e non al turno, la stessa risposta con e senza istantanea.

**Il peso misurato.** Una scrittura accettata con due regole consultate e quattro letture: 1.177 byte di ricevuta su
un record che senza ne ha 122. Senza regole consultate la ricevuta sta sotto i 160 byte. È la forma più semplice e
non è compressa: la base e il momento di validità sono ripetuti in ogni lettura. Resta così come riferimento fino
alla prova del fido, che misura byte per decisione, scritture al secondo e costo della verifica con e senza chiave
e con nessuna, una e molte letture. La forma su disco si sceglie dopo.

Dal passo C:

- **Un circuito mantenuto** è una somma tenuta pronta per ogni entità, scritta nella rete come le regole: per ogni
  termine dice quale tipo di fatto osserva, di chi è, quale ruolo somma e con quale peso. Il fido residuo è «fido
  accordato, meno gli ordini, più i pagamenti».
- **Lo stesso commit** che scrive o ritira un fatto aggiorna la somma. Un no e una prova la lasciano com'era.
- **Una regola può leggere un circuito**: si accende se il valore è sotto un limite. È consultata per ogni entità di
  cui la scrittura sposta il valore, qualunque sia il fatto che lo sposta. La ricevuta dichiara il valore prima
  della richiesta e quello dopo, con il circuito e la sua versione.
- **Il controllo dei circuiti** rifà le somme dai fatti in memoria e da una rete ricostruita dal solo giornale, e
  segnala ogni differenza. Dice fino a quale evento ha controllato: se il giornale è già più avanti risponde
  «indietro», che non è una somma sbagliata. Rifà la stessa somma: trova un circuito che ha perso il passo, non un
  errore di aritmetica, che è compito di un riferimento indipendente nella prova del fido.
- In una memoria **ogni evento porta la sua decisione**, anche una regola o un circuito scritti direttamente nel
  motore: non risultano più guasti solo perché non hanno una ricevuta.

I casi 1 e 6 del paragrafo 10 sono nei test con i loro numeri (`tests/test_circuiti.py`): 800 € prima e −200 € dopo
per il no, 1.200 € prima e +200 € dopo per il sì. C'è anche una prova con mille scritture, correzioni, rifiuti,
prove e riavvii a caso, confrontata a ogni passo con un modello in centesimi interi tenuto fuori dal motore. Dalla
revisione indipendente vengono i controlli sull'esattezza (un centesimo di scoperto è un no anche se chi
chiama usa quattro cifre di precisione; ventisette cifre intere; i valori fuori dominio) e sull'atomicità di una
definizione sbagliata.

**Misure indicative**, sulla macchina di sviluppo, dentro il programma, con trenta fatti per cliente: leggere il
fido dal circuito costa meno di 1 µs; rifare lo stesso numero con tre somme sui fatti ne costa più di 20. Una
scrittura passa da 21 a 43 µs con il circuito e la regola consultata, senza contare la sincronizzazione su disco.

Dal passo D:

- **Il periodo di validità.** Un fatto può dichiarare `vale da` e `vale fino a`, con date intere; la fine è esclusa.
  Senza dichiarazione vale dall'istante in cui è scritto nel giornale; mentre la sua richiesta è in valutazione
  vale dall'istante che si sta valutando. Un periodo scritto male fa rifiutare il racconto, e anche una scadenza
  che viene prima dell'istante di scrittura: il periodo sarebbe vuoto. Il periodo decide se un fatto si somma, non se
  il suo dato è buono: un importo che non è un numero è rifiutato anche se il fatto vale solo nel futuro.
- **Il circuito tiene il valore che vale adesso.** Una decorrenza o una scadenza lo cambiano anche se nessuno
  scrive: alla lettura e alla decisione, se nel frattempo è passato un confine, le somme si rifanno dai fatti.
  Non c'è un indice ordinato per validità: si aggiunge se una misura lo chiede.
- **Una regola vede gli stessi fatti di una lettura fatta allo stesso istante.** Anche i segnali di presenza e di
  assenza guardano solo i fatti che valgono all'istante della decisione.
- **Due valutazioni** quando una richiesta è servita dopo il suo arrivo, come dice il paragrafo 2. `verifica` le
  rifà tutte e due.
- **Le letture nella lingua** (vedi `docs/lingua.md`): senza clausola una memoria risponde con quello che vale
  adesso; `come è registrato` mostra tutto quello che è registrato; `come risultava il …` guarda quello che a
  quell'istante era già scritto, usando l'istante di scrittura; `come risultava all'evento …`; `come valeva il …`.
  La risposta dice sempre quale lettura ha fatto. La domanda senza clausola, `come valeva adesso` e il circuito
  usano lo stesso filtro, anche per i fatti che non dichiarano un periodo.
- **Le somme della lingua** non dipendono dalla precisione decimale di chi chiama, come quelle dei circuiti.

Nei test (`tests/test_validita.py`): il pagamento registrato martedì e valido da domenica; un anticipo valido solo
martedì che porta il fido da 800 a 1.200 e di nuovo a 800 senza scritture, anche dopo una riapertura; la richiesta
rimasta in coda; due ordini da 600 su un fido di 800 serviti in ordine inverso, di cui il secondo è rifiutato; una
sospensione scaduta che la regola non vede più; un pagamento arrivato alle 9 e scritto alle 11 che alle 10 non
risultava; trecento pagamenti con periodi a caso confrontati, a istanti a caso avanti e indietro, con un conto in
centesimi interi fatto fuori dal motore.

Dal passo E0:

- **Il riesame** (`Memoria.riesamina`), come dice il paragrafo 6. Lo scenario si rifà dal giornale: la base della
  decisione più le sole rettifiche indicate, prese come eventi interi. Gli eventi lasciati fuori non ci sono, nemmeno
  quello della decisione: la richiesta si applica una volta sola. Le regole e gli istanti sono quelli di allora. La
  risposta porta l'esito e le letture di allora, quelli ipotetici, gli eventi usati e quelli esclusi, e dichiara
  «per decisione». Ripetuto, è identico.
- **Una rettifica conta se vale all'istante della decisione.** Un pagamento registrato dopo e valido da prima sì;
  uno che non dichiara un periodo no, perché vale da quando è scritto.
- **Lo scenario incompleto.** Se una rettifica ritira un fatto scritto da un evento che non è nella base né tra
  quelli indicati, il riesame si ferma e lo dice. Vale per l'evento intero: uno spostamento da un cliente a un altro
  è un evento solo, e va indicato insieme al pagamento che sposta.
- **Le candidate** (`Memoria.candidate`), come dice il paragrafo 7: dalle letture registrate nelle ricevute, per
  entità e istante, contro il periodo di validità e i partecipanti dei fatti che la rettifica ha scritto o ritirato.
  Anche una scrittura accettata è una decisione, e può essere candidata. La risposta dice la sua copertura.
- **Le ricevute scritte prima** si verificano e si riesaminano nella loro forma, e si ritrovano tra le candidate.
  In `tests/dati` ci sono quattro giornali scritti davvero da quattro revisioni del ramo, con la stessa storia:
  `tests/test_forme_storiche.py` controlla che ogni loro ricevuta resti coerente, e che una ricevuta manomessa, o
  con due convenzioni mescolate, non coincida con nessuna forma.
- **Una rettifica è una scrittura come le altre**: se manda un fido sotto zero è rifiutata anche lei.

Nei test (`tests/test_riesame.py`) ci sono i casi da 1 a 11 del paragrafo 10, e il confronto chiesto dai revisori:
su sei clienti e una settimana di ordini, per ognuna di dodici rettifiche a caso si riesaminano tutti gli eventi
precedenti, uno per uno e senza indice; quelli in cui cambia una lettura o l'esito sono tutti tra le candidate, e
gli esiti cambiati sono gli stessi. Il confronto si ripete dopo aver tolto un termine al circuito e averne aggiunto
un altro, e dopo una riapertura: deve dare lo stesso risultato.

**Manca ancora:** la storia alternativa, in cui un sì ipotetico consuma il fido per le richieste dopo; la verifica con
un errore di lettura, i circuiti che leggono altri circuiti. Per gli eventi
scritti prima che l'istante di scrittura venisse registrato vale l'istante di arrivo: «come risultava all'evento»
resta esatto. Ogni lettura del passato rifà la rete dal giornale, e alla riapertura le somme si rifanno dai fatti: i
due tempi crescono con i dati. Anche il riesame rifà la rete dal giornale fino all'ultima rettifica, e la ricerca dei
fatti ritirati da una rettifica passa tutti i fatti: sono costi da misurare nella prova del fido. La ricerca delle
candidate è larga: quante ne propone che al riesame non cambiano è una misura a parte. Il segno durevole sta in un
file accanto al giornale: chi cancella quel file riporta il
giornale alla regola della posizione. Contro una manomissione fatta apposta serve il controllo esterno.
