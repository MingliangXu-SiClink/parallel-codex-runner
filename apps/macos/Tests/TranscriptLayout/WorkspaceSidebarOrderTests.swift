import AppKit
import SwiftUI
import Testing
@testable import PCRRemoteMac

@Suite(.serialized)
@MainActor
struct WorkspaceSidebarOrderTests {
    @Test func manualOrderSurvivesRefreshAndRestartWithoutChangingSourceOrder() throws {
        let available = ["a", "b", "c"]
        var order = WorkspaceSidebarOrder()
        #expect(order.orderedIDs(available, machine: "mac") == available)
        order.move("c", relativeTo: "a", after: false, available: available, machine: "mac")

        let suite = "WorkspaceSidebarOrderTests.\(UUID().uuidString)"
        let defaults = try #require(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        defaults.set(order.data, forKey: WorkspaceSidebarOrder.defaultsKey)
        let reopened = WorkspaceSidebarOrder(data: try #require(defaults.data(forKey: WorkspaceSidebarOrder.defaultsKey)))
        #expect(reopened.orderedIDs(["b", "a", "c"], machine: "mac") == ["c", "a", "b"])
        #expect(reopened.orderedIDs(["new", "b", "a", "c"], machine: "mac") == ["c", "a", "b", "new"])
        #expect(reopened.orderedIDs(["a", "b"], machine: "mac") == ["a", "b"])
        #expect(reopened.orderedIDs(available, machine: "mac") == ["c", "a", "b"])
        #expect(available == ["a", "b", "c"])
    }

    @Test func computersHaveIndependentOrderAndUseIDsRatherThanNames() {
        var order = WorkspaceSidebarOrder()
        order.move("c", relativeTo: "a", after: false, available: ["a", "b", "c"], machine: "one")
        order.move("a", relativeTo: "b", after: true, available: ["a", "b", "c"], machine: "two")
        #expect(order.orderedIDs(["a", "b", "c"], machine: "one") == ["c", "a", "b"])
        #expect(order.orderedIDs(["a", "b", "c"], machine: "two") == ["b", "a", "c"])
        #expect(order.orderedIDs(["b", "c", "a"], machine: "new") == ["b", "c", "a"])
    }

    @Test func newlySeenWorkspacesKeepTheirPlaceAndTemporaryAbsenceDoesNotDeletePreference() {
        var order = WorkspaceSidebarOrder()
        order.includeNewWorkspaces(["a", "b"], machine: "mac")
        #expect(order == WorkspaceSidebarOrder(), "Do not freeze the default before a manual move")
        order.move("b", relativeTo: "a", after: false, available: ["a", "b"], machine: "mac")
        order.includeNewWorkspaces(["new1", "a", "new2", "b"], machine: "mac")
        order.includeNewWorkspaces(["new2", "b", "new1", "a"], machine: "mac")
        #expect(order.orderedIDs(["new2", "b", "new1", "a"], machine: "mac") == ["b", "a", "new1", "new2"])
        order.move("new2", relativeTo: "new1", after: false, available: ["new1", "new2"], machine: "mac")
        #expect(order.orderedIDs(["a", "b", "new1", "new2"], machine: "mac") == ["b", "a", "new2", "new1"])
    }

    @Test(arguments: [
        ("a", "c", true, ["b", "c", "a"]),
        ("a", "c", false, ["b", "a", "c"]),
        ("c", "a", true, ["a", "c", "b"]),
        ("c", "a", false, ["c", "a", "b"]),
        ("a", "b", false, ["a", "b", "c"]),
        ("b", "b", true, ["a", "b", "c"]),
        ("removed", "b", true, ["a", "b", "c"]),
    ])
    func movingBeforeAndAfterUsesTheActualDropPosition(example: (String, String, Bool, [String])) {
        var order = WorkspaceSidebarOrder()
        order.move(example.0, relativeTo: example.1, after: example.2,
                   available: ["a", "b", "c"], machine: "mac")
        #expect(order.orderedIDs(["a", "b", "c"], machine: "mac") == example.3)
    }

    @Test func invalidSavedPreferenceDoesNotHideWorkspaces() {
        let order = WorkspaceSidebarOrder(data: Data("not json".utf8))
        #expect(order.orderedIDs(["a", "b"], machine: "mac") == ["a", "b"])
    }

    @Test func previewDoesNotPersistUntilDropAndCommitsExactlyTheVisibleGap() throws {
        let reorder = WorkspaceSidebarReorder()
        var order = WorkspaceSidebarOrder()
        var commits = 0
        let ids = ["a", "b", "c"]
        #expect(reorder.begin(source: "c", ids: ids, frames: frames(ids)))
        reorder.update(y: 0)
        let preview = try #require(reorder.session).displayedIDs
        #expect(preview == ["c", "a", "b"])
        #expect(order.orderedIDs(ids, machine: "mac") == ids)
        #expect(commits == 0)
        #expect(reorder.finish { source, target, after in
            commits += 1
            order.move(source, relativeTo: target, after: after, available: ids, machine: "mac")
        })
        #expect(order.orderedIDs(ids, machine: "mac") == preview)
        #expect(reorder.session == nil)
        #expect(!reorder.finish { _, _, _ in commits += 1 })
        #expect(commits == 1)
    }

    @Test(arguments: [
        ("c", 0.0, ["c", "a", "b"]), ("c", 40.0, ["a", "c", "b"]),
        ("c", 120.0, ["a", "b", "c"]), ("a", 120.0, ["b", "c", "a"]),
        ("a", 60.0, ["b", "a", "c"]), ("a", 40.0, ["a", "b", "c"]),
    ])
    func previewUsesStableAnchorsWithoutOscillating(example: (String, Double, [String])) {
        let reorder = WorkspaceSidebarReorder()
        let ids = ["a", "b", "c"]
        #expect(reorder.begin(source: example.0, ids: ids, frames: frames(ids)))
        for _ in 0..<10 {
            reorder.update(y: example.1)
            #expect(reorder.session?.displayedIDs == example.2)
        }
    }

    @Test func leavingReturningAndCancellingNeverSaveAPreview() {
        let reorder = WorkspaceSidebarReorder()
        let ids = ["a", "b", "c"]
        #expect(reorder.begin(source: "c", ids: ids, frames: frames(ids)))
        reorder.update(y: 0)
        reorder.exit()
        #expect(reorder.session?.displayedIDs == ids)
        #expect(reorder.session?.inside == false)
        #expect(!reorder.finish { _, _, _ in Issue.record("An outside drop must not commit") })
        reorder.update(y: 0)
        #expect(reorder.session?.displayedIDs == ["c", "a", "b"])
        reorder.cancel()
        #expect(reorder.session == nil)
        #expect(!reorder.finish { _, _, _ in Issue.record("A cancelled drag must not commit") })
        #expect(!reorder.begin(source: "removed", ids: ids, frames: frames(ids)))
        #expect(!reorder.begin(source: "a", ids: ids, frames: [:]))
    }

    private func frames(_ ids: [String]) -> [String: CGRect] {
        Dictionary(uniqueKeysWithValues: ids.enumerated().map { index, id in
            (id, CGRect(x: 8, y: index * 40, width: 264, height: 36))
        })
    }

    @Test func nativeSourceKeepsClicksSeparateFromDrags() throws {
        _ = NSApplication.shared
        let window = NSWindow(contentRect: CGRect(x: 0, y: 0, width: 280, height: 36),
                              styleMask: [.borderless], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        let view = WorkspaceSidebarDragView(frame: CGRect(x: 0, y: 0, width: 280, height: 36))
        window.contentView = view
        defer { window.close() }
        var clicks = 0
        view.click = { clicks += 1 }
        func event(_ type: NSEvent.EventType, x: Double) throws -> NSEvent {
            try #require(NSEvent.mouseEvent(with: type, location: CGPoint(x: x, y: 18),
                modifierFlags: [], timestamp: 0, windowNumber: window.windowNumber,
                context: nil, eventNumber: 1, clickCount: 1, pressure: 1))
        }
        view.mouseDown(with: try event(.leftMouseDown, x: 10))
        view.mouseDragged(with: try event(.leftMouseDragged, x: 12))
        view.mouseUp(with: try event(.leftMouseUp, x: 12))
        #expect(clicks == 1)
        view.mouseDown(with: try event(.leftMouseDown, x: 10))
        view.mouseUp(with: try event(.leftMouseUp, x: 300))
        #expect(clicks == 1)
        view.begin = { false }
        view.mouseDown(with: try event(.leftMouseDown, x: 10))
        view.mouseDragged(with: try event(.leftMouseDragged, x: 30))
        view.mouseUp(with: try event(.leftMouseUp, x: 30))
        #expect(clicks == 1, "Starting a drag cannot also select a conversation")
        #expect(!window.isVisible)
    }

    @Test(arguments: [280.0, 380.0], [false, true])
    func nativeSidebarRendersReorderedRowsWithoutChangingSelectionOrDraft(width: Double, dark: Bool) async throws {
        _ = NSApplication.shared
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        let suite = "WorkspaceSidebarRender.\(UUID().uuidString)"
        let defaults = try #require(UserDefaults(suiteName: suite))
        let id = UUID()
        let model = AppModel(client: PcrdClient(socketURL: directory.appendingPathComponent("missing.sock"),
            tokenURL: directory.appendingPathComponent("missing-token")), windowID: id,
            desktopStateStore: DesktopStateStore(windowID: id, directory: directory))
        defer {
            model.stopWindowObservation()
            _ = model.desktopStateStore.flushPendingWrites()
            defaults.removePersistentDomain(forName: suite)
            try? FileManager.default.removeItem(at: directory)
        }
        let names = ["小鼠实验设计", "hermes-webui", "TunLSCI 重投", "临策算法竞赛",
                     "记忆状态跨个体跨天解码", "高保真脑电数据压缩", "基于人类硬膜外脑电的运动解码"]
        model.workspaces = try names.enumerated().map { index, name in
            try JSONDecoder().decode(RemoteWorkspace.self, from: JSONSerialization.data(withJSONObject: [
                "workspaceId": "w\(index)", "name": name, "path": "/fixture/\(index)",
                "lastRunId": "", "lastResumeSessionId": "", "selected": index == 4,
                "available": true, "readable": true, "writable": true
            ]))
        }
        model.selectedWorkspaceID = "w4"
        model.prompt = "Unsent draft"
        var order = WorkspaceSidebarOrder()
        order.move("w4", relativeTo: "w0", after: false,
                   available: model.workspaces.map(\.workspaceId), machine: model.currentMachineContextID)
        defaults.set(order.data, forKey: WorkspaceSidebarOrder.defaultsKey)
        let reorder = WorkspaceSidebarReorder()
        let root = WorkspaceNavigationSidebar(workspacePendingRemoval: .constant(nil), searchRequestSequence: 0, reorder: reorder)
            .environmentObject(model).environmentObject(model.convState).defaultAppStorage(defaults)
            .environment(\.appLanguage, .chinese).environment(\.colorScheme, dark ? .dark : .light)
            .transaction { $0.disablesAnimations = true }
            .frame(width: width, height: 650)
        let host = NSHostingView(rootView: root)
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: width, height: 650),
            styleMask: [.borderless], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.appearance = NSAppearance(named: dark ? .darkAqua : .aqua)
        window.contentView = host
        defer { window.close() }
        host.layoutSubtreeIfNeeded()
        try await Task.sleep(for: .milliseconds(60))
        host.layoutSubtreeIfNeeded()
        let ids = order.orderedIDs(model.workspaces.map(\.workspaceId), machine: model.currentMachineContextID)
        func dragViews(in view: NSView) -> [WorkspaceSidebarDragView] {
            if let source = view as? WorkspaceSidebarDragView { return [source] }
            return view.subviews.flatMap { dragViews(in: $0) }
        }
        let source = try #require(dragViews(in: host).first { $0.payload.workspace == "w6" })
        let sourceCenter = source.convert(CGPoint(x: source.bounds.midX, y: source.bounds.midY), to: host.superview)
        let hit = host.hitTest(sourceCenter)
        #expect(hit.map { $0 === source } == true,
                "Drag hit: \(String(describing: hit.map { type(of: $0) })); frame \(source.frame), bounds \(source.bounds), center \(sourceCenter)")
        var images: [Data] = []
        for phase in ["idle", "gap", "cancelled"] {
            if phase == "gap" {
                #expect(source.begin(), "Use the sidebar's actual measured row frames")
                #expect(reorder.session?.original == ids)
                reorder.update(y: 0)
                #expect(reorder.session?.displayedIDs.first == "w6")
            } else if phase == "cancelled" { source.end() }
            try await Task.sleep(for: .milliseconds(60))
            host.layoutSubtreeIfNeeded()
            let bitmap = try #require(host.bitmapImageRepForCachingDisplay(in: host.bounds))
            host.cacheDisplay(in: host.bounds, to: bitmap)
            let png = try #require(bitmap.representation(using: .png, properties: [:]))
            images.append(png)
            if let path = ProcessInfo.processInfo.environment["PCR_SIDEBAR_PREVIEW_DIR"] {
                let output = URL(fileURLWithPath: path, isDirectory: true)
                try FileManager.default.createDirectory(at: output, withIntermediateDirectories: true)
                try png.write(to: output.appendingPathComponent("sidebar-\(Int(width))-\(dark)-\(phase).png"))
            }
        }
        #expect(images[0] != images[1], "A real row-sized gap is visible")
        #expect(images[0] == images[2], "Cancel restores the original layout exactly")
        #expect(defaults.data(forKey: WorkspaceSidebarOrder.defaultsKey) == order.data)
        #expect(model.selectedWorkspaceID == "w4")
        #expect(model.prompt == "Unsent draft")
        #expect(model.workspaces.map(\.workspaceId) == (0..<names.count).map { "w\($0)" })
        #expect(!window.isVisible)
    }
}
