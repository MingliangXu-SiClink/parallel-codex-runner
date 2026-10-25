# macOS Reading And Visual Polish

## Scope

- Refine color, typography, and spacing without changing conversation, queue,
  Agent, or backend behavior. Preserve the 2000-point transcript maximum,
  1040-point composer maximum, adjustable sidebar/workspace split, persistent
  result inspector, and adjacent file preview.
- Reference: the user's supplied ChatGPT/Codex screenshots. Live access to the
  reference app was denied by the UI tool; no alternative capture or control
  was attempted. This is not a claim of exact replication or a live performance
  comparison with that app.

## Findings And Changes

- Nearly black surfaces and bright sidebar selection made the old dark theme
  unnecessarily stark. Centralized explicit sRGB neutrals: dark canvas #212121,
  sidebar #282828, composer/user bubble #303030, and selection #3a3a3a. Light
  mode uses a white canvas, #f9f9f9 sidebar, and #f4f4f4 composer/bubble.
- Removed the composer's drop shadow and softened its normal/focused outline.
  Retained the user's accent selection for links and meaningful actions; the
  conversation overflow control is neutral rather than accent-colored.
- Unified user, streaming answer, final answer, and historical answer at 16pt
  regular body type. Previously, a normal answer changed from 14pt to 15pt on
  completion, rewrapping text. Work-process text remains a smaller secondary
  level. Markdown emphasis uses semibold; inline code is regular monospaced.
- Standardized sidebar rows, title, section headings, metric captions, and
  inspector spacing. The native multiline editor shares the body font and
  scaled line spacing without replacing marked IME text.
- Replaced full-height blank paragraph lines with explicit 12pt paragraph
  spacing. The first native walkthrough also found excessive code/table gaps
  and conspicuous table borders; a second implementation pass added measured
  unboxed TextKit spacers and softer table/header colors.
- Incremented persisted transcript layout schema from 12 to 13 so previous
  font metrics cannot clip newly laid-out rows after an upgrade.

## Verification

- Final related Swift/AppKit test run: **184 tests in 25 suites passed** in
  67.548 seconds. New regressions cover all text sizes, streaming/final/history
  font equality, paragraph/list rhythm, code/table spacers, emphasis weights,
  layout-cache invalidation, and primary/secondary/placeholder contrast of at
  least 4.5:1 on reading and input backgrounds in both appearances.
- Existing native layout, IME/selection, Markdown/math/table containment,
  work-process expansion, internal scrolling, and zoom tests passed.
- Built the actual app with the macOS 26.5 SDK and signed it with the existing
  `Parallel Codex Local Signing` identity. Strict code-signing validation passed.
- Used an isolated native client and local fixture service, not screenshots
  alone: checked light/dark surfaces, Chinese/English text, tables/code, a
  three-line composer draft, standard/enlarged text, the initial 1040x680 window
  and a wide window, work-process expansion/collapse and internal scrolling,
  result usage, and file preview beside the retained inspector. Rebuilt and
  rechecked the final spacing/table refinements. No live model task was sent.
- Installed production client restored the user's original conversation and
  empty composer. Its actual settings UI showed 0.1.383 (383), dark appearance,
  standard text size, and the original orange accent. Returned to the original
  conversation after this read-only check.
- No Android, pcrd, or Relay implementation changed. This iteration does not
  claim new Android acceptance or a live streaming/CPU/GPU benchmark.

## Deployment

- Published **macOS 0.1.383 (383)** to the private Relay channel, then installed
  and restarted `/Applications/Parallel Codex.app` after the user's approval.
- Installed binary matches the verified signed build. The previous installed
  0.1.382 bundle is retained in the maintenance directory for rollback.
- pcrd remained PID 869, started 2026-09-23 19:56:53, before and after client
  replacement. It reported healthy, connected, no startup warnings, and no
  active runs at both checks. No Agent or web service was stopped or restarted.
- Android 0.1.266, pcrd 0.1.58, and Relay 0.1.18 release records/artifacts remain
  unchanged. The isolated QA client/service were stopped; the temporary remote
  upload was removed after publication and read-back verification.

Release ID: `macos-release-4093e62357484e8eb3dc0ac058a25937` (active).

Archive: `release/clients/macos/Parallel-Codex-macos-0.1.383.zip`, 13,789,926 bytes.

Archive SHA-256: `56b91af7a189dece6afbb12dbd810c4ee5853e11902561cf52ab62311166689c`

Installed binary SHA-256: `efeabcbe8d03c72440d869ec735615aec17d9ec8b57943c51cd87e0c9b4b6ce2`

Build/test logs, isolated fixture, pre-edit sources, and previous installed app:
`/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/VisualPolish-20260926-ixlwlW/`.
