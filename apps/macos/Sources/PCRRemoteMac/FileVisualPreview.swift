import AppKit
import ImageIO
import PDFKit
import SwiftUI
import UniformTypeIdentifiers
import WebKit

enum VisualPreviewKind: String, Sendable {
    case image, docx, pdf

    static func resolve(path: String, contentType: String = "") -> Self? {
        let ext = (path as NSString).pathExtension.lowercased()
        if ext == "docx" { return .docx }
        if ext == "pdf" { return .pdf }
        let type = UTType(filenameExtension: ext) ?? UTType(mimeType: contentType)
        if ext == "svg" || type?.conforms(to: .image) == true { return .image }
        return nil
    }
}

struct FilePreviewSettings: Equatable {
    var zoom = 1.0
    var revisions = true
    var comments = true
}

@MainActor
final class FilePreviewSession: NSObject, ObservableObject, WKScriptMessageHandler, WKNavigationDelegate {
    @Published var rendered = false
    @Published var failed = false
    @Published var tooLarge = false
    @Published var hasRevisions = false
    @Published var hasComments = false
    @Published var detail = ""
    weak var webView: WKWebView?
    var onLink: ((URL) -> Void)?
    private var fileURL: URL?
    private var ready = false
    private var task: Task<Void, Never>?
    private var settings = FilePreviewSettings()

    static var shellURL: URL? {
        let resources: Bundle
        if Bundle.main.bundleURL.pathExtension == "app" {
            guard let url = Bundle.main.url(forResource: "ParallelCodexMac_PCRRemoteMac", withExtension: "bundle"),
                  let packaged = Bundle(url: url) else { return nil }
            resources = packaged
        } else {
            resources = Bundle.module
        }
        return resources.url(forResource: "preview", withExtension: "html", subdirectory: "FilePreview")
    }

    func attach(_ view: WKWebView) {
        ready = false
        webView = view
        if let shell = Self.shellURL {
            view.loadFileURL(shell, allowingReadAccessTo: shell.deletingLastPathComponent())
        } else { failed = true }
    }

    func load(_ url: URL) {
        guard fileURL != url else { return }
        fileURL = url
        rendered = false; failed = false; tooLarge = false
        hasRevisions = false; hasComments = false; detail = ""
        start()
    }

    func configure(_ value: FilePreviewSettings) {
        guard settings != value else { return }
        settings = value
        sendSettings()
    }

    private func sendSettings() {
        guard ready, let data = try? JSONSerialization.data(withJSONObject: [
            "zoom": settings.zoom, "revisions": settings.revisions, "comments": settings.comments
        ]), let json = String(data: data, encoding: .utf8) else { return }
        webView?.evaluateJavaScript("void window.PCRPreview.configure(\(json))", completionHandler: nil)
    }

    private func start() {
        guard ready, let url = fileURL else { return }
        task?.cancel()
        task = Task { [self] in
            do {
                let payload = try await Task.detached(priority: .userInitiated) {
                    try Self.payload(url)
                }.value
                try Task.checkCancellation()
                guard fileURL == url else { return }
                let json = String(decoding: try JSONSerialization.data(withJSONObject: payload), as: UTF8.self)
                _ = try await webView?.evaluateJavaScript("void window.PCRPreview.load(\(json))")
                sendSettings()
            } catch is CancellationError { }
            catch {
                guard fileURL == url, !Task.isCancelled else { return }
                tooLarge = (error as? FilePreviewError) == .tooLarge
                failed = true
            }
        }
    }

    nonisolated static func payload(_ url: URL) throws -> [String: String] {
        guard let kind = VisualPreviewKind.resolve(path: url.path), kind != .pdf else { throw FilePreviewError.invalid }
        let size = try url.resourceValues(forKeys: [.fileSizeKey, .isRegularFileKey])
        guard size.isRegularFile == true else { throw FilePreviewError.invalid }
        guard (size.fileSize ?? Int.max) <= 64 * 1024 * 1024 else { throw FilePreviewError.tooLarge }
        var data = try Data(contentsOf: url, options: .mappedIfSafe)
        var mime = UTType(filenameExtension: url.pathExtension)?.preferredMIMEType ?? "application/octet-stream"
        if kind == .image, let source = CGImageSourceCreateWithData(data as CFData, nil) {
            let properties = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any]
            let width = (properties?[kCGImagePropertyPixelWidth] as? Double) ?? 0
            let height = (properties?[kCGImagePropertyPixelHeight] as? Double) ?? 0
            guard width * height <= 100_000_000 else { throw FilePreviewError.tooLarge }
            if ["heic", "tif", "tiff", "bmp", "ico", "avif"].contains(url.pathExtension.lowercased()) {
                guard let image = CGImageSourceCreateThumbnailAtIndex(source, 0, [
                    kCGImageSourceCreateThumbnailFromImageAlways: true,
                    kCGImageSourceThumbnailMaxPixelSize: 8192,
                    kCGImageSourceCreateThumbnailWithTransform: true
                ] as CFDictionary) else { throw FilePreviewError.invalid }
                let output = NSMutableData()
                guard let destination = CGImageDestinationCreateWithData(output, UTType.png.identifier as CFString, 1, nil)
                else { throw FilePreviewError.invalid }
                CGImageDestinationAddImage(destination, image, nil)
                guard CGImageDestinationFinalize(destination) else { throw FilePreviewError.invalid }
                data = output as Data; mime = "image/png"
            }
        }
        return ["base64": data.base64EncodedString(), "kind": kind.rawValue, "mime": mime, "name": url.lastPathComponent]
    }

    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        guard let value = message.body as? [String: Any], let type = value["type"] as? String else { return }
        switch type {
        case "ready": ready = true; start()
        case "features":
            hasRevisions = value["revisions"] as? Bool == true
            hasComments = value["comments"] as? Bool == true
        case "rendered":
            rendered = true; failed = false
            if let width = value["width"] as? Int, let height = value["height"] as? Int { detail = "\(width) \u{00d7} \(height)" }
        case "error":
            tooLarge = (value["reason"] as? String)?.contains("too large") == true
            failed = true
        default: break
        }
    }

    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction) async -> WKNavigationActionPolicy {
        guard let url = navigationAction.request.url else { return .cancel }
        if let shell = Self.shellURL, url.isFileURL, url.path == shell.path {
            return .allow
        } else {
            if navigationAction.navigationType == .linkActivated { onLink?(url) }
            return .cancel
        }
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) { failed = true }
    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) { failed = true }
    func stop(_ view: WKWebView) {
        view.configuration.userContentController.removeScriptMessageHandler(forName: "preview")
        if webView === view { task?.cancel(); webView = nil; ready = false }
    }
}

enum FilePreviewError: Error, Equatable { case invalid, tooLarge }

struct RichFileWebPreview: NSViewRepresentable {
    @ObservedObject var session: FilePreviewSession
    let url: URL
    let settings: FilePreviewSettings
    var onLink: ((URL) -> Void)?
    func makeCoordinator() -> FilePreviewSession { session }
    func makeNSView(context: Context) -> WKWebView {
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .nonPersistent()
        configuration.userContentController.add(session, name: "preview")
        let view = WKWebView(frame: .zero, configuration: configuration)
        view.navigationDelegate = session
        view.setValue(false, forKey: "drawsBackground")
        session.attach(view)
        return view
    }
    func updateNSView(_ view: WKWebView, context: Context) {
        session.onLink = onLink
        session.load(url)
        session.configure(settings)
    }
    static func dismantleNSView(_ view: WKWebView, coordinator: FilePreviewSession) { coordinator.stop(view) }
}

struct FilePDFPreview: NSViewRepresentable {
    @ObservedObject var session: FilePreviewSession
    let url: URL
    let zoom: Double
    final class Coordinator { var url: URL?; var zoom: Double? }
    func makeCoordinator() -> Coordinator { Coordinator() }
    func makeNSView(context: Context) -> PDFView {
        let view = PDFView()
        view.displayMode = .singlePageContinuous
        view.displayDirection = .vertical
        view.backgroundColor = .clear
        return view
    }
    func updateNSView(_ view: PDFView, context: Context) {
        if context.coordinator.url != url {
            context.coordinator.url = url
            view.document = PDFDocument(url: url)
            view.autoScales = true
            context.coordinator.zoom = nil
            let valid = view.document != nil
            Task { @MainActor in session.failed = !valid; session.rendered = valid }
        }
        if context.coordinator.zoom != zoom {
            context.coordinator.zoom = zoom
            view.autoScales = zoom == 1
            if zoom != 1 { view.scaleFactor = view.scaleFactorForSizeToFit * zoom }
        }
    }
}

struct FilePreviewToolbar: View {
    @Environment(\.appLanguage) private var language
    @Binding var settings: FilePreviewSettings
    var revisions = false
    var comments = false
    var body: some View {
        HStack(spacing: 10) {
            if revisions { Toggle(language.text("修订", "Revisions"), isOn: $settings.revisions).toggleStyle(.checkbox) }
            if comments { Toggle(language.text("批注", "Comments"), isOn: $settings.comments).toggleStyle(.checkbox) }
            Spacer(minLength: 8)
            Button { settings.zoom = max(0.25, settings.zoom / 1.25) } label: { Image(systemName: "minus.magnifyingglass") }
                .help(language.text("缩小", "Zoom out")).disabled(settings.zoom <= 0.25)
            Button { settings.zoom = 1 } label: { Image(systemName: "arrow.up.left.and.down.right.magnifyingglass") }
                .help(language.text("适合宽度", "Fit width"))
            Button { settings.zoom = min(4, settings.zoom * 1.25) } label: { Image(systemName: "plus.magnifyingglass") }
                .help(language.text("放大", "Zoom in")).disabled(settings.zoom >= 4)
        }.buttonStyle(.borderless).appScaledSystemFont(size: 12).padding(.horizontal, 12).frame(height: 36)
    }
}

struct FileVisualPreview: View {
    @Environment(\.appLanguage) private var language
    @StateObject private var session = FilePreviewSession()
    @State private var settings = FilePreviewSettings()
    let url: URL
    var onLink: ((URL) -> Void)?
    var body: some View {
        VStack(spacing: 0) {
            FilePreviewToolbar(settings: $settings, revisions: session.hasRevisions, comments: session.hasComments)
            FileVisualPreviewContent(url: url, settings: settings, session: session, onLink: onLink)
            if !session.detail.isEmpty { Text(session.detail).appScaledSystemFont(size: 11).foregroundStyle(.secondary).padding(6) }
        }
    }
}

struct FileVisualPreviewContent: View {
    @Environment(\.appLanguage) private var language
    let url: URL
    let settings: FilePreviewSettings
    @ObservedObject var session: FilePreviewSession
    var onLink: ((URL) -> Void)?
    var body: some View {
        ZStack {
            if VisualPreviewKind.resolve(path: url.path) == .pdf {
                FilePDFPreview(session: session, url: url, zoom: settings.zoom)
            } else {
                RichFileWebPreview(session: session, url: url, settings: settings, onLink: onLink)
                if !session.rendered && !session.failed { ProgressView().controlSize(.small) }
            }
            if session.failed {
                VStack(spacing: 12) {
                    Image(systemName: "doc.questionmark")
                    Text(session.tooLarge ? language.text("文件过大，无法在此预览", "This file is too large to preview here")
                         : language.text("无法生成此文件的预览", "This file could not be previewed"))
                    Button { NSWorkspace.shared.open(url) } label: {
                        Label(language.text("使用其他应用打开", "Open with another app"), systemImage: "arrow.up.forward.app")
                    }
                }.foregroundStyle(.secondary).padding(20).frame(maxWidth: .infinity, maxHeight: .infinity)
                    .background(Color(nsColor: .windowBackgroundColor))
            }
        }.frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}
