// Pagina dei dati di SinapsiDB: la rete di neuroni e sinapsi, il diario degli eventi, il percorso di una domanda.
// Legge /api/rete. Finché il database è vuoto mostra la rete di esempio di /esempio-rete.json, con un avviso.
// Tutto il testo passa da textContent: nessun HTML costruito da stringhe.
(() => {
  "use strict";
  const NS = "http://www.w3.org/2000/svg";
  const FORME = [["cosa", "cosa, persona o concetto"], ["fatto", "fatto"], ["valore", "valore o data"], ["evento", "evento"], ["regola", "regola"]];
  const VISTE = ["rete", "diario", "domanda"];

  // Un neurone per ogni cosa nominata, per ogni fatto e per ogni evento. Una sinapsi collega due neuroni
  // con un ruolo e ricorda l'evento che l'ha creata (nato) e quello che l'ha ritirata (morto).
  let N = {};
  let S = [];
  let E = [];
  let EV = {};
  let DOMANDA = null;
  const stato = { centro: null, passato: false, passo: 0, vista: "rete" };

  const $ = (id) => document.getElementById(id);
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
  const piana = (s) => s.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
  const corta = (s, n) => (s.length > n ? `${s.slice(0, n - 1)}…` : s);
  const intercala = (pezzi, sep) => pezzi.flatMap((p, i) => (i ? [sep, p] : [p]));
  const visibili = () => S.filter((s) => stato.passato || !s.morto);
  const grado = (id) => visibili().filter((s) => s.da === id || s.a === id).length;
  const nomeLungo = (id) => (N[id].forma === "fatto" ? `fatto «${N[id].nome}»` : N[id].nome);
  const eventoDi = (fatto) => (S.find((s) => s.da === fatto) || {}).nato;

  // --- caricamento ---------------------------------------------------------

  async function leggi(percorso) {
    const ctrl = new AbortController();
    const scadenza = setTimeout(() => ctrl.abort(), 20000);
    try {
      const r = await fetch(percorso, { signal: ctrl.signal, cache: "no-store" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return await r.json();
    } finally {
      clearTimeout(scadenza);
    }
  }

  function prepara(dati) {
    N = {};
    for (const [id, n] of Object.entries(dati.neuroni || {})) N[id] = { ...n };
    E = (dati.eventi || []).map((e, i) => ({ ...e, n: i + 1 }));
    EV = {};
    for (const e of E) {
      EV[e.id] = e;
      N[e.id] = { nome: `evento ${e.n}`, tipo: `detto da ${e.origine}`, forma: "evento" };
    }
    // Si tengono solo le sinapsi i cui estremi ed eventi esistono: un dato incompleto non deve rompere la pagina.
    S = (dati.sinapsi || []).filter((s) => N[s.da] && N[s.a] && EV[s.nato] && (!s.morto || EV[s.morto]));
    DOMANDA = dati.domanda && N[dati.domanda.centro] ? dati.domanda : null;
    const ingressi = Object.keys(N).filter((id) => N[id].forma !== "evento" && N[id].forma !== "fatto");
    stato.centro = N[dati.partenza] ? dati.partenza : ingressi.sort((a, b) => grado(b) - grado(a))[0] || Object.keys(N)[0] || null;
    stato.passo = 0;
  }

  async function avvia() {
    const avviso = $("avviso");
    let dati;
    let esempio = false;
    try {
      dati = await leggi("/api/rete");
      if (!Object.keys(dati.neuroni || {}).length) {
        dati = await leggi("/esempio-rete.json");
        esempio = true;
      }
    } catch (e) {
      avviso.hidden = false;
      avviso.replaceChildren(el("strong", { text: "Dati non disponibili. " }), `La lettura è fallita: ${e.message || e}. Ricarica la pagina tra poco.`);
      $("sottotitolo").textContent = "La rete di neuroni e sinapsi memorizzata";
      return;
    }
    prepara(dati);
    avviso.hidden = !esempio;
    if (esempio) {
      avviso.replaceChildren(el("strong", { text: "Questo è un esempio. " }),
        "Il database non contiene ancora dati veri, perché il motore non è ancora collegato alle scritture e a questa pagina. Le frasi vengono dalla visione e dalla prova del 22 novembre 2026. Quando il motore registrerà i fatti, qui compariranno quelli veri.");
    }
    $("sottotitolo").textContent = `${esempio ? "Rete di esempio" : "Rete memorizzata"} · ${Object.keys(N).length - E.length} neuroni, ${S.length} sinapsi, ${E.length} eventi`;
    legenda();
    diario();
    mostra("rete");
  }

  // --- mappa del vicinato --------------------------------------------------

  function vicinato(centro) {
    const evento = N[centro].forma === "evento";
    const mie = evento ? S.filter((s) => s.nato === centro || s.morto === centro) : visibili();
    const anello1 = [];
    const figli = new Map();
    const via = new Map();
    const messi = new Set([centro]);
    const metti = (id, s) => { if (!messi.has(id)) { messi.add(id); anello1.push(id); figli.set(id, []); via.set(id, s); } };
    for (const s of mie) {
      if (evento) metti(s.da, null);
      else if (s.da === centro) metti(s.a, s);
      else if (s.a === centro) metti(s.da, s);
    }
    for (const id of anello1.slice()) {
      if (!evento && N[id].forma !== "fatto") continue;
      for (const s of mie) {
        const altro = s.da === id ? s.a : s.a === id ? s.da : null;
        if (altro && !messi.has(altro)) { messi.add(altro); figli.get(id).push(altro); via.set(altro, s); }
      }
    }
    return { anello1, figli, via, evento, archi: mie.filter((s) => messi.has(s.da) && messi.has(s.a)) };
  }

  function forma(n, r) {
    if (n.forma === "fatto") return sv("path", { d: `M0 ${-r}L${r} 0L0 ${r}L${-r} 0Z` });
    if (n.forma === "valore") return sv("rect", { x: -r * 0.9, y: -r * 0.9, width: r * 1.8, height: r * 1.8, rx: 4 });
    if (n.forma === "evento") return sv("rect", { x: -r * 1.25, y: -r * 0.8, width: r * 2.5, height: r * 1.6, rx: 3 });
    if (n.forma === "regola") {
      const p = [0, 1, 2, 3, 4, 5].map((k) => `${(r * Math.cos((Math.PI / 3) * k)).toFixed(1)} ${(r * Math.sin((Math.PI / 3) * k)).toFixed(1)}`);
      return sv("path", { d: `M${p.join("L")}Z` });
    }
    return sv("circle", { r });
  }

  // Dispone il vicinato su un'ellisse. I nodi che hanno figli (i fatti) stanno a metà strada, i loro figli sul bordo.
  function disponi(v, centro, w) {
    const stretto = w < 520;
    const n1 = v.anello1.length;
    const solo = n1 === 1;
    const h = stretto ? Math.max(360, Math.min(580, 250 + 34 * n1)) : Math.max(380, Math.min(520, 300 + 24 * n1));
    const cx = w / 2;
    const cy = solo ? h * 0.66 : h / 2;
    const rx = Math.max(90, w / 2 - (stretto ? 52 : 130));
    const ry = (solo ? cy : h / 2) - 58;
    const pos = new Map([[centro, { x: cx, y: cy, r: 0, a: null, via: null }]]);
    const pesi = v.anello1.map((id) => 1 + 0.7 * v.figli.get(id).length);
    const totale = pesi.reduce((a, b) => a + b, 0) || 1;
    let inizio = -Math.PI / 2 - (n1 ? (Math.PI * pesi[0]) / totale : 0);
    v.anello1.forEach((id, i) => {
      const settore = (2 * Math.PI * pesi[i]) / totale;
      const a = inizio + settore / 2;
      const figli = v.figli.get(id);
      const k = figli.length ? 0.5 : 1;
      pos.set(id, { x: cx + rx * k * Math.cos(a), y: cy + ry * k * Math.sin(a), r: 13, a, via: v.via.get(id) });
      const apertura = Math.min(settore * 0.9, 2.2);
      figli.forEach((f, j) => {
        const af = a + apertura * ((j + 0.5) / figli.length - 0.5);
        pos.set(f, { x: cx + rx * Math.cos(af), y: cy + ry * Math.sin(af), r: 13, a: af, via: v.via.get(f) });
      });
      inizio += settore;
    });
    return { pos, h, stretto };
  }

  function disegna(contenitore, centro, { accesi, archiAccesi } = {}) {
    const v = vicinato(centro);
    const w = Math.max(300, Math.round(contenitore.clientWidth || 600));
    const { pos, h, stretto } = disponi(v, centro, w);
    const c = pos.get(centro);
    const svg = sv("svg", { viewBox: `0 0 ${w} ${h}`, role: "group", "aria-label": `Vicinato di ${nomeLungo(centro)}` });
    if (v.evento) {
      for (const id of v.anello1) {
        const p = pos.get(id);
        svg.append(sv("line", { x1: c.x, y1: c.y, x2: p.x, y2: p.y, class: "raggio" }));
      }
    }
    const scritte = new Set([...pos.values()].map((p) => p.via).filter(Boolean));
    const etichette = [];
    for (const s of v.archi) {
      const a = pos.get(s.da);
      const b = pos.get(s.a);
      const acceso = archiAccesi && archiAccesi.has(s.id);
      svg.append(sv("line", { x1: a.x, y1: a.y, x2: b.x, y2: b.y, class: `arco${s.morto ? " ritirato" : ""}${acceso ? " acceso" : ""}` }));
      if (scritte.has(s)) continue; // il ruolo è già scritto accanto al nodo
      // A metà strada, salvo quando la metà cade sul neurone scelto: allora la scritta va vicino all'origine.
      const lontano = Math.hypot((a.x + b.x) / 2 - c.x, (a.y + b.y) / 2 - c.y) > 70;
      const q = lontano ? 0.5 : 0.22;
      const t = sv("text", { x: a.x + (b.x - a.x) * q, y: a.y + (b.y - a.y) * q - 4, "text-anchor": "middle", class: "ruolo" });
      t.textContent = corta(s.morto ? `prima: ${s.ruolo}` : s.ruolo, stretto ? 14 : 22);
      etichette.push(t);
    }
    for (const [id, p] of pos) {
      const n = N[id];
      const classi = ["nodo", `forma-${n.forma}`];
      if (id === centro) classi.push("centro");
      if (accesi && accesi.has(id)) classi.push("acceso");
      const g = sv("g", { class: classi.join(" "), transform: `translate(${p.x.toFixed(1)} ${p.y.toFixed(1)})`, tabindex: 0, role: "button", "aria-label": `${nomeLungo(id)}, ${n.tipo}` });
      if (id === centro) {
        const nome = corta(n.nome, 24);
        const larghezza = Math.max(60, 7.4 * nome.length + 26);
        g.append(sv("rect", { x: -larghezza / 2, y: -15, width: larghezza, height: 30, rx: 15 }));
        const t = sv("text", { y: 4.5, "text-anchor": "middle", class: "nome" });
        t.textContent = nome;
        g.append(t);
      } else {
        g.append(forma(n, p.r));
        const ruolo = p.via ? corta(p.via.morto ? `prima: ${p.via.ruolo}` : p.via.ruolo, stretto ? 14 : 22) : "";
        const diLato = !stretto && Math.abs(Math.cos(p.a)) > 0.45;
        const sopra = !stretto && !diLato && Math.sin(p.a) < 0;
        let x = 0;
        let ancora = "middle";
        let yRuolo = p.r + 13;
        let yNome = ruolo ? p.r + 27 : p.r + 14;
        if (diLato) {
          const verso = Math.cos(p.a) > 0 ? 1 : -1;
          x = verso * (p.r + 7);
          ancora = verso > 0 ? "start" : "end";
          yRuolo = -3;
          yNome = ruolo ? 11 : 4;
        } else if (sopra) {
          yNome = -(p.r + 8);
          yRuolo = -(p.r + 22);
        }
        if (ruolo) {
          const tr = sv("text", { x, y: yRuolo, "text-anchor": ancora, class: "ruolo" });
          tr.textContent = ruolo;
          g.append(tr);
        }
        const tn = sv("text", { x, y: yNome, "text-anchor": ancora, class: "nome" });
        tn.textContent = corta(n.nome, stretto ? 13 : 20);
        g.append(tn);
      }
      g.addEventListener("click", () => vai(id));
      g.addEventListener("keydown", (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); vai(id); } });
      svg.append(g);
    }
    svg.append(...etichette);
    contenitore.replaceChildren(svg);
  }

  // --- scheda leggibile ----------------------------------------------------

  function bottone(id, testo) {
    const b = el("button", { type: "button", class: "collegamento", text: testo || nomeLungo(id) });
    b.addEventListener("click", () => vai(id));
    return b;
  }
  function fonte(evId) {
    const e = EV[evId];
    if (!e) return null;
    return el("p", { class: "fonte" }, `Da ${e.origine}: «${e.frase}» `, bottone(evId, `evento ${e.n}`));
  }
  function rigaFatto(f, evidenzia) {
    const parti = visibili().filter((s) => s.da === f)
      .map((s) => [`${s.ruolo}: `, s.a === evidenzia ? el("strong", { text: N[s.a].nome }) : bottone(s.a)]);
    return el("p", {}, intercala(parti, " · "));
  }
  function schedaRegola(r) {
    const ingressi = Array.isArray(r.ingressi) ? r.ingressi : [];
    const somma = ingressi.reduce((a, x) => a + (x.attivo ? x.peso : 0), 0);
    const righe = ingressi.map((x) => el("tr", {}, el("td", { text: x.nome }), el("td", { class: "num", text: String(x.peso) }), el("td", { text: x.attivo ? "acceso" : "spento" })));
    const su = N[r.su] ? bottone(r.su) : "questo caso";
    return [
      el("h3", { text: "Come decide" }),
      el("div", { class: "scorri" }, el("table", {},
        el("thead", {}, el("tr", {}, el("th", { text: "Segnale" }), el("th", { class: "num", text: "Peso" }), el("th", {}, "Stato per ", N[r.su] ? bottone(r.su) : "questo caso"))),
        el("tbody", {}, righe))),
      el("p", { class: "voce" }, `Somma ${somma}, soglia ${r.soglia}: la regola ${somma >= r.soglia ? "scatta" : "non scatta"} per `, su, "."),
    ];
  }
  function schedaCosa(id) {
    const out = [];
    const vis = visibili();
    if (N[id].regola) out.push(...schedaRegola(N[id].regola));
    const fatti = [...new Set(vis.filter((s) => s.a === id && N[s.da].forma === "fatto").map((s) => s.da))];
    if (fatti.length) {
      out.push(el("h3", { text: "Fatti in cui compare" }));
      for (const f of fatti) out.push(el("div", { class: "voce" }, rigaFatto(f, id), fonte(eventoDi(f))));
    }
    const mie = S.filter((s) => s.da === id);
    const ruoli = [...new Set(mie.map((s) => s.ruolo))];
    if (ruoli.length) {
      out.push(el("h3", { text: "Collegamenti" }));
      for (const r of ruoli) {
        const voce = el("div", { class: "voce" });
        for (const s of mie.filter((x) => x.ruolo === r && !x.morto)) {
          voce.append(el("p", {}, `${r}: `, bottone(s.a), " · dall'", bottone(s.nato, `evento ${EV[s.nato].n}`)));
        }
        for (const s of mie.filter((x) => x.ruolo === r && x.morto)) {
          voce.append(el("p", { class: "passato" }, "prima: ", bottone(s.a), ", ritirata dall'", bottone(s.morto, `evento ${EV[s.morto].n}`)));
        }
        out.push(voce);
      }
    }
    const entranti = vis.filter((s) => s.a === id && N[s.da].forma !== "fatto");
    if (entranti.length) {
      out.push(el("h3", { text: "Chi punta qui" }));
      for (const s of entranti) out.push(el("p", { class: "voce" }, bottone(s.da), ` · ${s.ruolo}`, s.morto ? " (ritirata)" : ""));
    }
    if (!out.length) out.push(el("p", { class: "secondario", text: "Nessuna sinapsi in vigore. Spunta «Mostra anche il passato» per vedere quelle ritirate." }));
    return out;
  }
  function schedaFatto(id) {
    const out = [el("h3", { text: "Partecipanti" })];
    for (const s of visibili().filter((x) => x.da === id)) out.push(el("p", { class: "voce" }, `${s.ruolo}: `, bottone(s.a)));
    out.push(el("h3", { text: "Da dove viene" }), fonte(eventoDi(id)));
    return out;
  }
  function schedaEvento(id) {
    const e = EV[id];
    const riga = (s) => el("p", { class: "voce" }, bottone(s.da), ` · ${s.ruolo} · `, bottone(s.a));
    const create = S.filter((s) => s.nato === id);
    const ritirate = S.filter((s) => s.morto === id);
    const out = [el("blockquote", { text: `«${e.frase}»` }), el("h3", { text: "Sinapsi create" }), ...create.map(riga)];
    if (ritirate.length) out.push(el("h3", { text: "Sinapsi ritirate" }), ...ritirate.map(riga));
    return out;
  }
  function scheda() {
    const id = stato.centro;
    const n = N[id];
    const titolo = n.forma === "fatto" ? `Fatto: ${n.nome}` : n.forma === "evento" ? `Evento ${EV[id].n}` : n.nome;
    const corpo = n.forma === "evento" ? schedaEvento(id) : n.forma === "fatto" ? schedaFatto(id) : schedaCosa(id);
    $("scheda-neurone").replaceChildren(el("div", { class: "scheda-testa" }, el("h2", { text: titolo }), el("span", { class: "tag", text: n.tipo })), ...corpo);
  }

  // --- elenco, diario, domanda ---------------------------------------------

  function elenco() {
    const q = piana($("cerca").value.trim());
    const voci = Object.keys(N)
      .filter((id) => ["cosa", "valore", "regola"].includes(N[id].forma) && N[id].tipo !== "numero")
      .filter((id) => !q || piana(N[id].nome).includes(q) || piana(N[id].tipo).includes(q))
      .sort((a, b) => grado(b) - grado(a) || N[a].nome.localeCompare(N[b].nome, "it"))
      .slice(0, 200);
    if (!voci.length) { $("elenco").replaceChildren(el("li", { class: "vuoto", text: "Nessun neurone con questo nome." })); return; }
    $("elenco").replaceChildren(...voci.map((id) => {
      const b = el("button", { type: "button", class: id === stato.centro ? "attivo" : "" },
        el("span", { class: "voce-nome", text: N[id].nome }), el("span", { class: "voce-tipo", text: `${N[id].tipo} · ${grado(id)} sinapsi` }));
      b.addEventListener("click", () => vai(id));
      return el("li", {}, b);
    }));
  }
  function legenda() {
    const scelto = sv("svg", { width: 30, height: 16, viewBox: "-15 -8 30 16", "aria-hidden": "true" });
    const gs = sv("g", { class: "nodo centro" });
    gs.append(sv("rect", { x: -13, y: -6, width: 26, height: 12, rx: 6 }));
    scelto.append(gs);
    $("legenda").replaceChildren(...FORME.map(([f, nome]) => {
      const svg = sv("svg", { width: 22, height: 16, viewBox: "-11 -8 22 16", "aria-hidden": "true" });
      const g = sv("g", { class: `nodo forma-${f}` });
      g.append(forma({ forma: f }, 6));
      svg.append(g);
      return el("span", {}, svg, nome);
    }), el("span", {}, scelto, "il neurone scelto"));
  }
  function diario() {
    if (!E.length) { $("diario").replaceChildren(el("li", { class: "vuoto-tabella", text: "Nessun evento registrato." })); return; }
    $("diario").replaceChildren(...E.slice().reverse().map((e) => {
      const create = S.filter((s) => s.nato === e.id).length;
      const ritirate = S.filter((s) => s.morto === e.id).length;
      return el("li", { class: "scheda" },
        el("p", { class: "secondario", text: `Evento ${e.n} · detto da ${e.origine}` }),
        el("blockquote", { text: `«${e.frase}»` }),
        el("p", { class: "effetti" },
          el("span", { class: "pastiglia", text: create === 1 ? "1 sinapsi creata" : `${create} sinapsi create` }),
          ritirate ? el("span", { class: "pastiglia", text: ritirate === 1 ? "1 sinapsi ritirata" : `${ritirate} sinapsi ritirate` }) : null,
          bottone(e.id, "Vedi nella rete")));
    }));
  }
  function rendiDomanda() {
    $("domanda-corpo").hidden = !DOMANDA;
    $("domanda-vuota").hidden = Boolean(DOMANDA);
    if (!DOMANDA) return;
    const passi = DOMANDA.passi || [];
    const k = stato.passo;
    const p = k ? passi[k - 1] : { nodi: [], archi: [] };
    $("domanda-testo").textContent = `«${DOMANDA.testo}»`;
    disegna($("mappa-domanda"), DOMANDA.centro, { accesi: new Set(p.nodi), archiAccesi: new Set(p.archi) });
    $("passi").replaceChildren(...passi.map((x, i) => el("li", { class: i === k - 1 ? "ora" : "", text: x.testo })));
    $("conteggio").textContent = `Sinapsi percorse: ${p.archi.length} su ${S.length} della rete.`;
    $("avanti").disabled = k >= passi.length;
    const r = $("risposta");
    r.hidden = k < passi.length || !DOMANDA.risposta;
    if (!r.hidden) r.replaceChildren(el("p", {}, el("strong", { text: `Risposta: ${DOMANDA.risposta.testo}.` })), fonte(DOMANDA.risposta.evento));
  }
  function rendiRete() {
    if (!stato.centro) {
      $("mappa-titolo").textContent = "La rete è vuota";
      $("mappa").replaceChildren();
      $("scheda-neurone").replaceChildren(el("p", { class: "secondario", text: "Nessun neurone registrato." }));
      $("elenco").replaceChildren();
      return;
    }
    $("mappa-titolo").textContent = `Vicinato di ${nomeLungo(stato.centro)}`;
    disegna($("mappa"), stato.centro);
    scheda();
    elenco();
  }
  function mostra(vista) {
    stato.vista = vista;
    for (const v of VISTE) {
      $(`vista-${v}`).hidden = v !== vista;
      $(`tab-${v}`).setAttribute("aria-selected", String(v === vista));
      $(`tab-${v}`).tabIndex = v === vista ? 0 : -1;
    }
    if (vista === "rete") rendiRete();
    if (vista === "domanda") rendiDomanda();
  }
  function vai(id) {
    if (!N[id]) return;
    stato.centro = id;
    if (stato.vista === "rete") rendiRete();
    else mostra("rete");
  }

  for (const v of VISTE) $(`tab-${v}`).addEventListener("click", () => mostra(v));
  $("tab-rete").parentElement.addEventListener("keydown", (ev) => {
    if (ev.key !== "ArrowRight" && ev.key !== "ArrowLeft") return;
    const j = (VISTE.indexOf(stato.vista) + (ev.key === "ArrowRight" ? 1 : VISTE.length - 1)) % VISTE.length;
    mostra(VISTE[j]);
    $(`tab-${VISTE[j]}`).focus();
  });
  $("cerca").addEventListener("input", elenco);
  $("passato").addEventListener("change", (ev) => { stato.passato = ev.target.checked; rendiRete(); });
  $("avanti").addEventListener("click", () => { stato.passo = Math.min((DOMANDA.passi || []).length, stato.passo + 1); rendiDomanda(); });
  $("daccapo").addEventListener("click", () => { stato.passo = 0; rendiDomanda(); });
  let attesa = 0;
  window.addEventListener("resize", () => { clearTimeout(attesa); attesa = setTimeout(() => { if (stato.centro) mostra(stato.vista); }, 150); });

  avvia();
})();
