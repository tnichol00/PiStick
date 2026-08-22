package app.pistick.android;

import android.content.Context;
import android.content.SharedPreferences;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Base64;

import java.nio.charset.StandardCharsets;
import java.security.GeneralSecurityException;
import java.security.KeyStore;

import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

final class CredentialStore {
    private static final String ANDROID_KEY_STORE = "AndroidKeyStore";
    private static final String KEY_ALIAS = "pistick-tmdb-credential-v1";
    private static final String PREFERENCES = "pistick_secure";
    private static final String CIPHERTEXT = "tmdb_ciphertext";
    private static final String IV = "tmdb_iv";

    private final SharedPreferences preferences;

    CredentialStore(Context context) {
        preferences = context.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE);
    }

    synchronized String credential() {
        String encodedCiphertext = preferences.getString(CIPHERTEXT, "");
        String encodedIv = preferences.getString(IV, "");
        if (encodedCiphertext == null || encodedCiphertext.isEmpty()
                || encodedIv == null || encodedIv.isEmpty()) return "";
        try {
            Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
            cipher.init(
                    Cipher.DECRYPT_MODE,
                    secretKey(),
                    new GCMParameterSpec(128, Base64.decode(encodedIv, Base64.NO_WRAP))
            );
            byte[] plaintext = cipher.doFinal(Base64.decode(encodedCiphertext, Base64.NO_WRAP));
            return new String(plaintext, StandardCharsets.UTF_8);
        } catch (GeneralSecurityException | IllegalArgumentException error) {
            return "";
        }
    }

    synchronized boolean isConfigured() {
        return isUsable(credential());
    }

    synchronized void save(String candidate) throws PiStickException {
        String value = candidate == null ? "" : candidate.trim();
        if (!isUsable(value)) {
            throw new PiStickException("Paste your TMDB API Read Access Token or v3 API key.");
        }
        try {
            Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
            cipher.init(Cipher.ENCRYPT_MODE, secretKey());
            byte[] ciphertext = cipher.doFinal(value.getBytes(StandardCharsets.UTF_8));
            boolean saved = preferences.edit()
                    .putString(CIPHERTEXT, Base64.encodeToString(ciphertext, Base64.NO_WRAP))
                    .putString(IV, Base64.encodeToString(cipher.getIV(), Base64.NO_WRAP))
                    .commit();
            if (!saved) throw new PiStickException("PiStick could not save the TMDB credential.");
        } catch (GeneralSecurityException error) {
            throw new PiStickException("PiStick could not secure the TMDB credential.", error);
        }
    }

    static boolean isUsable(String candidate) {
        if (candidate == null) return false;
        String value = candidate.trim();
        if (value.length() < 20 || value.length() > 2048) return false;
        for (int index = 0; index < value.length(); index++) {
            if (Character.isWhitespace(value.charAt(index))) return false;
        }
        return true;
    }

    private SecretKey secretKey() throws GeneralSecurityException {
        KeyStore store = KeyStore.getInstance(ANDROID_KEY_STORE);
        try {
            store.load(null);
        } catch (java.io.IOException error) {
            throw new GeneralSecurityException(error);
        } catch (java.security.cert.CertificateException error) {
            throw new GeneralSecurityException(error);
        }
        java.security.Key existing = store.getKey(KEY_ALIAS, null);
        if (existing instanceof SecretKey) return (SecretKey) existing;

        KeyGenerator generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, ANDROID_KEY_STORE);
        generator.init(new KeyGenParameterSpec.Builder(
                KEY_ALIAS,
                KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT
        )
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setRandomizedEncryptionRequired(true)
                .build());
        return generator.generateKey();
    }
}
