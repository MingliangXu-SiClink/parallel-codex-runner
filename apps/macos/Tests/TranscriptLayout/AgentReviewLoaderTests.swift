import AppKit
import Testing
@testable import PCRRemoteMac

@Suite(.serialized)
@MainActor
struct AgentReviewLoaderTests {
    private func key(agent: Int = 1, workspace: String = "workspace", mode: AgentReviewMode = .changes) -> AgentReviewKey {
        AgentReviewKey(context: ReviewContext(target: .local, workspaceID: workspace, runID: "run", agent: agent), mode: mode)
    }

    private func patch(_ path: String, _ value: String = "new") -> String {
        "diff --git \"a/\(path)\" \"b/\(path)\"\n--- a/\(path)\n+++ b/\(path)\n@@ -1 +1 @@\n-old\n+\(value)\n"
    }

    @Test func selectedFileLoadsAutomaticallyWithoutDownloadingEveryOtherFile() async {
        let cache = AgentReviewCache(), loader = AgentReviewLoader()
        let head = "M  one.swift\nM  two.swift\nM  three.swift\n\n3 files changed\n\n"
        let first = head + patch("one.swift") + "diff --git \"a/two.swift\" \"b/two.swift\"\n"
        let second = "--- a/two.swift\n+++ b/two.swift\n@@ -1 +1 @@\n-old\n+SECOND-END\n" +
            "diff --git \"a/three.swift\" \"b/three.swift\"\n"
        var calls: [Int] = []
        await loader.load(key: key(), language: .english, cache: cache, requestedFile: "one.swift") { cursor, _ in
            calls.append(cursor)
            return AgentReviewPage(text: first, nextCursor: first.utf8.count, hasMore: true)
        }
        #expect(calls == [0])
        #expect(loader.snapshot?.paths == ["one.swift", "two.swift", "three.swift"])
        #expect(loader.snapshot?.completeFiles.map(\.path) == ["one.swift"])
        await loader.load(key: key(), language: .english, cache: cache, requestedFile: "two.swift") { cursor, _ in
            calls.append(cursor)
            return AgentReviewPage(text: second, nextCursor: cursor + second.utf8.count, hasMore: true)
        }
        #expect(calls == [0, first.utf8.count])
        #expect(loader.snapshot?.completeFiles.last?.text.contains("SECOND-END") == true)
        #expect(loader.snapshot?.hasMore == true)
        let reopened = AgentReviewLoader()
        await reopened.load(key: key(), language: .english, cache: cache, requestedFile: "one.swift") { _, _ in
            Issue.record("Reopening a complete cached file must not refetch")
            throw PcrdClientError.invalidResponse
        }
        #expect(reopened.snapshot?.completeFiles.first?.path == "one.swift")
        #expect(reopened.error == nil)
    }

    @Test func fileSplitAcrossManyPagesPublishesOnlyWhenComplete() async {
        let loader = AgentReviewLoader(), cache = AgentReviewCache()
        let first = "M  large.py\n\n1 file changed\n\n" + patch("large.py", "begin")
        var calls = 0
        await loader.load(key: key(), language: .english, cache: cache, requestedFile: "large.py") { cursor, _ in
            calls += 1
            #expect(loader.snapshot == nil)
            let text = calls == 1 ? first : (calls == 4 ? "+FINAL-LINE\n" : "+middle\n")
            return AgentReviewPage(text: text, nextCursor: cursor + text.utf8.count, hasMore: calls < 4)
        }
        #expect(calls == 4)
        #expect(loader.snapshot?.completeFiles.first?.text.contains("FINAL-LINE") == true)
        #expect(!loader.loading && loader.error == nil)
    }

    @Test func splitDirectoryAndPatchHeadersKeepEveryPathAndThePageTail() async {
        let loader = AgentReviewLoader(), cache = AgentReviewCache()
        let firstPatch = patch("folder/one file.swift", "first")
        let secondPatch = patch("folder/\u{4e2d}\u{6587}.py", "second")
        let directory = "M  folder/one file.swift\nM  folder/\u{4e2d}\u{6587}.py\nM  last.txt\n\n3 files changed\n\n"
        let fragments = [String(directory.prefix(25)), String(directory.dropFirst(25)) + "diff --g",
                         String(firstPatch.dropFirst(8)) + String(secondPatch.prefix(75)),
                         String(secondPatch.dropFirst(75)) + patch("last.txt")]
        var calls = 0
        let fetch: @MainActor (Int, String?) async throws -> AgentReviewPage = { cursor, _ in
            let text = fragments[calls]
            calls += 1
            return AgentReviewPage(text: text, nextCursor: cursor + text.utf8.count,
                                   hasMore: calls < fragments.count, version: "snapshot")
        }
        await loader.load(key: key(), language: .english, cache: cache,
                          requestedFile: "folder/one file.swift", fetch: fetch)
        #expect(calls == 3)
        #expect(loader.snapshot?.paths == ["folder/one file.swift", "folder/\u{4e2d}\u{6587}.py", "last.txt"])
        #expect(loader.snapshot?.completeFiles.first?.text == firstPatch.trimmingCharacters(in: .newlines))
        await loader.load(key: key(), language: .english, cache: cache,
                          requestedFile: "folder/\u{4e2d}\u{6587}.py", fetch: fetch)
        #expect(calls == 4)
        #expect(loader.snapshot?.completeFiles.first { $0.path == "folder/\u{4e2d}\u{6587}.py" }?.text ==
                secondPatch.trimmingCharacters(in: .newlines))
    }

    @Test func metadataAndBinaryChangesCompleteWithoutTextHunks() async {
        let loader = AgentReviewLoader(), cache = AgentReviewCache()
        let text = """
        R  renamed.txt
        M  tool.sh
        M  image.png

        3 files changed

        diff --git "a/old.txt" "b/renamed.txt"
        similarity index 100%
        rename from old.txt
        rename to renamed.txt
        diff --git "a/tool.sh" "b/tool.sh"
        old mode 100644
        new mode 100755
        diff --git "a/image.png" "b/image.png"
        Binary files a/image.png and b/image.png differ
        """
        for path in ["renamed.txt", "tool.sh", "image.png"] {
            await loader.load(key: key(), language: .english, cache: cache, requestedFile: path) { _, _ in
                AgentReviewPage(text: text, nextCursor: text.utf8.count, hasMore: false)
            }
            #expect(loader.snapshot?.completeFiles.contains { $0.path == path } == true)
            #expect(loader.error == nil)
        }
    }

    @Test func defaultComparisonFileAlsoWaitsForItsWholePatch() async {
        let loader = AgentReviewLoader(), cache = AgentReviewCache()
        var calls = 0
        await loader.load(key: key(), language: .english, cache: cache) { cursor, _ in
            calls += 1
            let text = calls == 1 ? self.patch("first.txt") : "+last\n"
            return AgentReviewPage(text: text, nextCursor: cursor + text.utf8.count, hasMore: calls == 1)
        }
        #expect(calls == 2)
        #expect(loader.snapshot?.completeFiles.first?.text.hasSuffix("+last\n") == true)
    }

    @Test func longAnswersLoadAllPagesWithoutManualRefresh() async {
        let loader = AgentReviewLoader(), cache = AgentReviewCache()
        var calls = 0
        await loader.load(key: key(mode: .answer), language: .english, cache: cache) { cursor, _ in
            calls += 1
            return AgentReviewPage(text: "part\(calls)", nextCursor: cursor + 5,
                                   hasMore: calls < 3, version: "answer")
        }
        #expect(calls == 3)
        #expect(loader.snapshot?.text == "part1part2part3")
        #expect(loader.snapshot?.hasMore == false)
    }

    @Test func expiredSnapshotRestartsOnceWithoutJoiningDifferentVersions() async {
        let loader = AgentReviewLoader(), cache = AgentReviewCache()
        let first = patch("one.txt", "OLD") + "diff --git a/two.txt b/two.txt\n"
        await loader.load(key: key(), language: .english, cache: cache, requestedFile: "one.txt") { _, _ in
            AgentReviewPage(text: first, nextCursor: first.utf8.count, hasMore: true, version: "old", live: true)
        }
        var calls: [Int] = []
        await loader.load(key: key(), language: .english, cache: cache, requestedFile: "two.txt") { cursor, version in
            calls.append(cursor)
            if version == "old" {
                throw PcrdClientError.remote("pcr_error", "This live file preview has expired. Refresh to load the current changes.")
            }
            let text = self.patch("one.txt", "CURRENT") + self.patch("two.txt", "CURRENT")
            return AgentReviewPage(text: text, nextCursor: text.utf8.count, hasMore: false, version: "new", live: true)
        }
        #expect(calls == [first.utf8.count, 0])
        #expect(loader.error == nil)
        #expect(loader.snapshot?.text.contains("OLD") == false)
        #expect(loader.snapshot?.completeFiles.count == 2)
    }

    @Test func changedAnswerRestartsInsteadOfAskingTheUserToRefresh() async {
        let loader = AgentReviewLoader(), cache = AgentReviewCache()
        var calls: [Int] = []
        await loader.load(key: key(mode: .answer), language: .english, cache: cache) { cursor, _ in
            calls.append(cursor)
            switch calls.count {
            case 1: return AgentReviewPage(text: "old", nextCursor: 3, hasMore: true, version: "old")
            case 2: return AgentReviewPage(text: "end", nextCursor: 6, hasMore: false, version: "new")
            default: return AgentReviewPage(text: "new complete", nextCursor: 12, hasMore: false, version: "new")
            }
        }
        #expect(calls == [0, 3, 0])
        #expect(loader.snapshot?.text == "new complete")
        #expect(loader.error == nil)
    }

    @Test func brokenCursorAndRepeatedExpirationDoNotLoopForever() async {
        for brokenCursor in [true, false] {
            let loader = AgentReviewLoader(), cache = AgentReviewCache()
            var calls = 0
            await loader.load(key: key(), language: .english, cache: cache) { _, _ in
                calls += 1
                if brokenCursor { return AgentReviewPage(text: "bad", nextCursor: 0, hasMore: true) }
                throw PcrdClientError.remote("pcr_error", "This live file preview has expired. Refresh to load the current changes.")
            }
            #expect(calls == (brokenCursor ? 1 : 2))
            #expect(loader.error != nil && !loader.loading)
        }
    }

    @Test func refreshFailureKeepsTheCompleteFileAndNeverRetriesPermissionErrors() async {
        let loader = AgentReviewLoader(), cache = AgentReviewCache()
        let value = patch("one.txt")
        await loader.load(key: key(), language: .english, cache: cache, requestedFile: "one.txt") { _, _ in
            AgentReviewPage(text: value, nextCursor: value.utf8.count, hasMore: false)
        }
        var calls = 0
        await loader.load(key: key(), language: .english, cache: cache, requestedFile: "one.txt", refresh: true) { _, _ in
            calls += 1
            #expect(loader.snapshot?.text == value)
            throw PcrdClientError.remote("permission_denied", "denied")
        }
        #expect(calls == 1)
        #expect(loader.snapshot?.text == value)
        #expect(loader.error?.contains("last loaded") == true)
    }

    @Test func failedPartialReadResumesFromItsOwnCursor() async {
        let loader = AgentReviewLoader(), cache = AgentReviewCache()
        let value = patch("one.txt")
        var calls = 0
        await loader.load(key: key(), language: .english, cache: cache, requestedFile: "one.txt") { _, _ in
            calls += 1
            if calls == 2 { throw PcrdClientError.connection("offline") }
            return AgentReviewPage(text: value, nextCursor: value.utf8.count, hasMore: true, version: "s")
        }
        #expect(loader.snapshot == nil && loader.error != nil)
        await loader.load(key: key(), language: .english, cache: cache, requestedFile: "one.txt") { cursor, version in
            #expect(cursor == value.utf8.count && version == "s")
            return AgentReviewPage(text: "+end\n", nextCursor: cursor + 5, hasMore: false, version: "s")
        }
        #expect(loader.snapshot?.text == value + "+end\n")
        #expect(loader.error == nil)
    }

    @Test func retryKeepsTheErrorAndReadableFileUntilTheReplacementIsReady() async {
        let loader = AgentReviewLoader(), cache = AgentReviewCache()
        let original = patch("one.txt", "original")
        await loader.load(key: key(), language: .english, cache: cache) { _, _ in
            AgentReviewPage(text: original, nextCursor: original.utf8.count, hasMore: false)
        }
        await loader.load(key: key(), language: .english, cache: cache, refresh: true) { _, _ in
            throw PcrdClientError.connection("offline")
        }
        let previousError = loader.error
        var pending: CheckedContinuation<AgentReviewPage, Never>?
        let retry = Task {
            await loader.load(key: key(), language: .english, cache: cache, refresh: true) { _, _ in
                await withCheckedContinuation { pending = $0 }
            }
        }
        while pending == nil { await Task.yield() }
        #expect(previousError != nil && loader.error == previousError)
        #expect(loader.loading && loader.snapshot?.text == original)
        let replacement = patch("one.txt", "replacement")
        pending?.resume(returning: AgentReviewPage(text: replacement,
            nextCursor: replacement.utf8.count, hasMore: false))
        await retry.value
        #expect(loader.error == nil && !loader.loading)
        #expect(loader.snapshot?.text == replacement)
        await loader.load(key: key(agent: 2), language: .english, cache: cache) { _, _ in
            #expect(loader.error == nil && loader.snapshot == nil)
            return AgentReviewPage(text: replacement, nextCursor: replacement.utf8.count, hasMore: false)
        }
    }

    @Test func slowReadKeepsItsImmutableCursorWhenSelectingALaterFile() async throws {
        let loader = AgentReviewLoader(), cache = AgentReviewCache()
        let first = patch("one.txt") + "diff --git a/two.txt b/two.txt\n"
        await loader.load(key: key(), language: .english, cache: cache, requestedFile: "one.txt") { _, _ in
            AgentReviewPage(text: first, nextCursor: first.utf8.count, hasMore: true, version: "stable", live: true)
        }
        var aged = try #require(cache.snapshot(for: key()))
        aged.capturedAt = .distantPast
        let token = cache.begin(key())
        cache.store(aged, for: key(), token: token)
        cache.finish(token, for: key())
        await loader.load(key: key(), language: .english, cache: cache, requestedFile: "two.txt") { cursor, version in
            #expect(cursor == first.utf8.count && version == "stable")
            let suffix = "--- a/two.txt\n+++ b/two.txt\n@@ -1 +1 @@\n-old\n+new\n"
            return AgentReviewPage(text: suffix, nextCursor: cursor + suffix.utf8.count,
                                   hasMore: false, version: "stable", live: true)
        }
        #expect(loader.snapshot?.completeFiles.last?.path == "two.txt")
        #expect(loader.error == nil)
    }

    @Test func lateAgentReplyCannotReplaceAnotherAgentsContentOrCache() async {
        let loader = AgentReviewLoader(), cache = AgentReviewCache()
        var pending: CheckedContinuation<AgentReviewPage, Never>?
        let old = Task {
            await loader.load(key: key(), language: .english, cache: cache) { _, _ in
                await withCheckedContinuation { pending = $0 }
            }
        }
        while pending == nil { await Task.yield() }
        let value = patch("two.txt", "agent two")
        await loader.load(key: key(agent: 2), language: .english, cache: cache) { _, _ in
            AgentReviewPage(text: value, nextCursor: value.utf8.count, hasMore: false)
        }
        pending?.resume(returning: AgentReviewPage(text: patch("one.txt"), nextCursor: 100, hasMore: false))
        await old.value
        #expect(loader.loadedKey == key(agent: 2))
        #expect(loader.snapshot?.text == value)
        #expect(cache.snapshot(for: key()) == nil)
        #expect(cache.snapshot(for: key(agent: 2))?.text == value)
    }

    @Test func missingFileIsNotReplacedWithTheFirstUnrelatedFile() async {
        let loader = AgentReviewLoader(), cache = AgentReviewCache()
        let value = patch("one.txt")
        await loader.load(key: key(), language: .english, cache: cache, requestedFile: "missing.txt") { _, _ in
            AgentReviewPage(text: value, nextCursor: value.utf8.count, hasMore: false)
        }
        #expect(loader.error?.contains("missing.txt") == true)
        #expect(loader.snapshot?.completeFiles.contains { $0.path == "missing.txt" } == false)
    }

    @Test func twoOpenViewsOfTheSameResultFinishWithoutOverwritingTheNewerCache() async {
        let first = AgentReviewLoader(), second = AgentReviewLoader(), cache = AgentReviewCache()
        var pending: CheckedContinuation<AgentReviewPage, Never>?
        let old = Task {
            await first.load(key: key(), language: .english, cache: cache) { _, _ in
                await withCheckedContinuation { pending = $0 }
            }
        }
        while pending == nil { await Task.yield() }
        let newer = patch("one.txt", "newer")
        await second.load(key: key(), language: .english, cache: cache) { _, _ in
            AgentReviewPage(text: newer, nextCursor: newer.utf8.count, hasMore: false, version: "newer")
        }
        let older = patch("one.txt", "older")
        pending?.resume(returning: AgentReviewPage(text: older, nextCursor: older.utf8.count,
                                                 hasMore: false, version: "older"))
        await old.value
        #expect(first.snapshot?.text == older)
        #expect(second.snapshot?.text == newer)
        #expect(cache.snapshot(for: key())?.text == newer)
        #expect(!first.loading && !second.loading)
    }

    @Test func closingDuringRefreshKeepsTheCompleteFileForReopening() async {
        let loader = AgentReviewLoader(), cache = AgentReviewCache()
        let complete = patch("one.txt", "complete old")
        await loader.load(key: key(), language: .english, cache: cache, requestedFile: "one.txt") { _, _ in
            AgentReviewPage(text: complete, nextCursor: complete.utf8.count, hasMore: false, version: "old")
        }
        var pending: CheckedContinuation<AgentReviewPage, Never>?
        let refresh = Task {
            await loader.load(key: key(), language: .english, cache: cache, requestedFile: "one.txt", refresh: true) { cursor, _ in
                if cursor == 0 { return AgentReviewPage(text: "diff --git ", nextCursor: 11, hasMore: true, version: "new") }
                return await withCheckedContinuation { pending = $0 }
            }
        }
        while pending == nil { await Task.yield() }
        #expect(cache.snapshot(for: key())?.text == complete)
        refresh.cancel()
        let reopened = AgentReviewLoader()
        await reopened.load(key: key(), language: .english, cache: cache, requestedFile: "one.txt") { _, _ in
            Issue.record("The readable cached file must survive closing during a refresh")
            throw PcrdClientError.invalidResponse
        }
        #expect(reopened.snapshot?.completeFiles.first?.text == complete)
        pending?.resume(returning: AgentReviewPage(text: "late", nextCursor: 15, hasMore: false, version: "new"))
        await refresh.value
        #expect(cache.snapshot(for: key())?.text == complete)
    }

    @Test func cacheIsBoundedAndScopesReadingPositionToTheExactResult() {
        let cache = AgentReviewCache(maxBytes: 1024, maxEntries: 2)
        let position = ReviewReadingPosition(origin: NSPoint(x: 15, y: 120), selection: NSRange(location: 4, length: 3))
        for agent in 1...3 {
            let key = key(agent: agent)
            let token = cache.begin(key)
            cache.store(AgentReviewSnapshot(text: "cached"), for: key, token: token)
            cache.finish(token, for: key)
            cache.remember(position, for: key, file: "file.txt")
        }
        #expect(cache.snapshot(for: key()) == nil)
        #expect(cache.position(for: key(), file: "file.txt") == nil)
        #expect(cache.position(for: key(agent: 3), file: "file.txt")?.origin == position.origin)
        #expect(cache.position(for: key(agent: 3, workspace: "other"), file: "file.txt") == nil)
        let token = cache.begin(key())
        cache.store(AgentReviewSnapshot(text: String(repeating: "x", count: 2048)), for: key(), token: token)
        #expect(cache.snapshot(for: key()) == nil)
    }
}
