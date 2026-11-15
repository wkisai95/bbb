/* bbb · interfaccia. Legge i file pubblicati dalla scansione (ramo `data` del repository) e calcola
   i prezzi sul telefono con pricing.js. Nessun dato lascia il telefono, tranne le chiamate a GitHub. */
(function () {
  "use strict";
  var P = window.Pricing, DAY = P.DAY;
  var KEY = "bbb.v1";
  var DEFAULT_REPO = { owner: "wkisai95", name: "bbb", branch: "data" };
  var QUALITY = { base: "Sotto la media", media: "Nella media", alta: "Sopra la media" };
  var CAT = { fiera: ["Fiera", "c1"], congresso: ["Congresso", "c1"], concerto: ["Concerto", "c2"], sport: ["Sport", "c3"],
    festivita: ["Festività", "c4"], cultura: ["Cultura", "c5"], mercato: ["Rilevato dai prezzi", "c6"], altro: ["Altro", "c5"] };
  var WEIGHT = { 1: "minimo", 2: "medio", 3: "alto", 4: "molto alto", 5: "massimo" };

  // ------------------------------------------------------------------ utilità
  function h(tag, props) {
    var el = document.createElement(tag), i, k;
    props = props || {};
    for (k in props) {
      if (k === "class") el.className = props[k];
      else if (k === "text") el.textContent = props[k];
      else if (k.slice(0, 2) === "on") el.addEventListener(k.slice(2), props[k]);
      else if (props[k] !== null && props[k] !== undefined && props[k] !== false) el.setAttribute(k, props[k] === true ? "" : props[k]);
    }
    for (i = 2; i < arguments.length; i++) append(el, arguments[i]);
    return el;
  }
  function append(el, c) {
    if (c === null || c === undefined || c === false) return;
    if (Array.isArray(c)) c.forEach(function (x) { append(el, x); });
    else el.appendChild(typeof c === "object" ? c : document.createTextNode(String(c)));
  }
  function svg(tag, attrs) {
    var el = document.createElementNS("http://www.w3.org/2000/svg", tag), k;
    for (k in attrs) el.setAttribute(k, attrs[k]);
    for (var i = 2; i < arguments.length; i++) if (arguments[i]) el.appendChild(typeof arguments[i] === "object" ? arguments[i] : document.createTextNode(String(arguments[i])));
    return el;
  }
  var nf = new Intl.NumberFormat("it-IT", { maximumFractionDigits: 0, useGrouping: "always" });
  function euro(n) { return nf.format(Math.round(n)) + " €"; }
  function pct(x) { return Math.round(x * 100) + "%"; }
  function signedPct(x) { var v = Math.round(x * 100); return (v > 0 ? "+" : "") + v + "%"; }
  function fmtDate(ms, opts) { opts = opts || { day: "numeric", month: "short" }; opts.timeZone = "UTC"; return new Intl.DateTimeFormat("it-IT", opts).format(new Date(ms)); }
  function longDate(ms) { return fmtDate(ms, { weekday: "long", day: "numeric", month: "long" }); }
  function todayMs() { var n = new Date(); return Date.UTC(n.getFullYear(), n.getMonth(), n.getDate()); }
  function icon(kind) {
    var s = svg("svg", { viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", "stroke-width": "2", "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true" });
    if (kind === "ok") { s.appendChild(svg("circle", { cx: 12, cy: 12, r: 9 })); s.appendChild(svg("path", { d: "M8 12.5l3 3 5-6" })); }
    else { s.appendChild(svg("path", { d: "M12 3.5l9.5 16.5h-19z" })); s.appendChild(svg("path", { d: "M12 10v4.5M12 17.6v.1" })); }
    return s;
  }
  function banner(kind, text, action) {
    return h("div", { class: "banner " + kind }, icon(kind === "ok" ? "ok" : "warn"), h("div", {}, text, action ? [" ", action] : null));
  }
  var toastTimer;
  function toast(msg) {
    var t = document.getElementById("toast");
    t.textContent = msg; t.classList.add("show");
    clearTimeout(toastTimer); toastTimer = setTimeout(function () { t.classList.remove("show"); }, 3200);
  }

  // ------------------------------------------------------------------ stato salvato sul telefono
  function defaults() {
    return { repo: { owner: DEFAULT_REPO.owner, name: DEFAULT_REPO.name, branch: DEFAULT_REPO.branch, token: "" },
      apartments: [{ id: "a1", name: "Il mio appartamento", zone: "c_maggiore", bedrooms: 1, baths: 1, guests: 3, quality: "media", minPrice: 0, cost: null }],
      current: "a1", configured: false, strategy: "resa", period: 30, customEvents: [] };
  }
  function loadState() {
    var d = defaults();
    try {
      var s = JSON.parse(localStorage.getItem(KEY) || "null");
      if (s && typeof s === "object") {
        Object.keys(d).forEach(function (k) { if (s[k] !== undefined) d[k] = s[k]; });
        d.repo = Object.assign(defaults().repo, s.repo || {});
        if (!Array.isArray(d.apartments) || !d.apartments.length) d.apartments = defaults().apartments;
      }
    } catch (e) { /* stato illeggibile: si riparte dai valori predefiniti */ }
    return d;
  }
  function saveState() { try { localStorage.setItem(KEY, JSON.stringify(state)); } catch (e) { toast("Spazio insufficiente per salvare le impostazioni"); } }
  var state = loadState();
  var data = { model: null, status: null, listings: null, error: "", loading: false, run: null, fetched: 0 };
  var ui = { tab: "prezzi", view: "cal", dim: "zone", evAll: false };
  function apt() { return state.apartments.filter(function (a) { return a.id === state.current; })[0] || state.apartments[0]; }

  // ------------------------------------------------------------------ dati da GitHub
  function gh(path, opts) {
    opts = opts || {};
    var headers = { Accept: opts.raw ? "application/vnd.github.raw+json" : "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28" };
    if (state.repo.token) headers.Authorization = "Bearer " + state.repo.token;
    if (opts.body) headers["Content-Type"] = "application/json";
    return fetch("https://api.github.com/repos/" + state.repo.owner + "/" + state.repo.name + path,
      { method: opts.method || "GET", headers: headers, body: opts.body ? JSON.stringify(opts.body) : undefined, cache: "no-store" });
  }
  function getPublic(file) {
    var r = state.repo;
    if (r.token) {
      return gh("/contents/public/" + file + "?ref=" + encodeURIComponent(r.branch), { raw: true }).then(function (res) {
        if (!res.ok) throw new Error("GitHub ha risposto " + res.status);
        return res.json();
      });
    }
    var url = "https://raw.githubusercontent.com/" + r.owner + "/" + r.name + "/" + r.branch + "/public/" + file + "?t=" + Math.floor(Date.now() / 300000);
    return fetch(url, { cache: "no-store" }).then(function (res) {
      if (res.status === 404) throw new Error("not-found");
      if (!res.ok) throw new Error("GitHub ha risposto " + res.status);
      return res.json();
    });
  }
  function loadData(force) {
    if (data.loading) return Promise.resolve();
    data.loading = true; data.error = "";
    if (!data.model) {
      try { var c = JSON.parse(localStorage.getItem(KEY + ".model") || "null"); if (c && c.idx) { data.model = c; data.cached = true; } } catch (e) { /* niente copia locale */ }
    }
    render();
    return Promise.all([getPublic("status.json").catch(function (e) { return { _err: e.message }; }),
      getPublic("model.json").catch(function (e) { return { _err: e.message }; })]).then(function (res) {
      var st = res[0], m = res[1];
      if (st && !st._err) data.status = st;
      if (m && !m._err && m.idx) {
        data.model = m; data.cached = false; data.fetched = Date.now(); eff = null;
        try { localStorage.setItem(KEY + ".model", JSON.stringify(m)); } catch (e) { /* troppo grande: si fa senza copia locale */ }
      } else if (m && m._err === "not-found" && !data.model) {
        data.error = "nodata";
      } else if (m && m._err) {
        data.error = m._err;
      }
    }).catch(function (e) { data.error = String(e.message || e); }).then(function () {
      data.loading = false; render();
      if (force) toast(data.error && data.error !== "nodata" ? "Aggiornamento non riuscito" : "Dati aggiornati");
    });
  }

  // ------------------------------------------------------------------ eventi aggiunti a mano, applicati al modello
  var eff = null;
  function nightsOf(e) {
    var a = P.parseDate(e.start), b = P.parseDate(e.end || e.start);
    if (e.category === "fiera" || e.category === "congresso" || e.category === "sport") { a -= DAY; b -= DAY; }
    else if (e.category === "cultura") { b = Math.max(a, b - DAY); }
    return [a, Math.max(a, b)];
  }
  function model() {
    if (!data.model) return null;
    if (eff) return eff;
    var m = data.model;
    if (!state.customEvents.length) { eff = m; return m; }
    var out = Object.assign({}, m), t0 = P.parseDate(m.start);
    out.idx = m.idx.slice(); out.events = m.events.slice();
    out.theta = {};
    Object.keys(m.theta).forEach(function (s) { out.theta[s] = {}; Object.keys(m.theta[s]).forEach(function (g) { out.theta[s][g] = m.theta[s][g].slice(); }); });
    var known = P.eventsByNight(m);
    state.customEvents.forEach(function (e) {
      var ns = nightsOf(e), w = Math.min(5, Math.max(1, +e.weight || 2));
      var already = m.events.some(function (x) { return x.source === "custom" && x.name === e.name && x.start === e.start; });
      if (!already) {
        out.events.push({ id: "mio-" + e.name + e.start, name: e.name, start: e.start, end: e.end || e.start, venue: e.venue || "", category: e.category || "altro",
          weight: w, source: "locale", note: "", night_first: P.isoDate(ns[0]), night_last: P.isoDate(ns[1]), uplift: null, measured: false });
      }
      for (var t = ns[0]; t <= ns[1]; t += DAY) {
        var k = Math.round((t - t0) / DAY), iso = P.isoDate(t);
        if (k < 0 || k >= out.idx.length || m.cov[k] >= 6) continue;          // notte già misurata: valgono i prezzi veri
        var have = (known[iso] || []).reduce(function (mx, x) { return Math.max(mx, x.weight); }, 0);
        if (have >= w) continue;
        var up = Math.log(1 + (m.event_effect[String(w)] || 0)) - (have ? Math.log(1 + (m.event_effect[String(have)] || 0)) : 0);
        out.idx[k] = m.idx[k] + up;
        var th = +(m.calibration.typical_theta || {})[String(w)] || 1;
        Object.keys(out.theta).forEach(function (s) { Object.keys(out.theta[s]).forEach(function (g) { out.theta[s][g][k] = Math.max(out.theta[s][g][k], th); }); });
      }
    });
    out.events.sort(function (a, b) { return a.start < b.start ? -1 : a.start > b.start ? 1 : b.weight - a.weight; });
    eff = out; return out;
  }

  function recommend(strategy, days, from) {
    var m = model(), a = apt();
    var start = from === undefined ? Math.max(todayMs(), P.parseDate(m.start)) : from;
    return P.recommend(m, { zone: a.zone, bedrooms: a.bedrooms, baths: a.baths, guests: a.guests, quality: a.quality,
      minPrice: a.minPrice || 0, cost: a.cost === null || a.cost === undefined || a.cost === "" ? null : +a.cost,
      strategy: strategy, start: P.isoDate(start), end: P.isoDate(start + (days - 1) * DAY) });
  }
  function zoneName(id, short) {
    var z = (model() ? model().zones : []).filter(function (x) { return x.id === id; })[0];
    return z ? (short ? z.short : z.name) : id;
  }
  function aptSpec(a) {
    return zoneName(a.zone, true) + ", " + (a.bedrooms === 0 ? "monolocale" : a.bedrooms + (a.bedrooms === 1 ? " camera" : " camere")) + ", " +
      String(a.baths).replace(".", ",") + (a.baths === 1 ? " bagno" : " bagni") + ", " + a.guests + " ospiti";
  }

  // ------------------------------------------------------------------ ponte con Android
  var native = window.Android || null;
  function saveFile(name, mime, text) {
    if (native && native.saveFile) {
      var ok = native.saveFile(name, mime, btoa(unescape(encodeURIComponent(text))));
      toast(ok ? "Salvato in Download: " + name : "Salvataggio non riuscito");
      return;
    }
    var a = h("a", { href: URL.createObjectURL(new Blob([text], { type: mime })), download: name });
    document.body.appendChild(a); a.click(); a.remove(); toast("Scaricato: " + name);
  }
  function openExternal(url) { if (native && native.openExternal) native.openExternal(url); else window.open(url, "_blank", "noopener"); }
  function downloadUrl(url, name) {
    if (native && native.download) { native.download(url, name, state.repo.token ? "Bearer " + state.repo.token : ""); toast("Download avviato: lo trovi nella cartella Download"); }
    else window.open(url, "_blank", "noopener");
  }

  // ------------------------------------------------------------------ schede dal basso
  var sheetStack = [];
  function openSheet(title, build) {
    var root = document.getElementById("sheet-root");
    var back = h("div", { class: "backdrop", onclick: closeSheet });
    var body = h("div", {});
    var sh = h("div", { class: "sheet", role: "dialog", "aria-modal": "true", "aria-label": title },
      h("div", { class: "grip" }),
      h("header", {}, h("h2", { text: title }), h("button", { class: "x", "aria-label": "Chiudi", onclick: closeSheet, text: "×" })), body);
    root.appendChild(back); root.appendChild(sh);
    sheetStack.push({ back: back, sh: sh, body: body, build: build });
    build(body);
    requestAnimationFrame(function () { back.classList.add("open"); sh.classList.add("open"); });
    try { history.pushState({ sheet: sheetStack.length }, ""); } catch (e) { /* senza cronologia va bene lo stesso */ }
    return body;
  }
  function closeSheet(fromPop) {
    var s = sheetStack.pop();
    if (!s) return false;
    s.back.classList.remove("open"); s.sh.classList.remove("open");
    setTimeout(function () { s.back.remove(); s.sh.remove(); }, 230);
    if (fromPop !== true) { try { history.back(); } catch (e) { /* idem */ } }
    return true;
  }
  var skipPop = 0;
  window.addEventListener("popstate", function () { if (skipPop) { skipPop--; return; } closeSheet(true); });
  window.bbbBack = function () { if (sheetStack.length) { closeSheet(); return true; } if (ui.tab !== "prezzi") { go("prezzi"); return true; } return false; };
  function refreshSheet() { var s = sheetStack[sheetStack.length - 1]; if (s) { s.body.textContent = ""; s.build(s.body); } }

  // ------------------------------------------------------------------ comandi riusabili
  function seg(options, value, onpick) {
    return h("div", { class: "seg", role: "group" }, options.map(function (o) {
      return h("button", { type: "button", "aria-pressed": String(o[0] === value), onclick: function () { onpick(o[0]); }, text: o[1] });
    }));
  }
  function chips(options, value, onpick) {
    return h("div", { class: "chips", role: "group" }, options.map(function (o) {
      return h("button", { class: "chip", type: "button", "aria-pressed": String(o[0] === value), onclick: function () { onpick(o[0]); }, text: o[1] });
    }));
  }
  function stepper(label, value, min, max, step, fmt, onchange) {
    var out = h("output", { text: fmt(value) });
    function set(v) { value = Math.min(max, Math.max(min, v)); out.textContent = fmt(value); onchange(value); }
    return h("div", { class: "field" }, h("span", { class: "lab", text: label }),
      h("div", { class: "stepper" },
        h("button", { type: "button", "aria-label": label + ": diminuisci", onclick: function () { set(value - step); }, text: "−" }), out,
        h("button", { type: "button", "aria-label": label + ": aumenta", onclick: function () { set(value + step); }, text: "+" })));
  }

  // ------------------------------------------------------------------ PREZZI
  function heatClass(up) {
    var t = [0.07, 0.15, 0.30, 0.50, 0.80, 1.20, 1.70];
    for (var i = t.length - 1; i >= 0; i--) if (up >= t[i]) return "h" + (i + 1);
    return "";
  }
  function viewPrezzi(root) {
    var m = model();
    if (!m) return emptyState(root);
    var a = apt();
    var rec = recommend(state.strategy, state.period), other = recommend(state.strategy === "resa" ? "occupazione" : "resa", state.period);
    var t0 = P.parseDate(m.start), stale = Math.round((todayMs() - t0) / DAY);

    root.appendChild(h("div", { class: "apt" },
      h("div", {}, h("div", { class: "apt-name", text: a.name }), h("div", { class: "apt-spec", text: aptSpec(a) })),
      h("button", { class: "btn", onclick: function () { editApartment(a.id); }, text: "Modifica" })));
    if (!state.configured) root.appendChild(banner("warn", "Questi prezzi valgono per un appartamento di esempio.", h("button", { class: "btn quiet", onclick: function () { editApartment(a.id); }, text: "Imposta il tuo" })));
    if (stale >= 2) root.appendChild(banner("warn", "I dati sono del " + fmtDate(t0, { day: "numeric", month: "long" }) + ": la scansione non gira da " + stale + " giorni.", h("button", { class: "btn quiet", onclick: function () { go("dati"); }, text: "Controlla" })));

    root.appendChild(seg([["resa", "Massima resa"], ["occupazione", "Massima occupazione"]], state.strategy, function (v) { state.strategy = v; saveState(); render(); }));
    root.appendChild(chips([[30, "30 notti"], [60, "60 notti"], [90, "90 notti"]], state.period, function (v) { state.period = v; saveState(); render(); }));

    root.appendChild(h("div", { class: "sum" },
      h("div", {}, h("b", { text: euro(rec.avgPrice) }), h("span", { text: "prezzo medio a notte" })),
      h("div", {}, h("b", { text: pct(rec.occupancy) }), h("span", { text: "notti che ti aspetti di vendere" })),
      h("div", {}, h("b", { text: euro(rec.revenue) }), h("span", { text: "incasso atteso nel periodo" }))));
    root.appendChild(h("p", { class: "alt" }, "Con " + (state.strategy === "resa" ? "massima occupazione" : "massima resa") + ": " + euro(other.avgPrice) + " a notte, " + pct(other.occupancy) + " di notti vendute, " + euro(other.revenue) + ". ",
      h("button", { onclick: function () { state.strategy = other.strategy; saveState(); render(); }, text: "Passa a questa" })));

    var r = rec.reliability, dots = r.label === "alta" ? 3 : r.label === "media" ? 2 : 1;
    root.appendChild(h("button", { class: "rely", onclick: function () { relySheet(rec); } },
      h("span", { class: "dots", "aria-hidden": "true" }, [1, 2, 3].map(function (i) { return h("i", { class: i <= dots ? "on" : "" }); })),
      h("span", {}, "Attendibilità ", h("b", { text: r.label })), h("u", { text: "Perché" })));

    root.appendChild(chips([["cal", "Calendario"], ["list", "Elenco"]], ui.view, function (v) { ui.view = v; render(); }));
    if (ui.view === "cal") {
      root.appendChild(h("div", { class: "legend" }, h("span", { text: "Prezzi nella norma" }),
        h("span", { class: "ramp", "aria-hidden": "true" }, [1, 2, 3, 4, 5, 6, 7].map(function (i) { return h("i", { style: "background:var(--h" + i + ")" }); })),
        h("span", { text: "molto sopra" }), h("span", {}, h("i", { class: "ev" }), "evento")));
      calendar(root, rec);
    } else {
      listView(root, rec);
    }
    root.appendChild(h("div", { class: "stack" }, h("button", { class: "btn block", onclick: function () { exportCsv(rec); }, text: "Esporta questi prezzi (CSV)" })));
    root.appendChild(h("p", { class: "note", text: "Prezzi per notte pagati dall'ospite, pulizie e commissioni incluse, tassa di soggiorno esclusa. Il prezzo è quello da impostare oggi: riapri l'app nei prossimi giorni, perché cambia con le prenotazioni del mercato." }));
  }

  function calendar(root, rec) {
    var byDate = {};
    rec.nights.forEach(function (n) { byDate[n.date] = n; });
    var first = P.parseDate(rec.nights[0].date), last = P.parseDate(rec.nights[rec.nights.length - 1].date), today = todayMs();
    var cur = new Date(first); cur = Date.UTC(cur.getUTCFullYear(), cur.getUTCMonth(), 1);
    while (cur <= last) {
      var d0 = new Date(cur), y = d0.getUTCFullYear(), mo = d0.getUTCMonth();
      var daysIn = new Date(Date.UTC(y, mo + 1, 0)).getUTCDate(), lead = (d0.getUTCDay() + 6) % 7;
      var grid = h("div", { class: "grid" }), i;
      // solo le settimane che contengono notti del periodo
      var firstDay = Math.max(1, cur < first ? new Date(first).getUTCDate() : 1), lastDay = Math.min(daysIn, last < Date.UTC(y, mo, daysIn) ? new Date(last).getUTCDate() : daysIn);
      var fromDay = firstDay - ((lead + firstDay - 1) % 7), toDay = lastDay + (6 - (lead + lastDay - 1) % 7);
      for (i = fromDay; i <= toDay; i++) {
        if (i < 1 || i > daysIn) { grid.appendChild(h("div", { class: "cell off" })); continue; }
        var ms = Date.UTC(y, mo, i), n = byDate[P.isoDate(ms)];
        if (!n) { grid.appendChild(h("div", { class: "cell off" }, h("span", { class: "d", text: i }))); continue; }
        var cls = "cell " + heatClass(n.uplift) + (n.measured ? "" : " guess") + (ms === today ? " today" : "");
        var label = longDate(ms) + ": " + n.price + " euro" + (n.events.length ? ", " + n.events[0] : "");
        grid.appendChild(h("button", { class: cls, "aria-label": label, onclick: (function (x) { return function () { daySheet(x); }; })(n) },
          h("span", { class: "d", text: i }), h("span", { class: "p", text: n.price }),
          n.weight >= 2 ? h("span", { class: "e", style: "height:" + n.weight + "px" }) : null));
      }
      root.appendChild(h("section", { class: "month" }, h("h3", { text: fmtDate(cur, { month: "long", year: "numeric" }) }),
        h("div", { class: "dow", "aria-hidden": "true" }, ["L", "M", "M", "G", "V", "S", "D"].map(function (x) { return h("span", { text: x }); })), grid));
      cur = Date.UTC(y, mo + 1, 1);
    }
  }

  function listView(root, rec) {
    var tb = h("tbody", {});
    rec.nights.forEach(function (n) {
      tb.appendChild(h("tr", { class: "tap", onclick: function () { daySheet(n); } },
        h("td", {}, fmtDate(P.parseDate(n.date), { weekday: "short", day: "numeric", month: "short" }), n.events.length ? h("small", { text: n.events[0] }) : null),
        h("td", {}, euro(n.price)), h("td", {}, pct(n.sell)), h("td", {}, euro(n.market))));
    });
    root.appendChild(h("table", { class: "table" }, h("thead", {}, h("tr", {}, h("th", { text: "Notte" }), h("th", { text: "Prezzo" }), h("th", { text: "Prob. vendita" }), h("th", { text: "Comparabili" }))), tb));
  }

  function daySheet(n) {
    var m = model(), ms = P.parseDate(n.date);
    openSheet(longDate(ms), function (body) {
      var alt = recommend(state.strategy === "resa" ? "occupazione" : "resa", 1, ms).nights[0];
      if (n.eventList.length) {
        body.appendChild(h("p", { class: "note" }, n.eventList.slice(0, 4).map(function (e) {
          return h("span", { class: "tag" }, h("i", { style: "background:var(--" + (CAT[e.category] || CAT.altro)[1] + ")" }), e.name + (e.venue ? " (" + e.venue + ")" : ""));
        })));
      }
      body.appendChild(h("div", { class: "price-hero" }, h("b", { text: euro(n.price) }),
        h("span", { text: n.lead === 0 ? "per stanotte" : "a notte, da impostare oggi" })));
      // dove cade il prezzo rispetto ai comparabili
      var lo = Math.min(n.floor, n.price, n.low), hi = Math.max(n.cap, n.price, n.high), span = Math.max(1, hi - lo);
      function x(v) { return ((v - lo) / span * 100).toFixed(2) + "%"; }
      var pos = (n.price - lo) / span * 100;
      body.appendChild(h("div", { class: "band-fig", role: "img", "aria-label": "Il prezzo consigliato è " + n.price + " euro; metà dei comparabili sta tra " + n.low + " e " + n.high + " euro, mediana " + n.market },
        h("span", { class: "lab up" + (pos < 14 ? " l" : pos > 86 ? " r" : ""), style: pos < 14 || pos > 86 ? "" : "left:" + x(n.price), text: "consigliato" }),
        h("span", { class: "rail" }), h("span", { class: "iqr", style: "left:" + x(n.low) + ";width:" + ((n.high - n.low) / span * 100).toFixed(2) + "%" }),
        h("span", { class: "med", style: "left:" + x(n.market) }), h("span", { class: "you", style: "left:" + x(n.price) }),
        h("span", { class: "lab dn l", text: euro(lo) }), h("span", { class: "lab dn r", text: euro(hi) })));
      body.appendChild(h("p", { class: "note", text: "Fascia colorata: metà degli appartamenti simili (" + euro(n.low) + "–" + euro(n.high) + "). Tacca: la loro mediana, " + euro(n.market) + "." }));

      var dl = h("dl", { class: "kv" });
      function kv(k, v) { dl.appendChild(h("dt", { text: k })); dl.appendChild(h("dd", { text: v })); }
      kv("Probabilità di vendere la notte", pct(n.sell));
      kv("…tenendo il prezzo dei comparabili", pct(n.sellMarket));
      kv("Prezzi rispetto a una notte normale", signedPct(n.uplift) + (n.measured ? "" : " (stima)"));
      kv("Mercato già occupato oggi", pct(n.occNow));
      kv("Mercato occupato alla data (previsione)", pct(n.occFin));
      kv("Con " + (state.strategy === "resa" ? "massima occupazione" : "massima resa"), euro(alt.price));
      body.appendChild(dl);
      if (n.plan.length >= 2) {
        body.appendChild(h("h3", { style: "margin-top:18px", text: "Se la notte resta libera" }));
        body.appendChild(h("div", { class: "plan" }, n.plan.map(function (p) {
          return h("div", {}, h("b", { text: euro(p.price) }), h("span", { text: "a " + p.lead + (p.lead === 1 ? " giorno" : " giorni") }));
        })));
        body.appendChild(h("p", { class: "note", text: "Il prezzo scende avvicinandosi alla data: più passa il tempo, meno occasioni restano per venderla." }));
      }
      if (!n.measured) body.appendChild(h("p", { class: "note", text: "Per questa data lo scanner non ha ancora letto abbastanza prezzi: il valore viene dal livello normale del periodo e dall'effetto tipico degli eventi." }));
    });
  }

  function relySheet(rec) {
    var m = model(), r = rec.reliability, rel = m.reliability;
    openSheet("Attendibilità " + r.label, function (body) {
      body.appendChild(h("p", { class: "note", text: "Quanto fidarsi di questi prezzi. Cresce da sola: ogni scansione aggiunge un giorno di storico e affina le stime." }));
      var dl = h("dl", { class: "kv" });
      function kv(k, v) { dl.appendChild(h("dt", { text: k })); dl.appendChild(h("dd", { text: v })); }
      kv("Giorni di scansioni raccolti", rel.scan_days + " (pieno a 45)");
      kv("Appartamenti simili nella tua zona", String(r.comparables) + (r.comparables < 30 ? " (pochi)" : ""));
      kv("Notti del periodo con prezzi letti", pct(r.parts.date));
      kv("Giorni di dati sulla domanda", (rel.demand_days || 0) + " (pieno a 21)");
      kv("Sensibilità al prezzo appresa dai dati", pct(r.parts.sensibilita));
      if (rel.seg_mdape !== null && rel.seg_mdape !== undefined) kv("Errore tipico sul prezzo dei comparabili", "±" + pct(rel.seg_mdape));
      var fc = rel.forecast && rel.forecast["7"];
      if (fc) kv("Errore delle previsioni a 7 giorni", "±" + pct(fc.price_err) + " (" + fc.n + " notti)");
      body.appendChild(dl);
      body.appendChild(h("p", { class: "note", style: "margin-top:12px", text: "L'occupazione e l'incasso atteso sono stime: dipendono da quanto il tuo appartamento somiglia ai comparabili e da ipotesi sul comportamento degli ospiti che i dati confermeranno o correggeranno col tempo." }));
    });
  }

  function exportCsv(rec) {
    var a = apt(), rows = [["data", "prezzo_consigliato", "probabilita_vendita", "prezzo_mediano_comparabili", "comparabili_25", "comparabili_75", "rispetto_al_normale", "misurato", "eventi"]];
    rec.nights.forEach(function (n) {
      rows.push([n.date, n.price, n.sell.toFixed(3), n.market, n.low, n.high, n.uplift.toFixed(3), n.measured ? "si" : "no", n.events.join(" | ")]);
    });
    var csv = rows.map(function (r) { return r.map(function (c) { c = String(c); return /[",;\n]/.test(c) ? '"' + c.replace(/"/g, '""') + '"' : c; }).join(";"); }).join("\r\n");
    saveFile("prezzi-" + rec.strategy + "-" + rec.nights[0].date + ".csv", "text/csv", "﻿# " + a.name + " · " + aptSpec(a) + " · strategia: " + rec.strategy + "\r\n" + csv);
  }

  function editApartment(id) {
    var m = model(), a = state.apartments.filter(function (x) { return x.id === id; })[0], d = Object.assign({}, a);
    openSheet("Il tuo appartamento", function (body) {
      var name = h("input", { type: "text", value: d.name, maxlength: 40, id: "f-name" });
      var zone = h("select", { id: "f-zone" });
      Object.keys(m.groups).forEach(function (g) {
        var og = h("optgroup", { label: m.groups[g] });
        m.zones.filter(function (z) { return z.group === g; }).forEach(function (z) { og.appendChild(h("option", { value: z.id, selected: z.id === d.zone, text: z.name })); });
        zone.appendChild(og);
      });
      var minp = h("input", { type: "number", inputmode: "numeric", min: 0, step: 5, value: d.minPrice || "", placeholder: "nessuno", id: "f-min" });
      var defCost = P.defaultCost(m, P.attrLevel(m, d.zone, d.bedrooms, d.baths, d.guests, d.quality));
      var cost = h("input", { type: "number", inputmode: "numeric", min: 0, step: 5, value: d.cost === null || d.cost === undefined ? "" : d.cost, placeholder: defCost + " (stima)", id: "f-cost" });
      body.appendChild(h("div", { class: "field" }, h("label", { for: "f-name", text: "Nome" }), name));
      body.appendChild(h("div", { class: "field" }, h("label", { for: "f-zone", text: "Zona" }), zone));
      body.appendChild(h("div", { class: "two" },
        stepper("Camere da letto", d.bedrooms, 0, 6, 1, function (v) { return v === 0 ? "Monolocale" : String(v); }, function (v) { d.bedrooms = v; }),
        stepper("Bagni", d.baths, 1, 4, 0.5, function (v) { return String(v).replace(".", ","); }, function (v) { d.baths = v; })));
      body.appendChild(stepper("Ospiti al massimo", d.guests, 1, 12, 1, String, function (v) { d.guests = v; }));
      body.appendChild(h("div", { class: "field" }, h("span", { class: "lab", text: "Recensioni e qualità rispetto agli altri" }),
        seg([["base", "Sotto"], ["media", "Nella media"], ["alta", "Sopra"]], d.quality, function (v) { d.quality = v; refreshSheet(); }),
        h("span", { class: "hint", text: "Sopra: 4,9 o più, Superhost, «Amato dagli ospiti». Sotto: meno di 4,7 o annuncio nuovo." })));
      body.appendChild(h("div", { class: "two" },
        h("div", { class: "field" }, h("label", { for: "f-min", text: "Prezzo minimo (€)" }), minp, h("span", { class: "hint", text: "Sotto questa cifra non scendi mai." })),
        h("div", { class: "field" }, h("label", { for: "f-cost", text: "Costi per notte venduta (€)" }), cost, h("span", { class: "hint", text: "Pulizie, biancheria, consumi. Conta per la massima resa." }))));
      function keep() { d.name = name.value.trim() || "Il mio appartamento"; d.zone = zone.value; d.minPrice = Math.max(0, +minp.value || 0); d.cost = cost.value === "" ? null : Math.max(0, +cost.value); }
      [name, zone, minp, cost].forEach(function (el) { el.addEventListener("change", keep); });
      body.appendChild(h("div", { class: "stack" },
        h("button", { class: "btn primary block", onclick: function () { keep(); Object.assign(a, d); state.configured = true; saveState(); closeSheet(); render(); toast("Appartamento salvato"); }, text: "Salva" }),
        state.apartments.length > 1 ? h("button", { class: "btn block danger", onclick: function () {
          state.apartments = state.apartments.filter(function (x) { return x.id !== a.id; }); state.current = state.apartments[0].id; saveState(); closeSheet(); render(); toast("Appartamento eliminato"); }, text: "Elimina questo appartamento" }) : null,
        h("button", { class: "btn block", onclick: function () {
          keep(); Object.assign(a, d);
          var n = Object.assign({}, d, { id: "a" + Date.now(), name: "Appartamento " + (state.apartments.length + 1) });
          state.apartments.push(n); state.current = n.id; state.configured = true; saveState(); closeSheet(); render(); editApartment(n.id); }, text: "Aggiungi un altro appartamento" })));
      if (state.apartments.length > 1) {
        body.appendChild(h("h3", { style: "margin-top:16px", text: "Passa a un altro" }));
        body.appendChild(h("ul", { class: "rows" }, state.apartments.filter(function (x) { return x.id !== a.id; }).map(function (x) {
          return h("li", {}, h("button", { class: "row", onclick: function () { state.current = x.id; saveState(); closeSheet(); render(); } },
            h("div", { class: "main" }, h("div", { class: "t", text: x.name }), h("div", { class: "s", text: aptSpec(x) }))));
        })));
      }
    });
  }

  // ------------------------------------------------------------------ MERCATO
  var DIMS = [["zone", "Zona"], ["bedrooms", "Camere"], ["baths", "Bagni"], ["guests", "Ospiti"], ["district", "Quartiere"]];
  function dimLabel(dim, key) {
    if (dim === "zone") return zoneName(key);
    if (dim === "bedrooms") return key === "0" ? "Monolocale" : key === "4" ? "4 camere o più" : key + (key === "1" ? " camera" : " camere");
    if (dim === "baths") return key === "3+" ? "2,5 bagni o più" : key.replace(".", ",") + (key === "1" ? " bagno" : " bagni");
    if (dim === "guests") return key === "7+" ? "7 ospiti o più" : key + " ospiti";
    return key || "Non indicato";
  }
  function viewMercato(root) {
    var m = model();
    if (!m) return emptyState(root);
    var mk = m.market, all = mk.all || {};
    root.appendChild(h("p", {}, h("b", { text: nf.format(all.n || 0) + " appartamenti interi" }), " con prezzi letti negli ultimi giorni. A metà strada: ", h("b", { text: euro(all.med || 0) }), " a notte."));
    root.appendChild(h("p", { class: "note", text: "Prezzi riportati a una notte normale, così zone e taglie si confrontano alla pari. Per ogni riga: la fascia dove sta metà degli annunci e, col pallino, la mediana." }));
    root.appendChild(chips(DIMS, ui.dim, function (v) { ui.dim = v; render(); }));
    var rows = (mk[ui.dim] || []).filter(function (r) { return r.key !== "" && r.key !== "altro"; });
    if (!rows.length) { root.appendChild(h("p", { class: "empty", text: "Ancora nessun dato per questa vista." })); return; }
    var lo = Math.min.apply(null, rows.map(function (r) { return r.p25; })), hi = Math.max.apply(null, rows.map(function (r) { return r.p75; }));
    lo = Math.floor(lo * 0.94 / 10) * 10; hi = Math.ceil(hi * 1.04 / 10) * 10;
    function x(v) { return ((v - lo) / (hi - lo) * 100).toFixed(2) + "%"; }
    var list = h("ul", { class: "rows" });
    rows.forEach(function (r) {
      list.appendChild(h("li", {}, h("button", { class: "rb", onclick: function () { listingsSheet(ui.dim, r.key); },
        "aria-label": dimLabel(ui.dim, r.key) + ": mediana " + r.med + " euro, da " + r.p25 + " a " + r.p75 + ", " + r.n + " annunci" },
        h("span", { class: "t", text: dimLabel(ui.dim, r.key) }),
        h("span", { class: "s", text: nf.format(r.n) + " annunci, " + r.p25 + "–" + r.p75 + " €" }),
        h("span", { class: "track" }, h("span", { class: "band", style: "left:" + x(r.p25) + ";width:" + ((r.p75 - r.p25) / (hi - lo) * 100).toFixed(2) + "%" }), h("span", { class: "dot", style: "left:" + x(r.med) })),
        h("span", { class: "v" }, r.med + " €", h("small", { text: "mediana" })))));
    });
    root.appendChild(list);
    root.appendChild(h("p", { class: "note", style: "margin-top:12px", text: "Scala da " + euro(lo) + " a " + euro(hi) + ". Tocca una riga per vedere gli annunci." }));
  }
  function listingsSheet(dim, key) {
    var m = model();
    openSheet(dimLabel(dim, key), function (body) {
      if (!data.listings) {
        body.appendChild(h("p", { class: "note", text: "Carico gli annunci…" }));
        getPublic("listings.json").then(function (l) { data.listings = l; refreshSheet(); }).catch(function () { body.textContent = ""; body.appendChild(banner("bad", "Non riesco a scaricare l'elenco degli annunci. Controlla la connessione.")); });
        return;
      }
      var f = data.listings.fields, ix = {}; f.forEach(function (n, i) { ix[n] = i; });
      var zoneIds = m.zones.map(function (z) { return z.id; });
      function match(r) {
        if (dim === "zone") return zoneIds[r[ix.zone]] === key;
        if (dim === "bedrooms") return String(Math.min(r[ix.bedrooms], 4)) === key;
        if (dim === "baths") { var b = r[ix.baths]; return (b < 1.5 ? "1" : b < 2 ? "1.5" : b < 2.5 ? "2" : "3+") === key; }
        if (dim === "guests") { var g = r[ix.guests]; return (g <= 2 ? "1-2" : g <= 4 ? "3-4" : g <= 6 ? "5-6" : "7+") === key; }
        return false;
      }
      var rows = dim === "district" ? [] : data.listings.rows.filter(match);
      if (dim === "district") { body.appendChild(h("p", { class: "note", text: "L'elenco degli annunci è disponibile per zona, camere, bagni e ospiti." })); return; }
      body.appendChild(h("p", { class: "note", text: nf.format(rows.length) + " annunci, dal meno caro. Prezzo tipico di una notte normale." }));
      var ul = h("ul", { class: "rows" });
      rows.slice(0, 120).forEach(function (r) {
        var br = r[ix.bedrooms];
        ul.appendChild(h("li", {}, h("button", { class: "row", onclick: function () { openExternal("https://www.airbnb.it/rooms/" + r[ix.id]); } },
          h("div", { class: "main" }, h("div", { class: "t", text: r[ix.name] || "Annuncio " + r[ix.id] }),
            h("div", { class: "s", text: zoneName(zoneIds[r[ix.zone]], true) + ", " + (br === 0 ? "monolocale" : br + " cam.") + ", " + String(r[ix.baths]).replace(".", ",") + (r[ix.baths] === 1 ? " bagno, " : " bagni, ") + r[ix.guests] + (r[ix.guests_est] ? " ospiti (stima)" : " ospiti") + (r[ix.rating] ? ", " + String(r[ix.rating]).replace(".", ",") + " su " + r[ix.reviews] + " rec." : "") })),
          h("div", { class: "side" }, r[ix.price] + " €", h("small", { text: "apri" })))));
      });
      body.appendChild(ul);
      if (rows.length > 120) body.appendChild(h("p", { class: "note", text: "Mostrati i primi 120. L'elenco completo è nel backup (data/listings.csv)." }));
    });
  }

  // ------------------------------------------------------------------ grafico a linee
  function lineChart(host, o) {
    var W = Math.max(280, host.clientWidth || 320), H = o.height || 200, mL = 38, mR = 10, mT = 22, mB = 22;
    var n = o.series[0].values.length, all = [];
    o.series.forEach(function (s) { s.values.forEach(function (v) { if (v !== null && isFinite(v)) all.push(v); }); });
    var lo = Math.min.apply(null, all), hi = Math.max.apply(null, all), pad = (hi - lo) * 0.08 || 1;
    lo = o.zero ? 0 : lo - pad; hi += pad;
    var step = niceStep((hi - lo) / 3), ticks = [];
    for (var t = Math.ceil(lo / step) * step; t <= hi; t += step) ticks.push(t);
    function X(i) { return mL + (W - mL - mR) * (n === 1 ? 0.5 : i / (n - 1)); }
    function Y(v) { return mT + (H - mT - mB) * (1 - (v - lo) / (hi - lo)); }
    var s = svg("svg", { viewBox: "0 0 " + W + " " + H, role: "img", "aria-label": o.label });
    ticks.forEach(function (v) {
      s.appendChild(svg("line", { class: "grid-line", x1: mL, x2: W - mR, y1: Y(v), y2: Y(v) }));
      s.appendChild(svg("text", { class: "axis", x: mL - 6, y: Y(v) + 4, "text-anchor": "end" }, o.yFmt(v)));
    });
    (o.xTicks || []).forEach(function (tk) { s.appendChild(svg("text", { class: "axis", x: X(tk[0]), y: H - 5, "text-anchor": tk[2] || "middle" }, tk[1])); });
    o.series.slice().reverse().forEach(function (se) {
      var d = "", pen = false;
      se.values.forEach(function (v, i) { if (v === null || !isFinite(v)) { pen = false; return; } d += (pen ? "L" : "M") + X(i).toFixed(1) + " " + Y(v).toFixed(1); pen = true; });
      s.appendChild(svg("path", { class: "line " + (se.cls || ""), d: d }));
    });
    (o.labels || []).forEach(function (lb) {
      var px = X(lb[0]), anchor = px < mL + 50 ? "start" : px > W - mR - 50 ? "end" : "middle";
      s.appendChild(svg("text", { class: "dlabel", x: px, y: Math.max(11, Y(o.series[0].values[lb[0]]) - 8), "text-anchor": anchor }, lb[1]));
    });
    var cross = svg("line", { class: "cross", y1: mT, y2: H - mB, visibility: "hidden" }), dot = svg("circle", { class: "mark", r: 5, visibility: "hidden" });
    s.appendChild(cross); s.appendChild(dot);
    var tip = h("div", { class: "tip", hidden: true });
    function show(clientX) {
      var r = s.getBoundingClientRect(), xx = (clientX - r.left) / r.width * W;
      var i = Math.round((xx - mL) / (W - mL - mR) * (n - 1)); i = Math.min(n - 1, Math.max(0, i));
      var v = o.series[0].values[i]; if (v === null || !isFinite(v)) return;
      cross.setAttribute("x1", X(i)); cross.setAttribute("x2", X(i)); cross.setAttribute("visibility", "visible");
      dot.setAttribute("cx", X(i)); dot.setAttribute("cy", Y(v)); dot.setAttribute("visibility", "visible");
      tip.textContent = ""; o.tip(i).forEach(function (line, j) { tip.appendChild(h(j === 0 ? "b" : j === 1 ? "span" : "i", { text: line })); if (j) tip.appendChild(h("br", {})); });
      tip.hidden = false;
      var left = X(i) / W * r.width; tip.style.left = Math.min(Math.max(left - tip.offsetWidth / 2, 0), r.width - tip.offsetWidth) + "px";
      tip.style.top = (Y(v) / H * r.height > 90 ? 0 : (H - mB) / H * r.height - tip.offsetHeight) + "px";
    }
    s.addEventListener("pointermove", function (e) { show(e.clientX); });
    s.addEventListener("pointerdown", function (e) { show(e.clientX); });
    s.addEventListener("pointerleave", function () { cross.setAttribute("visibility", "hidden"); dot.setAttribute("visibility", "hidden"); tip.hidden = true; });
    host.appendChild(s); host.appendChild(tip);
  }
  function niceStep(raw) { var p = Math.pow(10, Math.floor(Math.log10(raw))), f = raw / p; return (f < 1.5 ? 1 : f < 3.5 ? 2 : f < 7.5 ? 5 : 10) * p; }

  // ------------------------------------------------------------------ EVENTI
  function viewEventi(root) {
    var m = model();
    if (!m) return emptyState(root);
    var a = apt(), attr = P.attrLevel(m, a.zone, a.bedrooms, a.baths, a.guests, a.quality), t0 = P.parseDate(m.start);
    var evs = P.eventsByNight(m), weeks = [], i;
    // una media per settimana: giorno per giorno il fine settimana farebbe un dente di sega illeggibile
    for (i = 0; i + 6 < m.idx.length; i += 7) {
      var sp = 0, sn = 0, mx = 0, top = null;
      for (var j = i; j < i + 7; j++) {
        var pr = Math.exp(attr + m.idx[j]); sp += pr; sn += Math.exp(attr + m.base[j]); mx = Math.max(mx, pr);
        var l = evs[P.isoDate(t0 + j * DAY)];
        if (l && (!top || l[0].weight > top.weight)) top = l[0];
      }
      weeks.push({ start: t0 + i * DAY, price: sp / 7, normal: sn / 7, max: mx, ev: top });
    }
    var xt = [], seen = -1;
    weeks.forEach(function (w, k) { var d = new Date(w.start + 3 * DAY), mo = d.getUTCMonth(); if (mo !== seen && mo % 2 === 0 && k > 1) { xt.push([k, fmtDate(d.getTime(), { month: "short" }).replace(".", "") + (mo === 0 ? " " + String(d.getUTCFullYear()).slice(2) : "")]); } seen = mo; });
    var order = weeks.map(function (w, k) { return k; }).sort(function (x, y) { return weeks[y].price / weeks[y].normal - weeks[x].price / weeks[x].normal; });
    var labels = [];
    order.forEach(function (k) {
      var w = weeks[k];
      if (labels.length < 3 && w.ev && w.ev.weight >= 3 && w.price / w.normal > 1.08 && labels.every(function (q) { return Math.abs(q[0] - k) >= 7; })) labels.push([k, w.ev.name.split(/[·,(]/)[0].trim().slice(0, 18)]);
    });
    root.appendChild(h("h2", { style: "margin-top:6px", text: "Quanto costano i comparabili nei prossimi 12 mesi" }));
    root.appendChild(h("p", { class: "note", text: a.name + ": prezzo mediano a notte degli appartamenti simili, media di ogni settimana. Scorri col dito sul grafico per leggere i valori." }));
    var host = h("div", { class: "chart" });
    root.appendChild(host);
    lineChart(host, { label: "Prezzo mediano dei comparabili per notte, media settimanale dei prossimi 12 mesi", series: [{ values: weeks.map(function (w) { return w.price; }) }, { values: weeks.map(function (w) { return w.normal; }), cls: "ref" }],
      xTicks: xt, labels: labels, yFmt: function (v) { return nf.format(v); },
      tip: function (k) { var w = weeks[k]; return [euro(w.price), "settimana del " + fmtDate(w.start) + ", normale " + euro(w.normal), "notte più cara " + euro(w.max)].concat(w.ev && w.ev.weight >= 2 ? [w.ev.name] : []); } });
    root.appendChild(h("div", { class: "keys" }, h("span", { text: "Prezzo dei comparabili" }), h("span", { class: "ref", text: "Livello normale" })));

    root.appendChild(h("h2", { text: "Eventi in calendario" }));
    root.appendChild(h("div", { class: "chips" },
      h("button", { class: "chip", "aria-pressed": String(!ui.evAll), onclick: function () { ui.evAll = false; render(); }, text: "Che spostano i prezzi" }),
      h("button", { class: "chip", "aria-pressed": String(ui.evAll), onclick: function () { ui.evAll = true; render(); }, text: "Tutti" })));
    var today = P.isoDate(todayMs()), list = m.events.filter(function (e) { return e.night_last >= today && (ui.evAll || e.weight >= 2); });
    var ul = h("ul", { class: "rows" }), month = "";
    list.forEach(function (e) {
      var mo = fmtDate(P.parseDate(e.start), { month: "long", year: "numeric" });
      if (mo !== month) { month = mo; ul.appendChild(h("li", { style: "border:0" }, h("h3", { style: "margin:18px 0 2px;text-transform:capitalize", text: mo }))); }
      var c = CAT[e.category] || CAT.altro, same = e.start === e.end;
      var when = same ? fmtDate(P.parseDate(e.start), { weekday: "short", day: "numeric", month: "short" }) : fmtDate(P.parseDate(e.start), { day: "numeric", month: "short" }) + " – " + fmtDate(P.parseDate(e.end), { day: "numeric", month: "short" });
      var est = e.uplift === null || e.uplift === undefined ? (m.event_effect[String(e.weight)] || 0) : e.uplift;
      ul.appendChild(h("li", {}, h("button", { class: "row", onclick: function () { eventSheet(e); } },
        h("div", { class: "main" }, h("div", { class: "t", text: e.name }), h("div", { class: "s" }, when + (e.venue ? ", " + e.venue : "")),
          h("div", { class: "s" }, h("span", { class: "tag" }, h("i", { style: "background:var(--" + c[1] + ")" }), c[0]), "peso " + WEIGHT[e.weight])),
        h("div", { class: "side" }, signedPct(est), h("small", { text: e.measured ? "misurato" : "stima" })))));
    });
    if (!list.length) ul.appendChild(h("li", { class: "empty", text: "Nessun evento in arrivo." }));
    root.appendChild(ul);
    root.appendChild(h("div", { class: "stack" }, h("button", { class: "btn primary block", onclick: function () { addEvent(); }, text: "Aggiungi un evento" })));
    root.appendChild(h("p", { class: "note", text: "«Misurato» è l'aumento dei prezzi che lo scanner ha letto per quelle notti; «stima» è l'effetto tipico di eventi dello stesso peso, usato finché le notti non vengono lette." }));
  }
  function eventSheet(e) {
    var m = model();
    openSheet(e.name, function (body) {
      var c = CAT[e.category] || CAT.altro, dl = h("dl", { class: "kv" });
      function kv(k, v) { dl.appendChild(h("dt", { text: k })); dl.appendChild(h("dd", { text: v })); }
      kv("Quando", fmtDate(P.parseDate(e.start), { day: "numeric", month: "long", year: "numeric" }) + (e.end !== e.start ? " – " + fmtDate(P.parseDate(e.end), { day: "numeric", month: "long" }) : ""));
      if (e.venue) kv("Dove", e.venue);
      kv("Tipo", c[0]); kv("Peso", WEIGHT[e.weight]);
      kv("Notti interessate", fmtDate(P.parseDate(e.night_first)) + (e.night_last !== e.night_first ? " – " + fmtDate(P.parseDate(e.night_last)) : ""));
      kv("Effetto sui prezzi", e.measured ? signedPct(e.uplift) + " (misurato)" : signedPct(m.event_effect[String(e.weight)] || 0) + " (stima)");
      kv("Fonte", { seed: "elenco curato", auto: "calendari pubblici", festivita: "calendario", mercato: "rilevato dai prezzi", custom: "aggiunto da te", locale: "aggiunto da te (solo su questo telefono)" }[e.source] || e.source);
      body.appendChild(dl);
      if (e.note) body.appendChild(h("p", { class: "note", style: "margin-top:10px", text: e.note }));
      var mine = state.customEvents.filter(function (x) { return x.name === e.name && x.start === e.start; })[0];
      var from = P.parseDate(e.night_first) - 2 * DAY;
      body.appendChild(h("div", { class: "stack" },
        h("button", { class: "btn block", onclick: function () { closeSheet(); state.period = 90; saveState(); go("prezzi"); setTimeout(function () { var n = recommend(state.strategy, 1, Math.max(todayMs(), P.parseDate(e.night_first))).nights[0]; if (n) daySheet(n); }, 60); }, text: "Vedi il prezzo per la prima notte" }),
        mine ? h("button", { class: "btn block danger", onclick: function () { state.customEvents = state.customEvents.filter(function (x) { return x !== mine; }); saveState(); eff = null; closeSheet(); render(); toast("Evento eliminato"); syncEvents(true); }, text: "Elimina questo evento" }) : null));
      void from;
    });
  }
  function addEvent() {
    var d = { name: "", start: P.isoDate(todayMs() + 30 * DAY), end: "", category: "altro", weight: 3, venue: "" };
    openSheet("Nuovo evento", function (body) {
      var name = h("input", { type: "text", id: "e-name", maxlength: 60, value: d.name, placeholder: "es. Concerto allo stadio" });
      var s = h("input", { type: "date", id: "e-start", value: d.start }), e = h("input", { type: "date", id: "e-end", value: d.end || d.start });
      var cat = h("select", { id: "e-cat" }, ["fiera", "concerto", "sport", "cultura", "altro"].map(function (k) { return h("option", { value: k, selected: k === d.category, text: CAT[k][0] }); }));
      function keep() { d.name = name.value.trim(); d.start = s.value; d.end = e.value < s.value ? s.value : e.value; d.category = cat.value; }
      [name, s, e, cat].forEach(function (el) { el.addEventListener("change", keep); });
      body.appendChild(h("div", { class: "field" }, h("label", { for: "e-name", text: "Nome" }), name));
      body.appendChild(h("div", { class: "two" }, h("div", { class: "field" }, h("label", { for: "e-start", text: "Dal" }), s), h("div", { class: "field" }, h("label", { for: "e-end", text: "Al" }), e)));
      body.appendChild(h("div", { class: "field" }, h("label", { for: "e-cat", text: "Tipo" }), cat, h("span", { class: "hint", text: "Per fiere e sport conta anche la notte prima dell'inizio." })));
      body.appendChild(stepper("Quanto sposta i prezzi", d.weight, 1, 5, 1, function (v) { return v + " su 5, " + WEIGHT[v] + " (" + signedPct(model().event_effect[String(v)] || 0) + ")"; }, function (v) { d.weight = v; }));
      body.appendChild(h("div", { class: "stack" }, h("button", { class: "btn primary block", onclick: function () {
        keep(); if (!d.name || !d.start) { toast("Servono almeno nome e data"); return; }
        state.customEvents.push({ name: d.name, start: d.start, end: d.end || d.start, category: d.category, weight: d.weight, venue: d.venue });
        saveState(); eff = null; closeSheet(); render(); toast("Evento aggiunto"); syncEvents(true);
      }, text: "Aggiungi" })));
      body.appendChild(h("p", { class: "note", text: state.repo.token ? "L'evento viene salvato anche su GitHub: dalla prossima scansione lo scanner leggerà i prezzi veri di quelle notti." : "Senza token l'evento resta su questo telefono e usa l'effetto tipico. Con il token (sezione Dati) lo scanner leggerà i prezzi veri di quelle notti." }));
    });
  }
  function syncEvents(quiet) {
    if (!state.repo.token) return Promise.resolve(false);
    var path = "/contents/config/events_custom.json";
    var content = JSON.stringify({ events: state.customEvents }, null, 1) + "\n";
    return gh(path).then(function (r) { return r.ok ? r.json() : null; }).then(function (cur) {
      return gh(path, { method: "PUT", body: { message: "Eventi aggiunti dall'app", content: btoa(unescape(encodeURIComponent(content))), sha: cur ? cur.sha : undefined } });
    }).then(function (r) { if (!r.ok) throw new Error(r.status); if (!quiet) toast("Eventi salvati su GitHub"); return true; })
      .catch(function () { toast("Eventi salvati solo sul telefono: il token non permette di scrivere su GitHub"); return false; });
  }

  // ------------------------------------------------------------------ DATI
  function viewDati(root) {
    var m = model(), st = data.status, rel = m ? m.reliability : null;
    if (data.error && data.error !== "nodata") root.appendChild(banner("bad", "Non riesco a scaricare i dati (" + data.error + "). " + (data.model ? "Sto mostrando l'ultima copia salvata sul telefono." : "Controlla la connessione e il nome del repository.")));
    ((st && st.warnings) || []).forEach(function (w) { root.appendChild(banner("warn", w)); });
    if (data.run) {
      var r = data.run, live = r.status !== "completed";
      root.appendChild(banner(live ? "ok" : r.conclusion === "success" ? "ok" : "warn",
        live ? "Scansione in corso su GitHub: di solito dura 40-50 minuti. Poi tocca «Aggiorna i dati»." : "Ultima esecuzione su GitHub: " + (r.conclusion === "success" ? "riuscita" : "non riuscita") + ", " + new Intl.DateTimeFormat("it-IT", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }).format(new Date(r.updated_at)) + "."));
    }
    if (st || m) {
      var dl = h("dl", { class: "kv" });
      var kv = function (k, v) { dl.appendChild(h("dt", { text: k })); dl.appendChild(h("dd", { text: v })); };
      if (st && st.last_scan) kv("Ultima scansione", fmtDate(P.parseDate(st.last_scan), { day: "numeric", month: "long", year: "numeric" }));
      if (st && st.run) { kv("Prezzi letti nell'ultima scansione", nf.format(st.run.prices || 0)); kv("Richieste ad Airbnb, errori", nf.format(st.run.requests || 0) + ", " + (st.run.errors || 0)); }
      if (rel) { kv("Giorni di storico", String(rel.scan_days)); kv("Appartamenti con prezzi", nf.format(rel.n_listings)); kv("Prezzi usati dal modello", nf.format(rel.n_obs)); }
      if (st && st.inside_airbnb) kv("Catalogo di partenza (Inside Airbnb)", st.inside_airbnb.snapshot ? fmtDate(P.parseDate(st.inside_airbnb.snapshot), { day: "numeric", month: "long", year: "numeric" }) : "sì");
      root.appendChild(h("h2", { style: "margin-top:8px", text: "Stato" })); root.appendChild(dl);
    }
    if (rel) {
      root.appendChild(h("h2", { text: "Quanto è preciso il modello" }));
      var q = h("dl", { class: "kv" }), kq = function (k, v) { q.appendChild(h("dt", { text: k })); q.appendChild(h("dd", { text: v })); };
      if (rel.seg_mdape !== null && rel.seg_mdape !== undefined) kq("Errore sul prezzo mediano di zona e taglia", "±" + pct(rel.seg_mdape));
      if (rel.fit_mdape !== null && rel.fit_mdape !== undefined) kq("Errore sul singolo annuncio", "±" + pct(rel.fit_mdape));
      kq("Sensibilità al prezzo", String(m.beta).replace(".", ",") + (rel.beta.weight > 0.05 ? " (appresa al " + pct(rel.beta.weight) + ")" : " (valore di partenza)"));
      kq("Confronti tra letture complete", nf.format(rel.beta.pairs || 0));
      ["7", "14"].forEach(function (k) { var f = rel.forecast && rel.forecast[k]; if (f) kq("Previsioni a " + k + " giorni: errore sui prezzi", "±" + pct(f.price_err)); });
      root.appendChild(q);
      var hist = (rel.history || []).filter(function (x) { return x.seg_mdape !== null; });
      if (hist.length >= 3) {
        root.appendChild(h("p", { class: "note", style: "margin-top:14px", text: "Errore sul prezzo mediano, giorno per giorno: scende man mano che lo storico cresce." }));
        var host = h("div", { class: "chart" }); root.appendChild(host);
        lineChart(host, { label: "Errore del modello nel tempo", height: 150, zero: true, series: [{ values: hist.map(function (x) { return x.seg_mdape * 100; }) }],
          xTicks: [[0, fmtDate(P.parseDate(hist[0].date)), "start"], [hist.length - 1, fmtDate(P.parseDate(hist[hist.length - 1].date)), "end"]],
          yFmt: function (v) { return nf.format(v) + "%"; }, tip: function (k) { return ["±" + hist[k].seg_mdape.toLocaleString("it-IT", { style: "percent", maximumFractionDigits: 1 }), fmtDate(P.parseDate(hist[k].date)) + ", " + nf.format(hist[k].n_obs) + " prezzi"]; } });
      } else {
        root.appendChild(h("p", { class: "note", style: "margin-top:10px", text: "Tra qualche giorno qui comparirà l'andamento dell'errore: serve almeno uno storico di tre scansioni." }));
      }
    }
    var base = "https://github.com/" + state.repo.owner + "/" + state.repo.name;
    root.appendChild(h("h2", { text: "Azioni" }));
    root.appendChild(h("div", { class: "stack" },
      h("button", { class: "btn primary block", disabled: data.loading, onclick: function () { loadData(true); loadRun(); }, text: data.loading ? "Aggiorno…" : "Aggiorna i dati" }),
      h("button", { class: "btn block", onclick: startScan, text: "Avvia una scansione adesso" }),
      h("button", { class: "btn block", onclick: function () { downloadUrl(base + "/releases/download/backup/bbb-backup.zip", "bbb-backup.zip"); }, text: "Scarica il backup completo (ZIP)" }),
      h("button", { class: "btn block", onclick: function () { saveFile("bbb-impostazioni.json", "application/json", JSON.stringify({ apartments: state.apartments, customEvents: state.customEvents, strategy: state.strategy, period: state.period }, null, 1)); }, text: "Esporta appartamenti ed eventi" }),
      h("button", { class: "btn block", onclick: importSettings, text: "Importa appartamenti ed eventi" }),
      h("button", { class: "btn block", onclick: function () { openExternal(base + "/actions"); }, text: "Apri le esecuzioni su GitHub" })));
    root.appendChild(h("p", { class: "note", text: "Il backup contiene tutto lo storico: prezzi giorno per giorno, catalogo, conteggi, modello. Finisce nella cartella Download del telefono." }));

    root.appendChild(h("h2", { text: "Collegamento a GitHub" }));
    var owner = h("input", { type: "text", id: "r-owner", value: state.repo.owner, autocapitalize: "off", autocomplete: "off" });
    var name = h("input", { type: "text", id: "r-name", value: state.repo.name, autocapitalize: "off", autocomplete: "off" });
    var token = h("input", { type: "password", id: "r-token", value: state.repo.token, autocomplete: "off", placeholder: "facoltativo" });
    root.appendChild(h("div", { class: "two" }, h("div", { class: "field" }, h("label", { for: "r-owner", text: "Utente" }), owner), h("div", { class: "field" }, h("label", { for: "r-name", text: "Repository" }), name)));
    root.appendChild(h("div", { class: "field" }, h("label", { for: "r-token", text: "Token di accesso" }), token,
      h("span", { class: "hint", text: "Serve solo per avviare una scansione dall'app e per salvare su GitHub gli eventi che aggiungi. Per leggere i prezzi non serve. Resta su questo telefono." })));
    root.appendChild(h("div", { class: "stack" }, h("button", { class: "btn block", onclick: function () {
      state.repo.owner = owner.value.trim() || DEFAULT_REPO.owner; state.repo.name = name.value.trim() || DEFAULT_REPO.name; state.repo.token = token.value.trim();
      saveState(); data.model = null; data.status = null; data.listings = null; eff = null; localStorage.removeItem(KEY + ".model"); loadData(true); loadRun(); }, text: "Salva il collegamento" })));
    root.appendChild(h("details", {}, h("summary", { text: "Come creare il token" }), h("ol", {},
      h("li", { text: "Su github.com apri Settings, poi Developer settings, Personal access tokens, Fine-grained tokens." }),
      h("li", { text: "Generate new token. Repository access: Only select repositories, e scegli " + state.repo.name + "." }),
      h("li", { text: "Permissions del repository: Actions «Read and write», Contents «Read and write»." }),
      h("li", { text: "Genera, copia il token e incollalo qui sopra." }))));
    root.appendChild(h("details", {}, h("summary", { text: "Da dove vengono i dati" }), h("ul", {},
      h("li", { text: "Prezzi e disponibilità: ricerca pubblica di Airbnb, letta una volta al giorno dal programma sul tuo repository GitHub." }),
      h("li", { text: "Catalogo di partenza e capienze: Inside Airbnb (dati aperti, CC BY 4.0). Confini dei quartieri: Comune di Bologna via Inside Airbnb." }),
      h("li", { text: "Eventi: calendari pubblici di BolognaFiere e Bologna Welcome, più l'elenco curato e quelli che aggiungi." }),
      h("li", { text: "App " + (native && native.version ? native.version() : "web") + (m ? ", modello del " + m.generated : "") + "." }))));
  }
  function loadRun() {
    return gh("/actions/workflows/scan.yml/runs?per_page=1").then(function (r) { return r.ok ? r.json() : null; }).then(function (j) {
      data.run = j && j.workflow_runs && j.workflow_runs[0] ? j.workflow_runs[0] : null; if (ui.tab === "dati") render();
    }).catch(function () { /* senza rete lo stato dell'esecuzione resta vuoto */ });
  }
  function startScan() {
    if (!state.repo.token) { toast("Per avviare la scansione dall'app serve il token: lo imposti qui sotto"); var el = document.getElementById("r-token"); if (el) el.focus(); return; }
    gh("/actions/workflows/scan.yml/dispatches", { method: "POST", body: { ref: "main" } }).then(function (r) {
      if (r.status === 204) { toast("Scansione avviata: dura circa 40-50 minuti"); setTimeout(loadRun, 4000); }
      else if (r.status === 401 || r.status === 403) toast("Il token non ha il permesso Actions «Read and write»");
      else toast("GitHub ha risposto " + r.status);
    }).catch(function () { toast("Connessione assente"); });
  }
  function importSettings() {
    var inp = h("input", { type: "file", accept: "application/json,.json" });
    inp.addEventListener("change", function () {
      var f = inp.files[0]; if (!f) return;
      var rd = new FileReader();
      rd.onload = function () {
        try {
          var j = JSON.parse(String(rd.result));
          if (!Array.isArray(j.apartments) || !j.apartments.length) throw new Error("vuoto");
          state.apartments = j.apartments; state.current = j.apartments[0].id; state.customEvents = Array.isArray(j.customEvents) ? j.customEvents : [];
          state.configured = true; saveState(); eff = null; render(); toast("Importati " + j.apartments.length + " appartamenti");
        } catch (e) { toast("File non valido"); }
      };
      rd.readAsText(f);
    });
    inp.click();
  }

  // ------------------------------------------------------------------ cornice
  function emptyState(root) {
    if (data.loading) { root.appendChild(h("div", { class: "empty" }, h("p", { text: "Scarico i dati…" }))); return; }
    if (data.error === "nodata") {
      root.appendChild(h("div", { class: "empty" }, h("h2", { text: "Ancora nessun dato" }),
        h("p", { text: "La prima scansione non è ancora terminata. Parte da sola ogni notte; puoi anche avviarla subito dalla sezione Dati." }),
        h("div", { class: "stack" }, h("button", { class: "btn primary", onclick: function () { go("dati"); }, text: "Vai a Dati" }))));
    } else {
      root.appendChild(h("div", { class: "empty" }, h("h2", { text: "Dati non disponibili" }),
        h("p", { text: "Non riesco a raggiungere GitHub" + (data.error ? " (" + data.error + ")" : "") + ". Controlla la connessione." }),
        h("div", { class: "stack" }, h("button", { class: "btn primary", onclick: function () { loadData(true); }, text: "Riprova" }))));
    }
  }
  var TITLES = { prezzi: "Prezzi", mercato: "Mercato", eventi: "Eventi", dati: "Dati" };
  function go(tab) { ui.tab = tab; window.scrollTo(0, 0); render(); if (tab === "dati" && !data.run) loadRun(); }
  function render() {
    var root = document.getElementById("view");
    root.textContent = "";
    document.getElementById("title").textContent = TITLES[ui.tab];
    var m = data.model;
    document.getElementById("stamp").textContent = m ? "dati del " + fmtDate(P.parseDate(m.start), { day: "numeric", month: "short" }) + (data.cached ? " (copia)" : "") : "";
    Array.prototype.forEach.call(document.querySelectorAll(".tabs button"), function (b) { if (b.dataset.tab === ui.tab) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current"); });
    try {
      ({ prezzi: viewPrezzi, mercato: viewMercato, eventi: viewEventi, dati: viewDati })[ui.tab](root);
    } catch (e) {
      root.textContent = "";
      root.appendChild(banner("bad", "Qualcosa non ha funzionato nel mostrare questa pagina (" + (e && e.message) + "). Prova ad aggiornare i dati dalla sezione Dati."));
      if (window.console) console.error(e);
    }
  }
  Array.prototype.forEach.call(document.querySelectorAll(".tabs button"), function (b) { b.addEventListener("click", function () { go(b.dataset.tab); }); });
  window.addEventListener("resize", function () { if (ui.tab === "eventi" || ui.tab === "dati") render(); });
  window.bbbState = function () { return { state: state, data: data, ui: ui }; };
  loadData(false);
})();
