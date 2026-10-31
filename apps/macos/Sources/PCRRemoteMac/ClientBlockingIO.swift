import Foundation

/// A navigation change promotes the existing queued read, never a duplicate RPC.
final class ClientReadDemand: @unchecked Sendable {
    private let lock = NSLock()
    private var focused = false
    private var pending: [UUID: @Sendable () -> Void] = [:]

    func setFocused(_ value: Bool) {
        lock.lock()
        focused = value
        let promote = value ? Array(pending.values) : []
        lock.unlock()
        for action in promote { action() }
    }

    func register(_ id: UUID, promote: @escaping @Sendable () -> Void) {
        lock.lock()
        pending[id] = promote
        let now = focused
        lock.unlock()
        if now { promote() }
    }

    func remove(_ id: UUID) {
        lock.lock()
        pending.removeValue(forKey: id)
        lock.unlock()
    }
}

private final class ClientReadOnce: @unchecked Sendable {
    private let lock = NSLock()
    private var claimed = false
    func claim() -> Bool {
        lock.lock()
        defer { lock.unlock() }
        guard !claimed else { return false }
        claimed = true
        return true
    }
}

/// Synchronous sockets and cache locks must never occupy Swift's cooperative pool.
enum ClientBlockingIO {
    private static let backgroundRequests: OperationQueue = {
        let queue = OperationQueue()
        queue.name = "com.parallelcodexrunner.background-reads"
        queue.qualityOfService = .utility
        queue.maxConcurrentOperationCount = 2
        return queue
    }()
    private static let liveContentRequests: OperationQueue = {
        let queue = OperationQueue()
        queue.name = "com.parallelcodexrunner.live-content-reads"
        queue.qualityOfService = .utility
        queue.maxConcurrentOperationCount = 2
        return queue
    }()
    private static let workHistoryRequests: OperationQueue = {
        let queue = OperationQueue()
        queue.name = "com.parallelcodexrunner.work-history-reads"
        queue.qualityOfService = .utility
        queue.maxConcurrentOperationCount = 2
        return queue
    }()
    private static let cacheQueue = DispatchQueue(
        label: "com.parallelcodexrunner.conversation-cache", qos: .utility
    )
    private static let cacheWriteQueue = DispatchQueue(
        label: "com.parallelcodexrunner.conversation-cache-writes", qos: .utility
    )

    static func transport<Value: Sendable>(
        priority: TaskPriority,
        isObservation: Bool,
        isLiveContent: Bool = false,
        demand: ClientReadDemand? = nil,
        _ work: @escaping @Sendable () throws -> Value
    ) async throws -> Value {
        try await withCheckedThrowingContinuation { continuation in
            let id = UUID(), once = ClientReadOnce()
            let operation: @Sendable () -> Void = {
                guard once.claim() else { return }
                demand?.remove(id)
                do { continuation.resume(returning: try work()) }
                catch { continuation.resume(throwing: error) }
            }
            // Live replies, full work backfill, and metadata each have two
            // bounded slots. A slow history read cannot occupy the reply lane.
            if isObservation && priority <= .utility {
                let queue = isLiveContent
                    ? (priority > .background ? liveContentRequests : workHistoryRequests)
                    : backgroundRequests
                let request = BlockOperation(block: operation)
                request.queuePriority = priority > .background ? .high : .low
                demand?.register(id) {
                    guard !request.isExecuting, !request.isFinished, !request.isCancelled else { return }
                    request.cancel()
                    // Use the same lane as a direct foreground request. An
                    // obsolete in-flight read must not serialize the new focus.
                    DispatchQueue.global(qos: .userInitiated).async(execute: operation)
                }
                queue.addOperation(request)
            } else {
                let qos: DispatchQoS.QoSClass = priority >= .userInitiated ? .userInitiated
                    : priority <= .utility ? .utility : .default
                DispatchQueue.global(qos: qos).async(execute: operation)
            }
        }
    }

    static func cache<Value: Sendable>(
        _ work: @escaping @Sendable () -> Value
    ) async -> Value {
        await withCheckedContinuation { continuation in
            cacheQueue.async { continuation.resume(returning: work()) }
        }
    }

    static func cacheWrite<Value: Sendable>(
        _ work: @escaping @Sendable () -> Value
    ) async -> Value {
        await withCheckedContinuation { continuation in
            cacheWriteQueue.async { continuation.resume(returning: work()) }
        }
    }
}
