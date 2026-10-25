import AppKit
import CryptoKit
import Testing
import WebKit
@testable import PCRRemoteMac

@Suite(.serialized)
@MainActor
struct RichFilePreviewTests {
    private func evaluate(_ view: WKWebView, _ script: String) async throws -> String {
        let value = try await view.evaluateJavaScript(script)
        return value as? String ?? ""
    }

    private func waitForRender(_ session: FilePreviewSession) async throws {
        for _ in 0..<200 {
            if session.rendered || session.failed { break }
            try await Task.sleep(for: .milliseconds(50))
        }
        #expect(session.rendered)
        #expect(!session.failed)
    }

    @Test func offlineWordPreviewPreservesYellowShadingTablesImagesAndRevisions() async throws {
        _ = NSApplication.shared
        let session = FilePreviewSession()
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .nonPersistent()
        configuration.userContentController.add(session, name: "preview")
        let view = WKWebView(frame: NSRect(x: 0, y: 0, width: 680, height: 800), configuration: configuration)
        view.navigationDelegate = session
        let window = NSWindow(contentRect: view.frame, styleMask: [.borderless], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false; window.contentView = view
        defer { session.stop(view); window.close() }
        session.attach(view)
        for _ in 0..<200 {
            if (try? await evaluate(view, "typeof window.PCRPreview")) == "object" { break }
            try await Task.sleep(for: .milliseconds(50))
        }
        let xml = #"""
        <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"><w:body>
        <w:p><w:r><w:rPr><w:highlight w:val="yellow"/><w:b/></w:rPr><w:t>YELLOW HIGHLIGHT</w:t></w:r></w:p>
        <w:p><w:r><w:rPr><w:shd w:fill="FFFF00"/></w:rPr><w:t>YELLOW SHADING</w:t></w:r></w:p>
        <w:p><w:ins w:id="1"><w:r><w:t>INSERTED TEXT</w:t></w:r></w:ins><w:del w:id="2"><w:r><w:delText>DELETED TEXT</w:delText></w:r></w:del></w:p>
        <w:tbl><w:tblPr/><w:tblGrid><w:gridCol w:w="2400"/></w:tblGrid><w:tr><w:tc><w:p><w:r><w:t>TABLE CELL</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
        <w:p><w:r><w:drawing><wp:inline><wp:extent cx="914400" cy="914400"/><a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture"><pic:pic><pic:blipFill><a:blip r:embed="rIdImage"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill><pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="914400" cy="914400"/></a:xfrm><a:prstGeom prst="rect"/></pic:spPr></pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>
        <w:sectPr><w:pgSz w:w="12240" w:h="15840"/><w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"/></w:sectPr>
        </w:body></w:document>
        """#
        let json = String(decoding: try JSONSerialization.data(withJSONObject: xml, options: .fragmentsAllowed), as: UTF8.self)
        let script = """
        (async () => {
          const zip = new JSZip();
          zip.file('[Content_Types].xml', '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="png" ContentType="image/png"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>');
          zip.file('_rels/.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>');
          zip.file('word/document.xml', \(json));
          zip.file('word/_rels/document.xml.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rIdImage" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/image.png"/></Relationships>');
          zip.file('word/media/image.png', 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aL1sAAAAASUVORK5CYII=', {base64:true});
          await PCRPreview.load({base64:await zip.generateAsync({type:'base64'}), kind:'docx', name:'Highlighted.docx'});
        })();
        """
        _ = try await view.evaluateJavaScript("void " + script)
        try await waitForRender(session)
        let result = try await evaluate(view, "JSON.stringify(PCRPreview.inspection())")
        let info = try #require(JSONSerialization.jsonObject(with: Data(result.utf8)) as? [String: Any])
        #expect(info["highlights"] as? [String] == ["YELLOW HIGHLIGHT", "YELLOW SHADING"])
        #expect(info["tables"] as? Int == 1)
        #expect((info["images"] as? [[String: Any]])?.first?["width"] as? Int == 1)
        #expect(info["insertions"] as? Int == 1)
        #expect(info["deletions"] as? Int == 1)
        #expect(session.hasRevisions)
        let requests = try await evaluate(view, "JSON.stringify(performance.getEntriesByType('resource').map(x => x.name).filter(x => /^https?:/.test(x)))")
        #expect(requests == "[]")
        _ = try await view.evaluateJavaScript("void PCRPreview.configure({zoom:1.25,revisions:false,comments:true})")
        try await Task.sleep(for: .milliseconds(300))
        let revised = try await evaluate(view, "JSON.stringify(PCRPreview.inspection())")
        #expect(!revised.contains("DELETED TEXT"))
        #expect(revised.contains("YELLOW HIGHLIGHT"))
        let svg = Data(#"<svg xmlns="http://www.w3.org/2000/svg" width="120" height="80"><rect width="120" height="80" fill="green"/></svg>"#.utf8).base64EncodedString()
        session.rendered = false
        _ = try await view.evaluateJavaScript("void PCRPreview.load({base64:'\(svg)',kind:'image',mime:'image/svg+xml',name:'next.svg'})")
        try await waitForRender(session)
        _ = try await view.evaluateJavaScript("void PCRPreview.configure({zoom:1,revisions:true,comments:false})")
        try await Task.sleep(for: .milliseconds(100))
        let switched = try await evaluate(view, "JSON.stringify(PCRPreview.inspection())")
        let switchedInfo = try #require(JSONSerialization.jsonObject(with: Data(switched.utf8)) as? [String: Any])
        #expect(switchedInfo["tables"] as? Int == 0)
        #expect((switchedInfo["images"] as? [[String: Any]])?.first?["width"] as? Int == 120)
        #expect(!switched.contains("YELLOW HIGHLIGHT"))
    }

    @Test func imagePreviewShowsActualPixelsAndDoesNotExecuteSVGScriptOrFetchExternalResources() async throws {
        _ = NSApplication.shared
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent("visual-\(UUID())")
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: folder) }
        let url = folder.appendingPathComponent("figure.svg")
        try Data(##"<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360"><rect width="640" height="360" fill="#0ca67a"/><script>window.PCR_BAD=true;</script><image href="https://example.invalid/tracker.png" width="10" height="10"/></svg>"##.utf8).write(to: url)
        let session = FilePreviewSession(), configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .nonPersistent()
        configuration.userContentController.add(session, name: "preview")
        let view = WKWebView(frame: NSRect(x: 0, y: 0, width: 640, height: 480), configuration: configuration)
        view.navigationDelegate = session
        defer { session.stop(view) }
        session.attach(view); session.load(url)
        try await waitForRender(session)
        #expect(session.detail == "640 \u{00d7} 360")
        #expect(try await evaluate(view, "typeof window.PCR_BAD") == "undefined")
        #expect(try await evaluate(view, "JSON.stringify(performance.getEntriesByType('resource').map(x => x.name).filter(x => /^https?:/.test(x)))") == "[]")
        let original = try await evaluate(view, "document.querySelector('img').src")
        session.load(url)
        session.configure(FilePreviewSettings(zoom: 2))
        try await Task.sleep(for: .milliseconds(100))
        #expect(try await evaluate(view, "document.querySelector('img').src") == original)
    }
}
