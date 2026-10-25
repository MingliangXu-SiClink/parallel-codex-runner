package com.parallelcodexrunner.remote.data

import java.net.URI
import java.net.URLDecoder
import java.nio.charset.StandardCharsets

internal object PairingCode {
    fun normalize(value: String): String {
        var candidate = value.trim().trimStart('\uFEFF').trim()
        for (prefix in listOf("URI:", "URL:")) {
            if (candidate.startsWith(prefix, ignoreCase = true)) {
                candidate = candidate.substring(prefix.length).trim()
                break
            }
        }
        if (candidate.length >= 2) {
            val first = candidate.first()
            val last = candidate.last()
            if ((first == '`' && last == '`') ||
                (first == '"' && last == '"') ||
                (first == '\'' && last == '\'')
            ) {
                candidate = candidate.substring(1, candidate.lastIndex).trim()
            }
        }
        require(candidate.isNotEmpty()) { "Pairing code is empty" }
        return candidate
    }

    fun parse(value: String): PairingLink {
        val uri = runCatching { URI(normalize(value)) }
            .getOrElse { throw IllegalArgumentException("Pairing code is not a valid link", it) }
        require(
            uri.scheme.equals("pcr", ignoreCase = true) &&
                uri.host.equals("pair", ignoreCase = true)
        ) { "This is not a Parallel Codex pairing code" }

        val parameters = parseQuery(uri.rawQuery.orEmpty())
        fun required(name: String): String = parameters[name]
            ?.singleOrNull()
            ?.trim()
            ?.takeIf(String::isNotEmpty)
            ?: throw IllegalArgumentException("Pairing code is missing $name")

        return PairingLink(
            relayUrl = normalizeRelayBaseUrl(required("relay")),
            pairingId = required("pairing"),
            secret = required("secret"),
            approvalToken = required("auth"),
            macDeviceId = required("mac"),
            macPublicKey = required("mac_key"),
        )
    }

    private fun parseQuery(rawQuery: String): Map<String, List<String>> {
        require(rawQuery.isNotEmpty()) { "Pairing code has no connection details" }
        return rawQuery.split('&')
            .filter(String::isNotEmpty)
            .map { field ->
                val separator = field.indexOf('=')
                require(separator > 0) { "Pairing code contains an invalid field" }
                decode(field.substring(0, separator)) to decode(field.substring(separator + 1))
            }
            .groupBy({ it.first }, { it.second })
    }

    // QR readers may return an already-decoded '+' from standard Base64. Preserve
    // it rather than applying form-url-encoded space semantics a second time.
    private fun decode(value: String): String = URLDecoder.decode(
        value.replace("+", "%2B"),
        StandardCharsets.UTF_8.name(),
    )
}

internal fun normalizeRelayBaseUrl(value: String): String {
    val candidate = value.trim().trimEnd('/')
    val uri = runCatching { URI(candidate) }
        .getOrElse { throw IllegalArgumentException("Server address is invalid", it) }
    val scheme = uri.scheme?.lowercase()
    val host = uri.host?.lowercase()
    require(scheme != null && host != null) { "Server address must be an absolute URL" }
    val local = host in setOf("localhost", "127.0.0.1", "::1")
    require(scheme == "https" || (scheme == "http" && local)) {
        "Server address must use HTTPS"
    }
    require(uri.rawUserInfo == null && uri.rawQuery == null && uri.rawFragment == null) {
        "Server address must not include credentials, query, or fragment"
    }

    val authorityHost = if (':' in host && !host.startsWith('[')) "[$host]" else host
    val authority = if (uri.port >= 0) "$authorityHost:${uri.port}" else authorityHost
    val path = uri.rawPath.orEmpty().trimEnd('/')
    return "$scheme://$authority$path"
}
