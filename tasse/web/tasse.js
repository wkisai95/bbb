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
    "55.20.42": { name: "Case e appartamenti per vacanze (proprietario)", coef: 0.40 },
    "68.32.01": { name: "Gestione immobili per conto terzi (property manager)", coef: 0.86 }
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
  function note(msg, bad) { var b = $("banner"); b.innerHTML = ""; if (msg) b.appendChild(el("div", { class: "banner" + (bad ? " bad" : ""), text: msg })); }
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
    S.links.push(l); save(); note("Collegamento aggiunto: leggo il file da GitHub…"); refresh(); return true;
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
      if (changed && got) { $("kpis").classList.add("flash"); setTimeout(function () { $("kpis").classList.remove("flash"); }, 1300); }
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
  function setLive() {
    var d = $("dot"), txt = $("liveTxt");
    d.className = "dot" + (status.state === "ok" ? " on" : status.state === "error" ? " err" : "");
    var names = Object.keys(owners), o = names.length ? owners[S.sel] || owners[names[0]] : null;
    if (status.state === "loading") txt.textContent = "leggo da GitHub…";
    else if (!S.links.length) txt.textContent = "nessun collegamento";
    else if (o) txt.textContent = "Stime di bbb del " + when(o.saved) + (status.at ? " · controllato alle " + status.at.toLocaleTimeString("it-IT", { hour: "2-digit", minute: "2-digit" }) : "");
    else txt.textContent = status.state === "error" ? "file non letti" : "…";
  }
  function render() {
    setLive();
    var names = Object.keys(owners).sort(function (a, b) { return a === "Senza proprietario" ? 1 : b === "Senza proprietario" ? -1 : a.localeCompare(b, "it"); });
    $("empty").classList.toggle("hidden", S.links.length > 0);
    $("main").classList.toggle("hidden", !S.links.length);
    renderAddForms(); renderLinks();
    if (!names.length) { ["kpis", "periods", "pl", "taxcalc", "apts", "deadlines", "ownerForm", "owners"].forEach(function (id) { $(id).innerHTML = ""; }); return; }
    if (names.indexOf(S.sel) < 0) S.sel = names[0];
    var ob = $("owners"); ob.innerHTML = "";
    if (names.length > 1) names.forEach(function (n) { ob.appendChild(el("button", { class: n === S.sel ? "on" : "", text: n, onclick: function () { S.sel = n; save(); render(); } })); });
    var c = compute(S.sel);
    renderKpis(c); renderChart(c); renderPeriods(c); renderPL(c); renderTax(c); renderApts(c); renderDeadlines(c); renderForm(c);
  }
  function kpi(l, v, n, cls) { return el("div", { class: "kpi " + (cls || "") }, [el("div", { class: "l", text: l }), el("div", { class: "v", text: v }), n ? el("div", { class: "n", text: n }) : null]); }
  function renderKpis(c) {
    var k = $("kpis"), y = c.y; k.innerHTML = "";
    var dovuto = c.t.imposta + c.t.inps;
    k.appendChild(kpi("Incasso lordo, prossimi 12 mesi", eur.format(y.R), Math.round(y.sold) + " notti vendute attese · " + c.o.apts.length + (c.o.apts.length === 1 ? " appartamento" : " appartamenti")));
    k.appendChild(kpi("Da pagare: imposta + INPS", eur.format(dovuto), pct(c.rate) + " dell'incasso", "hi"));
    k.appendChild(kpi("IVA reverse charge sulle commissioni", eur.format(y.iva), "già compresa nelle commissioni"));
    k.appendChild(kpi("Resta al proprietario", eur.format(c.pocket), eur.format(c.pocket / 12) + " al mese", "ok"));
    if (y.R > LIMITE) k.appendChild(kpi("Attenzione", "> 85.000 €", "ricavi oltre il limite del forfettario", "hi"));
  }
  function renderChart(c) {
    var box = $("chart"), tip = $("tip");
    box.querySelectorAll("svg").forEach(function (s) { s.remove(); });
    var W = box.clientWidth || 600, H = 210, pl = 50, pb = 22, pt = 8, NS = "http://www.w3.org/2000/svg";
    var rows = PERIODS.map(function (p) {
      var x = c.per[p];
      return { p: p, R: x.R, parts: [["fee", x.fee + x.costs + x.other], ["mgmt", x.mgmt + x.mgmtIva], ["inps", x.inps], ["tax", x.tax], ["net", Math.max(0, x.pocket)]] };
    });
    var max = Math.max(1, rows[3].R), svg = document.createElementNS(NS, "svg");
    svg.setAttribute("width", W); svg.setAttribute("height", H);
    var col = { net: "var(--c-net)", tax: "var(--c-tax)", inps: "var(--c-inps)", mgmt: "var(--c-mgmt)", fee: "var(--c-fee)" };
    var y = function (v) { return pt + (H - pt - pb) * (1 - v / max); };
    function mk(tag, at) { var e = document.createElementNS(NS, tag); Object.keys(at).forEach(function (k) { e.setAttribute(k, at[k]); }); return e; }
    [0, 0.5, 1].forEach(function (f) {
      svg.appendChild(mk("line", { x1: pl, x2: W, y1: y(max * f), y2: y(max * f), stroke: "var(--line)" }));
      var t = mk("text", { x: pl - 6, y: y(max * f) + 4, "text-anchor": "end", "font-size": 11, fill: "var(--mute)" });
      t.textContent = Math.round(max * f).toLocaleString("it-IT") + "€"; svg.appendChild(t);
    });
    var bw = (W - pl) / rows.length;
    rows.forEach(function (r, i) {
      var x = pl + i * bw + bw * 0.2, w = bw * 0.6, acc = 0, g = mk("g", {});
      r.parts.forEach(function (q) { var v = Math.max(0, q[1]); g.appendChild(mk("rect", { x: x, width: w, y: y(acc + v), height: Math.max(0, y(acc) - y(acc + v)), fill: col[q[0]] })); acc += v; });
      var lab = mk("text", { x: x + w / 2, y: H - 6, "text-anchor": "middle", "font-size": 11, fill: "var(--mute)" });
      lab.textContent = r.p === "365" ? "12 mesi" : r.p + " giorni"; g.appendChild(lab);
      var hit = mk("rect", { x: pl + i * bw, width: bw, y: 0, height: H, fill: "transparent" });
      function show(ev) {
        var p = {}; r.parts.forEach(function (q) { p[q[0]] = q[1]; });
        tip.innerHTML = ""; tip.appendChild(el("b", { text: lab.textContent }));
        [["Incasso", r.R], ["Portale e costi", p.fee], ["Gestione", p.mgmt], ["INPS", p.inps], ["Imposta", p.tax], ["Resta", p.net]].forEach(function (q) { tip.appendChild(el("div", { text: q[0] + ": " + eur.format(q[1]) })); });
        var bx = box.getBoundingClientRect(), pt0 = ev.touches ? ev.touches[0] : ev, lx = pt0.clientX - bx.left + 12;
        if (lx + 160 > W) lx -= 180;
        tip.style.display = "block"; tip.style.left = Math.max(0, lx) + "px"; tip.style.top = Math.max(0, pt0.clientY - bx.top - 10) + "px";
      }
      hit.addEventListener("mousemove", show); hit.addEventListener("touchstart", show, { passive: true });
      hit.addEventListener("mouseleave", function () { tip.style.display = "none"; });
      g.appendChild(hit); svg.appendChild(g);
    });
    box.appendChild(svg);
    var sname = (c.o.strategies.filter(function (s) { return s.id === c.strat; })[0] || {}).nome || c.strat;
    $("chartNote").textContent = "Stime di bbb dalla notte del " + c.o.from + ", strategia «" + sname + "», prezzi di mercato del " + c.o.dataDay +
      ". Imposta e INPS dei periodi brevi sono la quota da accantonare, in proporzione all'incasso.";
  }
  function renderPeriods(c) {
    var t = $("periods"); t.innerHTML = "";
    t.appendChild(el("tr", {}, [el("th", { text: "" })].concat(PERIODS.map(function (p) { return el("th", { text: p === "365" ? "12 mesi" : p + " gg" }); }))));
    [["Incasso", "R"], ["Imposta + INPS", null], ["Resta", "pocket"]].forEach(function (r) {
      t.appendChild(el("tr", { class: r[1] === "pocket" ? "tot" : "" }, [el("td", { text: r[0] })].concat(PERIODS.map(function (p) {
        var x = c.per[p]; return el("td", { text: eur.format(r[1] ? x[r[1]] : x.tax + x.inps) });
      }))));
    });
    if (c.o.strategies.length > 1) {
      var s = el("select", { onchange: function () { S.strat = s.value; save(); render(); } });
      c.o.strategies.forEach(function (x) { var op = el("option", { value: x.id, text: x.nome + (x.id === c.o.managerStrategy ? " (usata dal gestore)" : "") }); if (x.id === c.strat) op.selected = true; s.appendChild(op); });
      t.parentNode.appendChild(el("div", { class: "strat" }, [el("label", { text: "Strategia di prezzo" }), s]));
      var old = t.parentNode.querySelectorAll(".strat"); for (var i = 0; i < old.length - 1; i++) old[i].remove();
    }
  }
  function row(t, label, v, cls) { t.appendChild(el("tr", { class: cls || "" }, [el("td", { text: label }), el("td", { class: v < 0 ? "minus" : "", text: eur.format(v) })])); }
  function renderPL(c) {
    var t = $("pl"), y = c.y; t.innerHTML = "";
    row(t, "Incasso lordo (ricavi)", y.R);
    row(t, "Commissioni portali" + (y.iva ? " con IVA reverse charge" : ""), -y.fee);
    if (y.costs) row(t, "Costi per notte indicati in bbb (pulizie…)", -y.costs);
    row(t, "Gestione " + String(c.cfg.mgmt).replace(".", ",") + "% " + (c.cfg.mgmtBase === "net" ? "sul netto del portale" : "sul lordo"), -y.mgmt);
    if (y.mgmtIva) row(t, "IVA sulla fattura del gestore", -y.mgmtIva);
    if (c.other) row(t, "Altri costi annui", -c.other);
    row(t, "Contributi INPS", -c.t.inps);
    row(t, "Imposta sostitutiva " + pct(c.t.aliq), -c.t.imposta);
    row(t, "Resta al proprietario", c.pocket, "tot");
    if (y.cityTax) row(t, "Tassa di soggiorno da riscuotere e versare (non è un ricavo)", y.cityTax, "sub");
  }
  function renderTax(c) {
    var t = $("taxcalc"), x = c.t; t.innerHTML = "";
    function r2(l, v, cls) { t.appendChild(el("tr", { class: cls || "" }, [el("td", { text: l }), el("td", { text: v })])); }
    r2("Ricavi dei prossimi 12 mesi", eur.format(c.y.R));
    r2("× coefficiente ATECO " + c.cfg.ateco, pct(x.coef));
    r2("= Reddito forfettario", eur.format(x.reddito));
    if (c.cfg.inps === "comm") {
      r2("INPS quota fissa" + (c.cfg.rid35 ? " (−35%)" : ""), eur.format(x.fisso));
      r2("INPS oltre il minimale" + (c.cfg.rid35 ? " (−35%)" : ""), eur.format(x.perc));
    } else r2("INPS Gestione Separata 26,07%", eur.format(x.perc));
    r2("− Contributi dedotti", eur.format(-x.inps));
    r2("= Base imponibile", eur.format(x.imponibile));
    r2("Imposta sostitutiva " + pct(x.aliq), eur2.format(x.imposta), "tot");
    $("taxNote").textContent = (c.cfg.inps === "comm" && x.reddito < INPS.minimale ? "Il reddito è sotto il minimale INPS: la quota fissa si paga comunque per intero. " : "") +
      "Su ogni 100 € incassati, accantona " + eur2.format(c.rate * 100) + " per imposta e INPS.";
  }
  function renderApts(c) {
    var t = $("apts"), y = c.y; t.innerHTML = "";
    t.appendChild(el("tr", {}, ["Appartamento", "Incasso", "Portale", "Gestione", "Imposta+INPS", "Resta"].map(function (h) { return el("th", { text: h }); })));
    c.o.apts.forEach(function (a) {
      var one = { apts: [a], strategies: c.o.strategies }, x = sums(one, c.strat, "365", c.cfg);
      var share = y.R ? x.R / y.R : 0, tx = (c.t.imposta + c.t.inps) * share, oth = c.other * share;
      var rest = x.R - x.fee - x.costs - x.mgmt - x.mgmtIva - oth - tx;
      t.appendChild(el("tr", {}, [
        el("td", {}, [el("div", { text: a.nome || "Appartamento" }), el("div", { class: "note", text: [a.zona, a.portale, a.commissione_percentuale != null ? String(a.commissione_percentuale).replace(".", ",") + "%" : ""].filter(Boolean).join(" · ") })]),
        el("td", { text: eur.format(x.R) }), el("td", { class: "minus", text: eur.format(-(x.fee + x.costs)) }), el("td", { class: "minus", text: eur.format(-(x.mgmt + x.mgmtIva)) }),
        el("td", { class: "minus", text: eur.format(-tx) }), el("td", { text: eur.format(rest) })]));
    });
  }
  function renderDeadlines(c) {
    var d = $("deadlines"), ul = el("ul", { class: "dl" }); d.innerHTML = "";
    ul.appendChild(el("li", { text: "Ogni 16 del mese: IVA reverse charge del mese prima, in media " + eur.format(c.y.iva / 12) + " (di più nei mesi pieni)" }));
    if (c.cfg.inps === "comm") ul.appendChild(el("li", { text: "16 maggio, 20 agosto, 16 novembre, 16 febbraio: INPS quota fissa, 4 rate da " + eur2.format(c.t.fisso / 4) }));
    var due = c.t.imposta + c.t.perc;
    ul.appendChild(el("li", { text: "30 giugno: saldo dell'anno prima + 1° acconto (40%) ≈ " + eur.format(due * 0.4) }));
    ul.appendChild(el("li", { text: "30 novembre: 2° acconto (60%) ≈ " + eur.format(due * 0.6) }));
    if (c.y.cityTax) ul.appendChild(el("li", { text: "Tassa di soggiorno al Comune: " + eur.format(c.y.cityTax) + " in 12 mesi, alle scadenze del Comune" }));
    d.appendChild(ul);
    d.appendChild(el("p", { class: "note", text: "Accantona circa " + eur.format((c.t.imposta + c.t.inps) / 12) + " al mese per imposta e INPS. Nel primo anno di attività non si versano acconti d'imposta." }));
  }
  function field(lbl, input) { return el("div", {}, [el("label", { text: lbl }), input]); }
  function sel(opts, val, on) { var s = el("select", { onchange: function () { on(s.value); } }); opts.forEach(function (o) { var op = el("option", { value: o[0], text: o[1] }); if (String(o[0]) === String(val)) op.selected = true; s.appendChild(op); }); return s; }
  function num(val, on, step) { var i = el("input", { type: "number", inputmode: "decimal", value: val, step: step || "1", onchange: function () { on(+i.value || 0); } }); return i; }
  function chk(lbl, val, on) { var i = el("input", { type: "checkbox", onchange: function () { on(i.checked); } }); i.checked = !!val; return el("label", { class: "chk" }, [i, lbl]); }
  function upd(fn) { return function (v) { fn(v); save(); render(); }; }
  function renderForm(c) {
    var f = $("ownerForm"), cfg = c.cfg; f.innerHTML = "";
    f.appendChild(el("div", { class: "pill", text: S.sel }));
    f.appendChild(field("Codice ATECO", sel(Object.keys(ATECO).map(function (k) { return [k, k + " · " + ATECO[k].name + " (" + pct(ATECO[k].coef) + ")"]; }), cfg.ateco, upd(function (v) { cfg.ateco = v; }))));
    f.appendChild(field("Gestione INPS", sel([["comm", "Commercianti (fisso + 24,48%)"], ["gs", "Gestione Separata (26,07%)"]], cfg.inps, upd(function (v) { cfg.inps = v; }))));
    if (cfg.inps === "comm") f.appendChild(chk("Riduzione contributiva del 35%", cfg.rid35, upd(function (v) { cfg.rid35 = v; })));
    f.appendChild(chk("Nuova attività: imposta al 5% (primi 5 anni)", cfg.startup, upd(function (v) { cfg.startup = v; })));
    f.appendChild(el("div", { class: "row2" }, [
      field("Gestione %", num(cfg.mgmt, upd(function (v) { cfg.mgmt = v; }), "0.5")),
      field("Calcolata su", sel([["gross", "lordo"], ["net", "netto portale"]], cfg.mgmtBase, upd(function (v) { cfg.mgmtBase = v; })))
    ]));
    f.appendChild(chk("Il gestore fattura con IVA 22%", cfg.mgmtVat, upd(function (v) { cfg.mgmtVat = v; })));
    f.appendChild(chk("Togli i costi per notte indicati in bbb", cfg.costs, upd(function (v) { cfg.costs = v; })));
    f.appendChild(field("Altri costi annui €", num(cfg.other, upd(function (v) { cfg.other = v; }), "50")));
    f.appendChild(el("p", { class: "note", text: "Nel forfettario le spese non abbassano le tasse: servono solo a calcolare quanto resta." }));
  }
  function renderLinks() {
    var box = $("links"); box.innerHTML = "";
    S.links.forEach(function (l, i) {
      box.appendChild(el("div", { class: "link" }, [
        el("div", {}, [el("div", { text: (l.tipo === "gestore" ? "Gestore (tutti i proprietari)" : l.nome || "Proprietario") + (l.errore ? " · non letto" : "") }),
          el("div", { class: "c", text: l.utente + "/" + l.repository + " · " + l.codice.slice(0, 4) + "-••••-••••-••••-••••-" + l.codice.slice(-4) })]),
        el("button", { text: "Togli", onclick: function () { S.links.splice(i, 1); save(); lastSig = ""; owners = {}; refresh(); } })]));
    });
  }
  function renderAddForms() {
    document.querySelectorAll(".addform").forEach(function (box) {
      if (box.firstChild) return;
      var ta = el("textarea", { placeholder: "bbb1:wkisai95/bbb/main/XXXX-XXXX-XXXX-XXXX-XXXX-XXXX" });
      box.appendChild(field("Invito o codice", ta));
      box.appendChild(el("div", { style: "margin-top:8px" }, [el("button", { class: "pri", text: "Collega", onclick: function () { if (addLink(ta.value)) ta.value = ""; } })]));
    });
  }

  // ------------------------------------------------------------------ avvio
  $("refresh").addEventListener("click", function () { refresh(); });
  var rz; window.addEventListener("resize", function () { clearTimeout(rz); rz = setTimeout(render, 150); });
  document.addEventListener("visibilitychange", function () { if (!document.hidden && (!status.at || Date.now() - status.at > 60000)) refresh(); });
  render(); refresh(); setInterval(function () { if (!document.hidden) refresh(); }, REFRESH_MS);
  if (window.Android && window.Android.ready) window.Android.ready();
})();
