import PCRRemoteMac

@main
enum ParallelCodexMain {
    @MainActor
    static func main() async {
        await ParallelCodexEntryPoint.main()
    }
}
