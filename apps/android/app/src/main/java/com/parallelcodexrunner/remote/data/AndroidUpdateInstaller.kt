package com.parallelcodexrunner.remote.data

import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageInfo
import android.content.pm.PackageInstaller
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.provider.Settings
import android.widget.Toast
import com.parallelcodexrunner.remote.BuildConfig
import com.parallelcodexrunner.remote.ui.AppLanguage
import com.parallelcodexrunner.remote.ui.text
import java.io.File
import java.io.IOException
import java.security.MessageDigest
import java.util.Locale

class InstallPermissionRequired : IllegalStateException(
    "Android needs permission to install updates from Parallel Codex",
)

class AndroidUpdateInstaller(context: Context) {
    private val appContext = context.applicationContext

    fun canInstallPackages(): Boolean =
        appContext.packageManager.canRequestPackageInstalls()

    fun ensureInstallPermission() {
        if (!canInstallPackages()) {
            appContext.startActivity(
                Intent(
                    Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                    Uri.parse("package:${appContext.packageName}"),
                ).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            )
            throw InstallPermissionRequired()
        }
    }

    fun verifyAndInstall(apk: File, expected: AndroidUpdateInfo) {
        try {
            verify(apk, expected)
            install(apk, expected)
        } finally {
            apk.delete()
        }
    }

    internal fun verify(apk: File, expected: AndroidUpdateInfo) {
        require(apk.isFile) { "Downloaded Android update is missing" }
        require(apk.length() == expected.sizeBytes) {
            "Downloaded Android update size does not match"
        }
        require(sha256(apk).equals(expected.sha256, ignoreCase = true)) {
            "Downloaded Android update checksum does not match"
        }

        val manager = appContext.packageManager
        val archive = manager.getPackageArchiveInfo(
            apk.absolutePath,
            PackageManager.GET_SIGNING_CERTIFICATES,
        ) ?: throw IOException("Downloaded file is not a valid Android package")
        require(archive.packageName == BuildConfig.APPLICATION_ID &&
            archive.packageName == expected.packageName
        ) { "Downloaded Android update belongs to a different app" }
        require(archive.longVersionCode == expected.versionCode) {
            "Downloaded Android update version does not match"
        }
        require(archive.longVersionCode > BuildConfig.VERSION_CODE.toLong()) {
            "Downloaded Android update is not newer than this app"
        }

        val installed = manager.getPackageInfo(
            BuildConfig.APPLICATION_ID,
            PackageManager.GET_SIGNING_CERTIFICATES,
        )
        require(signaturesAreCompatible(installed, archive)) {
            "Downloaded Android update is signed by a different key"
        }
        expected.signingCertSha256
            ?.let(::normalizeFingerprint)
            ?.takeIf(String::isNotEmpty)
            ?.let { expectedFingerprint ->
                require(expectedFingerprint in signerInfo(archive).current) {
                    "Downloaded Android update certificate does not match the release"
                }
            }
    }

    private fun install(apk: File, expected: AndroidUpdateInfo) {
        ensureInstallPermission()
        val installer = appContext.packageManager.packageInstaller
        val params = PackageInstaller.SessionParams(
            PackageInstaller.SessionParams.MODE_FULL_INSTALL,
        ).apply {
            setAppPackageName(expected.packageName)
            setSize(apk.length())
            setInstallReason(PackageManager.INSTALL_REASON_USER)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                setRequireUserAction(
                    PackageInstaller.SessionParams.USER_ACTION_REQUIRED
                )
            }
        }
        val sessionId = installer.createSession(params)
        var committed = false
        try {
            installer.openSession(sessionId).use { session ->
                apk.inputStream().use { input ->
                    session.openWrite("base.apk", 0, apk.length()).use { output ->
                        input.copyTo(output, 64 * 1024)
                        session.fsync(output)
                    }
                }
                val callback = Intent(appContext, UpdateInstallReceiver::class.java)
                    .setPackage(appContext.packageName)
                val pending = PendingIntent.getBroadcast(
                    appContext,
                    sessionId,
                    callback,
                    PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_MUTABLE,
                )
                session.commit(pending.intentSender)
                committed = true
            }
        } finally {
            if (!committed) installer.abandonSession(sessionId)
        }
    }

    private fun signaturesAreCompatible(
        installed: PackageInfo,
        archive: PackageInfo,
    ): Boolean {
        val current = signerInfo(installed)
        val candidate = signerInfo(archive)
        return if (current.multiple || candidate.multiple) {
            current.current.isNotEmpty() && current.current == candidate.current
        } else {
            current.current.size == 1 && current.current.single() in candidate.history
        }
    }

    private fun signerInfo(info: PackageInfo): SignerInfo {
        val signing = info.signingInfo ?: return SignerInfo(emptySet(), emptySet(), false)
        val current = signing.apkContentsSigners
            .map(::certificateFingerprint)
            .toSet()
        val history = if (signing.hasMultipleSigners()) {
            current
        } else {
            signing.signingCertificateHistory
                .orEmpty()
                .map(::certificateFingerprint)
                .toSet()
        }
        return SignerInfo(current, history, signing.hasMultipleSigners())
    }

    private fun certificateFingerprint(signature: android.content.pm.Signature): String =
        MessageDigest.getInstance("SHA-256")
            .digest(signature.toByteArray())
            .toHex()

    private fun sha256(file: File): String {
        val digest = MessageDigest.getInstance("SHA-256")
        file.inputStream().use { input ->
            val buffer = ByteArray(64 * 1024)
            while (true) {
                val count = input.read(buffer)
                if (count < 0) break
                digest.update(buffer, 0, count)
            }
        }
        return digest.digest().toHex()
    }

    private fun normalizeFingerprint(value: String): String {
        val normalized = value.trim().lowercase(Locale.ROOT).replace(":", "")
        require(normalized.matches(Regex("[0-9a-f]{64}"))) {
            "Android update certificate fingerprint is invalid"
        }
        return normalized
    }

    private data class SignerInfo(
        val current: Set<String>,
        val history: Set<String>,
        val multiple: Boolean,
    )
}

class UpdateInstallReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val language = AppLanguage.load(context)
        val status = intent.getIntExtra(
            PackageInstaller.EXTRA_STATUS,
            PackageInstaller.STATUS_FAILURE,
        )
        if (status == PackageInstaller.STATUS_PENDING_USER_ACTION) {
            val confirmation = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                intent.getParcelableExtra(Intent.EXTRA_INTENT, Intent::class.java)
            } else {
                @Suppress("DEPRECATION")
                intent.getParcelableExtra(Intent.EXTRA_INTENT)
            }
            if (confirmation != null) {
                confirmation.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                context.startActivity(confirmation)
            } else {
                Toast.makeText(
                    context,
                    language.text(
                        "无法打开 Android 安装确认界面",
                        "Android could not open the installation confirmation screen",
                    ),
                    Toast.LENGTH_LONG,
                ).show()
            }
            return
        }

        if (status != PackageInstaller.STATUS_SUCCESS) {
            val message = intent.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE)
                ?: language.text(
                    "Android 未能安装更新",
                    "Android could not install the update",
                )
            Toast.makeText(context, message, Toast.LENGTH_LONG).show()
        }
    }
}

private fun ByteArray.toHex(): String = joinToString("") {
    "%02x".format(Locale.ROOT, it.toInt() and 0xff)
}
