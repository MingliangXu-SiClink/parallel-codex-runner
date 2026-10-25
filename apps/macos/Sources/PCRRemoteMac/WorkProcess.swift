import Foundation

enum WorkOperationPresentation {
    static let rowSpacing: CGFloat = 5

    static func displayPath(_ path: String) -> String {
        path.hasPrefix("/") ? path.split(separator: "/").suffix(3).joined(separator: "/") : path
    }
    static func summary(_ operation: WorkOperationRecord, language: AppLanguage) -> String {
        let running = operation.status == .running
        if operation.command == "sleep", operation.kind == .unknown {
            switch operation.status {
            case .running:
                if let until = AgentActivityPresentation.clock(operation.waitUntil) {
                    return language.text("定时等待 · 计划至 \(until)", "Waiting · planned until \(until)")
                }
                return language.text("定时等待 · 截止时间未知", "Waiting · deadline unknown")
            case .completed: return language.text("等待已结束", "Wait ended")
            case .cancelled: return language.text("等待已取消", "Wait cancelled")
            case .failed: return language.text("等待失败", "Wait failed")
            case .unknown: return language.text("等待结果未知", "Wait outcome unknown")
            }
        }
        let target = operation.targets.count == 1
            ? displayPath(operation.targets[0].path) : language.text("\(operation.targets.count) 个文件", "\(operation.targets.count) files")
        let object: String
        let verb: String
        switch operation.kind {
        case .fileRead:
            verb = running ? language.text("正在读取", "Reading") : language.text("已读取", "Read")
            object = target
        case .fileSearch:
            verb = running ? language.text("正在搜索", "Searching") : language.text("已搜索", "Searched")
            object = "\(target) · \(operation.query ?? "")"
        case .directoryList:
            verb = running ? language.text("正在查看", "Listing") : language.text("已查看", "Listed")
            object = target
        case .fileChange:
            let change = operation.targets.count == 1 ? operation.targets[0].change : .update
            switch change {
            case .create: verb = running ? language.text("正在创建", "Creating") : language.text("已创建", "Created")
            case .delete: verb = running ? language.text("正在删除", "Deleting") : language.text("已删除", "Deleted")
            case .rename: verb = running ? language.text("正在重命名", "Renaming") : language.text("已重命名", "Renamed")
            default: verb = running ? language.text("正在修改", "Editing") : language.text("已修改", "Edited")
            }
            object = target
        case .webSearch:
            verb = running ? language.text("正在搜索网页", "Searching the web") : language.text("已搜索网页", "Searched the web")
            object = operation.query ?? ""
        default:
            verb = running ? language.text("正在运行", "Running") : language.text("已运行", "Ran")
            object = TranscriptFormatter.stripShellWrapper(operation.command ?? "")
        }
        let prefix: String
        switch operation.status {
        case .failed:
            switch operation.kind {
            case .fileRead: prefix = language.text("读取失败", "Read failed")
            case .fileSearch, .webSearch: prefix = language.text("搜索失败", "Search failed")
            case .directoryList: prefix = language.text("查看目录失败", "Listing failed")
            case .fileChange: prefix = language.text("文件修改失败", "File change failed")
            default: prefix = language.text("运行失败", "Command failed")
            }
        case .cancelled: prefix = language.text("已取消", "Cancelled")
        case .unknown: prefix = language.text("状态待确认", "Status unknown")
        default: prefix = verb
        }
        var text = prefix + " " + object.replacingOccurrences(of: "\n", with: " ")
        if let ms = operation.durationMs, ms >= 10_000, !running, operation.kind == .command {
            let duration = String(format: "%.1f", Double(ms) / 1_000)
            text += language.text(" · \(duration) 秒", " · \(duration) s")
        }
        if operation.kind == .fileChange, !operation.targets.isEmpty,
           operation.targets.allSatisfy({ $0.added != nil && $0.deleted != nil }) {
            text += " · +\(operation.targets.reduce(0) { $0 + ($1.added ?? 0) }) -\(operation.targets.reduce(0) { $0 + ($1.deleted ?? 0) })"
        }
        return text
    }

    static func icon(_ kind: WorkOperationKind) -> String {
        switch kind {
        case .fileRead: "book"
        case .fileSearch, .webSearch: "magnifyingglass"
        case .directoryList: "folder"
        case .fileChange: "pencil"
        default: "terminal"
        }
    }

    static func icon(_ operation: WorkOperationRecord) -> String {
        operation.kind == .unknown && operation.command == "sleep" ? "clock" : icon(operation.kind)
    }

    static func reusableOutputPrefix(_ cached: WorkOperationDetailPage?, cachedEvent: Int?,
                                     operation: WorkOperationRecord) -> WorkOperationDetailPage? {
        guard let cached, cached.availability == "available" else { return nil }
        if cachedEvent == operation.eventId { return cached }
        guard let bytes = operation.outputBytes, bytes >= cached.totalBytes,
              cached.nextCursor == cached.text.utf8.count else { return nil }
        return cached
    }

    static func acceptsPrefix(of requested: WorkOperationRecord, current: WorkOperationRecord?) -> Bool {
        guard let current, current.id == requested.id else { return false }
        if current.eventId == requested.eventId { return true }
        guard let oldBytes = requested.outputBytes, let newBytes = current.outputBytes else { return false }
        return current.eventId > requested.eventId && newBytes >= oldBytes
    }

    static func retryableOutputRead(_ error: Error) -> Bool {
        if let error = error as? PcrdClientError {
            switch error {
            case .connection, .invalidResponse: return true
            case .remote(let code, _):
                return ["pcr_error", "server_busy", "request_timeout", "relay_unreachable"].contains(code)
            default: return false
            }
        }
        return error is URLError
    }
}

struct WorkProcessState {
    var revision = 0
    var sections = UserDefaults.standard.dictionary(forKey: "ParallelCodex.WorkSections") as? [String: Bool] ?? [:]
    var expanded = Set(UserDefaults.standard.stringArray(forKey: "ParallelCodex.WorkOperations") ?? [])
    var details: [String: WorkOperationDetailPage] = [:]
    var errors: [String: String] = [:]
    var loading: Set<String> = []
    var loadedEvents: [String: Int] = [:]
    var detailEvents: [String: Int] = [:]
    var errorEvents: [String: Int] = [:]

    mutating func save() {
        if sections.count > 512 { sections = Dictionary(uniqueKeysWithValues: sections.suffix(384)) }
        if expanded.count > 128 { expanded = Set(expanded.suffix(96)) }
        UserDefaults.standard.set(sections, forKey: "ParallelCodex.WorkSections")
        UserDefaults.standard.set(Array(expanded), forKey: "ParallelCodex.WorkOperations")
        revision &+= 1
    }
}

private struct WorkOperationRequest: Encodable, Sendable {
    let runId: String
    let agent: Int
    let eventId: Int
    let cursor: Int
    let maxBytes = 64 * 1024
}

extension AppModel {
    var selectedWorkSectionOverrides: [String: Bool] {
        let prefix = (selectedAgentWorkContextKey ?? "") + "|"
        return Dictionary(uniqueKeysWithValues: workProcess.sections.compactMap { key, value in
            key.hasPrefix(prefix) ? (String(key.dropFirst(prefix.count)), value) : nil
        })
    }

    func refreshExpandedWorkOperations() {
        let context = selectedAgentWorkContextKey
        let prefix = context.map { $0 + "|" } ?? ""
        for key in Array(workOperationTasks.keys) where
            context == nil || !key.hasPrefix(prefix) || !workProcess.expanded.contains(key) {
            cancelWorkOperation(key)
        }
        guard let context else { return }
        let expandedBlocks = selectedTranscriptBlocks.filter {
            $0.operation != nil && workProcess.expanded.contains(context + "|" + $0.id)
        }.suffix(24)
        for block in expandedBlocks {
            let key = context + "|" + block.id
            guard let operation = block.operation, workProcess.expanded.contains(key),
                  workProcess.loadedEvents[key] != operation.eventId,
                  workProcess.errors[key] == nil || workProcess.errorEvents[key] != operation.eventId else { continue }
            loadWorkOperation(block, context: context)
        }
    }

    func activateWorkBlock(_ block: TranscriptBlock) {
        guard let context = selectedAgentWorkContextKey else { return }
        let key = context + "|" + block.id
        if block.activityType?.hasPrefix("work_summary") == true {
            workProcess.sections[key] = block.activityType != "work_summary_expanded"
            workProcess.save()
        } else if block.activityType?.hasPrefix("legacy_command") == true {
            if workProcess.expanded.remove(key) == nil { workProcess.expanded.insert(key) }
            workProcess.save()
        } else if block.operation != nil {
            guard block.operation?.detailsAvailable == true else { return }
            if workProcess.expanded.remove(key) == nil {
                workProcess.expanded.insert(key)
                if workProcess.loadedEvents[key] != block.operation?.eventId {
                    loadWorkOperation(block, context: context)
                }
            } else {
                cancelWorkOperation(key)
            }
            workProcess.save()
        } else if block.activityType == "operation_retry",
                  let original = selectedTranscriptBlocks.first(where: { block.id == $0.id + ":retry" }) {
            loadWorkOperation(original, context: context)
        }
    }

    func projectWorkOperations(_ blocks: [TranscriptBlock]) -> [TranscriptBlock] {
        guard let context = selectedAgentWorkContextKey else { return blocks }
        return blocks.flatMap { block -> [TranscriptBlock] in
            let key = context + "|" + block.id
            let expanded = workProcess.expanded.contains(key)
            guard let operation = block.operation else {
                guard block.kind == .command else { return [block] }
                let running = block.status == "running" || block.status == "in_progress"
                let failed = block.status == "failed" || block.status == "error"
                var row = block
                let prefix = failed ? appLanguage.text("运行失败", "Command failed")
                    : running ? appLanguage.text("正在运行", "Running") : appLanguage.text("已运行", "Ran")
                row.text = prefix + " " + block.text.replacingOccurrences(of: "\n", with: " ")
                row.activityType = expanded ? "legacy_command_expanded" : "legacy_command"
                if !failed { row.status = nil }
                return expanded ? [row, TranscriptBlock(id: block.id + ":details", kind: .activity,
                    text: block.text, activityType: "operation_details")] : [row]
            }
            var row = block
            row.text = WorkOperationPresentation.summary(operation, language: appLanguage)
            row.activityType = expanded ? "work_operation_expanded" : "work_operation"
            row.status = nil
            guard expanded, operation.detailsAvailable else { return [row] }
            var detail = operation.command ?? ""
            if !operation.targets.isEmpty {
                let label = operation.kind == .fileRead || operation.kind == .fileChange
                    ? appLanguage.text("当前文件：", "Current file: ") : ""
                detail += "\n" + operation.targets.map { label + $0.path }.joined(separator: "\n")
            }
            if let page = workProcess.details[key] {
                if !page.text.isEmpty { detail += "\n\n" + page.text }
            }
            if let error = workProcess.errors[key] { detail += "\n\n" + error }
            // Revalidation must not insert/remove a loading paragraph in an
            // already readable operation, shifting the user's reading position.
            if workProcess.loading.contains(key), workProcess.details[key] == nil {
                detail += appLanguage.text("\n\n正在读取输出…", "\n\nLoading output…")
            }
            if let ms = operation.durationMs {
                detail += appLanguage.text("\n耗时：", "\nDuration: ")
                    + String(format: "%.2f s", Double(ms) / 1_000)
            }
            if let exit = operation.exitCode { detail += appLanguage.text("\n退出码：\(exit)", "\nExit code: \(exit)") }
            var result = [row, TranscriptBlock(id: block.id + ":details", kind: .activity,
                text: detail.trimmingCharacters(in: .whitespacesAndNewlines), activityType: "operation_details")]
            if workProcess.errors[key] != nil {
                result.append(TranscriptBlock(id: block.id + ":retry", kind: .activity,
                    text: appLanguage.text("重新加载", "Retry"), activityType: "operation_retry"))
            }
            return result
        }
    }

    func cancelWorkOperation(_ key: String) {
        workOperationTasks.removeValue(forKey: key)?.cancel()
        workOperationRequestIDs.removeValue(forKey: key)
        workOperationRequests.removeValue(forKey: key)
        workProcess.loading.remove(key)
    }

    private func loadWorkOperation(_ block: TranscriptBlock, context: String) {
        guard let operation = block.operation, operation.detailsAvailable,
              let runID = activeRun?.runId, let agent = selectedAgent else { return }
        let key = context + "|" + block.id
        if let requested = workOperationRequests[key] {
            // A new immutable prefix extends the target, not the read's identity.
            if WorkOperationPresentation.acceptsPrefix(of: requested, current: operation) { return }
            cancelWorkOperation(key)
        }
        let requestID = UUID()
        workOperationRequestIDs[key] = requestID
        workOperationRequests[key] = operation
        workProcess.loading.insert(key)
        workProcess.errors.removeValue(forKey: key)
        workProcess.errorEvents.removeValue(forKey: key)
        workProcess.revision &+= 1
        let target = client.currentTarget()
        workOperationTasks[key] = Task { [weak self] in
            guard let self else { return }
            defer {
                if self.workOperationRequestIDs[key] == requestID {
                    self.workOperationTasks.removeValue(forKey: key)
                    self.workOperationRequestIDs.removeValue(forKey: key)
                    self.workOperationRequests.removeValue(forKey: key)
                    self.workProcess.loading.remove(key)
                    self.workProcess.revision &+= 1
                    self.refreshExpandedWorkOperations()
                }
            }
            do {
                let cached = WorkOperationPresentation.reusableOutputPrefix(self.workProcess.details[key],
                    cachedEvent: self.workProcess.detailEvents[key], operation: operation)
                var output = cached?.text ?? ""
                var cursor = cached?.nextCursor ?? 0
                var total = operation.outputBytes ?? cached?.totalBytes
                var publishedBytes = output.utf8.count
                var lastPublication = Date.distantPast
                var failures = 0
                while true {
                    try Task.checkCancellation()
                    let page: WorkOperationDetailPage
                    do {
                        page = try await self.client.call(target: target, "runs.operation",
                            body: WorkOperationRequest(runId: runID, agent: agent, eventId: operation.eventId, cursor: cursor))
                    } catch {
                        guard operation.outputBytes != nil, failures < 3,
                              WorkOperationPresentation.retryableOutputRead(error) else { throw error }
                        failures += 1
                        try await Task.sleep(for: .milliseconds(250 * (1 << (failures - 1))))
                        continue
                    }
                    failures = 0
                    guard !Task.isCancelled, self.workOperationRequestIDs[key] == requestID,
                          self.selectedAgentWorkContextKey == context, self.workProcess.expanded.contains(key),
                          WorkOperationPresentation.acceptsPrefix(of: operation,
                              current: self.selectedTranscriptBlocks.first(where: { $0.id == block.id })?.operation) else { return }
                    guard page.availability != "removed" else {
                        throw NSError(domain: "WorkOperation", code: 1, userInfo: [NSLocalizedDescriptionKey:
                            self.appLanguage.text("操作记录无法读取。", "Unable to read the operation record.")])
                    }
                    guard page.nextCursor == cursor + page.text.utf8.count,
                          page.nextCursor <= page.totalBytes,
                          page.hasMore == (page.nextCursor < page.totalBytes),
                          total == nil || total == page.totalBytes else {
                        throw NSError(domain: "WorkOperation", code: 2, userInfo: [NSLocalizedDescriptionKey:
                            self.appLanguage.text("未能完整读取这条命令的输出，请重试。", "Unable to read the complete command output. Try again.")])
                    }
                    guard !page.hasMore || page.nextCursor > cursor else {
                        throw NSError(domain: "WorkOperation", code: 3, userInfo: [NSLocalizedDescriptionKey:
                            self.appLanguage.text("未能继续读取命令输出，请重试。", "Unable to continue reading command output. Try again.")])
                    }
                    output += page.text
                    cursor = page.nextCursor
                    total = page.totalBytes
                    if !page.hasMore || cursor - publishedBytes >= 128 * 1024 || Date().timeIntervalSince(lastPublication) >= 0.15 {
                        var state = self.workProcess
                        state.details[key] = WorkOperationDetailPage(text: output, nextCursor: cursor,
                            hasMore: page.hasMore, totalBytes: page.totalBytes, availability: page.availability)
                        state.detailEvents[key] = operation.eventId
                        if !page.hasMore { state.loadedEvents[key] = operation.eventId }
                        state.revision &+= 1
                        self.workProcess = state
                        publishedBytes = cursor
                        lastPublication = Date()
                    }
                    if !page.hasMore { break }
                }
                var cachedBytes = self.workProcess.details.values.reduce(0) { $0 + $1.text.utf8.count }
                for old in self.workProcess.details.keys.filter({ $0 != key && !self.workProcess.loading.contains($0) }) {
                    guard self.workProcess.details.count > 24 || cachedBytes > 48 * 1024 * 1024 else { break }
                    cachedBytes -= self.workProcess.details.removeValue(forKey: old)?.text.utf8.count ?? 0
                    self.workProcess.loadedEvents.removeValue(forKey: old)
                    self.workProcess.detailEvents.removeValue(forKey: old)
                }
            } catch {
                guard !Task.isCancelled, self.workOperationRequestIDs[key] == requestID,
                      self.selectedAgentWorkContextKey == context, self.workProcess.expanded.contains(key),
                      WorkOperationPresentation.acceptsPrefix(of: operation,
                          current: self.selectedTranscriptBlocks.first(where: { $0.id == block.id })?.operation) else { return }
                self.workProcess.errors[key] = self.appLanguage.localizedClientMessage(error.localizedDescription)
                self.workProcess.errorEvents[key] = operation.eventId
            }
        }
    }
}
