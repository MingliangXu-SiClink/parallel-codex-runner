import AppKit
import CoreImage
import CoreImage.CIFilterBuiltins
import SwiftUI

struct RelayPairingView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.appTextSize) private var appTextSize

    var body: some View {
        VStack(alignment: .leading, spacing: 24) {
            SheetHeader(
                title: "Connect Your Phone",
                subtitle: "Scan once to use your connected computers from your phone.",
                systemImage: "iphone.and.arrow.forward",
                close: { model.pairingPresented = false }
            )

            if model.pairingIsLoading && model.relay.pairingUri?.isEmpty != false {
                loadingContent
            } else if let message = model.pairingError {
                errorContent(message)
            } else if let uri = model.relay.pairingUri, !uri.isEmpty {
                pairingCodeContent(uri: uri)
            } else if model.relay.hasConnectedPhone {
                pairedContent
            } else {
                loadingContent
            }
        }
        .padding(24)
        .controlSize(appTextSize.controlSize)
        .task(id: model.relay.pairingId) {
            await model.monitorMobilePairing()
        }
    }

    private var pairedContent: some View {
        VStack(spacing: 18) {
            Spacer()
            Image(systemName: "checkmark.circle.fill")
                .appScaledSystemFont(size: 54, weight: .medium)
                .foregroundStyle(.green)
                .symbolRenderingMode(.hierarchical)
            VStack(spacing: 6) {
                Text(model.appLanguage.localized("Phone Connected"))
                    .appScaledTextStyle(.title3, weight: .semibold)
                Text(model.appLanguage.localized(
                    "You can now open workspaces and manage runs from your phone."
                ))
                    .appScaledTextStyle(.body)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
                    .frame(maxWidth: 340)
            }
            Spacer()
            HStack(spacing: 10) {
                Spacer()
                Button {
                    Task { await model.createMobilePairing(force: true) }
                } label: {
                    Label(
                        model.appLanguage.localized("Connect Another Phone"),
                        systemImage: "plus"
                    )
                }
                .buttonStyle(.borderedProminent)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private func pairingCodeContent(uri: String) -> some View {
        VStack(spacing: 16) {
            Spacer(minLength: 0)
            pairingCode(uri: uri)
            VStack(spacing: 7) {
                Text(model.appLanguage.localized("Scan with Parallel Codex on your phone"))
                    .appScaledTextStyle(.headline)
                Text(model.appLanguage.localized(
                    "Open the app, tap the scan button, and point the camera at this code."
                ))
                    .appScaledTextStyle(.callout)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
                    .frame(maxWidth: 390)
            }
            HStack(spacing: 8) {
                ProgressView()
                    .controlSize(.small)
                Text(model.appLanguage.localized("Waiting for your phone"))
                    .appScaledTextStyle(.callout, weight: .medium)
                    .foregroundStyle(.secondary)
            }
            Spacer(minLength: 0)
            HStack {
                Spacer()
                Button {
                    Task { await model.createMobilePairing(force: true) }
                } label: {
                    Label(
                        model.appLanguage.localized("New Code"),
                        systemImage: "arrow.clockwise"
                    )
                }
                .disabled(model.pairingIsLoading)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private func pairingCode(uri: String) -> some View {
        QRCodeView(value: uri)
            .frame(width: 224, height: 224)
            .padding(12)
            .background(.white, in: RoundedRectangle(cornerRadius: 8))
            .shadow(color: .black.opacity(0.08), radius: 12, y: 4)
            .overlay {
                RoundedRectangle(cornerRadius: 8)
                    .stroke(.quaternary, lineWidth: 1)
            }
    }

    private var loadingContent: some View {
        VStack(spacing: 14) {
            Spacer()
            ProgressView()
                .controlSize(.large)
            Text(model.appLanguage.localized("Creating a secure connection..."))
                .appScaledTextStyle(.headline)
            Text(model.appLanguage.localized("This usually takes only a moment."))
                .appScaledTextStyle(.callout)
                .foregroundStyle(.secondary)
            Spacer()
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private func errorContent(_ message: String) -> some View {
        VStack(spacing: 16) {
            Spacer()
            Image(systemName: "wifi.exclamationmark")
                .appScaledSystemFont(size: 46, weight: .medium)
                .foregroundStyle(.orange)
                .symbolRenderingMode(.hierarchical)
            VStack(spacing: 6) {
                Text(model.appLanguage.localized("Couldn't Create a Pairing Code"))
                    .appScaledTextStyle(.title3, weight: .semibold)
                Text(message)
                    .appScaledTextStyle(.callout)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
                    .frame(maxWidth: 380)
            }
            Button {
                Task { await model.monitorMobilePairing() }
            } label: {
                Label(
                    model.appLanguage.localized("Try Again"),
                    systemImage: "arrow.clockwise"
                )
            }
            .buttonStyle(.borderedProminent)
            .disabled(model.pairingIsLoading)
            Spacer()
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

struct SheetHeader: View {
    @Environment(\.appLanguage) private var appLanguage
    @Environment(\.appAccentColor) private var appAccentColor
    @Environment(\.appTextSize) private var appTextSize
    let title: String
    let subtitle: String
    let systemImage: String
    let close: () -> Void

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: systemImage)
                .appScaledTextStyle(.title2)
                .foregroundStyle(appAccentColor.color)
                .frame(width: 30 * appTextSize.scale)
            VStack(alignment: .leading, spacing: 2) {
                Text(appLanguage.localized(title))
                    .appScaledTextStyle(.title2, weight: .semibold)
                Text(appLanguage.localized(subtitle))
                    .appScaledTextStyle(.callout)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            Button(action: close) {
                Image(systemName: "xmark")
                    .appScaledTextStyle(.body)
            }
            .buttonStyle(.borderless)
            .keyboardShortcut(.cancelAction)
            .help(appLanguage.localized("Close"))
        }
    }
}

private struct QRCodeView: View {
    let value: String

    var body: some View {
        if let image {
            Image(nsImage: image)
                .interpolation(.none)
                .resizable()
                .scaledToFit()
        } else {
            Color.secondary.opacity(0.1)
        }
    }

    private var image: NSImage? {
        let filter = CIFilter.qrCodeGenerator()
        filter.message = Data(value.utf8)
        filter.correctionLevel = "M"
        guard let output = filter.outputImage else { return nil }
        let context = CIContext()
        guard let cgImage = context.createCGImage(output, from: output.extent) else {
            return nil
        }
        return NSImage(cgImage: cgImage, size: NSSize(width: 220, height: 220))
    }
}

struct DirectoryBrowser: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.appTextSize) private var appTextSize
    @State private var pathDraft = ""
    @FocusState private var pathFocused: Bool

    var body: some View {
        VStack(spacing: 0) {
            browserToolbar
            Divider()
            HSplitView {
                locations
                    .frame(minWidth: 170, idealWidth: 190, maxWidth: 240)
                contents
                    .frame(minWidth: 480, maxWidth: .infinity)
            }
            Divider()
            browserFooter
        }
        .appScaledTextStyle(.body)
        .controlSize(appTextSize.controlSize)
        .onAppear { pathDraft = model.browserPage?.path ?? model.browserRoots?.home ?? "" }
        .onChange(of: model.browserPage?.path) { path in
            if let path { pathDraft = path }
        }
    }

    private var browserToolbar: some View {
        HStack(spacing: 10) {
            Button {
                guard let parent = model.browserPage?.parent else { return }
                Task { await model.navigateDirectory(parent) }
            } label: {
                Image(systemName: "chevron.up")
            }
            .disabled(model.browserPage?.parent == nil || browserBusy)
            .help(model.appLanguage.localized("Parent folder"))

            Button {
                pathFocused = true
            } label: {
                Image(systemName: "folder")
            }
            .buttonStyle(.plain)
            .keyboardShortcut("g", modifiers: [.command, .shift])
            .help(model.appLanguage.text("前往文件夹", "Go to Folder"))

            TextField(model.appLanguage.text("文件夹路径", "Folder path"), text: $pathDraft)
                .textFieldStyle(.plain)
                .appScaledTextStyle(.callout, design: .monospaced)
                .focused($pathFocused)
                .onSubmit(goToPath)
                .disabled(model.browserAdding)
                .accessibilityLabel(model.appLanguage.text("文件夹路径", "Folder path"))
            Button(action: goToPath) {
                Group {
                    if model.browserLoading {
                        ProgressView().controlSize(.small)
                    } else {
                        Image(systemName: "arrow.right")
                    }
                }
                .frame(width: 18, height: 18)
            }
            .buttonStyle(.plain)
            .disabled(browserBusy || pathDraft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            .help(model.appLanguage.text("前往", "Go"))
            Toggle(
                model.appLanguage.localized("Hidden Files"),
                isOn: $model.showHiddenFiles
            )
                .toggleStyle(.switch)
                .fixedSize()
                .disabled(browserBusy)
                .onChange(of: model.showHiddenFiles) { _ in
                    Task { await model.reloadDirectory() }
                }
        }
        .padding(12)
        .background(.bar)
    }

    private var locations: some View {
        List {
            Section {
                ForEach(model.browserRoots?.roots ?? []) { root in
                    Button {
                        Task { await model.navigateDirectory(root.path) }
                    } label: {
                        Label(
                            root.name,
                            systemImage: root.kind == "volume" ? "externaldrive" : "house"
                        )
                        .appScaledTextStyle(.body)
                        .frame(minHeight: 22 * appTextSize.scale)
                    }
                    .buttonStyle(.plain)
                    .disabled(browserBusy)
                }
            } header: {
                Text(model.appLanguage.localized("Locations"))
                    .appScaledTextStyle(.caption)
            }
        }
        .listStyle(.sidebar)
    }

    private var contents: some View {
        List {
            Section {
                if model.browserLoading && model.browserPage == nil {
                    HStack(spacing: 8) {
                        ProgressView()
                            .controlSize(.small)
                        Text(model.appLanguage.localized("Loading folder…"))
                            .appScaledTextStyle(.body)
                            .foregroundStyle(.secondary)
                    }
                    .frame(maxWidth: .infinity, alignment: .center)
                    .padding(.vertical, 18)
                }
                ForEach(model.browserPage?.entries ?? []) { entry in
                    DirectoryEntryRow(entry: entry) {
                        Task { await model.navigateDirectory(entry.path) }
                    }
                    .disabled(browserBusy)
                    .onAppear { Task { await model.prefetchDirectory(near: entry.path) } }
                }
                if let error = model.browserPaginationError {
                    HStack {
                        Text(error).foregroundStyle(.secondary).lineLimit(2)
                        Spacer()
                        Button {
                            Task { await model.retryDirectoryPagination() }
                        } label: {
                            Image(systemName: "arrow.clockwise")
                        }
                        .help(model.appLanguage.localized("Try Again"))
                    }
                    .appScaledTextStyle(.body)
                    .disabled(browserBusy)
                } else if model.browserPage?.hasMore == true {
                    Color.clear.frame(height: 1)
                        .task(id: model.browserPage?.nextCursor) { await model.loadMoreDirectory() }
                }
            } header: {
                Text(model.appLanguage.localized("Contents"))
                    .appScaledTextStyle(.caption)
            }
        }
        .listStyle(.inset)
    }

    private var browserFooter: some View {
        HStack(spacing: 12) {
            Label(
                model.browserPage?.isGitRepository == true
                    ? model.appLanguage.localized("Git repository")
                    : model.appLanguage.localized("Folder"),
                systemImage: model.browserPage?.isGitRepository == true
                    ? "arrow.triangle.branch" : "folder"
            )
            .appScaledTextStyle(.caption)
            .foregroundStyle(.secondary)
            if let error = model.browserError, !error.isEmpty {
                Text(error)
                    .appScaledTextStyle(.caption)
                    .foregroundStyle(.red)
                    .lineLimit(2)
                    .help(error)
            }
            Spacer()
            if model.browserPage == nil, model.browserError != nil {
                Button {
                    Task { await model.openBrowser() }
                } label: {
                    Label(
                        model.appLanguage.localized("Try Again"),
                        systemImage: "arrow.clockwise"
                    )
                }
                .disabled(browserBusy)
            }
            Button(model.appLanguage.localized("Cancel"), role: .cancel) {
                model.closeBrowser()
            }
                .disabled(model.browserAdding)
            Button {
                Task { await model.addCurrentDirectory() }
            } label: {
                if model.browserAdding {
                    HStack(spacing: 6) {
                        ProgressView()
                            .controlSize(.small)
                        Text(model.appLanguage.localized("Adding…"))
                    }
                } else {
                    Text(model.appLanguage.localized("Add Workspace"))
                }
            }
            .buttonStyle(.borderedProminent)
            .disabled(model.browserPage?.readable != true || browserBusy || model.browserError != nil
                      || pathDraft != model.browserPage?.path)
        }
        .padding(12)
        .background(.bar)
    }

    private var browserBusy: Bool {
        model.browserLoading || model.browserAdding
    }

    private func goToPath() {
        guard !browserBusy,
              let path = resolvedDirectoryBrowserPath(
                  pathDraft, current: model.browserPage?.path,
                  home: model.browserRoots?.home
              ) else { return }
        let submitted = pathDraft
        Task {
            await model.navigateDirectory(path)
            if pathDraft == submitted, model.browserError == nil, let page = model.browserPage {
                pathDraft = page.path
            }
        }
    }
}

private struct DirectoryEntryRow: View {
    @Environment(\.appAccentColor) private var appAccentColor
    @Environment(\.appTextSize) private var appTextSize
    let entry: FileSystemEntry
    let open: () -> Void

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: entry.isDirectory ? "folder.fill" : "doc")
                .appScaledTextStyle(.body)
                .foregroundStyle(entry.isDirectory ? appAccentColor.color : Color.secondary)
            Text(entry.name)
                .appScaledTextStyle(.body)
                .lineLimit(1)
            Spacer()
            if entry.isDirectory && entry.readable {
                Image(systemName: "chevron.right")
                    .appScaledTextStyle(.caption)
                    .foregroundStyle(.secondary)
                    .accessibilityHidden(true)
            }
        }
        .frame(minHeight: 22 * appTextSize.scale)
        .foregroundStyle(entry.readable ? Color.primary : Color.secondary)
        .contentShape(Rectangle())
        .onTapGesture {
            if entry.isDirectory && entry.readable { open() }
        }
    }
}

struct EmptyState: View {
    @Environment(\.appLanguage) private var appLanguage
    let title: String
    let systemImage: String

    var body: some View {
        VStack(spacing: 10) {
            Image(systemName: systemImage)
                .appScaledSystemFont(size: 36, weight: .light)
                .foregroundStyle(.secondary)
            Text(appLanguage.localized(title))
                .appScaledTextStyle(.title3, weight: .semibold)
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding(32)
    }
}
