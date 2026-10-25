import Foundation

func parseActivityDate(_ value: String) -> Date? {
    // Immutable format styles are safe on the background transport decoder.
    let fractional = Date.ISO8601FormatStyle(includingFractionalSeconds: true)
    let whole = Date.ISO8601FormatStyle()
    return (try? fractional.parse(value)) ?? (try? whole.parse(value))
}

struct AgentActivityReaderState: Equatable, Sendable {
    let state: String
    let observedMinute: Int?
    let lastOutputMinute: Int?
    let waitUntil: String?
    let activeOperationCount: Int
}

extension AgentActivityRecord {
    var readerState: AgentActivityReaderState {
        AgentActivityReaderState(state: state,
            observedMinute: observedAtDate.map { Int(floor($0.timeIntervalSince1970 / 60)) },
            lastOutputMinute: lastOutputAtDate.map { Int(floor($0.timeIntervalSince1970 / 60)) },
            waitUntil: waitUntil, activeOperationCount: activeOperationCount)
    }
}
