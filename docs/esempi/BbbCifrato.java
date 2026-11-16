import java.nio.charset.StandardCharsets;
import java.security.GeneralSecurityException;
import java.security.SecureRandom;
import java.util.Base64;
import javax.crypto.Cipher;
import javax.crypto.Mac;
import javax.crypto.spec.GCMParameterSpec;
import javax.crypto.spec.SecretKeySpec;

/**
 * Formato bbb-cifrato v1 (docs/cifratura.md) in Java, solo con la libreria standard.
 * Funziona così com'è anche su Android 8 o successivo: copia la classe nell'app (o traducila in Kotlin)
 * e leggi i campi del file con org.json.JSONObject.
 *
 * Prova da riga di comando:
 *   javac BbbCifrato.java
 *   java BbbCifrato id   7KQ2-M9XA-4F3C-PQ8D-ZX5N-W2RT
 *   java BbbCifrato apri 7KQ2-M9XA-4F3C-PQ8D-ZX5N-W2RT ID NONCE DATI   (i tre campi del file)
 */
public final class BbbCifrato {
    public static final String ALFABETO = "0123456789ABCDEFGHJKMNPQRSTVWXYZ";   // base32 di Crockford
    private static final byte[] SALE = "bbb-cifrato-v1".getBytes(StandardCharsets.UTF_8);

    private BbbCifrato() {}

    /** Codice scritto da una persona -> 15 byte (ignora spazi e trattini; O vale 0, I e L valgono 1). */
    public static byte[] leggiCodice(String testo) {
        String t = testo.replaceAll("[\\s-]", "").toUpperCase().replace('O', '0').replace('I', '1').replace('L', '1');
        if (t.length() != 24) throw new IllegalArgumentException("il codice deve avere 24 caratteri");
        byte[] out = new byte[15];
        int bits = 0, val = 0, n = 0;
        for (char c : t.toCharArray()) {
            int v = ALFABETO.indexOf(c);
            if (v < 0) throw new IllegalArgumentException("carattere non valido nel codice: " + c);
            val = ((val << 5) | v) & 0xfff;
            bits += 5;
            if (bits >= 8) { out[n++] = (byte) (val >>> (bits - 8)); bits -= 8; }
        }
        return out;
    }

    /** 15 byte -> codice di 24 caratteri in 6 gruppi da 4. */
    public static String scriviCodice(byte[] dati) {
        if (dati.length != 15) throw new IllegalArgumentException("un codice è fatto di 15 byte");
        StringBuilder sb = new StringBuilder();
        int bits = 0, val = 0;
        for (byte b : dati) {
            val = ((val << 8) | (b & 0xff)) & 0xfff;
            bits += 8;
            while (bits >= 5) {
                if (sb.length() > 0 && sb.length() % 5 == 4) sb.append('-');
                sb.append(ALFABETO.charAt((val >>> (bits - 5)) & 31));
                bits -= 5;
            }
        }
        return sb.toString();
    }

    public static String nuovoCodice() {
        byte[] b = new byte[15];
        new SecureRandom().nextBytes(b);
        return scriviCodice(b);
    }

    /** HKDF-SHA256 (RFC 5869) con sale "bbb-cifrato-v1", per al massimo 32 byte. */
    static byte[] hkdf(byte[] ikm, String info, int n) throws GeneralSecurityException {
        Mac mac = Mac.getInstance("HmacSHA256");
        mac.init(new SecretKeySpec(SALE, "HmacSHA256"));
        byte[] prk = mac.doFinal(ikm);
        mac.init(new SecretKeySpec(prk, "HmacSHA256"));
        mac.update(info.getBytes(StandardCharsets.UTF_8));
        mac.update((byte) 1);
        byte[] okm = mac.doFinal();
        byte[] out = new byte[n];
        System.arraycopy(okm, 0, out, 0, n);
        return out;
    }

    /** Nome del file (senza .json) che si apre con questo codice: sta in config/cifrati/. */
    public static String idFile(String codice) throws GeneralSecurityException {
        StringBuilder sb = new StringBuilder();
        for (byte b : hkdf(leggiCodice(codice), "file", 8)) sb.append(String.format("%02x", b & 0xff));
        return sb.toString();
    }

    private static Cipher aes(int modo, String codice, String id, byte[] nonce) throws GeneralSecurityException {
        Cipher c = Cipher.getInstance("AES/GCM/NoPadding");
        c.init(modo, new SecretKeySpec(hkdf(leggiCodice(codice), "chiave", 32), "AES"), new GCMParameterSpec(128, nonce));
        c.updateAAD(("bbb-cifrato-v1:" + id).getBytes(StandardCharsets.UTF_8));
        return c;
    }

    /** Apre il file: id, nonce e dati sono i campi del JSON salvato. Restituisce il documento JSON in chiaro. */
    public static String decifra(String codice, String id, String nonce, String dati) throws GeneralSecurityException {
        if (!idFile(codice).equals(id)) throw new GeneralSecurityException("questo codice non apre questo file");
        byte[] testo = aes(Cipher.DECRYPT_MODE, codice, id, Base64.getDecoder().decode(nonce)).doFinal(Base64.getDecoder().decode(dati));
        return new String(testo, StandardCharsets.UTF_8);
    }

    /** Cifra un documento JSON e restituisce il file da salvare. Il nonce va passato null (casuale): serve solo nelle prove. */
    public static String cifra(String codice, String json, byte[] nonce) throws GeneralSecurityException {
        if (nonce == null) { nonce = new byte[12]; new SecureRandom().nextBytes(nonce); }
        String id = idFile(codice);
        byte[] dati = aes(Cipher.ENCRYPT_MODE, codice, id, nonce).doFinal(json.getBytes(StandardCharsets.UTF_8));
        return "{\"formato\":\"bbb-cifrato\",\"versione\":1,\"id\":\"" + id + "\",\"nonce\":\""
            + Base64.getEncoder().encodeToString(nonce) + "\",\"dati\":\"" + Base64.getEncoder().encodeToString(dati) + "\"}";
    }

    private static byte[] daHex(String s) {
        byte[] out = new byte[s.length() / 2];
        for (int i = 0; i < out.length; i++) out[i] = (byte) Integer.parseInt(s.substring(2 * i, 2 * i + 2), 16);
        return out;
    }

    public static void main(String[] a) throws Exception {
        switch (a[0]) {
            case "id": System.out.println(idFile(a[1])); break;
            case "codice": System.out.println(scriviCodice(daHex(a[1]))); break;
            case "apri": System.out.println(decifra(a[1], a[2], a[3], a[4])); break;
            case "chiudi": System.out.println(cifra(a[1], a[3], daHex(a[2]))); break;
            default: throw new IllegalArgumentException("comandi: id, codice, apri, chiudi");
        }
    }
}
