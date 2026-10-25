import Testing
@testable import PCRRemoteMac

struct RenderingResourceTests {
    @Test
    @MainActor
    func mathAndSyntaxResourcesProduceRenderedContent() async {
        #expect(await RenderingResourceCheck.failures().isEmpty)
    }
}
