import SwiftUI

struct FileComparisonView: View {
    @EnvironmentObject private var model: AppModel
    @StateObject private var loader = FileComparisonLoader()
    @StateObject private var beforeSession = FilePreviewSession()
    @StateObject private var afterSession = FilePreviewSession()
    @State private var settings = FilePreviewSettings()
    @State private var retry = 0
    let context: ReviewContext
    let path: String
    var live = false
    private var key: VisualComparisonKey { VisualComparisonKey(context: context, path: path) }
    private struct Request: Hashable { let key: VisualComparisonKey; let retry: Int; let live: Bool }

    var body: some View {
        VStack(spacing: 0) {
            FilePreviewToolbar(settings: $settings,
                revisions: beforeSession.hasRevisions || afterSession.hasRevisions,
                comments: beforeSession.hasComments || afterSession.hasComments)
            if let snapshot = loader.snapshot {
                GeometryReader { geometry in
                    if geometry.size.width >= 600 {
                        HStack(spacing: 0) {
                            version(snapshot.before, before: true)
                            Divider()
                            version(snapshot.after, before: false)
                        }
                    } else {
                        VStack(spacing: 0) {
                            version(snapshot.before, before: true)
                            Divider()
                            version(snapshot.after, before: false)
                        }
                    }
                }
            } else {
                VStack { if loader.loading { ProgressView() } }.frame(maxWidth: .infinity, maxHeight: .infinity)
            }
            if let error = loader.error {
                HStack {
                    Text(error).appScaledSystemFont(size: 12).foregroundStyle(.secondary)
                    Spacer()
                    Button { retry &+= 1 } label: { Image(systemName: "arrow.clockwise") }
                        .help(model.appLanguage.text("重试", "Retry")).disabled(loader.loading)
                }.padding(12)
            }
        }
        .task(id: Request(key: key, retry: retry, live: live)) {
            await loader.load(client: model.client, key: key, cache: model.fileComparisonCache,
                              language: model.appLanguage,
                              refresh: retry > 0 || (!live && model.fileComparisonCache.snapshot(key)?.live == true))
            while !Task.isCancelled && live && loader.error == nil {
                do { try await Task.sleep(for: .seconds(8)) } catch { return }
                await loader.load(client: model.client, key: key, cache: model.fileComparisonCache,
                                  language: model.appLanguage, refresh: true)
            }
        }
    }

    private func version(_ value: VisualFileVersion, before: Bool) -> some View {
        VStack(spacing: 0) {
            HStack {
                Text(before ? model.appLanguage.text("修改前", "Before") : model.appLanguage.text("修改后", "After"))
                    .appScaledSystemFont(size: 12, weight: .medium)
                Spacer()
                let session = before ? beforeSession : afterSession
                if !session.detail.isEmpty { Text(session.detail).appScaledSystemFont(size: 11).foregroundStyle(.secondary) }
                if let url = value.url {
                    Button { NSWorkspace.shared.open(url) } label: { Image(systemName: "arrow.up.forward.app") }
                        .help(model.appLanguage.text("使用其他应用打开", "Open with another app"))
                }
            }.buttonStyle(.borderless).padding(.horizontal, 12).frame(height: 32)
            if let url = value.url {
                FileVisualPreviewContent(url: url, settings: settings, session: before ? beforeSession : afterSession,
                    onLink: { model.openTranscriptLink($0, reviewContext: context) })
                    .id(url)
            } else {
                VStack(spacing: 10) {
                    Image(systemName: value.info.state == "absent" ? (before ? "plus" : "minus") : "doc.questionmark")
                    Text(versionMessage(value.info.state, before: before)).multilineTextAlignment(.center)
                }.appScaledSystemFont(size: 13).foregroundStyle(.secondary)
                    .padding(20).frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }.frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private func versionMessage(_ state: String, before: Bool) -> String {
        if state == "absent" {
            return before ? model.appLanguage.text("新增文件", "New file") : model.appLanguage.text("文件已删除", "File deleted")
        }
        if state == "too_large" { return model.appLanguage.text("文件过大，无法预览", "This file is too large to preview") }
        return model.appLanguage.text("此历史版本未保存，无法还原预览", "This historical version was not retained and cannot be previewed")
    }
}
