// Usato da test_js_parity.py: calcola i prezzi con il codice dell'app e li stampa in JSON.
const fs = require("fs");
const path = require("path");
const Pricing = require(path.join(__dirname, "..", "app", "web", "pricing.js"));
const model = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const cases = JSON.parse(process.argv[3]);
const out = cases.map((p) => {
  const r = Pricing.recommend(model, p);
  return {
    cost: r.cost, avgPrice: r.avgPrice, occupancy: r.occupancy, revenue: r.revenue, net: r.net,
    marketAvg: r.marketAvg, marketOccupancy: r.marketOccupancy, reliability: r.reliability,
    nights: r.nights.map((x) => ({ date: x.date, price: x.price, sell: x.sell, market: x.market, floor: x.floor, cap: x.cap,
                                   measured: x.measured, weight: x.weight, events: x.events, plan: x.plan })),
  };
});
process.stdout.write(JSON.stringify(out));
