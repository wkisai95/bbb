/* App Tasse proprietario.
   Legge i file cifrati che l'app bbb salva in config/cifrati (formato in docs/cifratura.md), ne prende le stime di
   incasso e calcola imposta sostitutiva, INPS e quanto resta al proprietario in regime forfettario. */
(function () {
  "use strict";
  var K = window.BBBCifrato;
  var KEY = "tasse.v1", REFRESH_MS = 5 * 60 * 1000;
  var DEFAULT_REPO = { utente: "wkisai95", repository: "bbb", ramo: "main" };
  var PERIODS = ["30", "60", "90", "365"];
  var ATECO = {
    "55.20.42": { name: "Alloggi per vacanze", coef: 0.40 },
    "68.32.01": { name: "Gestione per conto terzi", coef: 0.86 }
  };
  var INPS = { minimale: 18808, fisso: 4611.64, al1: 0.2448, al2: 0.2548, soglia2: 56224, gs: 0.2607 };
  var VAT = 0.22, LIMITE = 85000;
  /* portali di bbb: servono solo per gli appartamenti senza proprietario, letti dal file del gestore */
  var CHANNELS = { airbnb: ["Airbnb", 15.5, true, "portale"], booking: ["Booking.com", 16.5, true, "proprietario"],
    vrbo: ["Vrbo", 8, true, "proprietario"], direct: ["Prenotazione diretta", 0, false, "proprietario"] };

  // ------------------------------------------------------------------ stato salvato sul telefono
  var S = { links: [], cfg: {}, sel: null, strat: null };
  try { var sv = JSON.parse(localStorage.getItem(KEY)); if (sv && Array.isArray(sv.links)) S = Object.assign(S, sv); } catch (e) {}
  function save() { try { localStorage.setItem(KEY, JSON.stringify(S)); } catch (e) {} }
  function cfgOf(name) {
    if (!S.cfg[name]) S.cfg[name] = { ateco: "55.20.42", inps: "comm", rid35: true, startup: true, mgmt: 20, mgmtBase: "gross", mgmtVat: false, costs: true, other: 0 };
    return S.cfg[name];
  }

  // dati letti da GitHub (non salvati: si rileggono a ogni avvio)
  var owners = {}, meta = null, status = { state: "idle", at: null, msg: "" }, lastSig = "";

  // ------------------------------------------------------------------ utilità
  var eur = new Intl.NumberFormat("it-IT", { style: "currency", currency: "EUR", maximumFractionDigits: 0, useGrouping: "always" });
  var eur2 = new Intl.NumberFormat("it-IT", { style: "currency", currency: "EUR", minimumFractionDigits: 2, maximumFractionDigits: 2, useGrouping: "always" });
  function pct(x) { return (x * 100).toLocaleString("it-IT", { maximumFractionDigits: 1 }) + "%"; }
  function $(id) { return document.getElementById(id); }
  function el(tag, attrs, kids) {
    var e = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      var v = attrs[k];
      if (k === "text") e.textContent = v; else if (k === "class") e.className = v;
      else if (k.slice(0, 2) === "on") e.addEventListener(k.slice(2), v); else e.setAttribute(k, v);
    });
    (kids || []).forEach(function (c) { if (c) e.appendChild(typeof c === "string" ? document.createTextNode(c) : c); });
    return e;
  }
  function note(msg, bad) { var b = $("alert"); b.innerHTML = ""; b.dataset.msg = msg ? "1" : ""; if (msg) b.appendChild(el("div", { class: "alert" + (bad ? " bad" : ""), text: msg })); }
  function when(iso) {
    if (!iso) return "?";
    var d = new Date(iso); if (isNaN(d)) return iso;
    return d.toLocaleDateString("it-IT", { day: "numeric", month: "short" }) + " alle " + d.toLocaleTimeString("it-IT", { hour: "2-digit", minute: "2-digit" });
  }

  // ------------------------------------------------------------------ collegamenti (inviti bbb1: o codici)
  function parseLink(text) {
    var inv = K.leggiInvito(text);
    if (inv) return inv;
    var t = String(text || "").trim();
    if (K.codiceValido(t)) return Object.assign({}, DEFAULT_REPO, { codice: K.normalizza(t) });
    var m = /[0-9A-Za-z]{4}(?:[-\s]?[0-9A-Za-z]{4}){5}/.exec(t);          // codice dentro un testo più lungo
    if (m && K.codiceValido(m[0])) return Object.assign({}, DEFAULT_REPO, { codice: K.normalizza(m[0]) });
    return null;
  }
  function addLink(text) {
    var l = parseLink(text);
    if (!l) { note("Non trovo un invito bbb1: né un codice di 24 caratteri nel testo.", true); return false; }
    if (S.links.some(function (x) { return x.codice === l.codice; })) { note("Questo codice è già collegato."); refresh(); return true; }
    S.links.push(l); save(); note(""); refresh(); return true;
  }
  window.tasseInvite = function (text) { addLink(text); };     // invito condiviso da bbb al guscio Android

  // ------------------------------------------------------------------ lettura dei file da GitHub
  function getFile(l, id) {
    var path = K.percorso(id);
    var api = "https://api.github.com/repos/" + l.utente + "/" + l.repository + "/contents/" + path + "?ref=" + encodeURIComponent(l.ramo);
    var raw = K.indirizzo(l.utente, l.repository, l.ramo, id) + "?t=" + Date.now();
    function json(r) { if (r.status === 404) return null; if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); }
    // l'API dà subito il file appena salvato; se il limite di richieste è finito si passa alla copia raw
    return fetch(api, { headers: { Accept: "application/vnd.github.raw+json" }, cache: "no-store" }).then(function (r) {
      if (r.status === 403 || r.status === 429 || r.status >= 500) return fetch(raw, { cache: "no-store" }).then(json);
      return json(r);
    }, function () { return fetch(raw, { cache: "no-store" }).then(json); });
  }
  function openDoc(l, codice) {
    return K.idFile(codice).then(function (id) {
      return getFile(l, id).then(function (busta) {
        if (!busta) { var e = new Error("Su GitHub non c'è il file di questo codice: è sbagliato oppure bbb l'ha sostituito con uno nuovo."); e.tipo = "assente"; throw e; }
        return K.decifra(busta, codice);
      });
    });
  }
  function channelInfo(a) {
    var c = CHANNELS[a.channel] || CHANNELS.airbnb;
    return { name: c[0], pct: a.fee === null || a.fee === undefined || a.fee === "" ? c[1] : +a.fee,
      vat: a.vat === null || a.vat === undefined ? c[2] : !!a.vat, tax: c[3] };
  }
  function addOwner(out, name, apts, doc, source) {
    out[name] = { name: name, apts: apts, saved: doc.salvato, dataDay: doc.dati_del, from: doc.dal,
      strategies: doc.strategie || [], managerStrategy: doc.strategia_gestore, source: source };
  }
  /* un file di proprietario: i suoi appartamenti già con le stime */
  function ownerFromDoc(out, doc, source) { addOwner(out, doc.proprietario || "Proprietario", doc.appartamenti || [], doc, source); }
  /* il file del gestore: apre i file dei proprietari con i loro codici; gli appartamenti senza proprietario li prende da qui */
  function ownersFromManager(out, l, doc) {
    var listed = {};
    var jobs = (doc.proprietari || []).map(function (p) {
      (p.appartamenti || []).forEach(function (id) { listed[id] = 1; });
      return openDoc(l, p.codice).then(function (d) { ownerFromDoc(out, d, "gestore"); }, function () {
        // file del proprietario non ancora scritto: ricostruisco dai dati del gestore
        var apts = (doc.appartamenti || []).filter(function (a) { return (p.appartamenti || []).indexOf(a.id) >= 0; }).map(function (a) { return aptFromManager(a, doc); });
        addOwner(out, p.nome, apts, doc, "gestore");
      });
    });
    var rest = (doc.appartamenti || []).filter(function (a) { return !listed[a.id]; }).map(function (a) { return aptFromManager(a, doc); });
    if (rest.length) addOwner(out, "Senza proprietario", rest, doc, "gestore");
    return Promise.all(jobs);
  }
  function aptFromManager(a, doc) {
    var c = channelInfo(a);
    return { id: a.id, nome: a.name || "Appartamento", zona: a.zone, portale: c.name, commissione_percentuale: c.pct,
      iva_sulla_commissione: c.vat, tassa_soggiorno: c.tax, stime: (doc.stime || {})[a.id] || {} };
  }

  var busy = false, again = false;
  function refresh() {
    if (busy) { again = true; return; }
    busy = true; again = false;
    if (!S.links.length) { busy = false; owners = {}; status = { state: "idle" }; render(); return; }
    if (!K || !window.crypto || !window.crypto.subtle) { busy = false; note("Questo telefono non permette di aprire i file cifrati.", true); return; }
    status.state = "loading"; setLive();
    var out = {}, errors = [];
    S.links.forEach(function (l) { delete l.errore; });
    Promise.all(S.links.map(function (l) {
      return openDoc(l, l.codice).then(function (doc) {
        l.tipo = doc.tipo; l.nome = doc.tipo === "gestore" ? "Gestore" : doc.proprietario;
        return doc.tipo === "gestore" ? ownersFromManager(out, l, doc) : ownerFromDoc(out, doc, "proprietario");
      }).catch(function (e) { l.errore = e && e.message || "errore"; errors.push((l.nome || l.codice.slice(0, 4) + "…") + ": " + l.errore); });
    })).then(function () {
      busy = false;
      save();
      var got = Object.keys(out).length;
      if (got) owners = out;
      status = { state: errors.length && !got ? "error" : "ok", at: new Date(), msg: errors.join(" · ") };
      var sig = JSON.stringify(Object.keys(out).map(function (k) { return [k, out[k].saved]; }));
      var changed = sig !== lastSig; lastSig = sig;
      if (errors.length) note("Non riesco ad aprire: " + errors.join(" · "), !got);
      else if (changed && got) note("");
      render();
      if (changed && got) { $("hero").classList.add("upd"); setTimeout(function () { $("hero").classList.remove("upd"); }, 1500); }
      if (again) refresh();
    }).catch(function () {
      busy = false; status = { state: "error", at: new Date(), msg: "rete" };
      note("GitHub non raggiungibile: riprovo tra qualche minuto.", !Object.keys(owners).length); setLive();
    });
  }

  // ------------------------------------------------------------------ calcoli
  function strategyOf(o) {
    var ids = o.strategies.map(function (s) { return s.id; });
    if (S.strat && ids.indexOf(S.strat) >= 0) return S.strat;
    return ids.indexOf(o.managerStrategy) >= 0 ? o.managerStrategy : (ids[0] || "resa");
  }
  function est(a, strat, p) { var s = a.stime && a.stime[strat]; return (s && s[p]) || {}; }
  function sums(o, strat, p, cfg) {
    var t = { R: 0, fee: 0, iva: 0, costs: 0, sold: 0, nights: 0, cityTax: 0 };
    o.apts.forEach(function (a) {
      var x = est(a, strat, p), fee = +x.commissioni || 0;
      t.R += +x.incasso_lordo || 0; t.fee += fee; t.costs += +x.costi || 0;
      t.iva += a.iva_sulla_commissione ? fee * VAT / (1 + VAT) : 0;            // bbb dà la commissione IVA compresa
      t.sold += +x.notti_vendute || 0; t.nights += +x.notti || 0; t.cityTax += +x.tassa_soggiorno_da_versare || 0;
    });
    var base = cfg.mgmtBase === "net" ? t.R - t.fee : t.R;
    t.mgmt = base * cfg.mgmt / 100; t.mgmtIva = cfg.mgmtVat ? t.mgmt * VAT : 0;
    t.costs = cfg.costs ? t.costs : 0;
    return t;
  }
  function taxes(R, cfg) {
    var at = ATECO[cfg.ateco] || ATECO["55.20.42"], reddito = R * at.coef, fisso = 0, perc = 0;
    if (cfg.inps === "comm") {
      fisso = INPS.fisso;
      perc = Math.min(Math.max(0, reddito - INPS.minimale), INPS.soglia2 - INPS.minimale) * INPS.al1 + Math.max(0, reddito - INPS.soglia2) * INPS.al2;
      if (cfg.rid35) { fisso *= 0.65; perc *= 0.65; }
    } else perc = reddito * INPS.gs;
    var inps = fisso + perc, imponibile = Math.max(0, reddito - inps), aliq = cfg.startup ? 0.05 : 0.15;
    return { coef: at.coef, reddito: reddito, fisso: fisso, perc: perc, inps: inps, imponibile: imponibile, aliq: aliq, imposta: imponibile * aliq };
  }
  function compute(name) {
    var o = owners[name], cfg = cfgOf(name), strat = strategyOf(o), per = {};
    PERIODS.forEach(function (p) { per[p] = sums(o, strat, p, cfg); });
    var y = per["365"], t = taxes(y.R, cfg), other = +cfg.other || 0;
    var dovuto = t.imposta + t.inps, rate = y.R ? dovuto / y.R : 0;
    PERIODS.forEach(function (p) {                    // tasse da accantonare nel periodo: in proporzione all'incasso
      var x = per[p]; x.tax = t.imposta * (y.R ? x.R / y.R : 0); x.inps = t.inps * (y.R ? x.R / y.R : 0);
      x.other = other * (+p / 365);
      x.pocket = x.R - x.fee - x.costs - x.mgmt - x.mgmtIva - x.other - x.tax - x.inps;
    });
    return { o: o, cfg: cfg, strat: strat, per: per, y: y, t: t, other: other, rate: rate, pocket: per["365"].pocket };
  }

  // ------------------------------------------------------------------ disegno
  var PLABEL = { "30": "30 giorni", "60": "60 giorni", "90": "90 giorni", "365": "12 mesi" };
  var PSHORT = { "30": "30 g", "60": "60 g", "90": "90 g", "365": "12 mesi" };
  if (!S.period) S.period = "365";
  if (!S.view) S.view = "sum";
  function setLive() {
    var box = $("status"), names = Object.keys(owners), o = names.length ? owners[S.sel] || owners[names[0]] : null, txt;
    $("refresh").classList.toggle("spin", status.state === "loading");
    if (status.state === "loading" && !o) txt = "Leggo i dati da GitHub…";
    else if (!S.links.length) txt = "Nessun collegamento";
    else if (o) txt = "Stime di bbb del " + when(o.saved);
    else txt = status.state === "error" ? "Dati non disponibili" : "…";
    box.innerHTML = "";
    box.appendChild(el("i", { class: status.state === "ok" ? "ok" : status.state === "error" ? "err" : "" }));
    box.appendChild(el("span", { text: txt }));
  }
  function seg(id) {
    var box = $(id); box.innerHTML = "";
    PERIODS.forEach(function (p) { box.appendChild(el("button", { class: p === S.period ? "on" : "", text: PSHORT[p], onclick: function () { S.period = p; save(); render(); } })); });
  }
  function li(k, v, opt) {
    opt = opt || {};
    var kk = el("span", { class: "k" }, [k]); if (opt.small) kk.appendChild(el("small", { text: opt.small }));
    return el("li", { class: opt.cls || "" }, [opt.color ? el("span", { class: "sw", style: "background:" + opt.color }) : null, kk,
      el("span", { class: "v" + (v < 0 ? " neg" : ""), text: typeof v === "number" ? eur.format(v) : v }), opt.p !== undefined ? el("span", { class: "p", text: opt.p }) : null]);
  }
  function card(id, title, lead) { var c = $(id); c.innerHTML = ""; if (title) c.appendChild(el("h2", { text: title })); if (lead) c.appendChild(el("p", { class: "lead", text: lead })); return c; }
  function render() {
    setLive();
    var names = Object.keys(owners).sort(function (a, b) { return a === "Senza proprietario" ? 1 : b === "Senza proprietario" ? -1 : a.localeCompare(b, "it"); });
    $("empty").classList.toggle("hidden", S.links.length > 0);
    $("main").classList.toggle("hidden", !S.links.length);
    renderAddForms(); renderLinks();
    document.querySelectorAll("#tabs button").forEach(function (b) { b.classList.toggle("on", b.getAttribute("data-v") === S.view); });
    document.querySelectorAll("[data-view]").forEach(function (v) { v.classList.toggle("hidden", v.getAttribute("data-view") !== S.view); });
    var os = $("owner");
    os.classList.toggle("hidden", names.length < 2); os.innerHTML = "";
    if (!names.length) { ["hero", "taxes", "dates", "apts", "regime"].forEach(function (id) { $(id).innerHTML = ""; }); return; }
    if (names.indexOf(S.sel) < 0) S.sel = names[0];
    names.forEach(function (n) { var op = el("option", { value: n, text: n }); if (n === S.sel) op.selected = true; os.appendChild(op); });
    var c = compute(S.sel);
    seg("period"); seg("period2");
    renderHero(c); renderTaxes(c); renderDates(c); renderApts(c); renderRegime(c);
  }
  function renderHero(c) {
    var x = c.per[S.period], h = card("hero");
    var parts = [["Resta al proprietario", x.pocket, "var(--k-net)"], ["Imposta sostitutiva " + pct(c.t.aliq), x.tax, "var(--k-tax)"],
      ["Contributi INPS", x.inps, "var(--k-inps)"], ["Gestione " + String(c.cfg.mgmt).replace(".", ",") + "%" + (c.cfg.mgmtBase === "net" ? " sul netto" : " sul lordo"), x.mgmt + x.mgmtIva, "var(--k-mgmt)"],
      ["Portali e costi", x.fee + x.costs + x.other, "var(--k-fee)"]];
    h.appendChild(el("div", { class: "label", text: "Resta al proprietario · " + PLABEL[S.period] }));
    h.appendChild(el("div", { class: "big", text: eur.format(x.pocket) }));
    h.appendChild(el("div", { class: "sub", text: "su " + eur.format(x.R) + " di incasso lordo · " + Math.round(x.sold) + " notti vendute stimate" }));
    var bar = el("div", { class: "stack" });
    parts.slice().reverse().forEach(function (q) { if (q[1] > 0 && x.R) bar.appendChild(el("span", { style: "width:" + (q[1] / x.R * 100) + "%;background:" + q[2] })); });
    h.appendChild(bar);
    var ul = el("ul", { class: "rows" });
    ul.appendChild(li("Incasso lordo", x.R, { p: "" }));
    ul.appendChild(li("Commissioni portali", -x.fee, { color: "var(--k-fee)", small: x.iva ? "con IVA in reverse charge (" + eur.format(x.iva) + ")" : "", p: pct1(x.fee, x.R) }));
    if (x.costs) ul.appendChild(li("Costi per notte", -x.costs, { color: "var(--k-fee)", small: "pulizie e simili, da bbb", p: pct1(x.costs, x.R) }));
    if (x.other) ul.appendChild(li("Altri costi", -x.other, { color: "var(--k-fee)", p: pct1(x.other, x.R) }));
    ul.appendChild(li(parts[3][0], -(x.mgmt + x.mgmtIva), { color: parts[3][2], small: x.mgmtIva ? "IVA compresa" : "", p: pct1(x.mgmt + x.mgmtIva, x.R) }));
    ul.appendChild(li("Contributi INPS", -x.inps, { color: parts[2][2], p: pct1(x.inps, x.R) }));
    ul.appendChild(li(parts[1][0], -x.tax, { color: parts[1][2], p: pct1(x.tax, x.R) }));
    ul.appendChild(li("Resta al proprietario", x.pocket, { cls: "total", color: parts[0][2], p: pct1(x.pocket, x.R) }));
    h.appendChild(ul);
    var al = $("alert"); if (!al.dataset.msg) al.innerHTML = "";
    if (c.y.R > LIMITE && !al.dataset.msg) al.appendChild(el("div", { class: "alert bad", text: "I ricavi stimati superano 85.000 €: oltre questa soglia si esce dal forfettario." }));
  }
  function pct1(v, R) { return R ? Math.round(v / R * 100) + "%" : ""; }
  function renderTaxes(c) {
    var x = c.per[S.period], t = c.t, h = card("taxes", "Da versare", S.period === "365" ? "Prossimi 12 mesi" : "Quota maturata nei prossimi " + PLABEL[S.period] + ", in proporzione all'incasso");
    var ul = el("ul", { class: "rows" });
    ul.appendChild(li("Imposta sostitutiva", x.tax, { small: pct(t.aliq) + " sul reddito forfettario" }));
    ul.appendChild(li("Contributi INPS", x.inps, { small: c.cfg.inps === "comm" ? "gestione commercianti" + (c.cfg.rid35 ? ", riduzione 35%" : "") : "gestione separata" }));
    if (x.iva) ul.appendChild(li("IVA sulle commissioni", x.iva, { small: "reverse charge, già tolta dall'incasso" }));
    ul.appendChild(li("Totale", x.tax + x.inps + x.iva, { cls: "total" }));
    h.appendChild(ul);
    h.appendChild(el("div", { class: "callout", text: "Accantona " + eur2.format(c.rate * 100) + " ogni 100 € incassati per imposta e INPS" + (x.iva ? ", più l'IVA delle commissioni ogni mese." : ".") }));
    var d = el("details", {}, [el("summary", { text: "Come si calcola" })]), u2 = el("ul", { class: "rows" });
    u2.appendChild(li("Ricavi dei prossimi 12 mesi", c.y.R));
    u2.appendChild(li("Coefficiente ATECO " + c.cfg.ateco, pct(t.coef)));
    u2.appendChild(li("Reddito forfettario", t.reddito));
    if (c.cfg.inps === "comm") {
      u2.appendChild(li("INPS, quota fissa", t.fisso, { small: c.cfg.rid35 ? "4.611,64 € ridotti del 35%" : "sul minimale di 18.808 €" }));
      u2.appendChild(li("INPS, oltre il minimale", t.perc, { small: "24,48% del reddito sopra 18.808 €" + (c.cfg.rid35 ? ", ridotto del 35%" : "") }));
    } else u2.appendChild(li("INPS gestione separata", t.perc, { small: "26,07% del reddito" }));
    u2.appendChild(li("Contributi dedotti", -t.inps));
    u2.appendChild(li("Base imponibile", t.imponibile));
    u2.appendChild(li("Imposta sostitutiva " + pct(t.aliq), eur2.format(t.imposta), { cls: "total" }));
    d.appendChild(u2);
    if (c.cfg.inps === "comm" && t.reddito < INPS.minimale) d.appendChild(el("p", { class: "muted", text: "Il reddito è sotto il minimale: la quota fissa INPS si paga comunque per intero." }));
    h.appendChild(d);
  }
  function renderDates(c) {
    var h = card("dates", "Scadenze"), ul = el("ul", { class: "rows" }), due = c.t.imposta + c.t.perc;
    if (c.y.iva) ul.appendChild(li("Ogni 16 del mese", c.y.iva / 12, { small: "IVA reverse charge del mese prima (media)" }));
    if (c.cfg.inps === "comm") ul.appendChild(li("16 mag · 20 ago · 16 nov · 16 feb", c.t.fisso / 4, { small: "INPS quota fissa, ogni rata" }));
    ul.appendChild(li("30 giugno", due * 0.4, { small: "saldo dell'anno prima + 1° acconto 40%" }));
    ul.appendChild(li("30 novembre", due * 0.6, { small: "2° acconto 60%" }));
    if (c.y.cityTax) ul.appendChild(li("Scadenze del Comune", c.y.cityTax, { small: "tassa di soggiorno da riversare, 12 mesi" }));
    h.appendChild(ul);
    h.appendChild(el("p", { class: "muted", text: "Acconti di un anno a regime: nel primo anno di attività non si versano acconti d'imposta." }));
  }
  function renderApts(c) {
    var y = c.per[S.period], h = card("apts", c.o.apts.length === 1 ? "1 appartamento" : c.o.apts.length + " appartamenti", "Quanto resta al proprietario · " + PLABEL[S.period]);
    var ul = el("ul", { class: "rows" });
    c.o.apts.forEach(function (a) {
      var x = sums({ apts: [a], strategies: c.o.strategies }, c.strat, S.period, c.cfg), share = y.R ? x.R / y.R : 0;
      var tx = (y.tax + y.inps) * share, rest = x.R - x.fee - x.costs - x.mgmt - x.mgmtIva - y.other * share - tx;
      var info = [a.zona, a.portale ? a.portale + (a.commissione_percentuale != null ? " " + String(a.commissione_percentuale).replace(".", ",") + "%" : "") : ""].filter(Boolean).join(" · ");
      ul.appendChild(li(a.nome || "Appartamento", rest, { small: info + "\nincasso " + eur.format(x.R) + " · tasse " + eur.format(tx) }));
    });
    ul.appendChild(li("Totale", y.pocket, { cls: "total" }));
    h.appendChild(ul);
    ul.querySelectorAll("small").forEach(function (s) { s.style.whiteSpace = "pre-line"; });
  }
  function field(lbl, input) { return el("label", { class: "field" }, [el("span", { text: lbl }), input]); }
  function sel(opts, val, on) { var s = el("select", { onchange: function () { on(s.value); } }); opts.forEach(function (o) { var op = el("option", { value: o[0], text: o[1] }); if (String(o[0]) === String(val)) op.selected = true; s.appendChild(op); }); return s; }
  function num(val, on, step) { var i = el("input", { type: "number", inputmode: "decimal", value: val, step: step || "1", onchange: function () { on(+i.value || 0); } }); return i; }
  function tog(lbl, sub, val, on) { var i = el("input", { type: "checkbox", onchange: function () { on(i.checked); } }); i.checked = !!val; return el("label", { class: "toggle" }, [el("span", {}, [lbl, sub ? el("small", { text: sub }) : null]), i]); }
  function upd(fn) { return function (v) { fn(v); save(); render(); }; }
  function renderRegime(c) {
    var h = card("regime", "Regime fiscale", "Impostazioni di " + S.sel), cfg = c.cfg;
    h.appendChild(field("Codice ATECO", sel(Object.keys(ATECO).map(function (k) { return [k, k + " · " + ATECO[k].name + " · " + pct(ATECO[k].coef)]; }), cfg.ateco, upd(function (v) { cfg.ateco = v; }))));
    h.appendChild(field("Previdenza", sel([["comm", "INPS commercianti"], ["gs", "INPS gestione separata"]], cfg.inps, upd(function (v) { cfg.inps = v; }))));
    h.appendChild(el("div", { class: "pair" }, [
      field("Gestione %", num(cfg.mgmt, upd(function (v) { cfg.mgmt = v; }), "0.5")),
      field("Calcolata sul", sel([["gross", "lordo"], ["net", "netto portale"]], cfg.mgmtBase, upd(function (v) { cfg.mgmtBase = v; })))]));
    if (c.o.strategies.length > 1) h.appendChild(field("Strategia di prezzo delle stime", sel(c.o.strategies.map(function (x) { return [x.id, x.nome + (x.id === c.o.managerStrategy ? " (quella del gestore)" : "")]; }), c.strat, upd(function (v) { S.strat = v; }))));
    h.appendChild(field("Altri costi annui (€)", num(cfg.other, upd(function (v) { cfg.other = v; }), "50")));
    h.appendChild(tog("Imposta al 5%", "Nuova attività, primi cinque anni", cfg.startup, upd(function (v) { cfg.startup = v; })));
    if (cfg.inps === "comm") h.appendChild(tog("Riduzione contributiva 35%", "Da chiedere all'INPS entro il 28 febbraio", cfg.rid35, upd(function (v) { cfg.rid35 = v; })));
    h.appendChild(tog("Gestore con IVA", "La fattura del gestore ha l'IVA al 22%", cfg.mgmtVat, upd(function (v) { cfg.mgmtVat = v; })));
    h.appendChild(tog("Costi per notte di bbb", "Pulizie e simili indicati in bbb", cfg.costs, upd(function (v) { cfg.costs = v; })));
  }
  function renderLinks() {
    var h = card("links", "Collegamenti a bbb", "I file si aggiornano da soli ogni 5 minuti"), box = el("div", {});
    S.links.forEach(function (l, i) {
      box.appendChild(el("div", { class: "link" }, [
        el("div", { class: "k" }, [el("div", { text: (l.tipo === "gestore" ? "Gestore, tutti i proprietari" : l.nome || "Proprietario") + (l.errore ? " · non letto" : "") }),
          el("code", { text: l.codice.slice(0, 4) + " ···· " + l.codice.slice(-4) + " · " + l.utente + "/" + l.repository })]),
        el("button", { class: "btn ghost", text: "Rimuovi", onclick: function () { S.links.splice(i, 1); save(); lastSig = ""; owners = {}; refresh(); } })]));
    });
    h.appendChild(box);
    h.appendChild(el("details", {}, [el("summary", { text: "Aggiungi un codice" }), el("div", { class: "addform", style: "padding-top:10px" })]));
  }
  function renderAddForms() {
    document.querySelectorAll(".addform").forEach(function (box) {
      if (box.firstChild) return;
      var ta = el("textarea", { placeholder: "bbb1:wkisai95/bbb/main/XXXX-XXXX-…", autocapitalize: "characters", spellcheck: "false" });
      box.appendChild(field("Invito o codice di 24 caratteri", ta));
      box.appendChild(el("button", { class: "btn", text: "Collega", onclick: function () { if (addLink(ta.value)) ta.value = ""; } }));
    });
  }

  // ------------------------------------------------------------------ avvio
  $("refresh").addEventListener("click", function () { refresh(); });
  $("owner").addEventListener("change", function () { S.sel = $("owner").value; save(); render(); });
  document.querySelectorAll("#tabs button").forEach(function (b) { b.addEventListener("click", function () { S.view = b.getAttribute("data-v"); save(); render(); window.scrollTo(0, 0); }); });
  var rz; window.addEventListener("resize", function () { clearTimeout(rz); rz = setTimeout(render, 150); });
  document.addEventListener("visibilitychange", function () { if (!document.hidden && (!status.at || Date.now() - status.at > 60000)) refresh(); });
  render(); refresh(); setInterval(function () { if (!document.hidden) refresh(); }, REFRESH_MS);
  if (window.Android && window.Android.ready) window.Android.ready();
})();
