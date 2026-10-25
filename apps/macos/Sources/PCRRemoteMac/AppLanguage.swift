import Foundation
import SwiftUI

enum AppErrorNotice: Equatable, Sendable {
    case message(String)
    case selectedConversationHistory(String)
    case previousConversationHistory(String)
    case agentDetail(runID: String, agent: Int, diagnostic: String)

    func message(language: AppLanguage) -> String {
        switch self {
        case .message(let message):
            return message
        case .selectedConversationHistory(let diagnostic):
            let detail = language.localizedClientMessage(diagnostic)
            return language.text(
                "无法加载所选对话。\(detail)",
                "The selected conversation could not be loaded. \(detail)"
            )
        case .previousConversationHistory(let diagnostic):
            let detail = language.localizedClientMessage(diagnostic)
            return language.text(
                "无法加载上一段对话。\(detail)",
                "The previous conversation could not be loaded. \(detail)"
            )
        case .agentDetail(_, _, let diagnostic):
            let detail = language.localizedClientMessage(diagnostic)
            return language.text(
                "未能刷新这条回复。\(detail)",
                "This response could not be refreshed. \(detail)"
            )
        }
    }
}

enum AppLanguage: String, CaseIterable, Identifiable, Sendable {
    case chinese = "zh-Hans"
    case english = "en"

    var id: String { rawValue }
    var locale: Locale { Locale(identifier: rawValue) }
    var title: String { self == .chinese ? "简体中文" : "English" }

    func text(_ chinese: String, _ english: String) -> String {
        self == .chinese ? chinese : english
    }

    func userMessage(for error: Error) -> String {
        localizedClientMessage(error.localizedDescription)
    }

    func localizedClientMessage(_ message: String) -> String {
        let normalized = message.trimmingCharacters(in: .whitespacesAndNewlines)
            .lowercased()
        if normalized.hasPrefix("saved files differ from the recorded result:") {
            let files = message.components(separatedBy: ": ").dropFirst().joined(separator: ": ")
                .replacingOccurrences(of: ". No files were changed", with: "")
            return text("这些文件与待保存结果不同，已保留你的文件，未重新覆盖：\(files)", message)
        }
        if normalized.contains("no verification record") || normalized.contains("no complete verification record") {
            return text("这次旧任务没有完整的保存核对记录。未改动你的文件，请在结果操作中手动检查后继续。", message)
        }
        if normalized.hasPrefix("git history or staged changes differ") || normalized.hasPrefix("the destination folder was replaced") {
            return text("原文件夹或版本记录已经改变，无法直接确认保存完成。已保留你的修改，请检查后继续。", message)
        }
        // Older servers report these conditions as unstructured pcr_error text.
        if normalized.hasPrefix("unknown pcr run:") {
            return text(
                "服务器上已找不到这轮任务。",
                "This turn is no longer available on the server."
            )
        }
        if normalized.hasPrefix("codex session "),
           normalized.contains(" is not resumable from ") {
            return text(
                "原始对话记录无法在此工作区恢复。",
                "The original conversation history cannot be restored in this workspace."
            )
        }
        if normalized.hasPrefix(
            "expired artifact cleanup failed: the recorded run root is no longer a real directory: "
        ) {
            return text(
                "本轮运行文件已不可用，无法完成清理。",
                "The working files for this turn are unavailable, so cleanup could not finish."
            )
        }
        guard self == .chinese else { return message }
        switch normalized {
        case "queue paused after stopping this turn.":
            return "已停止本轮回复，队列已暂停。可在排队消息菜单中继续发送。"
        case "the queue changed while you were editing. your draft has not been sent.":
            return "队列已发生变化，这次编辑未提交。请保留编辑内容并确认最新队列。"
        case "the next message is already being sent. open the latest turn to stop it.":
            return "下一条消息已经开始发送，请打开最新一轮后停止。"
        case "the first queued message is already being sent.":
            return "第一条排队消息已经开始发送，无法再修改。"
        case "this live file preview has expired. refresh to load the current changes.":
            return "这份文件预览已过期，请刷新以读取当前修改。"
        case "these live changes are too large to preview. review them after the agent finishes.":
            return "当前修改内容过大，请等待 Agent 完成后查看。"
        case "only links to files in this workspace can be opened":
            return "此文件不在当前工作区内，无法安全打开。请检查文件路径或切换到它所在的工作区。"
        case "this workspace is still running or syncing another turn. your new conversation can be sent when it finishes.":
            return "此工作区还有任务在运行或同步。新对话的草稿已保留，完成后即可发送。"
        case "this conversation has been deleted. restore it to continue.":
            return "此对话已删除，恢复后即可继续。"
        case "the computer server is not ready. start parallel codex server on this computer and try again.":
            return "设备服务尚未就绪。请先启动 Parallel Codex Server，再重试。"
        case "the local server could not start because its data path is too long.":
            return "本机服务的数据路径过长，无法启动。"
        case "server unavailable":
            return "服务端不可用"
        case "the selected computer is not available.":
            return "所选设备当前不可用。"
        case "the local server could not open a connection.":
            return "无法连接本机服务。"
        case "the local server connection was interrupted.":
            return "与本机服务的连接已中断。"
        case "the desktop app is waiting before reconnecting to the server.":
            return "桌面 App 正在等待重新连接服务端。"
        case "cannot connect to the computer server. check that parallel codex server is running and try again.":
            return "无法连接设备服务。请确认 Parallel Codex Server 正在运行后重试。"
        case "the local server returned an unreadable response.":
            return "设备服务返回了无法读取的内容，请重试。"
        case "this computer's server setup is incomplete. repair parallel codex server on this computer, then try again.":
            return "这台设备的服务配置不完整。请修复 Parallel Codex Server 后重试。"
        case "the server could not create a pairing code. check your connection and try again.":
            return "服务端无法创建配对码，请检查连接后重试。"
        case "the connected computer server needs to be updated before this action is available.":
            return "这项操作需要更新已连接设备上的 Parallel Codex Server。"
        case "this desktop app and the connected computer use incompatible versions. update parallel codex and try again.":
            return "客户端与设备服务版本不兼容，请更新 Parallel Codex 后重试。"
        case "this result is too large to display in one page. narrow the requested range and try again.":
            return "内容太多，无法一次显示。请缩小查看范围后重试。"
        case "confirm the interrupted workspace sync and finish cleanup before changing workspaces.":
            return "请先确认上次工作区同步结果并完成清理。"
        case "finish or discard the current task before changing workspaces.":
            return "请先完成或放弃当前任务。"
        case "the workspace file changed or failed its integrity check.":
            return "工作区文件已发生变化，或完整性校验失败。"
        case "the workspace file download did not finish.":
            return "工作区文件未下载完成。"
        case "this folder could not be opened in finder.":
            return "无法在访达中打开此文件夹，请确认文件夹仍然存在且有访问权限。"
        case "this file link is not a path on this mac.":
            return "这不是本机文件路径，请在文件所在的设备上打开。"
        case "only regular files and folders can be opened.":
            return "只能打开普通文件或文件夹，不能预览设备或管道文件。"
        case "this agent working copy is no longer available":
            return "此候选的工作副本已被清理，无法打开其中的原文件。可从工作区打开当前文件。"
        case "this workspace file is no longer available":
            return "此文件或文件夹已被移动或删除，请确认路径后重试。"
        case "cannot read this workspace folder":
            return "无法读取此文件夹，请检查访问权限。"
        case "connect this app to a computer before checking for updates.":
            return "请先将此 App 连接到一台设备，再检查更新。"
        case "the downloaded update archive is invalid.":
            return "下载的更新包无效。"
        case "the downloaded update failed integrity verification.":
            return "下载的更新未通过完整性校验。"
        case "the downloaded app does not match this parallel codex release.":
            return "下载的 App 与此 Parallel Codex 版本不匹配。"
        case "the downloaded app was signed by a different publisher.":
            return "下载的 App 使用了不同的发布者签名。"
        case "the app cannot be updated in its current location. move parallel codex to a writable applications folder, then try again.":
            return "应用当前的位置无法安装更新。请将 Parallel Codex 移入可写的“应用程序”文件夹后重试。"
        case "another copy of parallel codex is running. quit that copy, then retry the update.":
            return "另一个 Parallel Codex 实例仍在运行，请退出该实例后重试更新。"
        case "the update could not be prepared for restart. the current app has not been changed.":
            return "无法准备更新重启，当前应用未被更改，请稍后重试。"
        case "the downloaded update could not be verified. the current app has not been changed.":
            return "更新校验未通过，当前版本未被更改，请重试。"
        case "another update is being installed. the current app has not been changed.":
            return "应用正在安装更新，请稍后重试。"
        case "the update restart was cancelled. the current app has not been changed.":
            return "更新重启已取消，当前版本未被更改。"
        case "parallel codex did not quit. the current app has not been changed.":
            return "应用没有退出，当前版本未被更改，请重试更新。"
        case "settings are waiting for this computer to reconnect.":
            return "设置正在等待此设备重新连接。"
        case "the conversation changed before this queued message could be sent. open the queue to review it.":
            return "对话已发生变化，这条排队消息没有被发送。请打开队列确认后重试。"
        case "this workspace already has queued messages for another conversation. open the existing queue before changing it.":
            return "这个工作区的另一段对话已有排队消息，请先打开现有队列。"
        default:
            return message
        }
    }

    func localized(_ key: String) -> String {
        let baseBundles: [Bundle]
#if SWIFT_PACKAGE
        baseBundles = [.main, .module]
#else
        baseBundles = [.main]
#endif
        for baseBundle in baseBundles {
            guard let path = baseBundle.path(forResource: rawValue, ofType: "lproj"),
                  let languageBundle = Bundle(path: path) else { continue }
            return languageBundle.localizedString(forKey: key, value: key, table: nil)
        }
        return key
    }

    static func load() -> AppLanguage {
        guard let stored = UserDefaults.standard.string(forKey: storageKey),
              let language = AppLanguage(rawValue: stored) else { return .chinese }
        return language
    }

    static func save(_ language: AppLanguage) {
        UserDefaults.standard.set(language.rawValue, forKey: storageKey)
        UserDefaults.standard.set([language.rawValue], forKey: "AppleLanguages")
    }

    private static let storageKey = "ParallelCodex.AppLanguage"
}

private struct AppLanguageEnvironmentKey: EnvironmentKey {
    static let defaultValue = AppLanguage.chinese
}

extension EnvironmentValues {
    var appLanguage: AppLanguage {
        get { self[AppLanguageEnvironmentKey.self] }
        set { self[AppLanguageEnvironmentKey.self] = newValue }
    }
}
