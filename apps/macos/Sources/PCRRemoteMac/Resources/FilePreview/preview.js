"use strict";
(() => {
    const content = document.getElementById("content");
    let source, documentZip, imageURL, generation = 0, naturalWidth = 0;
    let settings = { zoom: 1, revisions: true, comments: true };
    const report = value => window.webkit?.messageHandlers.preview?.postMessage(value);
    const bytes = base64 => Uint8Array.from(atob(base64), c => c.charCodeAt(0));
    const xml = text => {
        const doc = new DOMParser().parseFromString(text, "application/xml");
        if (doc.querySelector("parsererror") || /<!DOCTYPE|<!ENTITY/i.test(text))
            throw new Error("Invalid document XML");
        return doc;
    };
    function fit() {
        if (!naturalWidth) return;
        const scale = Math.min(1, Math.max(1, innerWidth - 32) / naturalWidth) * settings.zoom;
        const element = source.kind === "image" ? content.querySelector("img") : content.querySelector(".docx-wrapper");
        if (element) element.style.zoom = scale;
    }
    async function prepareDocx(data) {
        const zip = await JSZip.loadAsync(data);
        const entries = Object.values(zip.files);
        if (entries.length > 10000) throw new Error("Document is too large");
        let expanded = 0;
        for (const entry of entries) {
            expanded += entry._data?.uncompressedSize || 0;
            if (expanded > 128 * 1024 * 1024 || (entry._data?.uncompressedSize || 0) > 32 * 1024 * 1024)
                throw new Error("Document is too large");
            if (entry.name.endsWith(".rels")) {
                const doc = xml(await entry.async("string"));
                for (const rel of Array.from(doc.documentElement.children)) {
                    if (rel.getAttribute("TargetMode") === "External" && !rel.getAttribute("Type")?.endsWith("/hyperlink"))
                        rel.remove();
                }
                zip.file(entry.name, new XMLSerializer().serializeToString(doc));
            }
        }
        const documentPart = zip.file("word/document.xml");
        if (!documentPart) throw new Error("Invalid Word document");
        const doc = xml(await documentPart.async("string"));
        const hasRevisions = doc.getElementsByTagNameNS("*", "ins").length + doc.getElementsByTagNameNS("*", "del").length > 0;
        report({ type: "features", revisions: hasRevisions, comments: !!zip.file("word/comments.xml") });
        return zip.generateAsync({ type: "uint8array", compression: "STORE" });
    }
    async function renderDocx(token) {
        const staged = document.createElement("div");
        await docx.renderAsync(documentZip, staged, staged, {
            renderChanges: settings.revisions, renderComments: settings.comments,
            renderAltChunks: false, ignoreLastRenderedPageBreak: false,
            renderHeaders: true, renderFooters: true, renderFootnotes: true, renderEndnotes: true,
            useBase64URL: true
        });
        if (token !== generation) return;
        const position = { x: scrollX, y: scrollY };
        content.replaceChildren(...staged.childNodes);
        naturalWidth = Math.max(1, ...Array.from(content.querySelectorAll("section.docx"), x => x.offsetWidth));
        fit();
        scrollTo(position.x, position.y);
        report({ type: "rendered", kind: "docx", pages: content.querySelectorAll("section.docx").length });
    }
    window.PCRPreview = {
        async load(value) {
            source = value;
            documentZip = undefined;
            naturalWidth = 0;
            if (imageURL) { URL.revokeObjectURL(imageURL); imageURL = undefined; }
            const token = ++generation;
            try {
                const data = bytes(value.base64);
                if (value.kind === "docx") {
                    documentZip = await prepareDocx(data);
                    if (token === generation) await renderDocx(token);
                } else {
                    imageURL = URL.createObjectURL(new Blob([data], { type: value.mime }));
                    const img = document.createElement("img");
                    img.className = "image-preview";
                    img.alt = value.name;
                    img.src = imageURL;
                    await img.decode();
                    if (token !== generation) return;
                    naturalWidth = img.naturalWidth;
                    content.replaceChildren(img);
                    fit();
                    report({ type: "rendered", kind: "image", width: img.naturalWidth, height: img.naturalHeight });
                }
            } catch (error) {
                if (token === generation) report({ type: "error", reason: String(error.message || error) });
            }
        },
        async configure(value) {
            const rerender = documentZip && (value.revisions !== settings.revisions || value.comments !== settings.comments);
            settings = value;
            if (rerender) await renderDocx(++generation);
            else fit();
        },
        inspection() {
            return {
                highlights: Array.from(content.querySelectorAll("span")).filter(x => getComputedStyle(x).backgroundColor === "rgb(255, 255, 0)").map(x => x.textContent),
                tables: content.querySelectorAll("table").length,
                images: Array.from(content.querySelectorAll("img")).map(x => ({ width: x.naturalWidth, height: x.naturalHeight })),
                insertions: content.querySelectorAll("ins").length, deletions: content.querySelectorAll("del").length,
                text: content.textContent
            };
        }
    };
    addEventListener("resize", fit);
    report({ type: "ready" });
})();
