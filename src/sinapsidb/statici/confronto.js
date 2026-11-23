// Pagina del confronto tra PostgreSQL, Redis e SinapsiDB.
// Legge /api/confronto e lancia i lavori (genera, azzera, importa, misura, redis, svuota) con POST /api/confronto/<lavoro>.
// La casella delle domande manda la frase a POST /api/confronto/chiedi e mostra risposta, piano e tempi.
// Tutto il testo passa da textContent: nessun HTML costruito da stringhe.
(() => {
  "use strict";
  const NS = "http://www.w3.org/2000/svg";
  const LAVORI = {
    genera: "Generazione dei dati in PostgreSQL", azzera: "Azzeramento della rete di prova", importa: "Importazione da PostgreSQL",
    misura: "Confronto", redis: "Preparazione delle risposte in Redis", svuota: "Pulizia di Redis",
    apertura: "Riapertura della rete di prova", // lo fa il portale da solo quando parte: con molti dati dura un minuto
    disco: "Passaggio della rete sul disco", memoria: "Ritorno della rete in memoria",
  };
  const PULSANTI = ["genera", "importa", "azzera", "misura", "redis", "svuota", "dove"];
  // che cosa si conta mentre un lavoro avanza
  const CONTEGGI = { genera: "record di circa", importa: "fatti di", redis: "righe di al massimo", disco: "fatti di" };
  // dove vive la rete di prova: il pulsante propone l'altro modo
  const DOVE = { memoria: "Tieni in memoria solo quello che serve", disco: "Riporta tutta la rete in memoria" };
  // i tre sistemi, sempre in quest'ordine e sempre con il loro colore
  // Redis si chiama sempre per quello che è qui: una cache di risposte preparate prima, non un database a cui si chiede
  const SISTEMI = { sinapsi: "SinapsiDB", postgres: "PostgreSQL", redis: "Redis (cache)" };
  const nf0 = new Intl.NumberFormat("it-CH", { maximumFractionDigits: 0, useGrouping: "always" });
  const nf1 = new Intl.NumberFormat("it-CH", { maximumFractionDigits: 1 });
  const nf2 = new Intl.NumberFormat("it-CH", { maximumFractionDigits: 2 });
  const fQuando = new Intl.DateTimeFormat("it-CH", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", second: "2-digit" });
  const $ = (id) => document.getElementById(id);
  let stato = null;
  let attesa = 0;
  let aiutoPer = -1; // per quanti fatti sono stati chiesti esempi e vocabolario
  const conferme = {}; // pulsante -> timer della conferma in corso

  function el(tag, attributi, ...figli) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attributi || {})) {
      if (v == null || v === false || v === "") continue;
      if (k === "text") e.textContent = v;
      else if (k === "class") e.className = v;
      else e.setAttribute(k, v);
    }
    for (const f of figli.flat(2)) if (f != null && f !== false) e.append(f);
    return e;
  }
  function sv(tag, attributi) {
    const e = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attributi || {})) e.setAttribute(k, String(v));
    return e;
  }
  function icona(tipo) {
    const svg = sv("svg", { width: 14, height: 14, viewBox: "0 0 16 16", "aria-hidden": "true", class: "icona" });
    if (tipo === "buono") {
      svg.append(sv("circle", { cx: 8, cy: 8, r: 7, class: "ic-buono" }), sv("path", { d: "M4.8 8.3 7 10.4 11.2 5.9", class: "ic-segno-chiaro" }));
    } else if (tipo === "critico") {
      svg.append(sv("circle", { cx: 8, cy: 8, r: 7, class: "ic-critico" }), sv("path", { d: "M5.5 5.5l5 5M10.5 5.5l-5 5", class: "ic-segno-chiaro" }));
    } else {
      svg.append(sv("path", { d: "M8 1.2 15.2 14.3H.8z", class: "ic-attenzione" }), sv("path", { d: "M8 6v3.8M8 12v.1", class: "ic-segno-scuro" }));
    }
    return svg;
  }
  // Un tempo in microsecondi, scritto nell'unità che si legge meglio.
  function tempo(us) {
    if (us == null) return "—";
    if (us < 10) return `${nf2.format(us)} µs`;
    if (us < 1000) return `${nf1.format(us)} µs`;
    if (us < 1e6) return `${nf2.format(us / 1000)} ms`;
    return `${nf2.format(us / 1e6)} s`;
  }
  const volte = (x) => (x >= 10 ? nf0.format(x) : nf1.format(x));
  // Da quanto dura un lavoro, in parole: 45 secondi, 3 minuti e 20 secondi.
  function durata(secondi) {
    const s = Math.max(0, Math.round(secondi));
    return s < 60 ? `${s} secondi` : `${Math.floor(s / 60)} min ${String(s % 60).padStart(2, "0")} s`;
  }
  // Una quantità di memoria in byte, in MB o in GB.
  const memoria = (b) => (b < 1024 ** 2 ? `${nf0.format(b / 1024)} KB` : b < 1024 ** 3 ? `${nf0.format(b / 1024 ** 2)} MB` : `${nf2.format(b / 1024 ** 3)} GB`);
  function elenco(coppie) {
    const dl = el("dl");
    for (const [nome, valore] of coppie) dl.append(el("dt", { text: nome }), el("dd", { text: valore }));
    return dl;
  }
  const messaggio = (tipo, testo) => el("p", { class: "messaggio" }, icona(tipo), el("span", { text: testo }));
  // Sostituisce il contenuto di un nodo, saltando i pezzi che non ci sono (replaceChildren scriverebbe «null»).
  const metti = (nodo, ...figli) => nodo.replaceChildren(...figli.flat(2).filter((f) => f != null && f !== false));

  // --- stato dei due database ------------------------------------------------

  function rendiTaglie() {
    const scelta = $("taglia");
    if (scelta.options.length || !stato.taglie.length) return; // si costruisce una volta sola: la scelta fatta resta
    const ultima = stato.postgres.generazione ? stato.postgres.generazione.righe_richieste : 0;
    const predefinita = stato.taglie.includes(ultima) ? ultima : stato.taglia_proposta;
    for (const t of stato.taglie) {
      const o = el("option", { value: String(t), text: `${nf0.format(t)} record` });
      o.selected = t === predefinita;
      scelta.append(o);
    }
  }

  function rendiPostgres(pg, generando) {
    if (!pg.raggiungibile) {
      metti($("stato-pg"), messaggio("critico", `PostgreSQL non risponde: ${pg.errore || "motivo non noto"}`));
      return;
    }
    const pezzi = [];
    if (pg.disponibile) {
      pezzi.push(el("p", { class: "totale-pg" }, nf0.format(pg.totale), el("span", { class: "unita", text: `record in ${pg.tabelle.length} tabelle` })));
      const g = pg.generazione;
      pezzi.push(el("p", { class: "secondario", text: g
        ? `Generati il ${fQuando.format(new Date(g.quando * 1000))} in ${nf1.format(g.secondi)} secondi · seme ${g.seme} · PostgreSQL ${pg.versione}`
        : `PostgreSQL ${pg.versione}` }));
      pezzi.push(el("ul", { class: "tabelle-pg", "aria-label": "Record per tabella" },
        pg.tabelle.map((t) => el("li", {}, el("span", { class: "nome", text: t.etichetta }), el("span", { class: "quante", text: nf0.format(t.righe) })))));
    } else if (generando) {
      pezzi.push(el("p", { class: "secondario", text: "Generazione in corso: i conteggi arrivano a lavoro finito." }));
    } else {
      pezzi.push(messaggio("attenzione", `${pg.errore || "I dati non ci sono"}. Scegli quanti record e premi «Genera i dati in PostgreSQL».`));
    }
    if (!pg.puo_scrivere) {
      pezzi.push(messaggio("attenzione", "Il portale non ha il permesso di scrivere in questa base di dati, quindi non può generare i dati: lo deve dare chi amministra."));
    }
    metti($("stato-pg"), pezzi);
  }

  function rendiRedis(re, pg, preparando) {
    if (!re.configurato) {
      metti($("stato-redis"), messaggio("attenzione", "Redis non è collegato a questo portale: il confronto si fa solo con PostgreSQL. Lo collega chi installa."));
      return;
    }
    if (!re.raggiungibile) {
      metti($("stato-redis"), messaggio("critico", `Redis non risponde: ${re.errore || "motivo non noto"}`));
      return;
    }
    if (preparando) {
      metti($("stato-redis"), el("p", { class: "secondario", text: "Preparazione in corso: i conteggi arrivano a lavoro finito." }));
      return;
    }
    const c = re.caricamento;
    const come = `Redis ${re.versione}, raggiunto ${re.via === "socket" ? "dal socket locale" : "da una porta di rete locale"}`;
    const lento = re.lettore_c ? null
      : messaggio("attenzione", "Al cliente di Redis manca il lettore in C (hiredis): i tempi di Redis visti dal programma sono peggiori del dovuto. Lo installa chi amministra.");
    // quanta memoria serve per i dati che ci sono ora, detto prima di provarci
    const stima = re.memoria_stimata == null || (c && re.allineato) ? null
      : re.memoria_basta ? el("p", { class: "secondario", text: `Per i dati che ci sono ora in PostgreSQL le risposte pronte occupano circa ${memoria(re.memoria_stimata)}.` })
        : messaggio("attenzione", `Per i dati che ci sono ora in PostgreSQL le risposte pronte occupano circa ${memoria(re.memoria_stimata)}: a Redis ne sono concessi ${memoria(re.memoria_tetto)} e non bastano. Il tetto lo alza chi amministra.`);
    const inTutto = `${memoria(re.memoria_usata)}${re.memoria_tetto ? ` su ${memoria(re.memoria_tetto)} concessi` : ", senza un tetto"}`;
    if (!c) {
      metti($("stato-redis"),
        messaggio("attenzione", pg.disponibile ? "In Redis non ci sono risposte pronte: senza, il confronto si fa solo con PostgreSQL."
          : "In Redis non ci sono risposte pronte. Prima vanno generati i dati in PostgreSQL."),
        el("p", { class: "secondario", text: `${come} · memoria usata ${inTutto}` }), stima, lento);
      return;
    }
    metti($("stato-redis"),
      elenco([["Chiavi", nf0.format(re.chiavi)], ["Righe di risposta", nf0.format(c.righe)],
        ["Memoria delle risposte", memoria(re.memoria_risposte)], ["Memoria di Redis in tutto", inTutto]]),
      el("p", { class: "secondario", text: `Preparate in ${nf1.format(c.secondi)} secondi, ${fQuando.format(new Date(c.quando * 1000))} · ${come}` }),
      lento, stima,
      re.allineato ? null
        : messaggio("attenzione", re.superate
          ? "Queste risposte sono state preparate quando il confronto aveva una domanda in meno: il confronto non le usa. Vanno preparate di nuovo."
          : "Queste risposte sono dei dati di prima, non di quelli che ci sono ora in PostgreSQL: il confronto non le usa. Vanno preparate di nuovo."));
  }

  function rendiStato() {
    const pg = stato.postgres;
    const si = stato.sinapsi;
    const re = stato.redis;
    const lavoro = stato.lavoro;
    const occupato = lavoro.in_corso;
    rendiTaglie();
    rendiPostgres(pg, occupato && lavoro.nome === "genera");
    rendiRedis(re, pg, occupato && lavoro.nome === "redis");

    const vuota = !si || !si.fatti;
    if (occupato) {
      metti($("stato-sinapsi"), el("p", { class: "secondario", text: "Lavoro in corso: i conteggi tornano a lavoro finito." }));
    } else if (si && si.errore) {
      metti($("stato-sinapsi"), messaggio("critico", `La rete di prova non si apre: ${si.errore}`));
    } else if (vuota) {
      metti($("stato-sinapsi"), messaggio("attenzione", "La rete di prova è vuota."));
    } else {
      const imp = stato.importazione;
      const dove = stato.rete;
      const sulDisco = dove.modo === "disco";
      metti($("stato-sinapsi"),
        elenco([["Fatti", nf0.format(si.fatti)], ["Neuroni", nf0.format(si.neuroni)], ["Sinapsi", nf0.format(si.sinapsi)], ["Eventi", nf0.format(si.eventi)],
          ...(sulDisco
            ? [["Dove sta", "sul disco"], ["Sul disco, la memoria profonda", memoria(dove.su_disco)],
              ["In memoria adesso, la memoria veloce", `${memoria(dove.in_memoria)} su ${memoria(dove.tetto)} al massimo`]]
            : [["Dove sta", "tutta in memoria"]]),
          ["Memoria del portale", memoria(dove.portale)]]),
        imp ? el("p", { class: "secondario", text: `Importata in ${nf1.format(imp.secondi)} secondi, ${fQuando.format(new Date(imp.quando * 1000))}` }) : null,
        sulDisco && dove.costruzione
          ? el("p", { class: "secondario", text: `Portata sul disco in ${nf1.format(dove.costruzione.secondi)} secondi. In memoria entrano i pezzi di rete che una domanda tocca, ed escono quelli usati meno di recente: finora ne sono entrati ${nf0.format(dove.caricati)} e usciti ${nf0.format(dove.espulsi)}.` })
          : null,
        pg.disponibile && si.fatti !== pg.totale
          ? messaggio("attenzione", "La rete di prova non corrisponde ai dati che ci sono ora in PostgreSQL: va azzerata e importata di nuovo.") : null);
    }

    const c = occupato && lavoro.nome === "misura" ? null : stato.confronto;
    const modoMisurato = c && c.rete ? c.rete.modo : "memoria"; // i confronti di prima non lo dicevano: erano tutti in memoria
    metti($("stato-confronto"), occupato && lavoro.nome === "misura"
      ? el("p", { class: "secondario", text: "Confronto in corso: i risultati di prima sono stati tolti, quelli nuovi arrivano quando finisce." })
      : c
      ? el("p", { class: "secondario", text: `Ultimo confronto: ${fQuando.format(new Date(c.quando * 1000))}, ${c.domande.length} domande su ${nf0.format(c.fatti)} fatti, ${c.redis ? "con PostgreSQL e con la cache Redis" : "con PostgreSQL"}, con la rete di SinapsiDB ${modoMisurato === "disco" ? "sul disco" : "tutta in memoria"}.` })
      : el("p", { class: "secondario", text: "Nessun confronto ancora." }),
      c && !c.redis && re.allineato ? messaggio("attenzione", "Nell'ultimo confronto Redis non c'era ancora: lancialo di nuovo per averlo nelle colonne.") : null,
      c && !vuota && modoMisurato !== stato.rete.modo
        ? messaggio("attenzione", `L'ultimo confronto è stato fatto con la rete ${modoMisurato === "disco" ? "sul disco" : "tutta in memoria"}, e adesso è ${stato.rete.modo === "disco" ? "sul disco" : "tutta in memoria"}: lancialo di nuovo per misurarla com'è.`) : null);

    $("genera").disabled = occupato || !pg.raggiungibile || !pg.puo_scrivere;
    $("taglia").disabled = $("genera").disabled;
    $("importa").disabled = occupato || !pg.disponibile || !vuota;
    $("azzera").disabled = occupato || (vuota && !(si && si.errore)); // una rete che non si apre si rimedia azzerandola
    $("misura").disabled = occupato || !pg.disponibile || vuota;
    $("dove").disabled = occupato || vuota;
    if (!conferme.dove) $("dove").textContent = DOVE[stato.rete.modo] || DOVE.memoria;
    $("redis").disabled = occupato || !re.raggiungibile || !pg.disponibile;
    $("svuota").disabled = occupato || !re.raggiungibile || !re.caricamento;
    $("chiedi").disabled = occupato || vuota;
    $("domanda").disabled = occupato || vuota;
    if (!occupato) caricaAiuto(vuota ? 0 : si.fatti);
    for (const id of Object.keys(conferme)) if ($(id).disabled) annullaConferma(id);

    const riga = $("lavoro");
    const testo = $("lavoro-testo");
    const barra = $("lavoro-barra");
    barra.hidden = true;
    riga.classList.toggle("in-corso", occupato); // finché dura resta in vista in fondo allo schermo, ovunque si sia nella pagina
    if (occupato) {
      riga.hidden = false;
      // i numeri li scrive la pagina, con le stesse migliaia del resto del portale. Dove il lavoro conta delle cose
      // (record, fatti, righe) si dice quante; dove conta dei passi, come il confronto, lo dice già il testo
      const conteggio = lavoro.da_fare && CONTEGGI[lavoro.nome]
        ? ` · ${nf0.format(lavoro.fatto || 0)} ${CONTEGGI[lavoro.nome]} ${nf0.format(lavoro.da_fare)}` : "";
      const percento = lavoro.da_fare ? ` · ${nf0.format((100 * Math.min(lavoro.fatto || 0, lavoro.da_fare)) / lavoro.da_fare)}%` : "";
      const da = lavoro.iniziato ? ` · da ${durata(Date.now() / 1000 - lavoro.iniziato)}` : "";
      metti(testo, el("strong", { text: `${LAVORI[lavoro.nome] || lavoro.nome} in corso.` }), ` ${lavoro.avanzamento}${conteggio}${percento}${da}`);
      barra.hidden = false;
      if (lavoro.da_fare) {
        barra.max = lavoro.da_fare;
        barra.value = Math.min(lavoro.fatto || 0, lavoro.da_fare);
      } else {
        barra.removeAttribute("value"); // non si sa quanto manca: la barra scorre, e dice che il lavoro è vivo
      }
    } else if (lavoro.errore) {
      riga.hidden = false;
      testo.replaceChildren(icona("critico"), el("span", { text: `${LAVORI[lavoro.nome] || lavoro.nome} non riuscito: ${lavoro.errore}` }));
    } else if (lavoro.nome) {
      riga.hidden = false;
      const secondi = lavoro.finito && lavoro.iniziato ? ` in ${nf1.format(lavoro.finito - lavoro.iniziato)} secondi` : "";
      testo.replaceChildren(icona("buono"), el("span", { text: `${LAVORI[lavoro.nome] || lavoro.nome} finito${secondi}.` }));
    } else {
      riga.hidden = true;
    }
  }

  // --- risultati -------------------------------------------------------------

  // I sistemi misurati in una domanda con il loro tempo mediano, nell'ordine fisso: Redis c'è solo se era nel confronto.
  const misurati = (d) => Object.keys(SISTEMI).filter((s) => d[s]).map((s) => [s, d[s].p50_us]);
  // Gli stessi dal più rapido: a parità di tempo resta davanti chi viene prima nell'ordine fisso.
  const classifica = (d) => misurati(d).sort((a, b) => a[1] - b[1]);
  function vincitore(d) {
    const [[chi, migliore], [, secondo]] = classifica(d);
    const stacco = secondo / migliore; // sotto il dieci per cento non è un distacco: cambia da un lancio all'altro
    return el("span", { class: "vincitore" }, el("span", { class: `chiave-linea chiave-${chi}` }),
      `${SISTEMI[chi]}, ${stacco < 1.1 ? "di poco" : `${volte(stacco)} volte più rapido`}`);
  }
  function tabella(colonne, righe) {
    const testa = el("tr");
    for (const c of colonne) testa.append(el("th", { class: c.num ? "num" : "", scope: "col", text: c.nome }));
    const corpo = el("tbody");
    for (const r of righe) {
      const tr = el("tr");
      for (const c of colonne) {
        const v = c.valore(r);
        const td = el("td", { class: c.num ? "num" : "", "data-nome": c === colonne[0] ? "" : c.nome });
        if (v instanceof Node) td.append(v);
        else td.textContent = v == null || v === "" ? "—" : String(v);
        tr.append(td);
      }
      corpo.append(tr);
    }
    return el("div", { class: "scorri" }, el("table", {}, el("thead", {}, testa), corpo));
  }
  function riquadro(etichetta, valore, nota, tipo) {
    return el("div", { class: "tile" }, el("div", { class: "etichetta", text: etichetta }), el("div", { class: "valore", text: valore }),
      el("div", { class: "nota" }, tipo ? icona(tipo) : null, nota));
  }

  // --- quante volte più rapido ---------------------------------------------------
  // La media dei tempi non dice chi è più rapido, perché la fa la domanda più lenta. Per ogni domanda si divide il tempo dell'altro per quello di SinapsiDB, sul tempo mediano; la media
  // geometrica di questi rapporti dice quante volte è più rapido in media, e in lei ogni domanda pesa uguale, lenta o
  // rapida che sia. Accanto si contano le domande in cui chi è più rapido in media stacca l'altro di oltre 2, 5 e 10
  // volte, e quelle in cui resta indietro. È la stessa regola del sito pubblico.
  const SOGLIE = [2, 5, 10];
  const geometrica = (xs) => Math.exp(xs.reduce((a, x) => a + Math.log(x), 0) / xs.length);
  function mediana(xs) {
    const s = [...xs].sort((a, b) => a - b);
    const m = s.length >> 1;
    return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
  }
  // SinapsiDB contro un altro tempo, domanda per domanda. A parità di tempo la domanda va a SinapsiDB, come nel resto
  // della pagina. Una domanda senza uno dei due tempi resta fuori, e quante ne restano dentro si dice sempre.
  function rapporti(contate, mio, altro) {
    const xs = contate.map((d) => [mio(d), altro(d)]).filter(([a, b]) => a > 0 && b > 0).map(([a, b]) => b / a);
    if (!xs.length) return null;
    const g = geometrica(xs);
    const vince = g >= 1; // in media è più rapido SinapsiDB
    const stacchi = xs.map((x) => (vince ? x : 1 / x)); // di quante volte chi è più rapido in media stacca l'altro
    const davanti = xs.filter((x) => x >= 1).length; // le domande in cui SinapsiDB è più rapido
    return { n: xs.length, vince, volte: vince ? g : 1 / g, davanti,
      oltre: SOGLIE.map((k) => stacchi.filter((x) => x > k).length), indietro: vince ? xs.length - davanti : davanti };
  }
  // I confronti della pagina: SinapsiDB contro il tempo totale e contro la sola esecuzione nel server degli altri.
  function confronti(contate, conRedis, dalDisco) {
    const sinapsi = (d) => d.sinapsi.p50_us;
    const righe = [
      { chiave: "pg", chi: "postgres", contro: "PostgreSQL", come: "tempo totale, visto dal programma", mio: sinapsi, altro: (d) => d.postgres.p50_us },
      { chiave: "pg-server", chi: "postgres", contro: "PostgreSQL", come: "solo l'esecuzione nel server, senza il viaggio della domanda e della risposta",
        mio: sinapsi, altro: (d) => d.postgres_nel_server_p50_us },
    ];
    if (conRedis) {
      righe.push(
        { chiave: "redis", chi: "redis", contro: SISTEMI.redis, come: "tempo totale, visto dal programma", mio: sinapsi, altro: (d) => d.redis && d.redis.p50_us },
        { chiave: "redis-server", chi: "redis", contro: SISTEMI.redis, come: "solo il comando nel server, senza il viaggio della domanda e della risposta",
          mio: sinapsi, altro: (d) => d.redis_nel_server_p50_us });
    }
    if (dalDisco) {
      righe.push({ chiave: "prima-volta", chi: "postgres", contro: "PostgreSQL", come: "tempo totale, con SinapsiDB la prima volta, quando prende i dati sul disco",
        mio: (d) => d.sinapsi_prima_volta && d.sinapsi_prima_volta.p50_us, altro: (d) => d.postgres.p50_us });
    }
    for (const r of righe) r.esito = rapporti(contate, r.mio, r.altro);
    return righe;
  }
  function rendiRapporti(righe, contate, fuori, conRedis, dalDisco) {
    const valide = righe.filter((r) => r.esito);
    $("nota-rapporti").textContent = !valide.length ? "Nessuna domanda da confrontare."
      : "Per ogni domanda, il tempo dell'altro diviso quello di SinapsiDB, sul tempo mediano. La media è quella geometrica di questi "
        + "rapporti: ogni domanda pesa uguale, lenta o rapida che sia. Le colonne «di oltre» contano le domande in cui chi è più rapido "
        + "in media stacca l'altro di più di 2, 5 o 10 volte; l'ultima quelle in cui resta indietro."
        + (fuori ? " Si contano le domande in cui la risposta si calcola al momento: la riga in cui tutti leggono una risposta tenuta pronta ha il suo riquadro, «Con la risposta pronta»." : "");
    const piuRapido = (r) => el("span", { class: "vincitore" }, el("span", { class: `chiave-linea chiave-${r.esito.vince ? "sinapsi" : r.chi}` }),
      `${r.esito.vince ? "SinapsiDB" : r.contro}, ${volte(r.esito.volte)} volte più rapido`);
    const su = (x, r) => `${x} su ${r.esito.n}`;
    $("tabella-rapporti").replaceChildren(valide.length ? tabella([
      { nome: "SinapsiDB contro", valore: (r) => el("span", {}, el("span", { class: "domanda-nome", text: r.contro }), el("span", { class: "domanda-cosa", text: r.come })) },
      { nome: "Più rapido, in media", valore: piuRapido },
      ...SOGLIE.map((k, i) => ({ nome: `Di oltre ${k} volte`, num: true, valore: (r) => su(r.esito.oltre[i], r) })),
      { nome: "Dove resta indietro", num: true, valore: (r) => su(r.esito.indietro, r) },
    ], valide) : "");
    // il tempo tipico di ognuno: la mediana delle domande contate, da leggere da solo
    const tipico = (f) => {
      const xs = contate.map(f).filter((x) => x > 0);
      return xs.length ? tempo(mediana(xs)) : "—";
    };
    const parti = [`SinapsiDB ${tipico((d) => d.sinapsi.p50_us)}`];
    if (dalDisco) parti.push(`SinapsiDB la prima volta ${tipico((d) => d.sinapsi_prima_volta && d.sinapsi_prima_volta.p50_us)}`);
    parti.push(`PostgreSQL ${tipico((d) => d.postgres.p50_us)} (solo nel server ${tipico((d) => d.postgres_nel_server_p50_us)})`);
    if (conRedis) parti.push(`${SISTEMI.redis} ${tipico((d) => d.redis && d.redis.p50_us)} (solo nel server ${tipico((d) => d.redis_nel_server_p50_us)})`);
    $("tipico").textContent = contate.length
      ? `Tempo tipico di una domanda, cioè la mediana delle ${contate.length}: ${parti.join(" · ")}. Chi è più rapido, e di quanto, lo dicono i rapporti qui sopra, domanda per domanda.`
      : "";
  }

  // --- il tempo di ogni domanda, in un grafico -------------------------------------
  // Una riga per domanda, su una scala che va per potenze di dieci: la stessa distanza vuol dire lo stesso numero di
  // volte in qualsiasi punto. Il segno pieno è il tempo visto dal programma, il cerchio vuoto quello della sola
  // esecuzione nel server; la linea fra i due è il viaggio della domanda e della risposta.
  function segno(forma, classe, x, testo) {
    let s;
    if (forma === "quadro") {
      // un quadrato centrato su una x in percentuale: un svg annidato porta l'origine sul punto
      s = sv("svg", { x, y: 0, width: 1, height: 18, overflow: "visible" });
      const q = sv("rect", { x: -4.5, y: 4.5, width: 9, height: 9, rx: 1.5, class: classe });
      s.append(q);
      if (testo) q.append(sv("title"));
      if (testo) q.lastChild.textContent = testo;
      return s;
    }
    s = sv("circle", { cx: x, cy: 9, r: forma === "vuoto" ? 4.5 : 5, class: classe });
    if (testo) {
      s.append(sv("title"));
      s.lastChild.textContent = testo;
    }
    return s;
  }
  function rendiGrafico(ds, fuori, conRedis, dalDisco) {
    const segni = (d) => {
      const s = [];
      const coppia = (chi, nome, totale, server) => {
        if (!(totale > 0)) return;
        if (server > 0) s.push({ chi, forma: "vuoto", us: server, verso: totale, nome: `${nome}, solo nel server` });
        s.push({ chi, forma: "pieno", us: totale, nome });
      };
      coppia("postgres", "PostgreSQL", d.postgres.p50_us, d.postgres_nel_server_p50_us);
      if (conRedis && d.redis) coppia("redis", SISTEMI.redis, d.redis.p50_us, d.redis_nel_server_p50_us);
      // SinapsiDB per ultimo, sopra gli altri: è lui che si misura
      if (dalDisco && d.sinapsi_prima_volta && d.sinapsi_prima_volta.p50_us > 0) {
        s.push({ chi: "sinapsi", forma: "quadro", us: d.sinapsi_prima_volta.p50_us, nome: "SinapsiDB, la prima volta" });
      }
      if (d.sinapsi.p50_us > 0) s.push({ chi: "sinapsi", forma: "pieno", us: d.sinapsi.p50_us, nome: "SinapsiDB" });
      // i cerchi vuoti sotto quelli pieni, così un punto pieno non sparisce dietro un cerchio
      return [...s.filter((m) => m.forma === "vuoto"), ...s.filter((m) => m.forma !== "vuoto")];
    };
    const tutti = ds.map(segni);
    const valori = tutti.flat().map((m) => m.us);
    if (!valori.length) {
      metti($("legenda-domande"));
      metti($("grafico-domande"));
      return;
    }
    // la scala: dalla potenza di dieci sotto il più rapido a quella sopra il più lento
    const basso = Math.floor(Math.log10(Math.min(...valori)));
    const alto = Math.max(basso + 1, Math.ceil(Math.log10(Math.max(...valori))));
    const x = (us) => `${((Math.log10(us) - basso) / (alto - basso)) * 100}%`;
    const tacche = [];
    for (let e = basso; e <= alto; e++) tacche.push(10 ** e);
    const pista = (s) => {
      const svg = sv("svg", { class: "pista", width: "100%", height: 18, "aria-hidden": "true" });
      for (const v of tacche) svg.append(sv("line", { x1: x(v), x2: x(v), y1: 0, y2: 18, class: "griglia" }));
      for (const m of s) if (m.verso) svg.append(sv("line", { x1: x(m.us), x2: x(m.verso), y1: 9, y2: 9, class: `viaggio viaggio-${m.chi}` }));
      for (const m of s) {
        const classe = m.forma === "vuoto" ? `segno-vuoto vuoto-${m.chi}` : `segno-pieno pieno-${m.chi}`;
        svg.append(segno(m.forma, classe, x(m.us), `${m.nome}: ${tempo(m.us)}`));
      }
      return svg;
    };
    const asse = sv("svg", { class: "pista asse", width: "100%", height: 16, "aria-hidden": "true" });
    tacche.forEach((v, i) => {
      const scritta = sv("text", { x: x(v), y: 12, "text-anchor": i === 0 ? "start" : i === tacche.length - 1 ? "end" : "middle" });
      scritta.textContent = tempo(v);
      asse.append(scritta);
    });
    const chip = (forma, classe) => {
      const svg = sv("svg", { width: 12, height: 18, viewBox: "0 0 12 18", "aria-hidden": "true" });
      svg.append(segno(forma, classe, 6));
      return svg;
    };
    metti($("legenda-domande"),
      el("span", {}, chip("pieno", "segno-pieno pieno-sinapsi"), "SinapsiDB"),
      el("span", {}, chip("pieno", "segno-pieno pieno-postgres"), "PostgreSQL"),
      conRedis ? el("span", {}, chip("pieno", "segno-pieno pieno-redis"), SISTEMI.redis) : null,
      dalDisco ? el("span", {}, chip("quadro", "segno-pieno pieno-sinapsi"), "SinapsiDB la prima volta, quando prende i dati sul disco") : null,
      el("span", {}, chip("pieno", "legenda-pieno"), "tempo visto dal programma"),
      el("span", {}, chip("vuoto", "legenda-vuoto"), "solo nel server: la distanza dal segno pieno è il viaggio della domanda e della risposta"));
    metti($("grafico-domande"), el("div", { class: "domande-grafico" },
      ds.map((d, i) => el("div", { class: `dom-riga${d.pronta ? " dom-fuori" : ""}` },
        el("div", { class: "dom-nome" }, el("span", { text: d.nome }),
          d.pronta ? el("span", { class: "domanda-segno", text: "risposta tenuta pronta da tutti · fuori dal conteggio" }) : null),
        pista(tutti[i]))),
      el("div", { class: "dom-riga" }, el("div", { class: "dom-nome" }), asse)));
    $("nota-domande").textContent = "Il tempo mediano di ogni domanda, su una scala che va per potenze di dieci: ogni tacca vale dieci volte quella prima, "
      + "e la stessa distanza vuol dire lo stesso numero di volte in qualsiasi punto. Più a sinistra è più rapido. I numeri sono nella tabella qui sotto: "
      + (conRedis ? "l'ultima colonna dice chi è il più rapido dei tre, e di quante volte stacca il secondo." : "l'ultima colonna dice chi è il più rapido, e di quante volte.")
      + (fuori ? ` Nei conteggi non ${fuori === 1 ? "entra la riga" : "entrano le righe"} in cui tutti leggono una risposta tenuta pronta: lì nessuno calcola niente.` : "");
  }

  function rendiRisultati() {
    // Mentre il confronto gira i risultati di prima non si mostrano: non devono sembrare quelli nuovi.
    const misurando = stato.lavoro.in_corso && stato.lavoro.nome === "misura";
    const c = misurando ? null : stato.confronto;
    $("risultati").hidden = !c;
    $("senza-risultati").hidden = Boolean(c);
    $("senza-risultati").textContent = misurando ? "Confronto in corso: i risultati arrivano quando finisce. A che punto è lo dice la barra in fondo allo schermo."
      : stato.lavoro.nome === "misura" && stato.lavoro.errore ? "Il confronto non è riuscito: non ci sono risultati da mostrare. Il motivo è scritto qui sopra."
        : "Non c'è ancora nessun confronto: genera i dati, importali, poi «Lancia il confronto».";
    if (!c) return;
    const ds = c.domande;
    const conRedis = Boolean(c.redis);
    const dalDisco = Boolean(c.rete && c.rete.modo === "disco");
    const prove = ds.reduce((a, d) => a + d.prove, 0);
    // Chi è più rapido si conta sulle domande in cui la risposta si calcola al momento. Dove tutti leggono una
    // risposta tenuta pronta nessuno calcola niente: quella riga ha il suo riquadro, e non fa punteggio per nessuno.
    const contate = ds.filter((d) => !d.pronta);
    const fuori = ds.length - contate.length;
    const righe = confronti(contate, conRedis, dalDisco);
    const esito = Object.fromEntries(righe.map((r) => [r.chiave, r.esito]));
    const quante = (e) => (e ? `${e.davanti} su ${e.n}` : "—");
    const inMedia = (e, altro) => (!e ? "" : e.vince ? `in media ${volte(e.volte)} volte più rapido` : `in media ${altro} è ${volte(e.volte)} volte più rapido`);
    // contro PostgreSQL e contro la cache Redis si conta a parte: sono due confronti diversi, e non si sommano
    const diPostgres = contate.filter((d) => d.postgres.p50_us < d.sinapsi.p50_us);
    const riquadri = [
      riquadro("Contro PostgreSQL", quante(esito.pg),
        `domande in cui SinapsiDB è più rapido · ${inMedia(esito.pg, "PostgreSQL")}`
        + (!diPostgres.length ? ""
          : diPostgres.every((d) => d.senza_partenza) ? " · PostgreSQL è più rapido nelle domande senza punto di partenza"
            : ` · PostgreSQL è più rapido in ${diPostgres.length}`)),
    ];
    if (conRedis) {
      // accanto al conteggio, e non in una nota in fondo: dentro il suo server Redis è più rapido
      const nel = esito["redis-server"];
      riquadri.push(riquadro("Contro la cache Redis", quante(esito.redis),
        `domande in cui SinapsiDB è più rapido, contando il viaggio della risposta · ${inMedia(esito.redis, "Redis")}`
        + (nel ? ` · dentro il suo server Redis è più rapido in ${nel.n - nel.davanti} su ${nel.n}` : "")));
    }
    riquadri.push(riquadro("Risposte uguali", `${nf0.format(prove - c.risposte_diverse)} su ${nf0.format(prove)}`,
      c.risposte_diverse ? "ci sono risposte diverse: i tempi non valgono"
        : conRedis && c.redis.risposte_diverse ? `ma ${nf0.format(c.redis.risposte_diverse)} di Redis sono diverse: i suoi tempi non valgono`
          : conRedis ? "di SinapsiDB, e altrettante di Redis: una per una" : "ogni risposta confrontata una per una",
      c.risposte_diverse || (conRedis && c.redis.risposte_diverse) ? "critico" : "buono"));
    // la riga in cui tutti leggono una risposta tenuta pronta: è il confronto alla pari con una cache
    const pronta = ds.find((d) => d.pronta);
    if (pronta) {
      const chi = conRedis && pronta.redis ? "redis" : "postgres";
      const mio = pronta.sinapsi.p50_us;
      const suo = pronta[chi].p50_us;
      const server = chi === "redis" ? pronta.redis_nel_server_p50_us : pronta.postgres_nel_server_p50_us;
      const vince = mio <= suo;
      riquadri.push(riquadro("Con la risposta pronta", `${vince ? "SinapsiDB" : SISTEMI[chi]}, ${volte(vince ? suo / mio : mio / suo)} volte più rapido`,
        `dove tutti leggono una risposta tenuta pronta: SinapsiDB ${tempo(mio)}, ${chi === "redis" ? "la cache Redis" : "la vista materializzata di PostgreSQL"} ${tempo(suo)}`
        + `, solo nel server ${tempo(server)}`));
    }
    riquadri.push(riquadro("Dati", nf0.format(c.fatti),
      `fatti nella rete, ${dalDisco ? `sul disco con ${memoria(c.rete.in_memoria)} in memoria` : "tutta in memoria"} · PostgreSQL ${String(c.versione_postgres).split(" ")[0]}${conRedis ? ` · Redis ${c.redis.versione}` : ""}`));
    if (conRedis) {
      // il prezzo della cache: memoria in più e tempo per prepararla, da pagare di nuovo a ogni cambio dei dati
      const s = c.redis.preparazione_s;
      riquadri.push(riquadro("Prezzo della cache", memoria(c.redis.memoria_risposte),
        `in più rispetto ai dati, per ${nf0.format(c.redis.chiavi)} chiavi`
        + (s != null ? ` preparate in ${(s < 10 ? nf1 : nf0).format(s)} secondi` : " (il tempo per prepararle questo confronto non lo registrava)")
        + "; da rifare quando i dati cambiano"));
    }
    metti($("riassunto"), riquadri);
    $("riassunto").dataset.quanti = String(riquadri.length);
    rendiRapporti(righe, contate, fuori, conRedis, dalDisco);
    rendiGrafico(ds, fuori, conRedis, dalDisco);
    // le domande rimaste fuori da questo confronto, con il perché
    metti($("saltate"), (c.saltate || []).map((s) => messaggio("attenzione", `Fuori da questo confronto: «${s.nome}». ${s.perche.charAt(0).toUpperCase()}${s.perche.slice(1)}.`)));
    // Redis da una porta di rete e PostgreSQL dal socket: una disparità che va detta
    $("nota-disco").hidden = !dalDisco;
    if (dalDisco) {
      $("nota-disco").textContent = `In questo confronto la rete di SinapsiDB stava sul disco (${memoria(c.rete.su_disco)}), con in memoria al massimo ${memoria(c.rete.tetto)}. `
        + "«SinapsiDB» è il tempo di una domanda quando i pezzi di rete che le servono sono già in memoria, come per gli altri dopo il giro di riscaldamento. "
        + "«SinapsiDB, la prima volta» è il tempo quando li deve andare a prendere sul disco: prima di ogni domanda la memoria viene svuotata apposta. "
        + "Il disco qui non è mai del tutto freddo, perché il sistema operativo tiene in memoria i file letti di recente: su un disco a riposo la prima volta costa di più.";
    }
    // chi ha passato i fatti nelle domande senza punto di partenza: il nucleo compilato, o il Python
    const delNucleo = ds.filter((d) => d.nucleo).length;
    $("nota-nucleo").hidden = !c.nucleo;
    if (c.nucleo) {
      $("nota-nucleo").textContent = c.nucleo.attivo
        ? `In questo confronto il nucleo compilato di SinapsiDB era attivo: ${delNucleo === 1 ? "una domanda l'ha calcolata lui" : `${delNucleo} domande le ha calcolate lui`}, in C sulle stesse colonne della rete, come PostgreSQL calcola le sue. Le altre le fa il Python.`
        : `In questo confronto il nucleo compilato di SinapsiDB non era attivo (${c.nucleo.perche}): tutte le domande le ha fatte il Python.`;
    }
    $("nota-via").hidden = !(conRedis && c.redis.via === "tcp");
    $("nota-lettore").hidden = !(conRedis && c.redis.lettore_c === false);
    const seRedis = (colonne) => (conRedis ? colonne : []);
    $("tabella-confronto").replaceChildren(tabella([
      { nome: "Domanda", valore: (d) => el("span", {}, el("span", { class: "domanda-nome", text: d.nome }),
        el("span", { class: "domanda-cosa", text: d.cosa }),
        d.senza_partenza ? el("span", { class: "domanda-segno", text: d.nucleo ? "senza punto di partenza · calcolata dal nucleo compilato" : "senza punto di partenza" })
          : d.pronta ? el("span", { class: "domanda-segno", text: "risposta tenuta pronta da tutti · fuori dal conteggio" }) : null) },
      { nome: "Righe in risposta", num: true, valore: (d) => nf1.format(d.righe_medie) },
      { nome: "SinapsiDB", num: true, valore: (d) => tempo(d.sinapsi.p50_us) },
      ...(dalDisco ? [{ nome: "SinapsiDB, la prima volta", num: true, valore: (d) => tempo(d.sinapsi_prima_volta.p50_us) }] : []),
      { nome: "PostgreSQL, totale", num: true, valore: (d) => tempo(d.postgres.p50_us) },
      { nome: "PostgreSQL, solo nel server", num: true, valore: (d) => tempo(d.postgres_nel_server_p50_us) },
      ...seRedis([
        { nome: "Redis (cache), totale", num: true, valore: (d) => tempo(d.redis.p50_us) },
        { nome: "Redis (cache), solo nel server", num: true, valore: (d) => tempo(d.redis_nel_server_p50_us) },
      ]),
      { nome: "Più rapido", valore: vincitore },
    ], ds));
    $("tabella-dettaglio").replaceChildren(tabella([
      { nome: "Domanda", valore: (d) => d.nome },
      { nome: "Prove", num: true, valore: (d) => nf0.format(d.prove) },
      { nome: conRedis ? "Risposte diverse, SinapsiDB" : "Risposte diverse", num: true, valore: (d) => nf0.format(d.risposte_diverse) },
      ...seRedis([{ nome: "Risposte diverse, Redis", num: true, valore: (d) => nf0.format(d.redis_risposte_diverse) }]),
      { nome: "SinapsiDB, tempo medio", num: true, valore: (d) => tempo(d.sinapsi.media_us) },
      { nome: "SinapsiDB, 99° percentile", num: true, valore: (d) => tempo(d.sinapsi.p99_us) },
      ...(dalDisco ? [{ nome: "SinapsiDB, la prima volta, tempo medio", num: true, valore: (d) => tempo(d.sinapsi_prima_volta.media_us) },
        { nome: "SinapsiDB, la prima volta, 99° percentile", num: true, valore: (d) => tempo(d.sinapsi_prima_volta.p99_us) }] : []),
      { nome: "PostgreSQL, tempo medio", num: true, valore: (d) => tempo(d.postgres.media_us) },
      { nome: "PostgreSQL, solo nel server, tempo medio", num: true, valore: (d) => tempo(d.postgres_nel_server_media_us) },
      { nome: "PostgreSQL, 99° percentile", num: true, valore: (d) => tempo(d.postgres.p99_us) },
      ...seRedis([{ nome: "Redis (cache), tempo medio", num: true, valore: (d) => tempo(d.redis.media_us) },
        { nome: "Redis (cache), solo nel server, tempo medio", num: true, valore: (d) => tempo(d.redis_nel_server_media_us) },
        { nome: "Redis (cache), 99° percentile", num: true, valore: (d) => tempo(d.redis.p99_us) }]),
      { nome: "Nella lingua di SinapsiDB", valore: (d) => el("code", { text: d.lingua || "" }) },
      { nome: "In SQL", valore: (d) => el("code", { text: d.sql }) },
      ...seRedis([{ nome: "In Redis", valore: (d) => el("code", { text: d.redis_comando || "" }) }]),
    ], ds));
  }

  // --- la casella delle domande --------------------------------------------------

  // Esempi e vocabolario dipendono da che cosa c'è nella rete: si richiedono quando cambia il numero dei fatti.
  async function caricaAiuto(fatti) {
    if (fatti === aiutoPer) return;
    aiutoPer = fatti;
    if (!fatti) {
      metti($("esempi"));
      metti($("vocabolario"), el("p", { class: "secondario", text: "La rete di prova è vuota: prima va importata." }));
      metti($("risposta"));
      return;
    }
    try {
      const r = await fetch("/api/confronto/aiuto", { cache: "no-store" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const aiuto = await r.json();
      metti($("esempi"), aiuto.esempi.map((e) => {
        const b = el("button", { type: "button", text: e.titolo, title: e.domanda });
        b.addEventListener("click", () => { $("domanda").value = e.domanda; chiedi(); });
        return b;
      }));
      metti($("vocabolario"), tabella([
        { nome: "Tipo di fatto", valore: (v) => v.tipo },
        { nome: "Ruoli", valore: (v) => v.ruoli.join(", ") },
        { nome: "Fatti", num: true, valore: (v) => nf0.format(v.fatti) },
      ], aiuto.vocabolario));
    } catch (e) {
      aiutoPer = -1; // si riprova al prossimo giro
    }
  }

  async function chiedi() {
    const domanda = $("domanda").value.trim();
    if (!domanda) return;
    $("chiedi").disabled = true;
    try {
      const r = await fetch("/api/confronto/chiedi", {
        method: "POST", headers: { "x-sinapsidb-azione": "1", "content-type": "application/json" }, body: JSON.stringify({ domanda }),
      });
      const corpo = await r.json().catch(() => ({}));
      if (!r.ok) metti($("risposta"), messaggio("critico", corpo.error || `HTTP ${r.status}`));
      else rendiRisposta(corpo);
    } catch (e) {
      metti($("risposta"), messaggio("critico", `Domanda non arrivata: ${e.message || e}`));
    }
    $("chiedi").disabled = Boolean(stato && (stato.lavoro.in_corso || !stato.sinapsi || !stato.sinapsi.fatti));
  }

  function rendiRisposta(r) {
    const scattati = r.fatti_scattati.length ? `fatti scattati: ${r.fatti_scattati.map((n) => nf0.format(n)).join(" → ")}` : null;
    const colonne = r.colonne.map((nome, i) => ({
      nome,
      // una domanda proposta (dalla frase «percorsi») si può portare nella casella con un tocco
      valore: (riga) => (nome === "domanda" ? usa(riga[i]) : riga[i]),
    }));
    metti($("risposta"),
      el("p", { class: "esito-domanda" },
        r.esecuzione_us == null ? null : el("span", { class: "tempo", text: tempo(r.esecuzione_us) }),
        el("span", { text: `${nf0.format(r.quante)} ${r.quante === 1 ? "riga" : "righe"}${r.troncata ? ", le prime: la risposta ne ha di più" : ""}` }),
        scattati ? el("span", { class: "secondario", text: scattati }) : null,
        r.preparazione_us == null ? null : el("span", { class: "secondario", text: `domanda capita in ${tempo(r.preparazione_us)}` })),
      el("ol", { class: "piano" }, r.piano.map((riga) => el("li", { text: riga }))),
      r.righe.length ? tabella(colonne, r.righe) : el("p", { class: "vuoto-tabella", text: "Nessun fatto scatta: la risposta è vuota." }));
  }

  function usa(domanda) {
    const b = el("button", { type: "button", class: "collegamento", title: "Porta questa domanda nella casella" }, el("code", { text: domanda }));
    b.addEventListener("click", () => { $("domanda").value = domanda; $("domanda").focus(); });
    return b;
  }

  $("chiedi-modulo").addEventListener("submit", (e) => { e.preventDefault(); chiedi(); });
  $("domanda").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); chiedi(); } // Invio chiede, Maiusc+Invio va a capo
  });

  // --- caricamento e comandi ---------------------------------------------------

  function errore(testo) {
    $("lavoro").hidden = false;
    $("lavoro").classList.remove("in-corso"); // un errore non è un lavoro in corso: la riga torna al suo posto
    $("lavoro-barra").hidden = true;
    $("lavoro-testo").replaceChildren(icona("critico"), el("span", { text: testo }));
  }

  async function aggiorna() {
    clearTimeout(attesa);
    try {
      const r = await fetch("/api/confronto", { cache: "no-store" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      stato = await r.json();
      rendiStato();
      rendiRisultati();
    } catch (e) {
      errore(`Stato non disponibile: ${e.message || e}`);
    }
    // durante un lavoro si riguarda spesso, altrimenti ogni tanto
    attesa = setTimeout(aggiorna, stato && stato.lavoro.in_corso ? 1000 : 15000);
  }

  async function lancia(lavoro, domanda) {
    for (const id of PULSANTI) $(id).disabled = true;
    // Chi preme deve vedere subito che è partito, senza aspettare il prossimo giro: la riga del lavoro compare qui,
    // e se è il confronto i risultati di prima spariscono in questo momento.
    $("lavoro").hidden = false;
    $("lavoro").classList.add("in-corso");
    $("lavoro-barra").hidden = false;
    $("lavoro-barra").removeAttribute("value");
    metti($("lavoro-testo"), el("strong", { text: `${LAVORI[lavoro] || lavoro} in corso.` }), " avvio");
    if (lavoro === "misura") {
      $("risultati").hidden = true;
      $("senza-risultati").hidden = false;
      $("senza-risultati").textContent = "Confronto in corso: i risultati arrivano quando finisce. A che punto è lo dice la barra in fondo allo schermo.";
    }
    try {
      const r = await fetch(`/api/confronto/${lavoro}${domanda || ""}`, { method: "POST", headers: { "x-sinapsidb-azione": "1" } });
      if (!r.ok) {
        const corpo = await r.json().catch(() => ({}));
        errore(`${LAVORI[lavoro]} non partito: ${corpo.error || `HTTP ${r.status}`}`);
        clearTimeout(attesa);
        attesa = setTimeout(aggiorna, 4000); // il messaggio resta leggibile un momento
        return;
      }
    } catch (e) {
      errore(`${LAVORI[lavoro]} non partito: ${e.message || e}`);
      return;
    }
    aggiorna();
  }

  // Un pulsante che cancella qualcosa chiede conferma su sé stesso: il primo clic cambia la scritta, il secondo agisce.
  const scritte = {};
  function annullaConferma(id) {
    clearTimeout(conferme[id]);
    delete conferme[id];
    $(id).textContent = scritte[id];
    $(id).classList.remove("conferma");
  }
  function conConferma(id, testo, serve, azione) {
    scritte[id] = $(id).textContent;
    $(id).addEventListener("click", () => {
      if (!conferme[id] && serve()) {
        $(id).textContent = testo;
        $(id).classList.add("conferma");
        conferme[id] = setTimeout(() => annullaConferma(id), 6000);
        return;
      }
      if (conferme[id]) annullaConferma(id);
      azione();
    });
  }

  $("importa").addEventListener("click", () => lancia("importa"));
  $("misura").addEventListener("click", () => lancia("misura"));
  $("redis").addEventListener("click", () => lancia("redis"));
  // Portare la rete sul disco o riportarla in memoria non cancella niente: nessuna conferma.
  $("dove").addEventListener("click", () => lancia(stato && stato.rete.modo === "disco" ? "memoria" : "disco"));
  conConferma("svuota", "Conferma: togli le risposte pronte da Redis", () => true, () => lancia("svuota"));
  conConferma("azzera", "Conferma: cancella tutta la rete di prova", () => true, () => lancia("azzera"));
  conConferma("genera", "Conferma: sostituisci i dati e svuota la rete di prova",
    () => stato && (stato.postgres.disponibile || (stato.sinapsi && stato.sinapsi.fatti > 0)),
    () => lancia("genera", `?righe=${encodeURIComponent($("taglia").value)}`));
  document.addEventListener("visibilitychange", () => { if (!document.hidden) aggiorna(); });

  aggiorna();
})();
