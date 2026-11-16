/* Formato bbb-cifrato v1 (docs/cifratura.md) con WebCrypto: lo usa l'app e, in node, i test.
   Codice di 24 caratteri (120 bit, base32 di Crockford) -> HKDF-SHA256 -> chiave AES-256-GCM e nome del file. */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory(require("crypto").webcrypto);
  else root.BBBCifrato = factory(root.crypto);
})(this, function (C) {
  "use strict";
  var ALFABETO = "0123456789ABCDEFGHJKMNPQRSTVWXYZ";
  var FORMATO = "bbb-cifrato", VERSIONE = 1, CARTELLA = "config/cifrati";

  function utf8(s) { return new TextEncoder().encode(s); }
  function b64(bytes) { var s = ""; for (var i = 0; i < bytes.length; i++) s += String.fromCharCode(bytes[i]); return btoa(s); }
  function unb64(s) { var bin = atob(s), out = new Uint8Array(bin.length); for (var i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i); return out; }
  function hex(bytes) { var s = ""; for (var i = 0; i < bytes.length; i++) s += ("0" + bytes[i].toString(16)).slice(-2); return s; }
  function errore(msg, tipo) { var e = new Error(msg); e.tipo = tipo; return e; }

  /* 15 byte -> "XXXX-XXXX-XXXX-XXXX-XXXX-XXXX" */
  function scriviCodice(bytes) {
    if (!bytes || bytes.length !== 15) throw errore("Un codice è fatto di 15 byte", "codice");
    var bits = 0, val = 0, out = "";
    for (var i = 0; i < 15; i++) {
      val = ((val << 8) | bytes[i]) & 0xfff; bits += 8;
      while (bits >= 5) { out += ALFABETO.charAt((val >>> (bits - 5)) & 31); bits -= 5; }
    }
    return out.match(/.{4}/g).join("-");
  }
  /* codice scritto da una persona -> 15 byte: ignora spazi e trattini, O vale 0, I e L valgono 1 */
  function leggiCodice(testo) {
    var t = String(testo || "").replace(/[\s-]/g, "").toUpperCase().replace(/O/g, "0").replace(/[IL]/g, "1");
    if (t.length !== 24) throw errore("Il codice deve avere 24 caratteri", "codice");
    var bits = 0, val = 0, out = new Uint8Array(15), n = 0;
    for (var i = 0; i < 24; i++) {
      var v = ALFABETO.indexOf(t.charAt(i));
      if (v < 0) throw errore("Carattere non valido nel codice: " + t.charAt(i), "codice");
      val = ((val << 5) | v) & 0xfff; bits += 5;
      if (bits >= 8) { out[n++] = (val >>> (bits - 8)) & 255; bits -= 8; }
    }
    return out;
  }
  function nuovoCodice() { var b = new Uint8Array(15); C.getRandomValues(b); return scriviCodice(b); }
  function codiceValido(testo) { try { leggiCodice(testo); return true; } catch (e) { return false; } }
  function normalizza(testo) { return scriviCodice(leggiCodice(testo)); }

  /* HKDF-SHA256 con sale "bbb-cifrato-v1" */
  function deriva(ikm, info, n) {
    return C.subtle.importKey("raw", ikm, "HKDF", false, ["deriveBits"]).then(function (k) {
      return C.subtle.deriveBits({ name: "HKDF", hash: "SHA-256", salt: utf8("bbb-cifrato-v1"), info: utf8(info) }, k, n * 8);
    }).then(function (b) { return new Uint8Array(b); });
  }
  var cache = {};
  function chiavi(codice) {          // -> Promise { id, chiave (CryptoKey AES-GCM) }
    var ikm;
    try { ikm = leggiCodice(codice); } catch (e) { return Promise.reject(e); }
    var k = hex(ikm);
    if (!cache[k]) {
      cache[k] = Promise.all([deriva(ikm, "chiave", 32), deriva(ikm, "file", 8)]).then(function (r) {
        return C.subtle.importKey("raw", r[0], "AES-GCM", false, ["encrypt", "decrypt"]).then(function (key) { return { id: hex(r[1]), chiave: key }; });
      });
    }
    return cache[k];
  }
  function idFile(codice) { return chiavi(codice).then(function (k) { return k.id; }); }
  function percorso(id) { return CARTELLA + "/" + id + ".json"; }
  function aad(id) { return utf8("bbb-cifrato-v1:" + id); }

  /* testo (stringa) -> file da salvare. Il nonce si passa solo nelle prove. */
  function cifraTesto(testo, codice, nonce) {
    return chiavi(codice).then(function (k) {
      var iv = nonce || C.getRandomValues(new Uint8Array(12));
      return C.subtle.encrypt({ name: "AES-GCM", iv: iv, additionalData: aad(k.id), tagLength: 128 }, k.chiave, utf8(testo)).then(function (ct) {
        return { formato: FORMATO, versione: VERSIONE, id: k.id, nonce: b64(iv), dati: b64(new Uint8Array(ct)) };
      });
    });
  }
  function decifraTesto(busta, codice) {
    if (!busta || busta.formato !== FORMATO || busta.versione !== VERSIONE) return Promise.reject(errore("Non è un file bbb-cifrato versione 1", "formato"));
    return chiavi(codice).then(function (k) {
      if (k.id !== busta.id) throw errore("Questo codice non apre questo file", "codice");
      return C.subtle.decrypt({ name: "AES-GCM", iv: unb64(busta.nonce), additionalData: aad(k.id), tagLength: 128 }, k.chiave, unb64(busta.dati))
        .then(function (pt) { return new TextDecoder().decode(pt); }, function () { throw errore("Il file è stato alterato o il codice è sbagliato", "alterato"); });
    });
  }
  function cifra(documento, codice, nonce) { return cifraTesto(JSON.stringify(documento), codice, nonce); }
  function decifra(busta, codice) { return decifraTesto(busta, codice).then(JSON.parse); }

  function invito(utente, repository, ramo, codice) { return "bbb1:" + utente + "/" + repository + "/" + ramo + "/" + codice; }
  function leggiInvito(testo) {
    var m = /bbb1:([A-Za-z0-9_.-]+)\/([A-Za-z0-9_.-]+)\/([A-Za-z0-9_.\/-]+?)\/([0-9A-Za-z]{4}(?:-?[0-9A-Za-z]{4}){5})\b/.exec(String(testo || ""));
    if (!m || !codiceValido(m[4])) return null;
    return { utente: m[1], repository: m[2], ramo: m[3], codice: normalizza(m[4]) };
  }
  function indirizzo(utente, repository, ramo, id) { return "https://raw.githubusercontent.com/" + utente + "/" + repository + "/" + ramo + "/" + percorso(id); }

  return { FORMATO: FORMATO, VERSIONE: VERSIONE, CARTELLA: CARTELLA, nuovoCodice: nuovoCodice, scriviCodice: scriviCodice,
    leggiCodice: leggiCodice, codiceValido: codiceValido, normalizza: normalizza, idFile: idFile, percorso: percorso,
    cifraTesto: cifraTesto, decifraTesto: decifraTesto, cifra: cifra, decifra: decifra,
    invito: invito, leggiInvito: leggiInvito, indirizzo: indirizzo };
});
