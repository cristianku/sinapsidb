// Comune alle pagine del portale: la versione installata accanto al nome, e il tema chiaro o scuro, che segue il
// sistema finché non lo si sceglie col pulsante.
(() => {
  "use strict";
  const leggi = () => { try { return localStorage.getItem("sinapsidb-tema"); } catch { return null; } };
  const scrivi = (v) => { try { localStorage.setItem("sinapsidb-tema", v); } catch { /* memoria del browser non disponibile */ } };
  const radice = document.documentElement;
  const salvato = leggi();
  if (salvato === "light" || salvato === "dark") radice.dataset.theme = salvato;
  // La versione installata, ben visibile accanto al nome in cima a ogni pagina: la dice il portale stesso, adesso.
  const titolo = document.querySelector(".marchio h1");
  if (titolo) {
    const versione = document.createElement("span");
    versione.className = "versione";
    versione.id = "versione";
    versione.textContent = "versione …";
    versione.title = "La versione di SinapsiDB installata su questa macchina";
    titolo.append(" ", versione);
    fetch("/healthz", { cache: "no-store" })
      .then((risposta) => (risposta.ok ? risposta.json() : Promise.reject(new Error(String(risposta.status)))))
      .then((stato) => { versione.textContent = `versione ${stato.version}`; })
      .catch(() => { versione.textContent = "versione non letta"; });
  }
  const bottone = document.getElementById("tema");
  if (!bottone) return;
  const scuro = () => (radice.dataset.theme ? radice.dataset.theme === "dark" : window.matchMedia("(prefers-color-scheme: dark)").matches);
  const etichetta = () => { bottone.textContent = scuro() ? "Tema chiaro" : "Tema scuro"; };
  etichetta();
  bottone.addEventListener("click", () => {
    radice.dataset.theme = scuro() ? "light" : "dark";
    scrivi(radice.dataset.theme);
    etichetta();
  });
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", etichetta);
})();
