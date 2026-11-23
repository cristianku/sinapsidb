// Pagina delle decisioni: una memoria di prova con il fido dei clienti.
// Legge GET /api/decisioni e manda le operazioni a POST /api/decisioni/<operazione>.
// Tutto il testo passa da textContent: nessun HTML costruito da stringhe.
(() => {
  "use strict";
  const NS = "http://www.w3.org/2000/svg";
  const nf = new Intl.NumberFormat("it-CH", { maximumFractionDigits: 6, useGrouping: "always" });
  const nf0 = new Intl.NumberFormat("it-CH", { maximumFractionDigits: 0, useGrouping: "always" });
  const fQuando = new Intl.DateTimeFormat("it-CH", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", second: "2-digit" });
  const $ = (id) => document.getElementById(id);
  let quadro = null;
  let aperta = null; // l'evento della decisione che ha il riquadro del riesame aperto
  let risposte = {}; // evento -> il riquadro con l'ultima risposta (riesame, verifica, decisioni toccate)
  let scelte = {}; // evento -> le scritture successive spuntate nel suo riesame: restano spuntate quando la lista si ridisegna
  let confermaAzzera = 0;

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
  // Un importo arriva come testo decimale esatto: si mostra con i separatori, senza toccarne il valore.
  const importo = (testo) => (testo == null || Number.isNaN(Number(testo)) ? "—" : nf.format(Number(testo)).replace("-", "−"));
  const quando = (secondi) => fQuando.format(new Date(secondi * 1000));
  const accettata = (esito) => esito === "applicato";
  const inParole = (esito) => (accettata(esito) ? "sì" : esito === "violazione_regola" ? "no" : esito === "non_applicabile" ? "non si può dire" : esito === "indeterminato" ? "il giornale non basta a dirlo" : esito);

  async function chiama(op, corpo) {
    const risposta = await fetch(`/api/decisioni/${op}`, {
      method: "POST",
      headers: { "content-type": "application/json", "x-sinapsidb-azione": "1" },
      body: JSON.stringify(corpo || {}),
    });
    const dati = await risposta.json().catch(() => ({ error: "il portale non ha risposto in modo leggibile" }));
    if (!risposta.ok) throw Object.assign(new Error(dati.error || "richiesta non riuscita"), { tipo: dati.tipo });
    return dati;
  }

  function avviso(testo) {
    $("avviso").hidden = !testo;
    $("avviso").textContent = testo || "";
  }

  async function carica() {
    try {
      const risposta = await fetch("/api/decisioni", { headers: { accept: "application/json" } });
      if (!risposta.ok) {
        // se la memoria di prova non si apre il portale lo dice: si mostra il suo perché, non un errore qualsiasi
        let perche = "il portale non risponde";
        try {
          perche = (await risposta.json()).error || perche;
        } catch (e) {
          // la risposta non è quella del portale
        }
        throw new Error(perche);
      }
      quadro = await risposta.json();
      avviso("");
      disegna();
    } catch (e) {
      avviso(`Non riesco a leggere la memoria di prova: ${e.message}.`);
    }
  }

  // --- il fido dei clienti ------------------------------------------------------

  function disegnaClienti() {
    const dove = $("clienti");
    if (!quadro.clienti.length) {
      dove.replaceChildren(el("p", { class: "vuoto", text: "Ancora nessun cliente. Accorda un fido, oppure prova l'esempio." }));
    } else {
      const corpo = el("tbody");
      for (const c of quadro.clienti) corpo.append(el("tr", {}, el("td", { text: c.nome }), el("td", { class: "num", text: importo(c.fido) })));
      dove.replaceChildren(el("table", {}, el("thead", {}, el("tr", {}, el("th", { text: "Cliente" }), el("th", { class: "num", text: "Fido residuo" }))), corpo));
    }
    const controllo = quadro.circuiti === "coerente"
      ? el("p", { class: "messaggio" }, icona("buono"), el("span", { text: "Il fido tenuto pronto coincide con quello rifatto dai fatti e dal giornale." }))
      : el("p", { class: "messaggio" }, icona("critico"), el("span", { text: `Il controllo del fido tenuto pronto dice: ${quadro.circuiti}.` }));
    const dl = el("dl");
    for (const [nome, valore] of [["Eventi scritti", nf0.format(quadro.conteggi.eventi)], ["Fatti in vigore", nf0.format(quadro.conteggi.fatti_in_vigore)]]) {
      dl.append(el("dt", { text: nome }), el("dd", { text: valore }));
    }
    $("sotto").replaceChildren(controllo, dl);
  }

  // --- le decisioni --------------------------------------------------------------

  function lettoInParole(letture) {
    return letture
      .filter((y) => y.prima != null || y.dopo != null)
      .map((y) => `${y.cosa} di ${y.di}: ${importo(y.prima)} prima, ${importo(y.dopo)} dopo`)
      .join(" · ");
  }

  function segno(esito) {
    const si = accettata(esito);
    return el("span", { class: "segno" }, icona(si ? "buono" : "critico"), el("span", { text: si ? "Accettata" : "Rifiutata" }));
  }

  function riquadroRiesame(d) {
    const successive = quadro.decisioni.filter((x) => x.evento > d.evento && x.tipo === "scrittura").sort((a, b) => a.evento - b.evento);
    const riquadro = el("div", { class: "riesame" });
    if (!successive.length) {
      riquadro.append(el("p", { text: "Dopo questa decisione non è stato scritto ancora niente: posso solo rifarla com'era." }));
    } else {
      riquadro.append(el("p", { text: "Quali scritture successive devo considerare già note?" }));
      const elenco = el("ul");
      for (const s of successive) {
        elenco.append(el("li", {}, el("label", {},
          el("input", { type: "checkbox", value: s.evento, checked: (scelte[d.evento] || []).includes(s.evento) ? "checked" : null }),
          el("span", { text: `${s.frase} (${quando(s.arrivo)})` }))));
      }
      riquadro.append(elenco);
    }
    const lancia = el("button", { type: "button", class: "primario", text: successive.length ? "Riesamina sapendo di queste" : "Rifai la decisione" });
    lancia.addEventListener("click", async () => {
      const spuntate = [...riquadro.querySelectorAll("input:checked")].map((x) => Number(x.value));
      scelte[d.evento] = spuntate;
      lancia.disabled = true;
      try {
        risposte[d.evento] = rispostaRiesame(d, await chiama("riesamina", { evento: d.evento, rettifiche: spuntate }), spuntate.length);
      } catch (e) {
        risposte[d.evento] = rispostaErrore(e);
      }
      disegnaDecisioni();
    });
    riquadro.append(el("div", { class: "azioni" }, lancia));
    return riquadro;
  }

  function valori(letture) {
    const mie = letture.filter((y) => y.quando === "arrivo");
    const prima = mie.find((y) => !y.con_proposta);
    const dopo = mie.find((y) => y.con_proposta);
    return prima && dopo ? `${importo(prima.valore)} prima, ${importo(dopo.valore)} dopo` : "nessuna regola consultata";
  }

  function rispostaRiesame(d, r, quante) {
    const riquadro = el("div", { class: "risposta" });
    const dl = el("dl");
    dl.append(el("dt", { text: "Allora" }), el("dd", { text: `${inParole(r.allora.esito)} · ${valori(r.allora.letture)}` }));
    // Una decisione scritta da un programma che non dichiarava come faceva le somme: se i modi possibili non danno
    // tutti la stessa risposta, si mostrano tutte e non se ne sceglie una.
    const incerta = r.ipotetico.esito === "indeterminato";
    if (incerta) {
      for (const a of r.ipotetico.alternative || []) {
        dl.append(el("dt", { text: `Con ${a.calcoli.length === 1 ? "il modo" : "i modi"} ${a.calcoli.join(", ")}` }),
          el("dd", { text: `${inParole(a.esito)} · ${a.esito === "non_applicabile" ? a.spiegazione : valori(a.letture)}` }));
      }
    } else {
      dl.append(el("dt", { text: quante ? "Sapendolo" : "Rifatta adesso" }), el("dd", { text: `${inParole(r.ipotetico.esito)} · ${r.ipotetico.esito === "non_applicabile" ? r.ipotetico.spiegazione : valori(r.ipotetico.letture)}` }));
    }
    riquadro.append(dl);
    const frase = incerta
      ? "Il giornale non dice come faceva le somme il programma che ha preso questa decisione, e i modi possibili qui non danno la stessa risposta: non posso dire se sarebbe cambiata."
      : !quante
        ? "Rifatta dal giornale, la decisione è la stessa di allora."
        : r.cambia
          ? `Sapendo di ${quante === 1 ? "quella scrittura" : "quelle scritture"}, la risposta sarebbe stata ${inParole(r.ipotetico.esito)}.`
          : "Anche sapendolo, la risposta sarebbe stata la stessa.";
    riquadro.append(el("p", { class: "conclusione" }, icona(incerta || r.cambia ? "attenzione" : "buono"), el("span", { text: frase })));
    riquadro.append(el("p", { class: "piccolo", text: "La decisione di allora resta com'è: il riesame non scrive niente e non autorizza niente." }));
    if (r.eventi_esclusi && r.eventi_esclusi.length) riquadro.append(el("p", { class: "piccolo", text: `Lasciate fuori: ${r.eventi_esclusi.map((x) => x.perche).join("; ")}.` }));
    return riquadro;
  }

  function rispostaErrore(e) {
    const testo = e.tipo === "scenario_incompleto"
      ? "Lo scenario è incompleto: una delle scritture scelte ne corregge un'altra che non hai scelto. Sceglile insieme."
      : `Non è riuscito: ${e.message}`;
    return el("div", { class: "risposta" }, el("p", { class: "conclusione" }, icona("attenzione"), el("span", { text: testo })));
  }

  function rispostaVerifica(r) {
    const buona = r.esito === "coerente";
    // «non_supportato» e «non_verificabile» non dicono che la ricevuta è sbagliata: dicono che qui non si può controllare
    const non_si_sa = r.esito === "non_supportato" || r.esito === "non_verificabile";
    const frase = buona ? "La ricevuta coincide con la decisione rifatta dal giornale."
      : non_si_sa ? "Questa decisione qui non si può controllare." : "La ricevuta non coincide con il giornale.";
    const riquadro = el("div", { class: "risposta" }, el("p", { class: "conclusione" }, icona(buona ? "buono" : non_si_sa ? "attenzione" : "critico"),
      el("span", { text: frase })));
    if (!buona && r.differenze && r.differenze.length) riquadro.append(el("ul", {}, r.differenze.map((x) => el("li", { text: x }))));
    if (r.spiegazione) riquadro.append(el("p", { class: "piccolo", text: `${r.spiegazione.charAt(0).toUpperCase()}${r.spiegazione.slice(1)}.` }));
    return riquadro;
  }

  function rispostaToccate(r) {
    const riquadro = el("div", { class: "risposta" });
    if (!r.quante) {
      riquadro.append(el("p", { class: "conclusione" }, icona("buono"), el("span", { text: "Questa scrittura non tocca nessuna decisione presa prima." })));
      return riquadro;
    }
    const cambiate = r.esiti_cambiati || [];
    const incerte = (r.esiti_indeterminati || []).length;
    riquadro.append(el("p", { class: "conclusione" }, icona(cambiate.length || incerte ? "attenzione" : "buono"), el("span", {
      text: `Tocca ${r.quante === 1 ? "una decisione presa prima" : `${nf0.format(r.quante)} decisioni prese prima`}: ${cambiate.length ? `${cambiate.length === 1 ? "una avrebbe" : `${cambiate.length} avrebbero`} avuto un altro esito.` : incerte ? "di nessuna posso dire con certezza che avrebbe avuto un altro esito." : "nessuna avrebbe avuto un altro esito."}`,
    })));
    if (cambiate.length) {
      const frasi = Object.fromEntries(quadro.decisioni.map((x) => [`evento:${x.evento}`, x.frase]));
      riquadro.append(el("ul", {}, cambiate.map((x) => el("li", { text: `${frasi[x.evento] || x.evento}: da ${inParole(x.allora)} a ${inParole(x.ipotetico)}` }))));
    }
    if (incerte) riquadro.append(el("p", { class: "piccolo", text: `Per ${incerte === 1 ? "una" : incerte} il giornale non basta a dirlo: è stata scritta da un programma che non dichiarava come faceva le somme. Riesaminala da sola per vedere le risposte possibili.` }));
    const incompleti = (r.scenari_incompleti || []).length;
    if (incompleti) riquadro.append(el("p", { class: "piccolo", text: `${incompleti === 1 ? "Per una" : `Per ${incompleti}`} da sola non basta: corregge un'altra scrittura, e vanno riesaminate insieme.` }));
    riquadro.append(el("p", { class: "piccolo", text: "Le decisioni di allora restano come sono." }));
    return riquadro;
  }

  function disegnaDecisioni() {
    const dove = $("decisioni");
    if (!quadro.decisioni.length) {
      dove.replaceChildren(el("p", { class: "vuoto", text: "Ancora nessuna decisione." }));
      return;
    }
    const righe = [];
    for (const d of quadro.decisioni) {
      const riga = el("div", { class: "decisione", "data-evento": d.evento });
      riga.append(el("div", { class: "capo" }, segno(d.esito), el("span", { class: "frase", text: d.frase }), el("span", { class: "quando", text: quando(d.arrivo) })));
      const letto = lettoInParole(d.letture);
      if (letto) riga.append(el("p", { class: "letto", text: letto }));
      const riesamina = el("button", { type: "button", text: aperta === d.evento ? "Chiudi" : "Riesamina", "aria-expanded": aperta === d.evento ? "true" : "false" });
      riesamina.addEventListener("click", () => {
        aperta = aperta === d.evento ? null : d.evento;
        delete risposte[d.evento];
        disegnaDecisioni();
      });
      const verifica = el("button", { type: "button", text: "Verifica" });
      verifica.addEventListener("click", async () => {
        verifica.disabled = true;
        try { risposte[d.evento] = rispostaVerifica(await chiama("verifica", { evento: d.evento })); } catch (e) { risposte[d.evento] = rispostaErrore(e); }
        disegnaDecisioni();
      });
      const azioni = el("div", { class: "azioni" }, riesamina, verifica);
      if (d.tipo === "scrittura") {
        const toccate = el("button", { type: "button", text: "Quali decisioni tocca?" });
        toccate.addEventListener("click", async () => {
          toccate.disabled = true;
          try { risposte[d.evento] = rispostaToccate(await chiama("candidate", { rettifica: d.evento })); } catch (e) { risposte[d.evento] = rispostaErrore(e); }
          disegnaDecisioni();
        });
        azioni.append(toccate);
      }
      riga.append(azioni);
      if (aperta === d.evento) riga.append(riquadroRiesame(d));
      if (risposte[d.evento]) riga.append(risposte[d.evento]);
      righe.push(riga);
    }
    dove.replaceChildren(...righe);
  }

  function disegna() {
    disegnaClienti();
    disegnaDecisioni();
  }

  // --- scrivere -------------------------------------------------------------------

  function mostraEsito(tipo, frase, seconda) {
    const righe = [el("p", {}, icona(tipo), el("span", { text: frase }))];
    if (seconda) righe.push(el("p", { text: seconda }));
    $("esito").replaceChildren(...righe);
  }

  function esitoScrittura(op, r) {
    const d = quadro.decisioni.find((x) => x.evento === r.evento);
    const letto = d ? lettoInParole(d.letture) : "";
    if (r.esito === "applicato") {
      mostraEsito("buono", { accorda: "Fido accordato.", ordine: "Ordine accettato.", pagamento: "Pagamento registrato." }[op] || "Scritto.", letto);
    } else if (r.esito === "violazione_regola") {
      mostraEsito("critico", "Rifiutato: manderebbe il fido residuo sotto zero. Non è stato scritto niente, ma la decisione resta registrata.", letto);
    } else {
      mostraEsito("attenzione", "Non è stato scritto niente.", r.spiegazione || "");
    }
  }

  $("modulo").addEventListener("submit", async (evento) => {
    evento.preventDefault();
    const op = evento.submitter && evento.submitter.dataset.op;
    if (!op) return;
    const corpo = { cliente: $("cliente").value.trim(), importo: $("importo").value.trim().replace(",", ".") };
    if (op === "pagamento" && $("vale-da").value) corpo.vale_da = $("vale-da").value;
    const bottoni = [...$("modulo").querySelectorAll("button")];
    bottoni.forEach((b) => { b.disabled = true; });
    try {
      const r = await chiama(op, corpo);
      aperta = null;
      risposte = {};
      scelte = {};
      await carica();
      esitoScrittura(op, r);
    } catch (e) {
      mostraEsito("attenzione", `Non è riuscito: ${e.message}`);
    } finally {
      bottoni.forEach((b) => { b.disabled = false; });
    }
  });

  $("esempio").addEventListener("click", async () => {
    $("esempio").disabled = true;
    try {
      const r = await chiama("esempio");
      aperta = r.ordine;
      risposte = {};
      scelte = {};
      await carica();
      mostraEsito("buono", `Fatto, con il cliente ${r.cliente}: 800 di fido, l'ordine da 1.000 rifiutato, poi il pagamento da 400 che vale da ieri.`,
        "Qui sotto, nell'ordine rifiutato, scegli il pagamento e premi «Riesamina sapendo di queste».");
      const riga = document.querySelector(`.decisione[data-evento="${r.ordine}"]`);
      if (riga) riga.scrollIntoView({ block: "center", behavior: "smooth" });
    } catch (e) {
      mostraEsito("attenzione", `Non è riuscito: ${e.message}`);
    } finally {
      $("esempio").disabled = false;
    }
  });

  $("azzera").addEventListener("click", async () => {
    const bottone = $("azzera");
    if (!confermaAzzera) {  // la prima pressione chiede conferma, sullo stesso bottone
      bottone.textContent = "Premi ancora per azzerare";
      bottone.classList.add("conferma");
      confermaAzzera = window.setTimeout(() => {
        confermaAzzera = 0;
        bottone.textContent = "Azzera questa memoria";
        bottone.classList.remove("conferma");
      }, 5000);
      return;
    }
    window.clearTimeout(confermaAzzera);
    confermaAzzera = 0;
    bottone.textContent = "Azzera questa memoria";
    bottone.classList.remove("conferma");
    try {
      await chiama("azzera");
      aperta = null;
      risposte = {};
      scelte = {};
      await carica();
      mostraEsito("buono", "La memoria di prova è stata azzerata.");
    } catch (e) {
      mostraEsito("attenzione", `Non è riuscito: ${e.message}`);
    }
  });

  carica();
})();
