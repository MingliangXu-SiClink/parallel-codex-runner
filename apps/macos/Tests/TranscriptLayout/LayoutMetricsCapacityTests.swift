import Foundation
import Testing
@testable import PCRRemoteMac

struct LayoutMetricsCapacityTests {
    @Test func saturatedCacheKeepsPageInsertionBoundedAndPersistsExactMetrics() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let url = directory.appendingPathComponent("metrics.json")
        let store = TranscriptLayoutMetricsStore(fileURL: url)
        let value = TranscriptLayoutMetrics(bodyWidth: 640, textWidth: 616, textHeight: 144, padding: 12)
        for index in 0..<12_000 { store.store(value, forKey: "original-\(index)") }
        #expect(store.count == 12_000)
        let began = ContinuousClock.now
        store.store(value, forKey: "new-page-0")
        let afterEviction = store.count
        #expect(afterEviction < 11_000)
        for index in 1..<400 { store.store(value, forKey: "new-page-\(index)") }
        #expect(store.count == afterEviction + 399)
        #expect(began.duration(to: .now) < .seconds(0.5))
        store.flush()
        let restored = TranscriptLayoutMetricsStore(fileURL: url)
        #expect(restored.count == store.count)
        for index in 0..<400 { #expect(restored.metrics(forKey: "new-page-\(index)") == value) }
        #expect(restored.metrics(forKey: "original-0") == nil)
    }
}
