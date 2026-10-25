import Foundation

struct ConversationHistoryEntry: Equatable {
    let id: String
    let itemIndex: Int
    let text: String

    static func make(from items: [ConversationTimelineItem]) -> [Self] {
        items.enumerated().compactMap { index, item in
            guard case .message(let block) = item, block.kind == .user else { return nil }
            let text = block.text.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !text.isEmpty else { return nil }
            return Self(id: item.id, itemIndex: index, text: text)
        }
    }
}

struct ConversationHistoryRailGeometry {
    let count: Int
    let firstY: CGFloat
    let length: CGFloat
    let tickCount: Int

    init(count: Int, height: CGFloat) {
        self.count = count
        // Keep the first marker below the sticky Agent controls without
        // rearranging the rail every time those controls appear or disappear.
        let available = max(0, height - 2 * min(84, height / 4))
        tickCount = min(count, max(2, Int(available / 6) + 1))
        length = min(available, CGFloat(max(0, tickCount - 1)) * 12)
        firstY = (height - length) / 2
    }

    func y(for index: Int) -> CGFloat {
        firstY + length * CGFloat(index) / CGFloat(max(1, count - 1))
    }

    func index(at y: CGFloat) -> Int? {
        guard count > 1, length > 0 else { return nil }
        let fraction = min(1, max(0, (y - firstY) / length))
        return min(count - 1, Int((fraction * CGFloat(count - 1)).rounded()))
    }

    func entryIndex(forTick tick: Int) -> Int {
        Int((CGFloat(tick) * CGFloat(max(0, count - 1)) / CGFloat(max(1, tickCount - 1))).rounded())
    }

    func tickIndex(forEntry index: Int) -> Int {
        Int((CGFloat(index) * CGFloat(max(0, tickCount - 1)) / CGFloat(max(1, count - 1))).rounded())
    }

    static func width(distance: CGFloat?, active: Bool) -> CGFloat {
        let proximity = distance.map { exp(-pow($0 / 26, 2) / 2) } ?? 0
        return max(active ? 14 : 6, 6 + 26 * proximity)
    }

    static func activeIndex(offset: CGFloat, questionOffsets: [CGFloat]) -> Int? {
        guard !questionOffsets.isEmpty else { return nil }
        var lower = 0
        var upper = questionOffsets.count
        while lower < upper {
            let middle = (lower + upper) / 2
            if questionOffsets[middle] <= offset {
                lower = middle + 1
            } else {
                upper = middle
            }
        }
        return max(0, lower - 1)
    }
}
