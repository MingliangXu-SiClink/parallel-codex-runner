# macOS Rich File Previews

## Release Status

Verified on 2026-10-03. macOS 0.1.398 (build 398) is published to the existing private relay, installed in `/Applications/Parallel Codex.app`, and relaunched. The native client reconnected to the selected conversation and showed the ongoing run.

pcrd 0.1.67 is built and installed in an isolated runtime, but is **not enabled yet**. The activation preflight returned 75 because `pcr-20261003T022623Z-6a750dd0` is still active. The live scheduler remains 0.1.66. Activate 0.1.67 only when every active run, including synthesis Agents and finalization, has finished. No Android device action is needed.

During the client update, the pcrd service definition stayed unchanged, the active run ID stayed unchanged, and all 38 sampled Node, Codex and Python process identities were preserved. No Agent was stopped, adopted, discarded or rerun. No research source files were edited.

## Changes

- DOCX previews use a bundled offline renderer rather than the lossy prior preview path. Common Word formatting includes yellow run highlighting, paragraph shading, tables, embedded images, headings, font emphasis, headers/footers and notes. Revision and comment controls appear when present.
- Image changes show the actual before/after versions, not just the Git binary marker. Word and PDF changes use the same version comparison. Wide panes arrange versions horizontally; narrow panes arrange them vertically. Zoom controls are shared.
- Result details remain visible beside the separate preview column. The optional raw-diff mode is retained without an extra visible picker label. SVG opens visually; RTF uses the rich preview path rather than showing control syntax.
- pcrd 0.1.67 saves immutable, hash-addressed version bytes before candidate cleanup. Automatic chunk loading verifies chunk and whole-file hashes. Unchanged refreshes reuse renderers and cached URLs rather than resetting the preview.
- Preview resources are inside the signed app, not dependent on the source `.build` folder or a CDN. External document resources, active SVG content and Word altChunks are blocked. Archive expansion and image/file size limits protect the client.
- Snapshots are persisted one file pair at a time to avoid holding every binary file in memory during finalization.

## Verification

- Python: **869 tests and 102 subtests passed** (`all-python-tests.log`).
- macOS: **269 tests in 37 suites passed** (`test-client-final.log`).
- Isolated installed pcrd runtime: **30 tests passed** (`installed-runtime-tests.log`).
- Signed app build, signature validation and packaged-resource rendering checks passed.
- Independent native QA app with a private HOME and synthetic, read-only run manager: direct DOCX link, yellow highlighting/shading, tables, embedded images, revisions, shared zoom, before/after PNG and PDF, narrow/wide layouts, and light/dark appearance verified. No production task was used as a test mutation.
- After installation, the real Chinese highlighted manuscript was opened read-only from its existing conversation link in the formal client. Yellow highlighting is visibly preserved, the result-details column remains beside it, and the ongoing run remains visible. Screenshot: `formal-highlighted-docx.png`. The document itself was not edited.
- Automated regressions cover DOCX-to-SVG switching, blocked network/script execution, immutable history after source cleanup, missing old versions, path/symlink isolation, add/delete states, chunk integrity and stable cached refreshes.

Evidence is under `/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/RichPreviews-20261003`, including `native-acceptance.json`, `native-docx-dark.png`, `client-installation.json`, and relay publication records.

## Artifact Identity

- macOS archive: `release/clients/macos/Parallel-Codex-macos-0.1.398.zip`
- Archive SHA-256: `a7f396696b47e923d49db1f4a1b65843271e466b3e85d715bf880e0cf2788dac`
- Installed executable SHA-256: `47268e9655582dda33fdc8e0739eaa7d3698dd10e0fa4b84dc642b4d7c150450`
- pcrd wheel: `release/pcrd/parallel_codex_runner-0.1.67-py3-none-any.whl`
- Wheel SHA-256: `a3417d46f6e95e3ef04d09809d9316cbf834c6baa8e084237d728a6536cda31a`
- Existing Android, relay and installer artifacts are unchanged.

## Deferred Activation

Use the isolated 0.1.67 Python to run `activate_pcrd.py` in the evidence directory. The script checks test/build evidence, current service version, all `active_runs`, `active_operation` and detached worker support, with repeated idle checks before changing the scheduler interpreter. Return code 75 means leave the live service untouched. If already 0.1.67 or newer, do not downgrade or restart again. On activation, verify service health, visual comparison capability, GPT-6.1 Sol availability, pairing and existing Node service identities. The script records successful activation or rollback diagnostics.

The existing thread heartbeat was updated to **Task completion activation: pcrd 0.1.67**, active every five minutes (automation ID `pcrd-0-1-66`, renamed rather than duplicated). It stays quiet while busy and reports only a meaningful activation result or failure. Latest live checks confirmed healthy pcrd 0.1.66, paired controller, detached workers, the same active run, and no active finalization operation. Only the scheduler will be restarted when the idle guard passes. The QA app and private fixture server were stopped after verification.

## Limits

Rich document rendering is not a promise of pixel-identical Microsoft Word pagination for every document. Existing binary diffs without retained original bytes cannot be reconstructed reliably from today's workspace, so they show an honest unavailable-history state. Full before/after snapshot support depends on the pending pcrd activation and newly retained version data.

Web design collaboration was attempted in the embedded browser; the account displayed a temporary usage restriction. No consultation response was obtained and no alternate account/model was used to bypass it.
