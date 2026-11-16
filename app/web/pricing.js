/* Prezzo consigliato: stesso calcolo di bnb/pricing.py (un test confronta i due risultati).
 *
 * Per ogni notte il modello dà il prezzo mediano dei comparabili (m), la probabilità giornaliera
 * che una notte libera venga prenotata a quel prezzo (h) e quanto questa cambia col prezzo (beta).
 *   massima resa         programmazione dinamica: il prezzo di oggi che rende massimo il margine atteso,
 *                        sapendo che nei giorni successivi il prezzo potrà scendere se la notte resta libera
 *   massima occupazione  il prezzo più alto che lascia almeno l'obiettivo di probabilità di vendere la notte
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.Pricing = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";
  var GRID = 25, H_MIN = 0.0005, DAY = 86400000;

  function logit(p) { p = Math.min(Math.max(p, 1e-6), 1 - 1e-6); return Math.log(p / (1 - p)); }
  function expit(x) { return 1 / (1 + Math.exp(-x)); }
  function clamp(x, a, b) { return Math.min(Math.max(x, a), b); }
  function parseDate(s) { var p = s.split("-"); return Date.UTC(+p[0], +p[1] - 1, +p[2]); }
  function isoDate(ms) { return new Date(ms).toISOString().slice(0, 10); }
  function segClass(b) { return b <= 1 ? "c01" : b === 2 ? "c2" : "c3"; }

  function attrLevel(model, zone, bedrooms, baths, guests, quality) {
    var c = model.coef;
    var q = c.quality[quality] !== undefined ? c.quality[quality] : c.quality.media;
    return c.intercept + (c.zone[zone] || 0) + c.bedrooms[String(clamp(Math.round(bedrooms), 0, 4))] +
      c.bath * (clamp(baths, 0.5, 4) - 1) + c.guests * Math.log(clamp(guests, 1, 12) / 4) + q;
  }

  function quantiles(model) {
    var q = {};
    for (var i = 0; i < model.q_levels.length; i++) q[model.q_levels[i]] = model.q_values[i];
    return q;
  }

  function priceBand(model) {
    var q = quantiles(model);
    var lo = Math.max(q[model.band[0]], Math.log(1 - (model.max_discount === undefined ? 0.25 : model.max_discount)));
    var hi = Math.min(q[model.band[1]], Math.log(1 + (model.max_premium === undefined ? 0.40 : model.max_premium)));
    return [lo, Math.max(hi, lo + 0.05)];
  }

  /* h[j]: probabilità di prenotazione nel giorno in cui mancano lead0-j giorni, a prezzo di mercato */
  function nightHazards(model, theta, lead0) {
    var h = new Float64Array(Math.max(0, lead0)), C = model.pace, k = theta / model.nu;
    for (var j = 0; j < lead0; j++) {
      var L = lead0 - j;
      var dC = Math.max(C[L - 1] - C[L], 0);
      h[j] = clamp(1 - Math.exp(-k * dC), H_MIN, 0.95);
    }
    return h;
  }

  function defaultCost(model, attr) {
    var base = model.base.slice(0, 45).sort(function (a, b) { return a - b; });
    var share = model.cost_share === undefined ? 0.30 : model.cost_share;
    return Math.round(share * Math.exp(attr + base[Math.floor(base.length / 2)]));
  }

  var DEFAULT_FEES = {
    vat: 0.22,
    channels: [
      { id: "airbnb", name: "Airbnb", fee: 15.5, vat: true, tax: "portal" },
      { id: "booking", name: "Booking.com", fee: 16.5, vat: true, tax: "host" },
      { id: "vrbo", name: "Vrbo", fee: 8.0, vat: true, tax: "host" },
      { id: "direct", name: "Prenotazione diretta", fee: 0.0, vat: false, tax: "host" }
    ],
    tourist_tax: { year: 2026, portal_rate: 0.105, cap: 7.0, bands: [[31, 4.0], [71, 5.8], [121, 6.5], [null, 7.0]], max_nights: 5 }
  };
  function feesOf(model) { return (model && model.fees) || DEFAULT_FEES; }
  function channel(model, id) {
    var list = feesOf(model).channels;
    for (var i = 0; i < list.length; i++) if (list[i].id === id) return list[i];
    return list[0];
  }
  /* quota del prezzo trattenuta dal portale: commissione in %, con l'IVA sopra se dovuta */
  function feeShare(model, id, fee, vat) {
    var ch = channel(model, id), f = feesOf(model);
    var pct = (fee === undefined || fee === null || fee === "") ? ch.fee : +fee;
    var withVat = (vat === undefined || vat === null) ? !!ch.vat : !!vat;
    return clamp(pct / 100 * (1 + (withVat ? (f.vat === undefined ? 0.22 : f.vat) : 0)), 0, 0.9);
  }
  /* imposta di soggiorno per una notte, per tutti gli ospiti paganti: "portal" = percentuale con tetto a persona
     (la riscuote il portale), "host" = scaglioni sul prezzo a persona (la riscuote chi ospita) */
  function touristTax(model, price, payers, mode) {
    var tt = feesOf(model).tourist_tax;
    if (!tt || !(payers > 0) || !(price > 0)) return 0;
    if (mode === "portal") return Math.min(tt.portal_rate * price, tt.cap * payers);
    var each = price / payers;
    for (var i = 0; i < tt.bands.length; i++) if (tt.bands[i][0] === null || each < tt.bands[i][0]) return tt.bands[i][1] * payers;
    return tt.bands[tt.bands.length - 1][1] * payers;
  }

  function solveNight(h, beta, m, lo, hi, strategy, target, cost, floorPrice, keepShare) {
    var ks = keepShare === undefined ? 1 : keepShare;
    var loEff = Math.max(lo, Math.log(Math.max(floorPrice || 0, 1e-9) / m));
    var hiEff = Math.max(hi, loEff);
    var lnr = new Float64Array(GRID), r = new Float64Array(GRID), i, k;
    for (i = 0; i < GRID; i++) { lnr[i] = loEff + (hiEff - loEff) * i / (GRID - 1); r[i] = Math.exp(lnr[i]); }
    var n = h.length;
    if (n === 0) return { rel: r[0], sell: 0, gross: 0, net: 0, sellMarket: 0, plan: [] };
    var lg = new Float64Array(n), keep = 1;
    for (k = 0; k < n; k++) { lg[k] = logit(h[k]); keep *= (1 - h[k]); }
    var sellMarket = 1 - keep;
    if (strategy === "occupazione") {
      var best = 0, bestSell = 0, first = true;
      for (i = 0; i < GRID; i++) {
        var surv = 1;
        for (k = 0; k < n; k++) surv *= 1 - expit(lg[k] + beta * lnr[i]);
        var s = 1 - surv;
        if (first) { bestSell = s; first = false; }
        if (s >= target) { best = i; bestSell = s; }
      }
      return { rel: r[best], sell: bestSell, gross: r[best] * m * bestSell, net: (r[best] * m * ks - cost) * bestSell, sellMarket: sellMarket, plan: [] };
    }
    var V = 0, G = 0, Q = 0, policy = new Float64Array(n);
    for (k = n - 1; k >= 0; k--) {
      var bj = 0, bv = -Infinity, bh = 0;
      for (i = 0; i < GRID; i++) {
        var hr = expit(lg[k] + beta * lnr[i]);
        var val = hr * (r[i] * m * ks - cost) + (1 - hr) * V;
        if (val > bv) { bv = val; bj = i; bh = hr; }
      }
      G = bh * r[bj] * m + (1 - bh) * G;
      Q = bh + (1 - bh) * Q;
      V = bv;
      policy[k] = r[bj];
    }
    var plan = [], leads = [30, 14, 7, 3, 1];
    for (i = 0; i < leads.length; i++) if (leads[i] < n) plan.push({ lead: leads[i], rel: policy[n - leads[i]] });
    return { rel: policy[0], sell: Q, gross: G, net: V, sellMarket: sellMarket, plan: plan };
  }

  function eventsByNight(model) {
    var map = {};
    (model.events || []).forEach(function (e) {
      var a = parseDate(e.night_first), b = parseDate(e.night_last);
      for (var t = a; t <= b; t += DAY) { var key = isoDate(t); (map[key] = map[key] || []).push(e); }
    });
    Object.keys(map).forEach(function (k) { map[k].sort(function (x, y) { return y.weight - x.weight; }); });
    return map;
  }

  function reliability(model, zone, bedrooms, nights) {
    var rel = model.reliability;
    var zb = ((model.market.zone_bedrooms || {})[zone] || {})[String(Math.min(bedrooms, 4))];
    var comp = zb ? zb.n : 0;
    var measured = 0;
    nights.forEach(function (x) { if (x.measured) measured++; });
    var parts = {
      storico: Math.min(1, rel.scan_days / 45),
      comparabili: Math.min(1, comp / 30),
      date: nights.length ? measured / nights.length : 0,
      domanda: Math.min(1, (rel.demand_days || 0) / 21),
      sensibilita: (rel.beta && rel.beta.weight) || 0
    };
    var score = 0.25 * parts.storico + 0.25 * parts.comparabili + 0.25 * parts.date + 0.15 * parts.domanda + 0.10 * parts.sensibilita;
    return { score: score, label: score >= 0.7 ? "alta" : score >= 0.4 ? "media" : "bassa", parts: parts, comparables: comp };
  }

  /* p = {zone, bedrooms, baths, guests, start, end, strategy, quality, minPrice, cost, fee, payers, taxMode}
     notti da start a end comprese; fee = quota trattenuta dal portale (0..1), payers = ospiti che pagano l'imposta */
  function recommend(model, p) {
    var t0 = parseDate(model.start), band = priceBand(model), q = quantiles(model);
    var lo = band[0], hi = band[1], beta = model.beta, target = model.target_occupancy;
    var attr = attrLevel(model, p.zone, p.bedrooms, p.baths, p.guests, p.quality || "media");
    var grp = "cintura";
    model.zones.forEach(function (z) { if (z.id === p.zone) grp = z.group; });
    var thetas = model.theta[segClass(Math.round(p.bedrooms))][grp];
    var cost = (p.cost === undefined || p.cost === null) ? defaultCost(model, attr) : p.cost;
    var fee = p.fee || 0, keepShare = 1 - fee, payers = p.payers === undefined ? 2 : p.payers, taxMode = p.taxMode || "portal";
    var evs = eventsByNight(model), nights = [];
    var sums = { price: 0, sell: 0, gross: 0, net: 0, market: 0, sellMarket: 0, marketRev: 0, tax: 0 };
    for (var t = parseDate(p.start); t <= parseDate(p.end); t += DAY) {
      var k = Math.round((t - t0) / DAY);
      if (k < 0 || k >= model.idx.length) continue;
      var m = Math.exp(attr + model.idx[k]);
      var s = solveNight(nightHazards(model, thetas[k], k), beta, m, lo, hi, p.strategy || "resa", target, cost, p.minPrice || 0, keepShare);
      var price = Math.max(1, Math.round(s.rel * m)), date = isoDate(t), list = evs[date] || [];
      var tax = touristTax(model, price, payers, taxMode);
      var night = {
        date: date, lead: k, price: price, sell: s.sell, gross: s.gross, net: s.net,
        fee: price * fee, payout: price * keepShare, tax: tax,
        market: Math.round(m), sellMarket: s.sellMarket,
        low: Math.round(m * Math.exp(q[25])), high: Math.round(m * Math.exp(q[75])),
        floor: Math.round(m * Math.exp(lo)), cap: Math.round(m * Math.exp(hi)),
        uplift: Math.exp(model.idx[k] - model.base[k]) - 1,
        occNow: model.occ_now[k], occFin: model.occ_fin[k],
        measured: model.cov[k] >= 6,
        events: list.map(function (e) { return e.name; }), eventList: list,
        weight: list.length ? list[0].weight : 0,
        plan: s.plan.map(function (x) { return { lead: x.lead, price: Math.round(x.rel * m) }; })
      };
      nights.push(night);
      sums.price += price; sums.sell += s.sell; sums.gross += s.gross; sums.net += s.net;
      sums.market += night.market; sums.sellMarket += s.sellMarket; sums.marketRev += night.market * s.sellMarket;
      sums.tax += tax * s.sell;
    }
    var n = nights.length || 1;
    return {
      strategy: p.strategy || "resa", cost: cost, nights: nights, fee: fee, taxMode: taxMode, payers: payers,
      fees: Math.round(sums.gross * fee), tax: Math.round(sums.tax),
      avgPrice: Math.round(sums.price / n), occupancy: sums.sell / n,
      revenue: Math.round(sums.gross), net: Math.round(sums.net),
      marketAvg: Math.round(sums.market / n), marketOccupancy: sums.sellMarket / n, marketRevenue: Math.round(sums.marketRev),
      reliability: reliability(model, p.zone, Math.round(p.bedrooms), nights)
    };
  }

  return {
    recommend: recommend, attrLevel: attrLevel, defaultCost: defaultCost, priceBand: priceBand,
    feesOf: feesOf, channel: channel, feeShare: feeShare, touristTax: touristTax,
    nightHazards: nightHazards, solveNight: solveNight, reliability: reliability, eventsByNight: eventsByNight,
    parseDate: parseDate, isoDate: isoDate, DAY: DAY
  };
});
