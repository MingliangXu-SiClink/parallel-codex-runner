import CryptoKit
import Foundation
import Testing
@testable import PCRRemoteMac

@Suite(.serialized)
@MainActor
struct FileComparisonLoaderTests {
    private func key(_ path: String = "figure.png") -> VisualComparisonKey {
        VisualComparisonKey(context: ReviewContext(target: .local, workspaceID: "qa", runID: "run", agent: 1), path: path)
    }
    private func digest(_ data: Data) -> String { SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined() }

    @Test func bothVersionsLoadAutomaticallyAndStayStableOnUnchangedRefresh() async throws {
        let data = Data("unique-\(UUID())".utf8), id = digest(data)
        let version = VisualFileVersionInfo(state: "available", fileId: id, sha256: id, sizeBytes: Int64(data.count))
        let cache = FileComparisonCache(), loader = FileComparisonLoader()
        var calls = 0
        let fetch: @MainActor (VisualFileRequest) async throws -> VisualFileResponse = { request in
            calls += 1
            if request.side == nil {
                return VisualFileResponse(runId: "run", agent: 1, path: "figure.png", contentType: "image/png",
                                          live: true, before: version, after: version)
            }
            let offset = Int(request.offset ?? 0), end = min(offset + 5, data.count)
            let chunk = data.subdata(in: offset..<end)
            return VisualFileResponse(runId: "run", agent: 1, path: "figure.png", contentType: "image/png",
                side: request.side, fileId: id, sizeBytes: Int64(data.count), offset: Int64(offset), nextOffset: Int64(end),
                hasMore: end < data.count, contentBase64: chunk.base64EncodedString(), chunkSha256: self.digest(chunk))
        }
        await loader.load(key: key(), cache: cache, language: .english, fetch: fetch)
        let before = try #require(loader.snapshot?.before.url)
        #expect(try Data(contentsOf: before) == data)
        #expect(loader.snapshot?.before.url == loader.snapshot?.after.url)
        let firstCalls = calls
        await loader.load(key: key(), cache: cache, language: .english, refresh: true, fetch: fetch)
        #expect(calls == firstCalls + 1)
        #expect(loader.snapshot?.before.url == before)
        #expect(loader.error == nil)
    }

    @Test func incorrectHashCannotPublishAnImageUnderAValidVersionName() async {
        let good = Data("good-\(UUID())".utf8), bad = Data(repeating: 1, count: good.count), id = digest(good)
        let version = VisualFileVersionInfo(state: "available", fileId: id, sha256: id, sizeBytes: Int64(good.count))
        let loader = FileComparisonLoader()
        await loader.load(key: key(), cache: FileComparisonCache(), language: .english) { request in
            if request.side == nil {
                return VisualFileResponse(runId: "run", agent: 1, path: "figure.png", contentType: "image/png",
                                          before: version, after: version)
            }
            return VisualFileResponse(runId: "run", agent: 1, path: "figure.png", contentType: "image/png",
                side: request.side, fileId: id, sizeBytes: Int64(bad.count), offset: 0, nextOffset: Int64(bad.count),
                hasMore: false, contentBase64: bad.base64EncodedString(), chunkSha256: self.digest(bad))
        }
        #expect(loader.snapshot == nil)
        #expect(loader.error != nil)
    }

    @Test func unavailableHistoryIsNotReplacedByTodaysWorkspaceFile() async throws {
        let loader = FileComparisonLoader()
        var calls = 0
        await loader.load(key: key("old.docx"), cache: FileComparisonCache(), language: .english) { request in
            calls += 1
            #expect(request.side == nil)
            return VisualFileResponse(runId: "run", agent: 1, path: "old.docx", contentType: "application/octet-stream",
                before: VisualFileVersionInfo(state: "unavailable"), after: VisualFileVersionInfo(state: "absent"))
        }
        #expect(calls == 1)
        #expect(loader.snapshot?.before.url == nil)
        #expect(loader.snapshot?.after.info.state == "absent")
        #expect(loader.error == nil)
    }
}
