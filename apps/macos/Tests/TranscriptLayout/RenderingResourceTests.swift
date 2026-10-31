import Foundation
import Testing
@testable import PCRRemoteMac

struct RenderingResourceTests {
    @Test(arguments: [true, false])
    func packagedLocalizationNeverBorrowsBuildResources(resourcesPresent: Bool) throws {
        let app = FileManager.default.temporaryDirectory.appendingPathComponent("PCR-\(UUID().uuidString).app")
        defer { try? FileManager.default.removeItem(at: app) }
        let resources = app.appendingPathComponent("Contents/Resources")
        try FileManager.default.createDirectory(at: resources, withIntermediateDirectories: true)
        try PropertyListSerialization.data(fromPropertyList: ["CFBundleIdentifier": "test.pcr.localization",
            "CFBundleDevelopmentRegion": "en", "CFBundlePackageType": "APPL"], format: .xml, options: 0)
            .write(to: app.appendingPathComponent("Contents/Info.plist"))
        let translated = "\u{6267}\u{884c}\u{65b9}\u{5f0f}"
        if resourcesPresent {
            let localization = resources.appendingPathComponent("zh-Hans.lproj")
            try FileManager.default.createDirectory(at: localization, withIntermediateDirectories: true)
            try PropertyListSerialization.data(fromPropertyList: ["Execution": translated], format: .xml, options: 0)
                .write(to: localization.appendingPathComponent("Localizable.strings"))
        }
        let bundle = try #require(Bundle(url: app))
        #expect(AppLanguage.chinese.localized("Execution", in: bundle) == (resourcesPresent ? translated : "Execution"))
    }

    @Test
    @MainActor
    func mathAndSyntaxResourcesProduceRenderedContent() async {
        let failures = await RenderingResourceCheck.failures()
        #expect(failures.isEmpty, "\(failures)")
    }
}
