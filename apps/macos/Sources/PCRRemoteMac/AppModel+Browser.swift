import Foundation

extension AppModel {
    func openBrowser() async {
        let target = client.currentTarget()
        invalidateDirectoryPagination()
        directoryRequestID += 1
        let requestID = directoryRequestID
        browserPage = nil
        browserError = nil
        browserAdding = false
        browserLoading = true
        browserPresented = true
        do {
            let roots: FileSystemRoots = try await client.call(
                target: target,
                "filesystem.roots",
                body: EmptyBody()
            )
            guard isCurrentMachine(target.machineID),
                  requestID == directoryRequestID else { return }
            browserRoots = roots
        } catch {
            guard isCurrentMachine(target.machineID),
                  requestID == directoryRequestID else { return }
            browserLoading = false
            browserError = appLanguage.userMessage(for: error)
            return
        }
        do {
            try await loadDirectory(browserRoots?.home ?? "")
        } catch {
            browserError = appLanguage.userMessage(for: error)
        }
    }

    func closeBrowser() {
        invalidateDirectoryPagination()
        directoryRequestID += 1
        browserLoading = false
        browserAdding = false
        browserError = nil
        browserPresented = false
    }

    func loadDirectory(_ path: String) async throws {
        let target = client.currentTarget()
        invalidateDirectoryPagination()
        directoryRequestID += 1
        let requestID = directoryRequestID
        browserError = nil
        browserLoading = true
        do {
            let page: DirectoryPage = try await client.call(
                target: target,
                "filesystem.list",
                body: ListDirectoryBody(
                    path: path,
                    cursor: 0,
                    limit: 500,
                    includeHidden: showHiddenFiles,
                    directoriesOnly: true,
                    sort: "modified_desc"
                )
            )
            guard isCurrentMachine(target.machineID),
                  requestID == directoryRequestID,
                  browserPresented else { return }
            browserPage = page
            browserLoading = false
        } catch {
            guard isCurrentMachine(target.machineID),
                  requestID == directoryRequestID else { return }
            browserLoading = false
            throw error
        }
    }

    func loadMoreDirectory() async {
        guard let current = browserPage, current.hasMore, browserPresented,
              !browserLoading, !browserAdding, !browserLoadingNextPage,
              browserError == nil, browserPaginationError == nil else { return }
        let target = client.currentTarget()
        let requestID = directoryRequestID
        let paginationID = UUID()
        directoryPaginationRequestID = paginationID
        browserLoadingNextPage = true
        defer {
            if directoryPaginationRequestID == paginationID {
                directoryPaginationRequestID = nil
                browserLoadingNextPage = false
            }
        }
        do {
            let page: DirectoryPage = try await client.call(
                target: target,
                "filesystem.list",
                body: ListDirectoryBody(
                    path: current.path,
                    cursor: current.nextCursor,
                    limit: 500,
                    includeHidden: showHiddenFiles,
                    directoriesOnly: true,
                    sort: "modified_desc"
                )
            )
            guard isCurrentMachine(target.machineID),
                  requestID == directoryRequestID,
                  directoryPaginationRequestID == paginationID,
                  browserPresented,
                  browserPage?.path == current.path,
                  browserPage?.nextCursor == current.nextCursor,
                  page.path == current.path else { return }
            guard page.cursor == current.nextCursor,
                  page.nextCursor > current.nextCursor || !page.hasMore else {
                throw NSError(domain: "DirectoryBrowser", code: 1, userInfo: [NSLocalizedDescriptionKey:
                    appLanguage.text("无法读取文件夹的后续内容，请重试。", "Unable to read the rest of this folder. Try again.")])
            }
            var paths = Set(current.entries.map(\.path))
            browserPage = DirectoryPage(
                name: page.name,
                path: page.path,
                parent: page.parent,
                readable: page.readable,
                writable: page.writable,
                isGitRepository: page.isGitRepository,
                cursor: current.cursor,
                nextCursor: page.nextCursor,
                hasMore: page.hasMore,
                total: page.total,
                entries: current.entries + page.entries.filter { paths.insert($0.path).inserted }
            )
        } catch {
            guard isCurrentMachine(target.machineID),
                  requestID == directoryRequestID,
                  directoryPaginationRequestID == paginationID,
                  browserPresented else { return }
            browserPaginationError = appLanguage.userMessage(for: error)
        }
    }

    func prefetchDirectory(near path: String) async {
        guard browserPage?.entries.suffix(16).contains(where: { $0.path == path }) == true else { return }
        await loadMoreDirectory()
    }

    func retryDirectoryPagination() async {
        browserPaginationError = nil
        await loadMoreDirectory()
    }

    private func invalidateDirectoryPagination() {
        directoryPaginationRequestID = nil
        browserLoadingNextPage = false
        browserPaginationError = nil
    }

    func reloadDirectory() async {
        guard let path = browserPage?.path else { return }
        do {
            try await loadDirectory(path)
        } catch {
            browserError = appLanguage.userMessage(for: error)
        }
    }

    func navigateDirectory(_ path: String) async {
        do {
            try await loadDirectory(path)
        } catch {
            browserError = appLanguage.userMessage(for: error)
        }
    }

    func addCurrentDirectory() async {
        guard let page = browserPage, page.readable,
              !browserAdding, !browserLoading, browserError == nil else { return }
        let target = client.currentTarget()
        let requestID = directoryRequestID
        browserAdding = true
        browserError = nil
        defer {
            if isCurrentMachine(target.machineID),
               requestID == directoryRequestID {
                browserAdding = false
            }
        }
        do {
            let mutation: WorkspaceMutation = try await client.call(
                target: target,
                "workspaces.add",
                body: AddWorkspaceBody(path: page.path, select: false)
            )
            guard isCurrentMachine(target.machineID),
                  requestID == directoryRequestID,
                  browserPresented,
                  browserPage?.path == page.path else { return }
            closeBrowser()
            await selectAddedWorkspace(mutation.workspace)
        } catch {
            guard isCurrentMachine(target.machineID),
                  requestID == directoryRequestID,
                  browserPresented else { return }
            browserError = appLanguage.userMessage(for: error)
        }
    }

    func selectAddedWorkspace(_ workspace: RemoteWorkspace) async {
        if let index = workspaces.firstIndex(where: { $0.workspaceId == workspace.workspaceId }) {
            workspaces[index] = workspace
        } else {
            workspaces.append(workspace)
        }
        // Adding a bookmark must use the same context swap as sidebar selection.
        // Changing only the ID lets the old history merge into the new workspace.
        await selectWorkspace(workspace.workspaceId)
    }
}
