package com.parallelcodexrunner.remote.ui

import android.content.Context

internal fun workspaceOverviewRefreshInterval(pane: String): Long? = when (pane) {
    PANE_WORKSPACES -> 5_000L
    PANE_RUN -> 10_000L
    else -> null
}

internal fun workspacePaneContextKey(
    macId: String?,
    workspaceId: String?,
): String? {
    val mac = macId?.trim().orEmpty()
    val workspace = workspaceId?.trim().orEmpty()
    if (mac.isEmpty() || workspace.isEmpty()) return null
    return "$mac\u001f$workspace"
}

internal fun shouldRestoreRunPane(
    rememberedContext: String?,
    macId: String?,
    workspaceId: String?,
): Boolean = rememberedContext != null &&
    rememberedContext == workspacePaneContextKey(macId, workspaceId)

internal class WorkspacePaneStore(context: Context) {
    private val preferences = context.getSharedPreferences(
        PREFERENCES,
        Context.MODE_PRIVATE,
    )

    fun rememberedRunContext(): String? = preferences
        .getString(RUN_CONTEXT_KEY, null)
        ?.takeIf(String::isNotBlank)

    fun rememberRun(macId: String?, workspaceId: String?): String? {
        val contextKey = workspacePaneContextKey(macId, workspaceId) ?: return null
        preferences.edit().putString(RUN_CONTEXT_KEY, contextKey).apply()
        return contextKey
    }

    fun clearRun() {
        preferences.edit().remove(RUN_CONTEXT_KEY).apply()
    }

    private companion object {
        const val PREFERENCES = "pcr_workspace_navigation"
        const val RUN_CONTEXT_KEY = "open_run_context"
    }
}
