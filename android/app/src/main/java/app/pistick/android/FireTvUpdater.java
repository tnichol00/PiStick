package app.pistick.android;

import android.annotation.SuppressLint;
import android.app.DownloadManager;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.pm.PackageInfo;
import android.content.pm.PackageManager;
import android.content.pm.Signature;
import android.database.Cursor;
import android.net.Uri;
import android.os.Build;
import android.os.Environment;
import android.provider.Settings;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.Arrays;
import java.util.HashSet;
import java.util.Locale;
import java.util.Set;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicBoolean;

final class FireTvUpdater implements AutoCloseable {
    private static final String RELEASES_URL =
            "https://api.github.com/repos/tnichol00/PiStick/releases?per_page=30";
    private static final String APK_MIME = "application/vnd.android.package-archive";
    private static final long MAXIMUM_RELEASE_JSON_BYTES = 1024L * 1024L;

    private final MainActivity activity;
    private final DownloadManager downloads;
    private final ExecutorService executor = Executors.newSingleThreadExecutor(runnable -> {
        Thread thread = new Thread(runnable, "PiStick-Updater");
        thread.setPriority(Thread.NORM_PRIORITY - 1);
        return thread;
    });
    private final AtomicBoolean busy = new AtomicBoolean(false);
    private final BroadcastReceiver receiver = new BroadcastReceiver() {
        @Override
        public void onReceive(Context context, Intent intent) {
            if (!DownloadManager.ACTION_DOWNLOAD_COMPLETE.equals(intent.getAction())) return;
            long completed = intent.getLongExtra(DownloadManager.EXTRA_DOWNLOAD_ID, -1L);
            if (completed == pendingDownloadId) verifyCompletedDownload();
        }
    };

    private boolean receiverRegistered;
    private long pendingDownloadId = -1L;
    private FireTvRelease pendingRelease;
    private File pendingFile;
    private boolean waitingForInstallPermission;

    @SuppressLint("UnspecifiedRegisterReceiverFlag")
    FireTvUpdater(MainActivity activity) {
        this.activity = activity;
        this.downloads = (DownloadManager) activity.getSystemService(Context.DOWNLOAD_SERVICE);
        File updateDirectory = activity.getExternalFilesDir(Environment.DIRECTORY_DOWNLOADS);
        if (updateDirectory != null) cleanupOldUpdates(updateDirectory, "");
        IntentFilter filter = new IntentFilter(DownloadManager.ACTION_DOWNLOAD_COMPLETE);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            activity.registerReceiver(receiver, filter, Context.RECEIVER_NOT_EXPORTED);
        } else {
            activity.registerReceiver(receiver, filter);
        }
        receiverRegistered = true;
    }

    void checkForUpdates() {
        if (!busy.compareAndSet(false, true)) {
            activity.sendUpdateStatus("checking", "An update check is already running.", 0);
            return;
        }
        activity.sendUpdateStatus("checking", "Checking GitHub for a Fire TV update…", 0);
        executor.execute(() -> {
            try {
                String response = fetchReleaseJson();
                FireTvRelease release = FireTvRelease.findLatest(response, BuildConfig.VERSION_CODE);
                if (release == null) {
                    busy.set(false);
                    activity.sendUpdateStatus(
                            "current",
                            "PiStick " + BuildConfig.VERSION_NAME + " is already up to date.",
                            BuildConfig.VERSION_CODE
                    );
                    return;
                }
                activity.runOnUiThread(() -> download(release));
            } catch (Throwable error) {
                fail("PiStick could not check for updates. " + safeMessage(error));
            }
        });
    }

    void onResume() {
        if (!waitingForInstallPermission || pendingFile == null || pendingRelease == null) return;
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O
                || activity.getPackageManager().canRequestPackageInstalls()) {
            waitingForInstallPermission = false;
            openInstaller();
        } else {
            waitingForInstallPermission = false;
            fail("PiStick still needs permission to install unknown apps before it can update.");
        }
    }

    private String fetchReleaseJson() throws Exception {
        HttpURLConnection connection = (HttpURLConnection) new URL(RELEASES_URL).openConnection();
        connection.setConnectTimeout(12_000);
        connection.setReadTimeout(18_000);
        connection.setInstanceFollowRedirects(false);
        connection.setUseCaches(false);
        connection.setRequestProperty("Accept", "application/vnd.github+json");
        connection.setRequestProperty("X-GitHub-Api-Version", "2022-11-28");
        connection.setRequestProperty("User-Agent", "PiStick-FireTV/" + BuildConfig.VERSION_NAME);
        try {
            if (connection.getResponseCode() != HttpURLConnection.HTTP_OK) {
                throw new IOException("GitHub returned HTTP " + connection.getResponseCode() + ".");
            }
            return readLimited(connection.getInputStream(), MAXIMUM_RELEASE_JSON_BYTES);
        } finally {
            connection.disconnect();
        }
    }

    private void download(FireTvRelease release) {
        if (activity.isFinishing() || activity.isDestroyed()) return;
        if (downloads == null) {
            fail("Fire OS download service is unavailable.");
            return;
        }
        File directory = activity.getExternalFilesDir(Environment.DIRECTORY_DOWNLOADS);
        if (directory == null) {
            fail("Fire OS did not provide an update storage folder.");
            return;
        }
        if (!directory.isDirectory() && !directory.mkdirs()) {
            fail("PiStick could not create its update storage folder.");
            return;
        }
        cleanupOldUpdates(directory, release.assetName);
        File destination = new File(directory, release.assetName);
        if (destination.exists() && !destination.delete()) {
            fail("PiStick could not replace an old update download.");
            return;
        }

        try {
            DownloadManager.Request request = new DownloadManager.Request(Uri.parse(release.downloadUrl));
            request.setTitle("PiStick Fire TV update");
            request.setDescription("Downloading " + release.versionName);
            request.setMimeType(APK_MIME);
            request.setAllowedOverMetered(true);
            request.setAllowedOverRoaming(false);
            request.setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED);
            request.setDestinationInExternalFilesDir(
                    activity,
                    Environment.DIRECTORY_DOWNLOADS,
                    release.assetName
            );
            pendingRelease = release;
            pendingFile = destination;
            pendingDownloadId = downloads.enqueue(request);
            activity.sendUpdateStatus(
                    "downloading",
                    "Downloading " + release.versionName + "…",
                    release.versionCode
            );
        } catch (Throwable error) {
            fail("PiStick could not start the update download. " + safeMessage(error));
        }
    }

    private void verifyCompletedDownload() {
        FireTvRelease release = pendingRelease;
        File file = pendingFile;
        if (release == null || file == null) return;
        DownloadManager.Query query = new DownloadManager.Query().setFilterById(pendingDownloadId);
        try (Cursor cursor = downloads.query(query)) {
            if (cursor == null || !cursor.moveToFirst()) {
                fail("Fire OS lost the update download.");
                return;
            }
            int status = cursor.getInt(cursor.getColumnIndexOrThrow(DownloadManager.COLUMN_STATUS));
            if (status != DownloadManager.STATUS_SUCCESSFUL) {
                fail("The Fire TV update download failed.");
                return;
            }
        }
        activity.sendUpdateStatus("verifying", "Verifying the downloaded update…", release.versionCode);
        executor.execute(() -> {
            try {
                verifyFile(release, file);
                activity.runOnUiThread(this::requestInstallPermissionOrOpenInstaller);
            } catch (Throwable error) {
                fail("The downloaded update was rejected. " + safeMessage(error));
            }
        });
    }

    private void verifyFile(FireTvRelease release, File file) throws Exception {
        if (!file.isFile() || file.length() != release.size) {
            throw new IOException("Its file size does not match GitHub.");
        }
        if (!release.sha256.equals(sha256(file))) {
            throw new IOException("Its SHA-256 digest does not match GitHub.");
        }
        PackageManager manager = activity.getPackageManager();
        int flags = Build.VERSION.SDK_INT >= Build.VERSION_CODES.P
                ? PackageManager.GET_SIGNING_CERTIFICATES : PackageManager.GET_SIGNATURES;
        PackageInfo archive = manager.getPackageArchiveInfo(file.getAbsolutePath(), flags);
        if (archive == null || !activity.getPackageName().equals(archive.packageName)) {
            throw new IOException("It is not a PiStick Fire TV APK.");
        }
        long archiveVersion = Build.VERSION.SDK_INT >= Build.VERSION_CODES.P
                ? archive.getLongVersionCode() : archive.versionCode;
        if (archiveVersion != release.versionCode || archiveVersion <= BuildConfig.VERSION_CODE) {
            throw new IOException("Its Android version does not match the release.");
        }
        PackageInfo installed = manager.getPackageInfo(activity.getPackageName(), flags);
        Set<String> installedSigners = signerDigests(installed);
        Set<String> archiveSigners = signerDigests(archive);
        if (installedSigners.isEmpty() || archiveSigners.isEmpty()
                || !installedSigners.equals(archiveSigners)) {
            throw new IOException("Its signing key does not match this installation.");
        }
    }

    private void requestInstallPermissionOrOpenInstaller() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O
                && !activity.getPackageManager().canRequestPackageInstalls()) {
            waitingForInstallPermission = true;
            busy.set(false);
            activity.sendUpdateStatus(
                    "permission",
                    "Allow PiStick to install unknown apps, then return to PiStick.",
                    pendingRelease == null ? 0 : pendingRelease.versionCode
            );
            try {
                activity.startActivity(new Intent(
                        Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                        Uri.parse("package:" + activity.getPackageName())
                ));
            } catch (Throwable error) {
                waitingForInstallPermission = false;
                fail("Open Fire TV Settings and allow PiStick to install unknown apps.");
            }
            return;
        }
        openInstaller();
    }

    private void openInstaller() {
        if (pendingDownloadId < 0 || downloads == null || pendingRelease == null) return;
        waitingForInstallPermission = false;
        Uri contentUri = downloads.getUriForDownloadedFile(pendingDownloadId);
        if (contentUri == null) {
            fail("Fire OS could not open the downloaded APK.");
            return;
        }
        try {
            Intent install = new Intent(Intent.ACTION_VIEW);
            install.setDataAndType(contentUri, APK_MIME);
            install.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_GRANT_READ_URI_PERMISSION);
            install.putExtra(Intent.EXTRA_RETURN_RESULT, false);
            busy.set(false);
            activity.sendUpdateStatus(
                    "installing",
                    "Update ready. Confirm Install on the Fire TV screen.",
                    pendingRelease.versionCode
            );
            activity.startActivity(install);
        } catch (Throwable error) {
            fail("Fire OS could not open its package installer. " + safeMessage(error));
        }
    }

    private void fail(String message) {
        waitingForInstallPermission = false;
        busy.set(false);
        activity.sendUpdateStatus("error", message, 0);
    }

    private static void cleanupOldUpdates(File directory, String keepName) {
        File[] files = directory.listFiles((parent, name) ->
                name.startsWith("PiStick-Fire-TV-v") && name.endsWith(".apk") && !name.equals(keepName));
        if (files == null) return;
        for (File file : files) {
            //noinspection ResultOfMethodCallIgnored
            file.delete();
        }
    }

    private static String sha256(File file) throws Exception {
        MessageDigest digest = MessageDigest.getInstance("SHA-256");
        try (InputStream input = new FileInputStream(file)) {
            byte[] buffer = new byte[32 * 1024];
            int count;
            while ((count = input.read(buffer)) != -1) digest.update(buffer, 0, count);
        }
        StringBuilder result = new StringBuilder(64);
        for (byte value : digest.digest()) result.append(String.format(Locale.ROOT, "%02x", value & 0xff));
        return result.toString();
    }

    private static Set<String> signerDigests(PackageInfo info) throws Exception {
        Signature[] signatures;
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            if (info.signingInfo == null) return new HashSet<>();
            signatures = info.signingInfo.getApkContentsSigners();
        } else {
            signatures = info.signatures;
        }
        Set<String> result = new HashSet<>();
        if (signatures == null) return result;
        MessageDigest digest = MessageDigest.getInstance("SHA-256");
        for (Signature signature : signatures) {
            result.add(Arrays.toString(digest.digest(signature.toByteArray())));
            digest.reset();
        }
        return result;
    }

    private static String readLimited(InputStream input, long maximumBytes) throws IOException {
        try (InputStream source = input; ByteArrayOutputStream output = new ByteArrayOutputStream()) {
            byte[] buffer = new byte[16 * 1024];
            int count;
            long total = 0;
            while ((count = source.read(buffer)) != -1) {
                total += count;
                if (total > maximumBytes) throw new IOException("GitHub response was unexpectedly large.");
                output.write(buffer, 0, count);
            }
            return output.toString(StandardCharsets.UTF_8.name());
        }
    }

    private static String safeMessage(Throwable error) {
        String message = error.getMessage();
        if (message == null || message.trim().isEmpty()) return "Try again later.";
        return message.replaceAll("[\\r\\n]+", " ").trim();
    }

    @Override
    public void close() {
        executor.shutdownNow();
        if (receiverRegistered) {
            receiverRegistered = false;
            try {
                activity.unregisterReceiver(receiver);
            } catch (IllegalArgumentException ignored) {
                // The Activity already removed the receiver.
            }
        }
    }
}
