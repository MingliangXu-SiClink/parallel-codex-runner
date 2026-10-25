import Foundation

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

    static func transport<Value: Sendable>(
        priority: TaskPriority,
        isObservation: Bool,
        isLiveContent: Bool = false,
        _ work: @escaping @Sendable () throws -> Value
    ) async throws -> Value {
        try await withCheckedThrowingContinuation { continuation in
            let operation: @Sendable () -> Void = {
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
}
