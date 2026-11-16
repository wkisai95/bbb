// Aiuto per i test: usa app/web/cifrato.js (lo stesso file dell'app) da node.
//   node js_cifrato.js vettori <vettori.json>      controlla i vettori di prova, stampa un JSON di esiti
//   node js_cifrato.js cifra <codice> <file testo>  stampa il file cifrato (nonce casuale)
//   node js_cifrato.js apri <codice> <file.json>    stampa il testo in chiaro
//   node js_cifrato.js varie                        codici nuovi, inviti, normalizzazione
const fs = require("fs");
const path = require("path");
const K = require(path.join(__dirname, "..", "app", "web", "cifrato.js"));

function hexToBytes(h) { return Uint8Array.from(h.match(/../g).map((x) => parseInt(x, 16))); }

async function vettori(file) {
  const v = JSON.parse(fs.readFileSync(file, "utf8"));
  const out = { vettori: [], rifiutati: [] };
  for (const x of v.vettori) {
    const r = { nome: x.nome };
    r.id = (await K.idFile(x.codice)) === x.id;
    r.scritto_diversamente = (await Promise.all(x.codice_scritto_diversamente.map(K.idFile))).every((i) => i === x.id);
    r.codice = K.scriviCodice(hexToBytes(x.codice_hex)) === x.codice;
    const busta = await K.cifraTesto(x.testo, x.codice, hexToBytes(x.nonce_hex));
    r.cifra = JSON.stringify(busta) === JSON.stringify(x.file);
    r.apri = (await K.decifraTesto(x.file, x.codice)) === x.testo;
    out.vettori.push(r);
  }
  for (const y of v.da_rifiutare) {
    try { await K.decifraTesto(y.file, y.codice); out.rifiutati.push({ motivo: y.motivo, rifiutato: false }); }
    catch (e) { out.rifiutati.push({ motivo: y.motivo, rifiutato: true, tipo: e.tipo || "" }); }
  }
  return out;
}

async function main() {
  const [cmd, a, b] = process.argv.slice(2);
  if (cmd === "vettori") console.log(JSON.stringify(await vettori(a)));
  else if (cmd === "cifra") console.log(JSON.stringify(await K.cifraTesto(fs.readFileSync(b, "utf8"), a)));
  else if (cmd === "apri") process.stdout.write(await K.decifraTesto(JSON.parse(fs.readFileSync(b, "utf8")), a));
  else if (cmd === "varie") {
    const codici = Array.from({ length: 200 }, K.nuovoCodice);
    const inv = K.invito("wkisai95", "bbb", "main", codici[0]);
    console.log(JSON.stringify({
      formato: codici.every((c) => /^[0-9A-HJKMNP-TV-Z]{4}(-[0-9A-HJKMNP-TV-Z]{4}){5}$/.test(c)),
      diversi: new Set(codici).size === codici.length,
      andata_ritorno: codici.every((c) => K.scriviCodice(K.leggiCodice(c)) === c),
      invito: K.leggiInvito("Ecco il codice: " + inv + ". Tienilo per te."),
      codice0: codici[0],
      sbagliati: ["", "ABC", codici[0] + "X", codici[0].replace(/.$/, "U")].map(K.codiceValido),
    }));
  } else throw new Error("comando sconosciuto");
}
main().catch((e) => { console.error(e.message); process.exit(1); });
