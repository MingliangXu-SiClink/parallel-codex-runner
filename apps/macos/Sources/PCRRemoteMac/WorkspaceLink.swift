import Foundation

enum WorkspaceLinkKind: Equatable {
    case workspaceFile
    case external
    case unsupported
}

private let commonWorkspaceFileSuffixes: Set<String> = [
    "c", "cc", "cfg", "conf", "cpp", "css", "csv", "go", "h", "hpp",
    "html", "ini", "java", "js", "json", "kt", "kts", "log", "m",
    "markdown", "md", "mm", "pdf", "plist", "py", "rb", "rs", "sh",
    "sql", "swift", "toml", "ts", "tsx", "txt", "xml", "yaml", "yml",
    "avif", "bmp", "gif", "heic", "heif", "ico", "jpeg", "jpg", "png", "svg", "tif", "tiff", "webp",
    "doc", "docx", "odt", "ppt", "pptx", "rtf", "xls", "xlsx",
    "aac", "flac", "m4a", "mov", "mp3", "mp4", "ogg", "wav", "webm", "zip",
]

private func isValidBareWebHostLabel(_ label: Substring) -> Bool {
    guard !label.isEmpty, label.first != "-", label.last != "-" else { return false }
    return label.allSatisfy { character in
        character.isLetter || character.isNumber || character == "-"
    }
}

private func isBareWebAddress(_ value: String) -> Bool {
    guard !value.contains(where: { $0.isWhitespace }) else { return false }
    let hostAndPort = value.split(
        maxSplits: 1,
        omittingEmptySubsequences: true,
        whereSeparator: { $0 == "/" || $0 == "?" || $0 == "#" }
    ).first.map(String.init) ?? ""
    let host = hostAndPort.split(separator: ":", maxSplits: 1).first.map(String.init) ?? ""
    let labels = host.split(separator: ".", omittingEmptySubsequences: false)
    guard labels.count >= 2,
          labels.allSatisfy(isValidBareWebHostLabel),
          let suffix = labels.last?.lowercased(),
          suffix.count >= 2,
          suffix.allSatisfy({ $0.isLetter }),
          !commonWorkspaceFileSuffixes.contains(suffix) else {
        return false
    }
    return URLComponents(string: "https://\(value)")?.host != nil
}

func workspaceLinkKind(_ value: String) -> WorkspaceLinkKind {
    let reference = value.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !reference.isEmpty, !reference.hasPrefix("#") else { return .unsupported }
    if reference.hasPrefix("/")
        || reference.hasPrefix("~/")
        || reference.hasPrefix("./")
        || reference.hasPrefix("../")
        || reference.lowercased().hasPrefix("file:") {
        return .workspaceFile
    }
    // A web port is not a source-code line number (e.g. localhost:8891).
    let scheme = URLComponents(string: reference)?.scheme?.lowercased()
    if let scheme, ["http", "https", "mailto", "tel"].contains(scheme) {
        return .external
    }
    if reference.range(
        of: #"^.+:[0-9]+(?::[0-9]+)?(?:#.*)?$"#,
        options: .regularExpression
    ) != nil {
        return .workspaceFile
    }
    if scheme != nil { return .unsupported }
    if isBareWebAddress(reference) { return .external }
    return .workspaceFile
}

func externalWorkspaceLinkURL(_ value: String) -> URL? {
    let reference = value.trimmingCharacters(in: .whitespacesAndNewlines)
    guard workspaceLinkKind(reference) == .external else { return nil }
    if URLComponents(string: reference)?.scheme != nil {
        return URL(string: reference)
    }
    return URL(string: "https://\(reference)")
}

func workspaceLinkReference(_ url: URL) -> String {
    if url.isFileURL { return url.absoluteString }
    return url.relativeString.removingPercentEncoding ?? url.relativeString
}

func resolveWorkspaceLink(_ reference: String, relativeTo documentPath: String?) -> String {
    guard workspaceLinkKind(reference) == .workspaceFile,
          !reference.hasPrefix("/"),
          !reference.hasPrefix("~/"),
          !reference.lowercased().hasPrefix("file:"),
          let documentPath,
          !documentPath.isEmpty else {
        return reference
    }
    let parent = (documentPath as NSString).deletingLastPathComponent
    guard !parent.isEmpty, parent != "." else { return reference }
    return "\(parent)/\(reference)"
}
