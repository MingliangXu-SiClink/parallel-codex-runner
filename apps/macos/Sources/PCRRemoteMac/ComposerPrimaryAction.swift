import Foundation

/// Only an explicit submission changes the send affordance into a progress
/// indicator. Reading, polling and candidate preloading never own this control.
struct ComposerPrimaryAction: Equatable {
    enum Kind: Equatable {
        case send
        case queue
        case stop
        case sending
    }

    let kind: Kind
    let enabled: Bool

    static func make(
        hasDraft: Bool,
        canSend: Bool,
        runIsStoppable: Bool,
        queuesMessage: Bool = false,
        canStop: Bool,
        submissionInFlight: Bool
    ) -> Self {
        if submissionInFlight {
            return Self(kind: .sending, enabled: false)
        }
        if !hasDraft, runIsStoppable {
            return Self(kind: .stop, enabled: canStop)
        }
        return Self(kind: queuesMessage ? .queue : .send, enabled: canSend)
    }
}
