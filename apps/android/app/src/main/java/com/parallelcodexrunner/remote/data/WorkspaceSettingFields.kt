package com.parallelcodexrunner.remote.data

internal object WorkspaceSettingFields {
    const val AGENTS = "agents"
    const val SYNTHESIS_AGENTS = "synthesis_agents"
    const val FOLLOW_UP_DELAY = "follow_up_delay"
    const val EXECUTION = "execution"
    const val AUTOMATIC_MAX_PARALLEL = "automatic_max_parallel"
    const val MAX_PARALLEL = "max_parallel"
    const val SUBAGENTS = "subagents"
    const val SUBAGENTS_LIMIT = "subagents_limit"
    const val RECOMMEND_BY = "recommend_by"
    const val MODEL = "model"
    const val EFFORT = "effort"
    const val FAST_MODE = "fast_mode"
    const val SYNTHESIS_MODEL = "synthesis_model"
    const val SYNTHESIS_EFFORT = "synthesis_effort"
    const val SYNTHESIS_FAST_MODE = "synthesis_fast_mode"
    const val SYNC_BACK = "sync_back"
    const val KEEP_WORKSPACES = "keep_workspaces"
    const val RESUME_SESSION_ID = "resume_session_id"
    const val RESUME_INCLUDE_NON_INTERACTIVE = "resume_include_non_interactive"
    const val RUNS_DIR = "runs_dir"
    const val CODEX_BIN = "codex_bin"

    val all: Set<String> = setOf(
        AGENTS,
        SYNTHESIS_AGENTS,
        FOLLOW_UP_DELAY,
        EXECUTION,
        AUTOMATIC_MAX_PARALLEL,
        MAX_PARALLEL,
        SUBAGENTS,
        SUBAGENTS_LIMIT,
        RECOMMEND_BY,
        MODEL,
        EFFORT,
        FAST_MODE,
        SYNTHESIS_MODEL,
        SYNTHESIS_EFFORT,
        SYNTHESIS_FAST_MODE,
        SYNC_BACK,
        KEEP_WORKSPACES,
        RESUME_SESSION_ID,
        RESUME_INCLUDE_NON_INTERACTIVE,
        RUNS_DIR,
        CODEX_BIN,
    )
    val defaultConversation: Set<String> = all - setOf(
        RESUME_SESSION_ID,
        RESUME_INCLUDE_NON_INTERACTIVE,
        RUNS_DIR,
        CODEX_BIN,
    )

    fun normalized(fields: Set<String>): Set<String> = buildSet {
        addAll(fields.intersect(all))
        if (AUTOMATIC_MAX_PARALLEL in this || MAX_PARALLEL in this) {
            add(AUTOMATIC_MAX_PARALLEL)
            add(MAX_PARALLEL)
        }
    }
}

internal fun RemoteRunSettings.changedFields(
    previous: RemoteRunSettings,
): Set<String> = buildSet {
    if (agents != previous.agents) add(WorkspaceSettingFields.AGENTS)
    if (synthesisAgents != previous.synthesisAgents) {
        add(WorkspaceSettingFields.SYNTHESIS_AGENTS)
    }
    if (followUpDelay != previous.followUpDelay) add(WorkspaceSettingFields.FOLLOW_UP_DELAY)
    if (execution != previous.execution) add(WorkspaceSettingFields.EXECUTION)
    if (automaticMaxParallel != previous.automaticMaxParallel ||
        maxParallel != previous.maxParallel
    ) {
        add(WorkspaceSettingFields.AUTOMATIC_MAX_PARALLEL)
        add(WorkspaceSettingFields.MAX_PARALLEL)
    }
    if (subagents != previous.subagents) add(WorkspaceSettingFields.SUBAGENTS)
    if (subagentsLimit != previous.subagentsLimit) add(WorkspaceSettingFields.SUBAGENTS_LIMIT)
    if (recommendBy != previous.recommendBy) add(WorkspaceSettingFields.RECOMMEND_BY)
    if (model != previous.model) add(WorkspaceSettingFields.MODEL)
    if (effort != previous.effort) add(WorkspaceSettingFields.EFFORT)
    if (fastMode != previous.fastMode) add(WorkspaceSettingFields.FAST_MODE)
    if (synthesisModel != previous.synthesisModel) add(WorkspaceSettingFields.SYNTHESIS_MODEL)
    if (synthesisEffort != previous.synthesisEffort) add(WorkspaceSettingFields.SYNTHESIS_EFFORT)
    if (synthesisFastMode != previous.synthesisFastMode) {
        add(WorkspaceSettingFields.SYNTHESIS_FAST_MODE)
    }
    if (syncBack != previous.syncBack) add(WorkspaceSettingFields.SYNC_BACK)
    if (keepWorkspaces != previous.keepWorkspaces) add(WorkspaceSettingFields.KEEP_WORKSPACES)
    if (resumeSessionId != previous.resumeSessionId) {
        add(WorkspaceSettingFields.RESUME_SESSION_ID)
    }
    if (resumeIncludeNonInteractive != previous.resumeIncludeNonInteractive) {
        add(WorkspaceSettingFields.RESUME_INCLUDE_NON_INTERACTIVE)
    }
    if (runsDir != previous.runsDir) add(WorkspaceSettingFields.RUNS_DIR)
    if (codexBin != previous.codexBin) add(WorkspaceSettingFields.CODEX_BIN)
}

internal fun RemoteRunSettings.mergingRemote(
    remote: RemoteRunSettings,
    preservingFields: Set<String>,
): RemoteRunSettings {
    val fields = WorkspaceSettingFields.normalized(preservingFields)
    return RemoteRunSettings(
        configured = configured || remote.configured,
        agents = if (WorkspaceSettingFields.AGENTS in fields) agents else remote.agents,
        synthesisAgents = if (WorkspaceSettingFields.SYNTHESIS_AGENTS in fields) {
            synthesisAgents
        } else {
            remote.synthesisAgents
        },
        followUpDelay = if (WorkspaceSettingFields.FOLLOW_UP_DELAY in fields) {
            followUpDelay
        } else {
            remote.followUpDelay
        },
        execution = if (WorkspaceSettingFields.EXECUTION in fields) {
            execution
        } else {
            remote.execution
        },
        automaticMaxParallel = if (
            WorkspaceSettingFields.AUTOMATIC_MAX_PARALLEL in fields
        ) {
            automaticMaxParallel
        } else {
            remote.automaticMaxParallel
        },
        maxParallel = if (WorkspaceSettingFields.MAX_PARALLEL in fields) {
            maxParallel
        } else {
            remote.maxParallel
        },
        subagents = if (WorkspaceSettingFields.SUBAGENTS in fields) {
            subagents
        } else {
            remote.subagents
        },
        subagentsLimit = if (WorkspaceSettingFields.SUBAGENTS_LIMIT in fields) {
            subagentsLimit
        } else {
            remote.subagentsLimit
        },
        recommendBy = if (WorkspaceSettingFields.RECOMMEND_BY in fields) {
            recommendBy
        } else {
            remote.recommendBy
        },
        model = if (WorkspaceSettingFields.MODEL in fields) model else remote.model,
        effort = if (WorkspaceSettingFields.EFFORT in fields) effort else remote.effort,
        fastMode = if (WorkspaceSettingFields.FAST_MODE in fields) {
            fastMode
        } else {
            remote.fastMode
        },
        synthesisModel = if (WorkspaceSettingFields.SYNTHESIS_MODEL in fields) {
            synthesisModel
        } else {
            remote.synthesisModel
        },
        synthesisEffort = if (WorkspaceSettingFields.SYNTHESIS_EFFORT in fields) {
            synthesisEffort
        } else {
            remote.synthesisEffort
        },
        synthesisFastMode = if (WorkspaceSettingFields.SYNTHESIS_FAST_MODE in fields) {
            synthesisFastMode
        } else {
            remote.synthesisFastMode
        },
        syncBack = if (WorkspaceSettingFields.SYNC_BACK in fields) {
            syncBack
        } else {
            remote.syncBack
        },
        keepWorkspaces = if (WorkspaceSettingFields.KEEP_WORKSPACES in fields) {
            keepWorkspaces
        } else {
            remote.keepWorkspaces
        },
        resumeSessionId = if (WorkspaceSettingFields.RESUME_SESSION_ID in fields) {
            resumeSessionId
        } else {
            remote.resumeSessionId
        },
        resumeIncludeNonInteractive = if (
            WorkspaceSettingFields.RESUME_INCLUDE_NON_INTERACTIVE in fields
        ) {
            resumeIncludeNonInteractive
        } else {
            remote.resumeIncludeNonInteractive
        },
        runsDir = if (WorkspaceSettingFields.RUNS_DIR in fields) runsDir else remote.runsDir,
        codexBin = if (WorkspaceSettingFields.CODEX_BIN in fields) codexBin else remote.codexBin,
    ).normalized()
}
